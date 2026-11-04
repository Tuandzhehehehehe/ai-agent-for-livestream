from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote_plus

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import ConnectionError as RedisLibConnectionError
from redis.exceptions import RedisError, TimeoutError as RedisLibTimeoutError

from backend.database.mongodb import DatabaseError, MongoSettings

logger = logging.getLogger(__name__)

RedisUrlStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=8,
        pattern=r"^(?:redis|rediss|unix)://.+",
    ),
]


class RedisConnectionError(DatabaseError):
    pass


class RedisOperationError(DatabaseError):
    pass


def translate_redis_error(operation: str, exc: RedisError) -> RedisOperationError:
    return RedisOperationError(f"Redis operation '{operation}' failed: {exc}")


class RedisSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: RedisUrlStr = "redis://localhost:6379/0"
    max_connections: int = Field(default=50, ge=1, le=500)
    socket_timeout_seconds: float = Field(default=5.0, ge=0.2, le=60.0)
    socket_connect_timeout_seconds: float = Field(default=5.0, ge=0.2, le=60.0)
    decode_responses: bool = True
    health_check_interval_seconds: int = Field(default=30, ge=0, le=300)

    @classmethod
    def from_env(cls) -> RedisSettings:
        explicit_url = os.getenv("REDIS_URL")
        if not explicit_url:
            host = os.getenv("REDIS_HOST", "localhost")
            port = os.getenv("REDIS_PORT", "6379")
            db = os.getenv("REDIS_DB", "0")
            password = os.getenv("REDIS_PASSWORD", "")
            if password:
                pass_enc = quote_plus(password)
                explicit_url = f"redis://:{pass_enc}@{host}:{port}/{db}"
            else:
                explicit_url = f"redis://{host}:{port}/{db}"

        return cls(
            url=explicit_url,
            max_connections=int(os.getenv("REDIS_MAX_CONNECTIONS", "50")),
            socket_timeout_seconds=float(os.getenv("REDIS_SOCKET_TIMEOUT_SECONDS", "5.0")),
            socket_connect_timeout_seconds=float(os.getenv("REDIS_CONNECT_TIMEOUT_SECONDS", "5.0")),
            decode_responses=True,
            health_check_interval_seconds=int(os.getenv("REDIS_HEALTH_CHECK_INTERVAL_SECONDS", "30")),
        )


class DatabaseSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mongo: MongoSettings = Field(default_factory=MongoSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> DatabaseSettings:
        if env_file is not None:
            load_dotenv(dotenv_path=env_file, override=False)
        else:
            load_dotenv(override=False)

        return cls(
            mongo=MongoSettings.from_env(),
            redis=RedisSettings.from_env(),
        )


class RedisManager:
    def __init__(self, settings: RedisSettings) -> None:
        self._settings = settings
        self._pool: ConnectionPool | None = None
        self._client: Redis | None = None
        self._connected = False

    @property
    def settings(self) -> RedisSettings:
        return self._settings

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    @property
    def client(self) -> Redis:
        if self._client is None or not self._connected:
            raise RedisConnectionError("Redis client is not connected. Call connect() first.")
        return self._client

    async def connect(self) -> None:
        if self._connected and self._client is not None:
            return

        if self._client is None:
            self._pool = ConnectionPool.from_url(
                self._settings.url,
                max_connections=self._settings.max_connections,
                socket_timeout=self._settings.socket_timeout_seconds,
                socket_connect_timeout=self._settings.socket_connect_timeout_seconds,
                decode_responses=self._settings.decode_responses,
                health_check_interval=self._settings.health_check_interval_seconds,
            )
            self._client = Redis(connection_pool=self._pool)

        try:
            pong = await self._client.ping()
            if not pong:
                raise RedisConnectionError("Redis ping returned falsy response.")
            self._connected = True
            logger.info("Connected to Redis operational store.")
        except (RedisLibConnectionError, RedisLibTimeoutError, RedisError) as exc:
            await self.close()
            raise RedisConnectionError(f"Unable to connect to Redis: {exc}") from exc

    async def ping(self) -> dict[str, Any]:
        if self._client is None or not self._connected:
            return {"status": "disconnected"}
        try:
            pong = await self._client.ping()
            return {"status": "healthy" if pong else "degraded"}
        except RedisError as exc:
            return {"status": "unhealthy", "error": str(exc)}

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as exc:
                logger.warning("Error closing Redis client: %s", exc)
            finally:
                self._client = None
                self._connected = False

        if self._pool is not None:
            try:
                await self._pool.disconnect()
            except Exception as exc:
                logger.warning("Error disconnecting Redis pool: %s", exc)
            finally:
                self._pool = None
