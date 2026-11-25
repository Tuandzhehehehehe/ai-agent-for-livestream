"""Local API for receiving and classifying YouTube Live comments."""

import asyncio
from collections import Counter, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Literal
import unicodedata

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai.agent_tools import execute_agent_tool, get_tool_definitions
from ai.customer_qa import CustomerQuestionRequest, draft_customer_answer
from ai.customer_qa.safety import MAX_CUSTOMER_MESSAGE_LENGTH
from ai.customer_qa.schemas import AnswerStatus, Intent, SourceType
from ai.data import get_effective_policies, load_synthetic_data
from ai.multi_agent import MultiAgentOrchestrator, compute_lead_score
from ai.operations import CommentClassification, SimulatedComment, classify_comment
from ai.vector_rag import get_vector_rag_store
from backend.chat_safety import (
    ContentModerator,
    ViewerRateLimiter,
    configured_rate_limiter,
)
from backend.database import *
from backend.database.seed import load_demo_orders, seed_demo_store_data
from backend.infrastructure.cache import RedisKeys
from backend.local_model import LocalIntentModel, MODEL_NAME, get_local_intent_model
from backend.ollama_provider import get_ollama_provider

logger = logging.getLogger(__name__)
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
MAX_STORED_COMMENTS = 1000
MAX_COMMENT_LENGTH = 16_384
ANSWERABLE_INTENTS = frozenset(
    {
        "shipping_policy",
        "return_policy",
        "promotion_question",
        "stock_question",
        "price_question",
        "product_information",
    }
)
LOCAL_MODEL_REVIEW_REASONS = {
    "prompt_injection": "The local model detected a possible prompt injection.",
    "refund_request": "Refund requests require human handling.",
    "price_change_request": "Price changes require human handling.",
    "consequential_action": "This action requires human handling.",
    "complaint": "A customer complaint may require operator intervention.",
    "spam_or_irrelevant": "This comment may be unrelated or spam.",
}


class LiveCommentPayload(BaseModel):
    """Comment payload posted by the browser extension."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    comment_id: str = Field(alias="id", min_length=1, max_length=256)
    sender: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=MAX_COMMENT_LENGTH)
    author_type: str = Field(alias="authorType", min_length=1, max_length=64)
    timestamp: int = Field(gt=0)


class ThreadMessage(BaseModel):
    """Message in a comment's private thread."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    sender: str = Field(default="Moderator", min_length=1, max_length=256)
    author_role: Literal["ai", "moderator"] = Field(
        default="moderator", alias="authorRole"
    )
    text: str = Field(min_length=1, max_length=MAX_COMMENT_LENGTH)
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000), gt=0)


class ClassifiedLiveComment(BaseModel):
    """Accepted comment with its deterministic AI classification."""

    comment_id: str
    sender: str
    text: str
    author_type: str
    timestamp: int
    intent: Intent
    intent_source: Literal["rules", "local_model"] = "rules"
    intent_confidence: float | None = None
    lead_score: int = 0
    lead_label: Literal["hot", "warm", "cold"] = "cold"
    needs_human_review: bool
    reason: str | None = None
    moderation_status: Literal["allowed", "blocked"] = "allowed"
    moderation_reason: str | None = None
    answer_draft: "AnswerDraftResponse | None" = None
    auto_order: dict[str, Any] | None = None
    thread_messages: list[ThreadMessage] = Field(default_factory=list)


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_COMMENT_LENGTH)
    sender: str = Field(default="Viewer", min_length=1, max_length=256)
    author_type: str = Field(default="viewer", min_length=1, max_length=64)
    session_id: str | None = None


class VectorRAGSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)
    source_type: SourceType | None = None


class AnswerSourceResponse(BaseModel):
    source_type: SourceType
    source_id: str
    title: str
    excerpt: str


class AnswerDraftResponse(BaseModel):
    answer: str
    intent: Intent
    sources: list[AnswerSourceResponse]
    status: AnswerStatus
    needs_human_review: bool
    reason: str | None
    is_draft: Literal[True]


class OrderUpdateStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: OrderStatus
    payment_status: PaymentStatus | None = None
    shipping_address: str | None = None
    notes: str | None = None


def _to_safe_id(raw_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._@-]+", "_", raw_id.strip()).strip("_")
    if not cleaned:
        return "id"
    if len(cleaned) <= 128:
        return cleaned
    digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:12]
    return f"{cleaned[:115]}_{digest}"


def _safe_timestamp_to_datetime(timestamp_ms: int) -> datetime:
    try:
        return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return utc_now()


def _strip_accents(text: str) -> str:
    decomp = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomp if unicodedata.category(ch) != "Mn")
    return stripped.replace("đ", "d").replace("Đ", "D")


_BOT_ECHO_PATTERN = re.compile(
    r"^\s*(?:🤖|\[AI\])"
    r"|Order\s+#ORD-(?:LIVE|DEMO)-\d+\s+created!"
    r"|Đã\s+(?:tạo|chốt|cập\s+nhật)\s+đơn\s+#ORD-(?:LIVE|DEMO)-\d+"
    r"|^(?:@[A-Za-z0-9_.-]+\s+)?(?:Draft\s*\(synthetic\s+demo\s+data\)|Bản\s+nháp\s*\(dữ\s+liệu\s+mô\s+phỏng\)|Draft:\s*Order\s+#ORD-|Bản\s+nháp:\s*Đã\s+(?:tạo|chốt|cập\s+nhật)\s+đơn\s+#ORD-)"
    r"|the\s+catalog\s+lists\s+\d+\s+simulated\s+units\s+for"
    r"|có\s+giá\s+niêm\s+yết\s+là\s+\$\d+\.\d{2}\s*\((?:còn|hiện)",
    re.IGNORECASE,
)


def _is_bot_echo_text(text: str) -> bool:
    return bool(_BOT_ECHO_PATTERN.search(text.strip()))


_GREETING_PATTERN = re.compile(
    r"^\s*(?:hello|hi|hey|yo|good\s+(?:morning|afternoon|evening)|xin\s+chào|chào(?:\s+shop|\s+ad)?|hi\s+shop|hello\s+shop)[!.,?\s]*$",
    re.IGNORECASE,
)
_GENERAL_POLICY_PATTERN = re.compile(
    r"\b(?:purchasing|purchase|buying|store|sales|order|shop)?\s*polic(?:y|ies)\b|\bchính\s+sách\b",
    re.IGNORECASE,
)
_VIETNAMESE_CHAR_PATTERN = re.compile(
    r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]"
    r"|\b(?:giá\s+bao\s+nhiêu|gia\s+bao\s+nhieu|bao\s+nhiêu\s+tiền|bao\s+nhieu\s+tien|còn\s+hàng|con\s+hang|hết\s+hàng|het\s+hang|phí\s+ship|phi\s+ship|tiền\s+ship|tien\s+ship|giao\s+hàng|giao\s+hang|đổi\s+trả|doi\s+tra|bảo\s+hành|bao\s+hanh|khuyến\s+mãi|khuyen\s+mai|thu\s+hộ|thu\s+ho|chốt\s+đơn|chot\s+don|lên\s+đơn|len\s+don|shop\s+ơi|shop\s+oi|chot\s+\d|dat\s+\d|lay\s+\d|mua\s+\d|cho\s+(?:em|minh|anh|chi)\s+\d|den\s+ban|coc\s+su|ly\s+su|quat\s+cam\s+tay|binh\s+giu\s+nhiet|tui\s+tote|am\s+dun|khan\s+len|khan\s+choang|cu\s+sac|sac\s+usb|son\s+moi|son\s+li|mic\s+khong\s+day|bot\s+matcha|dia\s+chi|sđt|sdt)\b",
    re.IGNORECASE,
)
_ORDER_ACTION_PATTERN = re.compile(
    r"\b(?:buy|purchase|checkout|book|order|place\s+an?\s+order|i\s+want\s+to\s+(?:buy|order)|i'?d\s+like\s+to\s+(?:buy|order)|i'?ll\s+take|i\s+will\s+take|chốt|chot|chốt\s+đơn|chot\s+don|lên\s+đơn|len\s+don|mua|đặt|dat|đặt\s+hàng|dat\s+hang|lấy|lay|cho\s+(?:em|mình|minh|anh|chị|chi|tôi|toi|shop)\s+(?:mua|đặt|dat|lấy|lay|chốt|chot|\d+|một|mot|hai|ba|bốn|bon|năm|nam))\b",
    re.IGNORECASE,
)
_NEGATIVE_ORDER_PATTERN = re.compile(
    r"\b(?:don'?t|do\s+not|never|not|won'?t|wouldn'?t|shouldn'?t|cannot|can'?t)\s+(?:buy|order|purchase|take|get|book)\b"
    r"|\b(?:buy|order|purchase)\s+(?:something|anything)?\s*later\b"
    r"|\b(?:maybe\s+later|give\s+me\s+a\s+(?:shoutout|hand|break|second|minute))\b"
    r"|\b(?:đừng|dung|không|khong|chưa|chua|chẳng|chang|ko|k)\s*(?:muốn\s+|muon\s+|nên\s+|nen\s+|định\s+|dinh\s+|cần\s+|can\s+)?(?:mua|chốt|chot|đặt|dat|lấy|lay)\b"
    r"|\b(?:phân\s+vân|phan\s+van|suy\s+nghĩ\s+thêm|suy\s+nghi\s+them)\b"
    r"|\b(?:mua|đặt|dat)\s+sau(?!\s+(?:cái|cai|chiếc|chiec|hộp|hop|bình|binh|cây|cay|thỏi|thoi|bộ|bo|túi|tui|ly|cốc|coc|đèn|den|quạt|quat|khăn|khan|son|mic|ấm|am|bột|bot|củ|cu|sạc|sac)\b)\b",
    re.IGNORECASE,
)
_PAST_OR_INQUIRY_ORDER_PATTERN = re.compile(
    r"\b(?:already\s+(?:bought|ordered|purchased)|bought|ordered\s+(?:last|yesterday|a\s+week|two\s+days)|purchased\s+(?:last|yesterday)|where\s+is\s+my\s+order|track\s+my\s+order|check\s+my\s+order|my\s+order\s+status|order\s+status|take\s+a\s+look|take\s+care|take\s+time)\b"
    r"|\b(?:đã\s+(?:mua|đặt|nhận)|da\s+(?:mua|dat|nhan)|mua\s+(?:tuần\s+trước|tuan\s+truoc|hôm\s+qua|hom\s+qua|lần\s+trước|lan\s+truoc|tháng\s+trước|thang\s+truoc|hôm\s+trước|hom\s+truoc)|đơn\s+(?:hàng\s+)?của\s+(?:mình|tôi|em)\s+(?:đâu|ở\s+đâu|sao\s+rồi|khi\s+nào\s+giao)|don\s+(?:hang\s+)?cua\s+(?:minh|toi|em)\s+(?:dau|o\s+dau|sao\s+roi|khi\s+nao\s+giao))\b",
    re.IGNORECASE,
)
_INFO_INQUIRY_PATTERN = re.compile(
    r"\b(?:how\s+much|what\s+is|what\s+are|how\s+long|when\s+will|where\s+is|is\s+it\s+in\s+stock|in\s+stock\s*\?|do\s+you\s+have\s+any|giá\s+bao\s+nhiêu|gia\s+bao\s+nhieu|bao\s+nhiêu\s+tiền|bao\s+nhieu\s+tien|giá\s+sao|gia\s+sao|còn\s+hàng\s+không|con\s+hang\s+khong|còn\s+không|con\s+khong|hết\s+hàng\s+chưa|het\s+hang\s+chua|phí\s+ship|phi\s+ship|tiền\s+ship|tien\s+ship|ship\s+bao\s+(?:nhiêu|nhieu|lâu|lau)|giao\s+bao\s+lâu|giao\s+bao\s+lau|có\s+freeship\s+không|co\s+freeship\s+khong)\b",
    re.IGNORECASE,
)
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+84|0)\d(?:[\s.\-]?\d){7,9}(?!\d)")
_ADDRESS_MARKER_START_PATTERN = re.compile(
    r"\b(?:địa\s+chỉ|dia\s+chi|đ/c|d/c|address|giao\s+tới|giao\s+toi|giao\s+đến|giao\s+den|giao\s+về|giao\s+ve|ship\s+to|ship\s+tới|ship\s+toi|nhận\s+tại|nhan\s+tai)\b\s*[:\-]?\s*",
    re.IGNORECASE,
)
_ORDER_CLAUSE_BOUNDARY_IN_ADDRESS_PATTERN = re.compile(
    r"(?:[,;.\-]|\s)+\b(?:chốt|chot|chốt\s+đơn|chot\s+don|lên\s+đơn|len\s+don|mua|đặt|dat|đặt\s+hàng|dat\s+hang|lấy|lay|buy|purchase|order|checkout|book|i\s+want\s+to\s+(?:buy|order)|i'?ll\s+take|cho\s+(?:em|mình|minh|anh|chị|chi|tôi|toi|shop)\s+(?:mua|đặt|dat|lấy|lay|chốt|chot|\d+|một|mot|hai|ba|bốn|bon|năm|nam))\b",
    re.IGNORECASE,
)
_DEFERRED_OR_INVALID_ADDRESS_PATTERN = re.compile(
    r"\b(?:nhắn\s+sau|nhan\s+sau|gửi\s+sau|gui\s+sau|bổ\s+sung\s+sau|bo\s+sung\s+sau|inbox|ib\b|nhắn\s+riêng|nhan\s+rieng|chưa\s+có|chua\s+co|shop\s+ở\s+đâu|shop\s+o\s+dau|ở\s+đâu\s+vậy|o\s+dau\s+vay)\b",
    re.IGNORECASE,
)
_ADDRESS_LOCATION_HINT_PATTERN = re.compile(
    r"\b(?:quận|quan|q\.?\d+|phường|phuong|p\.?\d+|huyện|huyen|tỉnh|tinh|tp\.?|hcm|hà\s+nội|ha\s+noi|đà\s+nẵng|da\s+nang|đường|duong|phố|pho|ngõ|ngo|hẻm|hem|số\s+nhà|so\s+nha|street|st|dist|district|ward|city)\b",
    re.IGNORECASE,
)
_FAN_PERSON_IDIOM_PATTERN = re.compile(
    r"\b(?:huge|big|biggest|super|loyal|true|real|diehard|a|your|his|her|our|their)\s+fan\b|\bfan\s+(?:of|club)\b",
    re.IGNORECASE,
)
_SON_ENGLISH_KINSHIP_PATTERN = re.compile(
    r"\b(?:my|our|his|her|your|their|a|the|oldest|youngest|little|dear)\s+son\b|\bson\s+(?:loves|likes|wants|is|was|has)\b",
    re.IGNORECASE,
)
_PRODUCT_ALIASES: dict[str, tuple[str, ...]] = {
    "SYN-PROD-001": ("lumanest", "desk lamp", "lamp", "đèn bàn", "den ban"),
    "SYN-PROD-002": ("pebblebrook", "ceramic mug", "mug", "cốc sứ", "coc su", "ly sứ", "ly su", "cốc"),
    "SYN-PROD-003": ("kettle fern", "mini kettle", "kettle", "ấm đun", "am dun", "ấm siêu tốc", "am sieu toc"),
    "SYN-PROD-004": ("cloudstep", "canvas tote", "tote bag", "tote", "túi tote", "tui tote", "túi vải", "tui vai"),
    "SYN-PROD-005": ("northstar", "knit scarf", "scarf", "khăn choàng", "khan choang", "khăn len", "khan len", "khăn", "khan"),
    "SYN-PROD-006": ("morrow", "desk charger", "usb charger", "65w charger", "charger", "củ sạc", "cu sac", "sạc usb", "sac usb", "sạc nhanh", "sac nhanh"),
    "SYN-PROD-007": ("aurora", "studio ringlight", "ringlight", "ring light", "đèn livestream", "den livestream", "đèn ringlight", "den ringlight"),
    "SYN-PROD-008": ("velvet", "matte lipstick", "lipstick", "son môi", "son moi", "son lì", "son li", "son kem", "thỏi son", "thoi son", "cây son", "cay son", "son"),
    "SYN-PROD-009": ("breeze", "portable fan", "handheld fan", "mini fan", "quạt cầm tay", "quat cam tay", "quạt mini", "quat mini", "quạt", "quat", "fan"),
    "SYN-PROD-010": ("zenith", "wireless microphone", "microphone", "lavalier", "mic không dây", "mic khong day", "micro không dây", "micro khong day", "micro", "mic"),
    "SYN-PROD-011": ("matcha glow", "collagen powder", "matcha", "collagen", "bột matcha", "bot matcha", "bột collagen", "bot collagen"),
    "SYN-PROD-012": ("solaris", "insulated bottle", "thermal flask", "bottle", "bình giữ nhiệt", "binh giu nhiet"),
}
_EN_WORD_QTY: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_VI_ACCENTED_WORD_QTY: dict[str, int] = {
    "một": 1,
    "hai": 2,
    "bốn": 4,
    "năm": 5,
    "sáu": 6,
    "bảy": 7,
    "tám": 8,
    "chín": 9,
    "mười": 10,
}
_VI_UNACCENTED_WORD_QTY: dict[str, int] = {
    "mot": 1,
    "hai": 2,
    "ba": 3,
    "bon": 4,
    "nam": 5,
    "sau": 6,
    "bay": 7,
    "tam": 8,
    "chin": 9,
    "muoi": 10,
}


class OrderParseAttempt:
    def __init__(
        self,
        items: list[tuple[Any, int]],
        *,
        invalid_quantity_reason: str | None = None,
        phone: str | None = None,
        shipping_address: str | None = None,
    ) -> None:
        self.items = items
        self.invalid_quantity_reason = invalid_quantity_reason
        self.phone = phone
        self.shipping_address = shipping_address


def _is_vietnamese_text(text: str) -> bool:
    return bool(_VIETNAMESE_CHAR_PATTERN.search(text))


def _extract_phone_number(text: str) -> str | None:
    m = _PHONE_PATTERN.search(text)
    if not m:
        return None
    normalized = re.sub(r"[\s.\-]+", "", m.group(0))
    return normalized


def _locate_address_span(text: str) -> tuple[str | None, tuple[int, int] | None]:
    """Locate (cleaned_shipping_address, (span_start, span_end)) without swallowing trailing product clauses."""
    m = _ADDRESS_MARKER_START_PATTERN.search(text)
    if not m:
        return None, None

    marker_start = m.start()
    content_start = m.end()
    tail = text[content_start:]
    cut_idx = len(tail)

    # 1. Stop if an explicit order action verb starts a new clause after the address
    for candidate_tail in (tail, _strip_accents(tail)):
        b_match = _ORDER_CLAUSE_BOUNDARY_IN_ADDRESS_PATTERN.search(candidate_tail)
        if b_match and b_match.start() > 0:
            cut_idx = min(cut_idx, b_match.start())

    # 2. Stop if a comma/semicolon is followed by a product segment that is not an address part
    dataset = load_synthetic_data()
    for cm in re.finditer(r"[,;]+", tail[:cut_idx]):
        if cm.start() < 3:
            continue
        after_delim = tail[cm.end() : cut_idx]
        first_piece = re.split(r"[,;]", after_delim)[0].strip()
        if not first_piece:
            continue
        if not _ADDRESS_LOCATION_HINT_PATTERN.search(first_piece) and not _ADDRESS_LOCATION_HINT_PATTERN.search(
            _strip_accents(first_piece)
        ):
            if _match_product_in_segment(first_piece, dataset.products, full_text=first_piece) is not None:
                cut_idx = min(cut_idx, cm.start())
                break

    # 3. Stop if an undelimited "<qty><unit> <product>" phrase follows the address (e.g. "Ship tới 88 Lý Tự Trọng 2 cái đèn bàn")
    undelim_qty_pattern = re.compile(
        r"\s+(?:\d+\s*(?:x|c|cái|cai|chiếc|chiec|hộp|hop|bình|binh|cây|cay|thỏi|thoi|bộ|bo|túi|tui|ly|cốc|coc)\b"
        r"|(?:một|mot|hai|ba|bốn|bon|năm|nam|sáu|sau)\s+(?:cái|cai|chiếc|chiec|hộp|hop|bình|binh|cây|cay|thỏi|thoi|bộ|bo|túi|tui|ly|cốc|coc)\b)",
        re.IGNORECASE,
    )
    for qm in undelim_qty_pattern.finditer(tail[:cut_idx]):
        if qm.start() < 5:
            continue
        trailing_seg = tail[qm.start() : cut_idx].strip()
        if _match_product_in_segment(trailing_seg, dataset.products, full_text=trailing_seg) is not None:
            cut_idx = min(cut_idx, qm.start())
            break

    raw_addr = tail[:cut_idx]
    span = (marker_start, content_start + cut_idx)

    # Check if the viewer is deferring or asking for the shop's address ("địa chỉ mình nhắn sau nhé")
    if _DEFERRED_OR_INVALID_ADDRESS_PATTERN.search(raw_addr) or _DEFERRED_OR_INVALID_ADDRESS_PATTERN.search(
        _strip_accents(raw_addr)
    ):
        return None, span

    cleaned_addr = _PHONE_PATTERN.sub(" ", raw_addr)
    cleaned_addr = re.sub(
        r"\b(?:sđt|sdt|số\s+điện\s+thoại|so\s+dien\s+thoai|điện\s+thoại|dien\s+thoai|phone|tel|liên\s+hệ|lien\s+he)\b\s*[:\-]?",
        " ",
        cleaned_addr,
        flags=re.IGNORECASE,
    )
    cleaned_addr = re.sub(r"\s+", " ", cleaned_addr).strip(" ,;:-.")
    return (cleaned_addr if len(cleaned_addr) >= 3 else None), span


def _extract_shipping_address(text: str) -> str | None:
    addr_val, _ = _locate_address_span(text)
    return addr_val


def _strip_shipping_and_phone_spans(text: str) -> str:
    """Remove only the address span and phone numbers from order text while preserving product clauses before and after."""
    _, addr_span = _locate_address_span(text)
    if addr_span is not None:
        s_idx, e_idx = addr_span
        without_addr = f"{text[:s_idx]} , {text[e_idx:]}"
    else:
        without_addr = text
    without_phone = _PHONE_PATTERN.sub(" ", without_addr)
    without_labels = re.sub(
        r"\b(?:sđt|sdt|số\s+điện\s+thoại|so\s+dien\s+thoai|điện\s+thoại|dien\s+thoai|phone|tel|địa\s+chỉ|dia\s+chi)\b\s*[:\-]?",
        " ",
        without_phone,
        flags=re.IGNORECASE,
    )
    # Remove standalone location qualifiers like "ở quận 3", "quận 1", "q1"
    without_loc = re.sub(
        r"\b(?:ở|o|tại|tai)?\s*(?:quận|quan|q\.?|phường|phuong|p\.?|huyện|huyen)\s*\d+[a-z]?\b",
        " ",
        without_labels,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", without_loc).strip()


def _alias_allowed_in_context(alias: str, full_text: str) -> bool:
    low = full_text.casefold()
    if alias == "fan" and _FAN_PERSON_IDIOM_PATTERN.search(low):
        return False
    if alias == "son":
        if _SON_ENGLISH_KINSHIP_PATTERN.search(low):
            return False
        if not _is_vietnamese_text(full_text):
            return False
    return True


def _match_product_in_segment(
    segment: str,
    products: tuple[Any, ...],
    *,
    full_text: str | None = None,
) -> Any | None:
    ctx_text = full_text if full_text is not None else segment
    lowered = segment.casefold()
    unaccented = _strip_accents(lowered)
    matched = None
    best_len = 0
    for product in products:
        prod_name_low = product.name.casefold()
        if product.id.casefold() in lowered or prod_name_low in lowered:
            if len(prod_name_low) > best_len:
                matched = product
                best_len = len(prod_name_low)
            continue
        for alias in _PRODUCT_ALIASES.get(product.id, ()):
            if not _alias_allowed_in_context(alias, ctx_text):
                continue
            alias_unacc = _strip_accents(alias)
            if (
                re.search(rf"\b{re.escape(alias)}\b", lowered)
                or re.search(rf"\b{re.escape(alias_unacc)}\b", unaccented)
            ) and len(alias) > best_len:
                matched = product
                best_len = len(alias)
    return matched


def _find_all_products_in_text(text: str, products: tuple[Any, ...]) -> list[Any]:
    lowered = text.casefold()
    unaccented = _strip_accents(lowered)
    found: list[tuple[int, Any]] = []
    for product in products:
        prod_name_low = product.name.casefold()
        pos = lowered.find(prod_name_low)
        if pos != -1:
            found.append((pos, product))
            continue
        id_pos = lowered.find(product.id.casefold())
        if id_pos != -1:
            found.append((id_pos, product))
            continue
        best_pos = -1
        best_len = 0
        for alias in _PRODUCT_ALIASES.get(product.id, ()):
            if not _alias_allowed_in_context(alias, text):
                continue
            m = re.search(rf"\b{re.escape(alias)}\b", lowered)
            if not m:
                alias_unacc = _strip_accents(alias)
                m = re.search(rf"\b{re.escape(alias_unacc)}\b", unaccented)
            if m and len(alias) > best_len:
                best_pos = m.start()
                best_len = len(alias)
        if best_pos != -1:
            found.append((best_pos, product))
    found.sort(key=lambda item: item[0])
    return [p for _, p in found]


def _find_product_span_in_segment(segment: str, product: Any | None) -> tuple[int, int] | None:
    if product is None:
        return None
    lowered = segment.casefold()
    unaccented = _strip_accents(lowered)
    prod_name_low = product.name.casefold()
    pos = lowered.find(prod_name_low)
    if pos != -1:
        return (pos, pos + len(prod_name_low))
    id_low = product.id.casefold()
    id_pos = lowered.find(id_low)
    if id_pos != -1:
        return (id_pos, id_pos + len(id_low))
    best_span: tuple[int, int] | None = None
    best_len = 0
    for alias in _PRODUCT_ALIASES.get(product.id, ()):
        m = re.search(rf"\b{re.escape(alias)}\b", lowered)
        if not m:
            alias_unacc = _strip_accents(alias)
            m = re.search(rf"\b{re.escape(alias_unacc)}\b", unaccented)
        if m and len(alias) > best_len:
            best_span = (m.start(), m.end())
            best_len = len(alias)
    return best_span


def _extract_quantity_from_segment(
    segment: str,
    product: Any | None = None,
) -> tuple[int, bool]:
    """Return (quantity, is_valid). Numbers outside 1..20 return (raw_num, False)."""
    cleaned_seg = _strip_shipping_and_phone_spans(segment).casefold()
    # Strip product IDs (e.g. SYN-PROD-007), SKUs, order IDs, and technical specs (e.g. 65W, 500ml)
    cleaned_seg = re.sub(
        r"\b(?:syn-prod-\d+|sku-[a-z0-9-]+|ord-(?:live|demo)-\d+|var-[a-z0-9-]+)\b",
        " ",
        cleaned_seg,
        flags=re.IGNORECASE,
    )
    cleaned_seg = re.sub(
        r"\b\d+\s*(?:w|v|mah|ml|l|gb|mb|kg|g|cm|mm|inch|%|k|đ|vnd|usd)\b",
        " ",
        cleaned_seg,
        flags=re.IGNORECASE,
    )

    # 1. Match Arabic digits with optional unit/multiplier suffix like "2x", "x2", "3c", "3 cái", "2 chiếc"
    x_prefix_match = re.search(r"(?<![A-Za-z0-9_-])x\s*(-?\d+)\b", cleaned_seg)
    num_matches = re.findall(
        r"(?<![A-Za-z0-9_-])(-?\d+)(?:\s*(?:x|c|cái|cai|chiếc|chiec|sp|món|mon|hộp|hop|bình|binh|cây|cay|thỏi|thoi|bộ|bo|túi|tui|ly|cốc|coc))?(?![A-Za-z0-9_-])",
        cleaned_seg,
    )
    if num_matches:
        parsed_num = int(num_matches[0])
        if 1 <= parsed_num <= 20:
            return parsed_num, True
        return parsed_num, False
    if x_prefix_match:
        parsed_num = int(x_prefix_match.group(1))
        if 1 <= parsed_num <= 20:
            return parsed_num, True
        return parsed_num, False

    # 2. Context-aware Word Quantity extraction (only in quantifier position before the product, avoiding Vietnamese homophones!)
    prod_span = _find_product_span_in_segment(cleaned_seg, product)
    before_prod = cleaned_seg[: prod_span[0]] if prod_span is not None else cleaned_seg
    # Remove non-quantity homophone phrases ("cho ba", "tặng ba", "cho nam", "hôm sau", "trả sau", "ngã ba", "không dây")
    before_prod = re.sub(
        r"\b(?:cho|tặng|tang|biếu|bieu|của|cua|gửi|gui)\s+(?:ba\s+mẹ|ba\s+me|ba\s+má|ba\s+ma|ba|nam)\b",
        " ",
        before_prod,
        flags=re.IGNORECASE,
    )
    before_prod = re.sub(
        r"\b(?:hôm|hom|lần|lan|bữa|bua|tuần|tuan|tháng|thang|cổng|cong|phía|phia|đằng|dang|trả|tra|giao|nhận|nhan)\s+sau\b"
        r"|\b(?:miền|mien|việt|viet|phía|phia)\s+nam\b"
        r"|\b(?:ngã|nga|thứ|thu)\s+ba\b"
        r"|\b(?:sân|san)\s+bay\b",
        " ",
        before_prod,
        flags=re.IGNORECASE,
    )
    before_prod = re.sub(r"\s+", " ", before_prod).strip()

    # 2a. Explicit zero quantity ("buy zero <prod>", "mua không [cái] <prod>")
    if re.search(r"\b(?:buy|order|take|purchase)\s+zero\s*$", before_prod) or re.search(
        r"\b(?:mua|chốt|chot|đặt|dat|lấy|lay|lên\s+đơn|len\s+don|số\s+lượng|so\s+luong)\s+(?:không|khong)(?:\s+(?:cái|cai|chiếc|chiec|hộp|hop|bình|binh|cây|cay|thỏi|thoi|bộ|bo|túi|tui|ly|cốc|coc|sp|sản\s*phẩm|san\s*pham|món|mon))?\s*$",
        before_prod,
    ):
        return 0, False

    # 2b. English word quantities (one..ten) before product
    for en_word, val in _EN_WORD_QTY.items():
        if en_word == "zero":
            continue
        if re.search(rf"\b{re.escape(en_word)}\b", before_prod):
            return val, True

    # 2c. Accented Vietnamese number words (một, hai, bốn, năm, sáu, bảy, tám, chín, mười) before product
    for vi_word, val in _VI_ACCENTED_WORD_QTY.items():
        if re.search(
            rf"(?:^\s*|\b(?:mua|chốt|chot|đặt|dat|lấy|lay|lên\s+đơn|len\s+don|cho\s+\S+|và|va|với|voi|kèm|kem|thêm|them)\s+){re.escape(vi_word)}(?:\s+(?:cái|cai|chiếc|chiec|hộp|hop|bình|binh|cây|cay|thỏi|thoi|bộ|bo|túi|tui|ly|cốc|coc|sp|sản\s*phẩm|món|mon))?\s*$",
            before_prod,
        ):
            return val, True

    # 2d. Homophone-sensitive "ba" (3) and unaccented Vietnamese number words (mot..muoi)
    # If the original segment has Vietnamese diacritics, do NOT coerce unaccented "nam"/"sau" into "năm"(5)/"sáu"(6)
    has_diacritics = bool(
        re.search(
            r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]",
            segment.casefold(),
        )
    )
    if re.search(
        r"(?:^\s*|\b(?:mua|chốt|chot|đặt|dat|lấy|lay|lên\s+đơn|len\s+don|cho\s+(?:em|mình|minh|anh|chị|chi|tôi|toi|shop)|và|va|với|voi|kèm|kem|thêm|them)\s+)ba(?:\s+(?:cái|cai|chiếc|chiec|hộp|hop|bình|binh|cây|cay|thỏi|thoi|bộ|bo|túi|tui|ly|cốc|coc|sp|sản\s*phẩm|món|mon))?\s*$",
        before_prod,
    ):
        return 3, True

    if not has_diacritics:
        unacc_before = _strip_accents(before_prod)
        for unacc_word, val in _VI_UNACCENTED_WORD_QTY.items():
            if re.search(
                rf"(?:^\s*|\b(?:mua|chot|dat|lay|len\s+don|cho\s+(?:em|minh|anh|chi|toi|shop)|va|voi|kem|them)\s+){re.escape(unacc_word)}(?:\s+(?:cai|chiec|hop|binh|cay|thoi|bo|tui|ly|coc|sp|san\s*pham|mon))?\s*$",
                unacc_before,
            ):
                return val, True

    return 1, True


def _parse_order_request(text: str) -> OrderParseAttempt | None:
    """Parse single- or multi-item purchase requests while filtering out negations, past purchases, and invalid quantities."""
    cleaned = text.strip()
    if not cleaned or _is_bot_echo_text(cleaned):
        return None
    lowered = cleaned.casefold()
    unaccented = _strip_accents(lowered)

    if any(
        blocked in lowered or blocked in unaccented
        for blocked in (
            "refund",
            "return",
            "cancel",
            "damaged",
            "broken",
            "hoàn tiền",
            "hoan tien",
            "hủy đơn",
            "huy don",
            "đổi trả",
            "doi tra",
        )
    ):
        return None
    if _NEGATIVE_ORDER_PATTERN.search(lowered):
        return None
    if _PAST_OR_INQUIRY_ORDER_PATTERN.search(lowered) or _PAST_OR_INQUIRY_ORDER_PATTERN.search(unaccented):
        return None
    if _INFO_INQUIRY_PATTERN.search(lowered) or _INFO_INQUIRY_PATTERN.search(unaccented):
        return None
    if not (_ORDER_ACTION_PATTERN.search(lowered) or _ORDER_ACTION_PATTERN.search(unaccented)):
        return None

    dataset = load_synthetic_data()
    phone_val = _extract_phone_number(cleaned)
    addr_val = _extract_shipping_address(cleaned)
    order_core = _strip_shipping_and_phone_spans(cleaned)

    clauses = re.split(
        r"\s*(?:,|;|\+|&|\band\b|\bwith\b|\bvà\b|\bva\b|\bvới\b|\bvoi\b|\bkèm\b|\bkem\b|\bthêm\b|\bthem\b)\s*",
        order_core,
        flags=re.IGNORECASE,
    )
    aggregated: dict[str, tuple[Any, int]] = {}
    for clause in clauses:
        if not clause.strip():
            continue
        prod = _match_product_in_segment(clause, dataset.products, full_text=cleaned)
        if prod is None:
            continue
        qty, is_valid = _extract_quantity_from_segment(clause, product=prod)
        if not is_valid:
            is_vi = _is_vietnamese_text(cleaned)
            reason = (
                f"Số lượng đặt ({qty}) cho sản phẩm {prod.name} không hợp lệ (phải từ 1 đến 20)."
                if is_vi
                else f"Invalid order quantity ({qty}) for {prod.name}. Quantity must be between 1 and 20."
            )
            return OrderParseAttempt([], invalid_quantity_reason=reason)
        if prod.id in aggregated:
            prev_prod, prev_qty = aggregated[prod.id]
            new_qty = prev_qty + qty
            if new_qty > 20:
                return OrderParseAttempt(
                    [],
                    invalid_quantity_reason=f"Total quantity ({new_qty}) for {prod.name} exceeds per-order limit of 20.",
                )
            aggregated[prod.id] = (prev_prod, new_qty)
        else:
            aggregated[prod.id] = (prod, qty)

    if not aggregated:
        prod = _match_product_in_segment(order_core, dataset.products, full_text=cleaned)
        if prod is None:
            return None
        qty, is_valid = _extract_quantity_from_segment(order_core, product=prod)
        if not is_valid:
            is_vi = _is_vietnamese_text(cleaned)
            reason = (
                f"Số lượng đặt ({qty}) cho sản phẩm {prod.name} không hợp lệ (phải từ 1 đến 20)."
                if is_vi
                else f"Invalid order quantity ({qty}) for {prod.name}. Quantity must be between 1 and 20."
            )
            return OrderParseAttempt([], invalid_quantity_reason=reason)
        aggregated[prod.id] = (prod, qty)

    return OrderParseAttempt(
        list(aggregated.values()),
        phone=phone_val,
        shipping_address=addr_val,
    )


_EXPLICIT_QUESTION_CUE_PATTERN = re.compile(
    r"[?？]|\b(?:what|how|which|where|when|who|why|can|could|does|do|is|are|tell\s+me|giá|gia|bao\s+nhiêu|bao\s+nhieu|còn\s+hàng|con\s+hang|còn\s+không|con\s+khong|hết\s+hàng|het\s+hang|phí\s+ship|phi\s+ship|freeship|cod|đổi\s+trả|doi\s+tra|bảo\s+hành|bao\s+hanh|thông\s+tin|thong\s+tin|thông\s+số|thong\s+so|chất\s+liệu|chat\s+lieu|kích\s+thước|kich\s+thuoc|công\s+suất|cong\s+suat|màu\s+gì|mau\s+gi|dùng\s+sao|như\s+thế\s+nào|nhu\s+the\s+nao|tư\s+vấn|tu\s+van|cho\s+hỏi|cho\s+em\s+hỏi|cho\s+minh\s+hoi)\b",
    re.IGNORECASE,
)


def _fallback_livestream_draft(text: str) -> tuple[Intent, AnswerDraftResponse] | None:
    """Provide grounded livestream draft replies for greetings, general policy queries, and RAG hits."""
    cleaned = text.strip()
    if not cleaned or _is_bot_echo_text(cleaned):
        return None

    dataset = load_synthetic_data()
    if _GREETING_PATTERN.match(cleaned):
        featured = dataset.products[:3]
        featured_str = ", ".join(f"{p.name} (${p.price:.2f})" for p in featured)
        first_prod = featured[0]
        return (
            "product_information",
            AnswerDraftResponse(
                answer=(
                    f"Welcome to our livestream! Featured items today include {featured_str}. "
                    "Ask about any product price, stock, shipping, or return policy!"
                ),
                intent="product_information",
                sources=[
                    AnswerSourceResponse(
                        source_type="product",
                        source_id=first_prod.id,
                        title=first_prod.name,
                        excerpt=first_prod.description,
                    )
                ],
                status="answered",
                needs_human_review=False,
                reason=None,
                is_draft=True,
            ),
        )

    if _GENERAL_POLICY_PATTERN.search(cleaned):
        effective = get_effective_policies(dataset.policies)
        if effective:
            summaries = " ".join(p.content for p in effective[:2])
            return (
                "shipping_policy",
                AnswerDraftResponse(
                    answer=f"Draft (synthetic demo data): {summaries}",
                    intent="shipping_policy",
                    sources=[
                        AnswerSourceResponse(
                            source_type="policy",
                            source_id=p.id,
                            title=p.category,
                            excerpt=p.content,
                        )
                        for p in effective[:2]
                    ],
                    status="answered",
                    needs_human_review=False,
                    reason=None,
                    is_draft=True,
                ),
            )

    if not _EXPLICIT_QUESTION_CUE_PATTERN.search(cleaned):
        return None

    rag_hits = get_vector_rag_store(dataset).search(cleaned, top_k=1, min_score=0.18)
    if rag_hits:
        top = rag_hits[0]
        inferred_intent: Intent = (
            "shipping_policy"
            if top.source_type == "policy" and "ship" in top.title.casefold()
            else (
                "return_policy"
                if top.source_type == "policy" and "return" in top.title.casefold()
                else "product_information"
            )
        )
        return (
            inferred_intent,
            AnswerDraftResponse(
                answer=f"Draft (synthetic demo data): {top.excerpt}",
                intent=inferred_intent,
                sources=[
                    AnswerSourceResponse(
                        source_type=top.source_type,
                        source_id=top.source_id,
                        title=top.title,
                        excerpt=top.excerpt,
                    )
                ],
                status="answered",
                needs_human_review=False,
                reason=None,
                is_draft=True,
            ),
        )

    return None


_COMPLIMENT_ONLY_PATTERN = re.compile(
    r"\b(?:đẹp\s+quá|dep\s+qua|xịn\s+ghê|xin\s+ghe|xịn\s+quá|xin\s+qua|xinh\s+quá|xinh\s+qua|dễ\s+thương|de\s+thuong|ưng\s+quá|ung\s+qua|thích\s+quá|thich\s+qua|tuyệt\s+vời|tuyet\s+voi|nhìn\s+(?:đẹp|xịn|xinh|sang|ưng)|nhin\s+(?:dep|xin|xinh|sang|ung)|looks?\s+(?:so\s+)?(?:good|great|nice|awesome|pretty|cool)|love\s+this|so\s+(?:pretty|cute|nice))\b",
    re.IGNORECASE,
)


def _is_pure_compliment(text: str) -> bool:
    if "?" in text or "？" in text:
        return False
    low = text.casefold()
    unacc = _strip_accents(low)
    if _EXPLICIT_QUESTION_CUE_PATTERN.search(low) or _EXPLICIT_QUESTION_CUE_PATTERN.search(unacc):
        return False
    if _ORDER_ACTION_PATTERN.search(low) or _ORDER_ACTION_PATTERN.search(unacc):
        return False
    return bool(_COMPLIMENT_ONLY_PATTERN.search(low) or _COMPLIMENT_ONLY_PATTERN.search(unacc))


def _process_comment(
    payload: LiveCommentPayload,
    *,
    session_id: str,
    intent_model: LocalIntentModel,
    moderator: ContentModerator,
) -> ClassifiedLiveComment:
    safe_comment_id = _to_safe_id(payload.comment_id)
    author_type = payload.author_type.casefold()
    if _is_bot_echo_text(payload.text) or author_type in {"owner", "moderator"}:
        return ClassifiedLiveComment(
            comment_id=safe_comment_id,
            sender=payload.sender,
            text=payload.text,
            author_type=payload.author_type,
            timestamp=payload.timestamp,
            intent="unknown",
            needs_human_review=False,
            reason=None,
            moderation_status="allowed",
            answer_draft=None,
        )

    moderation = moderator.inspect(payload.text)
    if not moderation.allowed:
        return ClassifiedLiveComment(
            comment_id=safe_comment_id,
            sender=payload.sender,
            text=payload.text,
            author_type=payload.author_type,
            timestamp=payload.timestamp,
            intent="spam_or_irrelevant",
            needs_human_review=True,
            reason="Comment blocked by chat moderation.",
            moderation_status="blocked",
            moderation_reason=moderation.reason,
        )

    if _is_pure_compliment(payload.text):
        return ClassifiedLiveComment(
            comment_id=safe_comment_id,
            sender=payload.sender,
            text=payload.text,
            author_type=payload.author_type,
            timestamp=payload.timestamp,
            intent="unknown",
            needs_human_review=False,
            reason=None,
            moderation_status="allowed",
            answer_draft=None,
        )

    classification = classify_comment(
        SimulatedComment(comment_id=safe_comment_id, text=payload.text)
    )
    intent_source: Literal["rules", "local_model"] = "rules"
    intent_confidence = None
    if (
        classification.intent == "unknown"
        and len(payload.text) <= MAX_CUSTOMER_MESSAGE_LENGTH
    ):
        prediction = intent_model.predict(payload.text)
        intent_confidence = prediction.confidence
        if prediction.intent != "unknown":
            intent_source = "local_model"
            review_reason = LOCAL_MODEL_REVIEW_REASONS.get(prediction.intent)
            classification = CommentClassification(
                comment_id=safe_comment_id,
                intent=prediction.intent,
                needs_human_review=review_reason is not None,
                reason=review_reason,
            )

    answer_draft = None
    final_intent = classification.intent
    needs_human_review = classification.needs_human_review
    reason = classification.reason

    if author_type in {"viewer", "member", "owner", "moderator"} and (
        classification.intent in ANSWERABLE_INTENTS
        or "?" in payload.text
        or "？" in payload.text
    ):
        draft = draft_customer_answer(
            CustomerQuestionRequest(
                session_id=session_id,
                comment_id=safe_comment_id,
                text=payload.text,
            ),
            intent_override=(
                classification.intent if intent_source == "local_model" else None
            ),
        )
        if final_intent == "unknown" and draft.intent != "unknown":
            final_intent = draft.intent
        if final_intent in ANSWERABLE_INTENTS:
            needs_human_review = draft.needs_human_review
            reason = draft.reason

        if (
            draft.status == "answered"
            or draft.sources
            or "?" in payload.text
            or "？" in payload.text
        ):
            answer_draft = AnswerDraftResponse(
                answer=draft.answer,
                intent=draft.intent,
                sources=[
                    AnswerSourceResponse(
                        source_type=source.source_type,
                        source_id=source.source_id,
                        title=source.title,
                        excerpt=source.excerpt,
                    )
                    for source in draft.sources
                ],
                status=draft.status,
                needs_human_review=draft.needs_human_review,
                reason=draft.reason,
                is_draft=draft.is_draft,
            )

    if (
        answer_draft is None
        and final_intent == "unknown"
        and len(payload.text) <= MAX_CUSTOMER_MESSAGE_LENGTH
    ):
        fallback = _fallback_livestream_draft(payload.text)
        if fallback is not None:
            final_intent, answer_draft = fallback
            needs_human_review = False
            reason = None

    lead_score, lead_label = compute_lead_score(payload.text, final_intent)

    return ClassifiedLiveComment(
        comment_id=safe_comment_id,
        sender=payload.sender,
        text=payload.text,
        author_type=payload.author_type,
        timestamp=payload.timestamp,
        intent=final_intent,
        intent_source=intent_source,
        intent_confidence=intent_confidence,
        lead_score=lead_score,
        lead_label=lead_label,
        needs_human_review=needs_human_review,
        reason=reason,
        moderation_status="allowed",
        answer_draft=answer_draft,
    )


def create_app(
    *,
    local_model: LocalIntentModel | None = None,
    rate_limiter: ViewerRateLimiter | None = None,
    content_moderator: ContentModerator | None = None,
    db_facade: StoreDatabaseFacade | None = None,
) -> FastAPI:
    """Build an app wired to MongoDB/Redis with an in-memory fallback buffer."""
    recent_comments: deque[ClassifiedLiveComment] = deque(maxlen=MAX_STORED_COMMENTS)
    threads_by_comment: dict[str, list[ThreadMessage]] = {}
    intent_model = local_model or get_local_intent_model()
    viewer_rate_limiter = rate_limiter or configured_rate_limiter()
    moderator = content_moderator or ContentModerator.from_environment()

    store_id = _to_safe_id(os.getenv("LIVESTREAM_STORE_ID", "default-store"))
    session_id = _to_safe_id(os.getenv("LIVESTREAM_SESSION_ID", "youtube-live"))
    tenant_ctx = TenantContext.system_context(store_id)
    db_settings = DatabaseSettings.from_env()
    mongo_manager = MongoDatabaseManager(db_settings.mongo)
    redis_manager = RedisManager(db_settings.redis)
    live_orders: list[OrderDocument] = list(
        load_demo_orders(store_id=store_id, session_id=session_id)
    )
    in_memory_stock_sold: dict[str, int] = {}
    order_lock = asyncio.Lock()

    def _upsert_recent_comment(item: ClassifiedLiveComment) -> None:
        for idx, existing in enumerate(recent_comments):
            if existing.comment_id == item.comment_id:
                recent_comments[idx] = item
                return
        if len(recent_comments) == MAX_STORED_COMMENTS:
            evicted = recent_comments[0]
            if evicted.comment_id != item.comment_id:
                threads_by_comment.pop(evicted.comment_id, None)
        recent_comments.append(item)

    async def _get_live_stock(
        facade: StoreDatabaseFacade | None,
        product_id: str,
        default_stock: int,
    ) -> int:
        if facade is not None:
            try:
                inv = await facade.products.get_inventory(
                    tenant_ctx, product_id=product_id
                )
                if inv is not None:
                    return inv.quantity_available
            except DatabaseError:
                pass
        return max(0, default_stock - in_memory_stock_sold.get(product_id, 0))

    async def _next_order_id(facade: StoreDatabaseFacade | None) -> str:
        max_seq = 0
        candidates: list[OrderDocument] = list(live_orders)
        if facade is not None:
            try:
                db_orders = await facade.orders.list_orders(tenant_ctx, limit=500)
                candidates.extend(db_orders)
            except DatabaseError:
                pass
        for ord_doc in candidates:
            m = re.search(r"ORD-(?:LIVE|DEMO)-(\d+)", ord_doc.order_id)
            if m:
                max_seq = max(max_seq, int(m.group(1)))
        next_seq = max(max_seq + 1, len(live_orders) + 1)
        while True:
            candidate_id = _to_safe_id(f"ORD-LIVE-{next_seq:04d}")
            if any(o.order_id == candidate_id for o in live_orders):
                next_seq += 1
                continue
            if facade is not None:
                try:
                    if await facade.orders.get_by_id(tenant_ctx, candidate_id) is not None:
                        next_seq += 1
                        continue
                except DatabaseError:
                    pass
            return candidate_id

    async def _enhance_vietnamese_and_live_stock_qa(
        facade: StoreDatabaseFacade | None,
        text: str,
        result: ClassifiedLiveComment,
    ) -> None:
        """Improve Vietnamese Q&A accuracy and ground stock/price answers in live DB inventory."""
        cleaned = text.strip()
        if not cleaned or _is_bot_echo_text(cleaned) or _is_pure_compliment(cleaned):
            return
        lowered = cleaned.casefold()
        unaccented = _strip_accents(lowered)
        is_vi = _is_vietnamese_text(cleaned)
        dataset = load_synthetic_data()
        matched_prods = _find_all_products_in_text(cleaned, dataset.products)
        policies_by_cat = {
            p.category.casefold(): p for p in get_effective_policies(dataset.policies)
        }

        # 1. If English/Vietnamese stock_question was already answered from static JSON, refresh with live DB stock
        if result.intent == "stock_question" and matched_prods:
            prod = matched_prods[0]
            live_qty = await _get_live_stock(facade, prod.id, prod.stock_quantity)
            prod_source = AnswerSourceResponse(
                source_type="product",
                source_id=prod.id,
                title=prod.name,
                excerpt=prod.description,
            )
            if is_vi:
                ans = (
                    f"Bản nháp (dữ liệu mô phỏng): Sản phẩm {prod.name} hiện còn {live_qty} sản phẩm trong kho (Giá: ${prod.price:.2f})."
                    if live_qty > 0
                    else f"Bản nháp (dữ liệu mô phỏng): Sản phẩm {prod.name} hiện đã hết hàng (tồn kho: 0 sản phẩm)."
                )
            else:
                ans = (
                    f"Draft (synthetic demo data): the catalog lists {live_qty} simulated units for {prod.name}. This is not live inventory."
                    if live_qty > 0
                    else f"Draft (synthetic demo data): {prod.name} is currently out of stock (0 simulated units)."
                )
            result.needs_human_review = False
            result.reason = None
            result.answer_draft = AnswerDraftResponse(
                answer=ans,
                intent="stock_question",
                sources=[prod_source],
                status="answered",
                needs_human_review=False,
                reason=None,
                is_draft=True,
            )
            return

        if not is_vi:
            return

        # 2. Vietnamese shipping fee / COD / freeship vs delivery time
        asks_shipping_fee_or_cod = bool(
            re.search(
                r"\b(?:phí\s+ship|phi\s+ship|tiền\s+ship|tien\s+ship|ship\s+bao\s+nhiêu|ship\s+bao\s+nhieu|giá\s+ship|gia\s+ship|freeship|miễn\s+phí\s+(?:ship|vận\s+chuyển)|mien\s+phi\s+(?:ship|van\s+chuyen)|cod|thu\s+hộ|thu\s+ho|nhận\s+hàng\s+trả\s+tiền|nhan\s+hang\s+tra\s+tien)\b",
                lowered,
            )
            or re.search(
                r"\b(?:phi\s+ship|tien\s+ship|ship\s+bao\s+nhieu|gia\s+ship|freeship|cod|thu\s+ho)\b",
                unaccented,
            )
        )
        asks_delivery_time = bool(
            re.search(
                r"\b(?:giao\s+(?:hàng\s+)?bao\s+lâu|giao\s+(?:hang\s+)?bao\s+lau|ship\s+bao\s+lâu|ship\s+bao\s+lau|mấy\s+ngày\s+(?:nhận|giao|tới)|may\s+ngay\s+(?:nhan|giao|toi)|bao\s+lâu\s+nhận\s+được|chính\s+sách\s+(?:vận\s+chuyển|giao\s+hàng)|chinh\s+sach\s+(?:van\s+chuyen|giao\s+hang))\b",
                lowered,
            )
        )
        if asks_shipping_fee_or_cod or asks_delivery_time:
            ship_pol = policies_by_cat.get("shipping")
            promo_pol = policies_by_cat.get("promotions")
            sources: list[AnswerSourceResponse] = []
            if ship_pol:
                sources.append(
                    AnswerSourceResponse(
                        source_type="policy",
                        source_id=ship_pol.id,
                        title=ship_pol.category,
                        excerpt=ship_pol.content,
                    )
                )
            if promo_pol and asks_shipping_fee_or_cod:
                sources.append(
                    AnswerSourceResponse(
                        source_type="policy",
                        source_id=promo_pol.id,
                        title=promo_pol.category,
                        excerpt=promo_pol.content,
                    )
                )
            if sources:
                ans = (
                    "Bản nháp (dữ liệu mô phỏng): Shop hỗ trợ ship COD toàn quốc (nhận hàng thanh toán). Phí ship tiêu chuẩn $4.99, Freeship cho đơn từ $30.00, xuất kho trong 3 ngày làm việc."
                    if asks_shipping_fee_or_cod
                    else "Bản nháp (dữ liệu mô phỏng): Đơn hàng được xuất kho trong 3 ngày làm việc (Freeship đơn từ $30.00, đơn dưới $30.00 phí ship $4.99)."
                )
                result.intent = "shipping_policy"
                result.needs_human_review = False
                result.reason = None
                result.answer_draft = AnswerDraftResponse(
                    answer=ans,
                    intent="shipping_policy",
                    sources=sources,
                    status="answered",
                    needs_human_review=False,
                    reason=None,
                    is_draft=True,
                )
                return

        # 3. Vietnamese return / exchange policy
        if (
            re.search(
                r"\b(?:đổi\s+trả|doi\s+tra|trả\s+hàng|tra\s+hang|hoàn\s+trả|hoan\s+tra|chính\s+sách\s+đổi|chinh\s+sach\s+doi|bảo\s+hành|bao\s+hanh)\b",
                lowered,
            )
            and not re.search(
                r"\b(?:hoàn\s+tiền\s+ngay|hoan\s+tien\s+ngay|bắt\s+đền|bat\s+den|lừa\s+đảo|lua\s+dao)\b",
                lowered,
            )
        ):
            ret_pol = policies_by_cat.get("returns")
            if ret_pol:
                result.intent = "return_policy"
                result.needs_human_review = False
                result.reason = None
                result.answer_draft = AnswerDraftResponse(
                    answer="Bản nháp (dữ liệu mô phỏng): Theo chính sách đổi trả, yêu cầu đổi hoặc trả hàng được tiếp nhận xem xét trong vòng 14 ngày kể từ khi nhận hàng.",
                    intent="return_policy",
                    sources=[
                        AnswerSourceResponse(
                            source_type="policy",
                            source_id=ret_pol.id,
                            title=ret_pol.category,
                            excerpt=ret_pol.content,
                        )
                    ],
                    status="answered",
                    needs_human_review=False,
                    reason=None,
                    is_draft=True,
                )
                return

        # 4. Vietnamese price / stock / explicit product info questions (do NOT trigger on compliments!)
        asks_price_vi = bool(
            re.search(
                r"\b(?:giá|gia|bao\s+nhiêu\s+tiền|bao\s+nhieu\s+tien|bao\s+nhiêu|bao\s+nhieu|báo\s+giá|bao\s+gia|mấy\s+tiền|may\s+tien)\b",
                lowered,
            )
        )
        asks_stock_vi = bool(
            re.search(
                r"\b(?:còn\s+hàng|con\s+hang|hết\s+hàng|het\s+hang|còn\s+không|con\s+khong|tồn\s+kho|ton\s+kho|còn\s+mấy|con\s+may)\b",
                lowered,
            )
        )
        asks_product_info_vi = bool(
            "?" in cleaned
            or "？" in cleaned
            or re.search(
                r"\b(?:thông\s+tin|thong\s+tin|thông\s+số|thong\s+so|chất\s+liệu|chat\s+lieu|kích\s+thước|kich\s+thuoc|công\s+suất|cong\s+suat|màu\s+gì|mau\s+gi|có\s+màu|co\s+mau|pin\s+bao\s+lâu|dùng\s+sao|như\s+thế\s+nào|nhu\s+the\s+nao|tư\s+vấn|tu\s+van|cho\s+(?:em\s+|mình\s+|minh\s+)?hỏi|cho\s+(?:em\s+|minh\s+)?hoi)\b",
                lowered,
            )
        )
        if matched_prods and (asks_price_vi or asks_stock_vi or asks_product_info_vi):
            prod = matched_prods[0]
            live_qty = await _get_live_stock(facade, prod.id, prod.stock_quantity)
            prod_source = AnswerSourceResponse(
                source_type="product",
                source_id=prod.id,
                title=prod.name,
                excerpt=prod.description,
            )
            if asks_stock_vi and not asks_price_vi:
                target_intent: Intent = "stock_question"
                ans = (
                    f"Bản nháp (dữ liệu mô phỏng): Sản phẩm {prod.name} hiện còn {live_qty} sản phẩm trong kho (Giá: ${prod.price:.2f})."
                    if live_qty > 0
                    else f"Bản nháp (dữ liệu mô phỏng): Sản phẩm {prod.name} hiện đã hết hàng (tồn kho: 0 sản phẩm)."
                )
            elif asks_price_vi:
                target_intent = "price_question"
                stock_note = f"còn {live_qty} sản phẩm" if live_qty > 0 else "hiện đang hết hàng"
                ans = f"Bản nháp (dữ liệu mô phỏng): Sản phẩm {prod.name} có giá niêm yết là ${prod.price:.2f} ({stock_note})."
            else:
                target_intent = "product_information"
                attrs = ", ".join(f"{k}={v}" for k, v in sorted(prod.attributes.items()))
                ans = f"Bản nháp (dữ liệu mô phỏng): {prod.name} có giá ${prod.price:.2f}, tồn kho còn {live_qty} sản phẩm (thuộc tính: {attrs})."

            result.intent = target_intent
            result.needs_human_review = False
            result.reason = None
            result.answer_draft = AnswerDraftResponse(
                answer=ans,
                intent=target_intent,
                sources=[prod_source],
                status="answered",
                needs_human_review=False,
                reason=None,
                is_draft=True,
            )

    async def _load_comments_from_repository(
        facade: StoreDatabaseFacade,
        *,
        limit: int = MAX_STORED_COMMENTS,
    ) -> list[ClassifiedLiveComment]:
        db_comments = await facade.comments.list_recent_for_session(
            tenant_ctx, session_id, limit=limit
        )
        db_drafts = await facade.answer_drafts.list_for_session(
            tenant_ctx, session_id, limit=min(limit * 2, MAX_STORED_COMMENTS * 2)
        )
        draft_by_comment: dict[str, AnswerDraftDocument] = {}
        for draft_doc in db_drafts:
            draft_by_comment.setdefault(draft_doc.comment_id, draft_doc)

        order_by_comment: dict[str, dict[str, Any]] = {}
        try:
            db_orders = await facade.orders.list_orders(tenant_ctx, limit=500)
            for ord_doc in db_orders:
                for cid_match in re.findall(r"\[comment_id:([^\]]+)\]", ord_doc.notes or ""):
                    order_by_comment.setdefault(
                        cid_match, ord_doc.model_dump(mode="json")
                    )
        except DatabaseError:
            pass

        memory_by_id = {c.comment_id: c for c in recent_comments}
        items: list[ClassifiedLiveComment] = []

        for doc in db_comments:
            mem_item = memory_by_id.get(doc.comment_id)
            draft_doc = draft_by_comment.get(doc.comment_id)
            intent_val: Intent = (
                doc.intent  # type: ignore[assignment]
                if doc.intent in LOCAL_MODEL_REVIEW_REASONS
                or doc.intent in ANSWERABLE_INTENTS
                or doc.intent == "unknown"
                else (mem_item.intent if mem_item else "unknown")
            )
            answer_draft: AnswerDraftResponse | None = None
            if draft_doc is not None:
                draft_needs_review = not draft_doc.grounding_passed or draft_doc.fallback_used
                draft_reason = (
                    mem_item.answer_draft.reason
                    if mem_item and mem_item.answer_draft
                    else (
                        "Draft requires operator verification before sending."
                        if draft_needs_review
                        else None
                    )
                )
                answer_draft = AnswerDraftResponse(
                    answer=draft_doc.suggested_reply,
                    intent=intent_val,
                    sources=[
                        AnswerSourceResponse(
                            source_type=(
                                src.source_type  # type: ignore[arg-type]
                                if src.source_type in {"product", "faq", "policy"}
                                else "product"
                            ),
                            source_id=src.source_id,
                            title=src.title,
                            excerpt=src.snippet,
                        )
                        for src in draft_doc.sources
                    ],
                    status="needs_review" if draft_needs_review else "answered",
                    needs_human_review=draft_needs_review,
                    reason=draft_reason,
                    is_draft=True,
                )
            elif mem_item is not None:
                answer_draft = mem_item.answer_draft

            needs_review = (
                True
                if doc.safety_blocked
                else (
                    answer_draft.needs_human_review
                    if answer_draft is not None
                    else (
                        mem_item.needs_human_review
                        if mem_item is not None
                        else intent_val in LOCAL_MODEL_REVIEW_REASONS
                    )
                )
            )
            reason = (
                "Comment blocked by chat moderation."
                if doc.safety_blocked
                else (
                    mem_item.reason
                    if mem_item is not None
                    else (
                        answer_draft.reason
                        if answer_draft is not None
                        else LOCAL_MODEL_REVIEW_REASONS.get(intent_val)
                    )
                )
            )

            thread = threads_by_comment.get(doc.comment_id)
            if thread is None:
                reconstructed_thread: list[ThreadMessage] = []
                ts_ms = int(doc.timestamp.timestamp() * 1000)
                if (
                    answer_draft is not None
                    and answer_draft.status == "answered"
                    and not needs_review
                    and answer_draft.answer
                ):
                    reconstructed_thread.append(
                        ThreadMessage(
                            sender="AI Draft",
                            author_role="ai",
                            text=answer_draft.answer,
                            timestamp=ts_ms,
                        )
                    )
                if (
                    draft_doc is not None
                    and draft_doc.seller_action in {"approved", "edited"}
                    and draft_doc.final_reply_text
                    and (
                        not reconstructed_thread
                        or draft_doc.final_reply_text != reconstructed_thread[0].text
                    )
                ):
                    acted_ms = (
                        int(draft_doc.acted_at.timestamp() * 1000)
                        if draft_doc.acted_at
                        else ts_ms
                    )
                    reconstructed_thread.append(
                        ThreadMessage(
                            sender="Moderator",
                            author_role="moderator",
                            text=draft_doc.final_reply_text,
                            timestamp=acted_ms,
                        )
                    )
                threads_by_comment[doc.comment_id] = reconstructed_thread
                thread = reconstructed_thread

            lead_label_val: Literal["hot", "warm", "cold"] = (
                doc.lead_label  # type: ignore[assignment]
                if doc.lead_label in {"hot", "warm", "cold"}
                else (mem_item.lead_label if mem_item else "cold")
            )
            reconstructed_order = (
                order_by_comment.get(doc.comment_id)
                or (mem_item.auto_order if mem_item else None)
            )
            items.append(
                ClassifiedLiveComment(
                    comment_id=doc.comment_id,
                    sender=doc.user_handle,
                    text=doc.message,
                    author_type=mem_item.author_type if mem_item else "viewer",
                    timestamp=int(doc.timestamp.timestamp() * 1000),
                    intent=intent_val,
                    intent_source=mem_item.intent_source if mem_item else "rules",
                    intent_confidence=doc.intent_confidence,
                    lead_score=doc.lead_score if doc.lead_score is not None else 0,
                    lead_label=lead_label_val,
                    needs_human_review=needs_review,
                    reason=reason,
                    moderation_status="blocked" if doc.safety_blocked else "allowed",
                    moderation_reason=doc.safety_reason,
                    answer_draft=answer_draft,
                    auto_order=reconstructed_order,
                    thread_messages=list(thread),
                )
            )
        return items

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if db_facade is not None:
            app.state.db = db_facade
            try:
                existing = await _load_comments_from_repository(
                    db_facade, limit=MAX_STORED_COMMENTS
                )
                for item in reversed(existing):
                    _upsert_recent_comment(item)
            except DatabaseError as exc:
                logger.warning("Failed to hydrate initial comments from custom facade: %s", exc)
            yield
            return

        app.state.db = None
        mongo_connected = False
        redis_connected = False
        active_mongo: Any = mongo_manager
        active_redis: Any = redis_manager
        try:
            await mongo_manager.connect()
            await initialize_database(mongo_manager.database)
            mongo_connected = True
        except DatabaseError as exc:
            logger.info("External MongoDB unavailable (%s); activating persistent local livestream_sales_db engine.", exc)
            active_mongo = EmbeddedMongoDatabaseManager(
                db_settings.mongo.database,
                persist_path=DEFAULT_LOCAL_DB_PATH,
            )
            await active_mongo.connect()
            await initialize_database(active_mongo.database)

        if mongo_connected:
            try:
                await redis_manager.connect()
                redis_connected = True
            except DatabaseError as exc:
                logger.info("External Redis unavailable (%s); activating embedded Redis engine.", exc)
                active_redis = EmbeddedRedisManager()
                await active_redis.connect()
        else:
            active_redis = EmbeddedRedisManager()
            await active_redis.connect()

        try:
            facade = StoreDatabaseFacade(active_mongo, active_redis)
            await seed_demo_store_data(facade, tenant_ctx, session_id=session_id)
            app.state.db = facade
            db_orders = await facade.orders.list_orders(tenant_ctx, limit=500)
            if db_orders:
                live_orders.clear()
                live_orders.extend(db_orders)
            existing = await _load_comments_from_repository(
                facade, limit=MAX_STORED_COMMENTS
            )
            for item in reversed(existing):
                _upsert_recent_comment(item)
        except DatabaseError as exc:
            logger.warning("Database session initialization failed, using in-memory mode: %s", exc)

        try:
            yield
        finally:
            if not redis_connected and hasattr(active_redis, "close"):
                await active_redis.close()
            if not mongo_connected and hasattr(active_mongo, "close"):
                await active_mongo.close()
            if redis_connected:
                await redis_manager.close()
            if mongo_connected:
                await mongo_manager.close()

    app = FastAPI(title="Livestream AI Backend", version="0.1.0", lifespan=lifespan)
    app.state.db = db_facade
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    orchestrator = MultiAgentOrchestrator(
        intent_model=intent_model,
        moderator=moderator,
    )
    vector_store = get_vector_rag_store()
    dashboard_path = Path(__file__).resolve().parent / "dashboard.html"

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/dashboard", response_class=HTMLResponse)
    def serve_dashboard() -> HTMLResponse:
        if dashboard_path.is_file():
            return HTMLResponse(dashboard_path.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Livestream AI Backend is running</h1>")

    @app.get("/health")
    async def health() -> dict[str, str | int | bool | None]:
        ollama = get_ollama_provider()
        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        mongo_ping = (
            await facade.mongo.ping()
            if facade is not None and getattr(facade, "mongo", None) is not None
            else await mongo_manager.ping()
        )
        redis_ping = (
            await facade.redis.ping()
            if facade is not None and getattr(facade, "redis", None) is not None
            else await redis_manager.ping()
        )
        return {
            "status": "ok",
            "ai_status": "ready",
            "ai_model": MODEL_NAME,
            "training_examples": intent_model.training_example_count,
            "ollama_enabled": ollama.enabled,
            "ollama_configured": ollama.is_configured,
            "ollama_model": ollama.model or None,
            "mongo_status": mongo_ping.get("status"),
            "redis_status": redis_ping.get("status"),
            "orchestrator_backend": orchestrator.architecture_summary["orchestrator_backend"],
            "vector_rag_backend": vector_store.backend,
        }

    @app.post("/api/plugin/comments", response_model=ClassifiedLiveComment)
    async def receive_comment(payload: LiveCommentPayload) -> ClassifiedLiveComment:
        safe_incoming_id = _to_safe_id(payload.comment_id)
        for existing in recent_comments:
            if existing.comment_id == safe_incoming_id:
                return existing

        author_type = payload.author_type.casefold()
        if author_type in {"viewer", "member"}:
            retry_after = viewer_rate_limiter.consume(payload.sender)
            if retry_after is not None:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "viewer_rate_limited",
                        "message": "Too many comments from this viewer. Try again later.",
                    },
                    headers={"Retry-After": str(retry_after)},
                )

        result = _process_comment(
            payload,
            session_id=session_id,
            intent_model=intent_model,
            moderator=moderator,
        )
        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)

        if result.moderation_status == "allowed" and author_type not in {"owner", "moderator"}:
            await _enhance_vietnamese_and_live_stock_qa(facade, payload.text, result)

        can_attempt_order = (
            result.moderation_status == "allowed"
            and author_type not in {"owner", "moderator"}
            and not _is_bot_echo_text(payload.text)
        )
        order_attempt = (
            _parse_order_request(payload.text) if can_attempt_order else None
        )

        def _sync_live_order(updated_doc: OrderDocument) -> None:
            for idx, existing_ord in enumerate(live_orders):
                if existing_ord.order_id == updated_doc.order_id:
                    live_orders[idx] = updated_doc
                    return
            live_orders.insert(0, updated_doc)

        def _apply_shipping_update_to_order(
            ord_doc: OrderDocument,
            new_phone: str | None,
            new_addr: str | None,
            cid: str,
        ) -> OrderDocument:
            prev_addr_raw = ord_doc.shipping_address or ""
            prev_phone = _extract_phone_number(prev_addr_raw)
            prev_addr: str | None = None
            if prev_addr_raw and not prev_addr_raw.lower().startswith("pending"):
                stripped_prev = re.sub(r"\s*\(Phone:[^)]*\)\s*$", "", prev_addr_raw).strip()
                if stripped_prev and not stripped_prev.lower().startswith("pending"):
                    prev_addr = stripped_prev

            merged_phone = new_phone or prev_phone
            merged_addr = new_addr or prev_addr
            if merged_phone and merged_addr:
                ord_doc.shipping_address = f"{merged_addr} (Phone: {merged_phone})"[:500]
                ord_doc.status = "confirmed"
                ord_doc.payment_status = "cod"
            elif merged_addr:
                ord_doc.shipping_address = f"{merged_addr} (Phone: pending)"[:500]
            elif merged_phone:
                ord_doc.shipping_address = f"Pending shipping address (Phone: {merged_phone})"[:500]

            tag = f"[comment_id:{cid}]"
            if tag not in (ord_doc.notes or ""):
                ord_doc.notes = f"{tag} {ord_doc.notes or ''}".strip()[:1000]
            _sync_live_order(ord_doc)
            return ord_doc

        async def _find_viewer_pending_orders(handle: str) -> list[OrderDocument]:
            candidates: list[OrderDocument] = []
            seen_ord_ids: set[str] = set()
            for o in live_orders:
                if (
                    o.customer_handle.casefold() == handle.casefold()
                    and o.status == "pending"
                    and o.order_id not in seen_ord_ids
                ):
                    candidates.append(o)
                    seen_ord_ids.add(o.order_id)
            if facade is not None:
                try:
                    db_cust_orders = await facade.orders.list_orders(
                        tenant_ctx, customer_handle=handle, limit=20
                    )
                    for o in db_cust_orders:
                        if (
                            o.customer_handle.casefold() == handle.casefold()
                            and o.status == "pending"
                            and o.order_id not in seen_ord_ids
                        ):
                            candidates.append(o)
                            seen_ord_ids.add(o.order_id)
                except DatabaseError:
                    pass
            return candidates

        if order_attempt is not None:
            is_vi = _is_vietnamese_text(payload.text)
            if order_attempt.invalid_quantity_reason is not None:
                result.needs_human_review = True
                result.reason = order_attempt.invalid_quantity_reason
                result.answer_draft = AnswerDraftResponse(
                    answer=f"Draft: {order_attempt.invalid_quantity_reason}",
                    intent="consequential_action",
                    sources=[],
                    status="needs_review",
                    needs_human_review=True,
                    reason=order_attempt.invalid_quantity_reason,
                    is_draft=True,
                )
            elif order_attempt.items:
                async with order_lock:
                    clean_handle = payload.sender.strip() or "@viewer"
                    clean_name = clean_handle.lstrip("@").strip() or "Livestream Buyer"
                    customer_id = _to_safe_id(f"cust-{clean_name.lower()}")
                    requested_sig = tuple(
                        sorted((p.id, int(q)) for p, q in order_attempt.items)
                    )

                    # Check if viewer already has an identical pending order
                    existing_dup: OrderDocument | None = None
                    for prev_ord in await _find_viewer_pending_orders(clean_handle):
                        prev_sig = tuple(
                            sorted((it.product_id, int(it.quantity)) for it in prev_ord.items)
                        )
                        if prev_sig == requested_sig:
                            existing_dup = prev_ord
                            break

                    is_followup_shipping_update = bool(
                        existing_dup is not None
                        and (order_attempt.phone or order_attempt.shipping_address)
                    )
                    out_of_stock_items: list[Any] = []
                    insufficient_items: list[tuple[Any, int, int]] = []
                    if not is_followup_shipping_update:
                        for prod, qty in order_attempt.items:
                            avail_qty = await _get_live_stock(
                                facade, prod.id, prod.stock_quantity
                            )
                            if avail_qty <= 0:
                                out_of_stock_items.append(prod)
                            elif qty > avail_qty and existing_dup is None:
                                insufficient_items.append((prod, qty, avail_qty))

                    if out_of_stock_items:
                        oos_names = ", ".join(p.name for p in out_of_stock_items)
                        oos_sources = [
                            AnswerSourceResponse(
                                source_type="product",
                                source_id=p.id,
                                title=p.name,
                                excerpt=p.description,
                            )
                            for p in out_of_stock_items
                        ]
                        oos_msg = (
                            f"Bản nháp (dữ liệu mô phỏng): Rất tiếc, sản phẩm {oos_names} hiện đã hết hàng (tồn kho: 0) nên chưa thể lên đơn."
                            if is_vi
                            else f"Draft (synthetic demo data): Sorry, {oos_names} is currently out of stock (0 available) and cannot be ordered right now."
                        )
                        result.intent = "stock_question"
                        result.needs_human_review = False
                        result.reason = None
                        result.answer_draft = AnswerDraftResponse(
                            answer=oos_msg,
                            intent="stock_question",
                            sources=oos_sources,
                            status="answered",
                            needs_human_review=False,
                            reason=None,
                            is_draft=True,
                        )
                    elif insufficient_items:
                        prod, req_qty, avail_qty = insufficient_items[0]
                        ins_reason = (
                            f"Số lượng đặt ({req_qty}) cho {prod.name} vượt quá tồn kho hiện tại (chỉ còn {avail_qty} sản phẩm)."
                            if is_vi
                            else f"Requested quantity ({req_qty}) for {prod.name} exceeds available stock ({avail_qty} remaining)."
                        )
                        result.intent = "stock_question"
                        result.needs_human_review = True
                        result.reason = ins_reason
                        result.answer_draft = AnswerDraftResponse(
                            answer=f"Draft: {ins_reason}",
                            intent="stock_question",
                            sources=[
                                AnswerSourceResponse(
                                    source_type="product",
                                    source_id=prod.id,
                                    title=prod.name,
                                    excerpt=prod.description,
                                )
                            ],
                            status="needs_review",
                            needs_human_review=True,
                            reason=ins_reason,
                            is_draft=True,
                        )
                    else:
                        if existing_dup is not None:
                            order_doc = _apply_shipping_update_to_order(
                                existing_dup,
                                order_attempt.phone,
                                order_attempt.shipping_address,
                                result.comment_id,
                            )
                            if facade is not None:
                                try:
                                    await facade.orders.upsert_order(tenant_ctx, order_doc)
                                except DatabaseError as exc:
                                    logger.warning(
                                        "Failed to update duplicate order %s: %s",
                                        order_doc.order_id,
                                        exc,
                                    )
                        else:
                            order_id = await _next_order_id(facade)
                            snapshots: list[OrderItemSnapshot] = []
                            subtotal = 0.0
                            for prod, qty in order_attempt.items:
                                sku_code = f"SKU-{prod.id.replace('SYN-', '')}"
                                line_total = round(float(prod.price) * qty, 2)
                                subtotal = round(subtotal + line_total, 2)
                                snapshots.append(
                                    OrderItemSnapshot(
                                        product_id=prod.id,
                                        sku_code=sku_code,
                                        product_name=prod.name,
                                        quantity=qty,
                                        unit_price=float(prod.price),
                                        line_total=line_total,
                                    )
                                )
                            shipping_fee = 0.0 if subtotal >= 30.0 else 4.99
                            total_amount = round(subtotal + shipping_fee, 2)
                            has_shipping_details = bool(
                                order_attempt.phone and order_attempt.shipping_address
                            )
                            order_status: OrderStatus = (
                                "confirmed" if has_shipping_details else "pending"
                            )
                            pay_status: PaymentStatus = (
                                "cod" if has_shipping_details else "unpaid"
                            )
                            if has_shipping_details:
                                ship_addr = f"{order_attempt.shipping_address} (Phone: {order_attempt.phone})"
                            elif order_attempt.shipping_address:
                                ship_addr = f"{order_attempt.shipping_address} (Phone: pending)"
                            elif order_attempt.phone:
                                ship_addr = f"Pending shipping address (Phone: {order_attempt.phone})"
                            else:
                                ship_addr = "Pending customer phone & shipping address"

                            order_doc = OrderDocument(
                                store_id=store_id,
                                order_id=order_id,
                                session_id=session_id,
                                customer_id=customer_id,
                                customer_handle=clean_handle[:256],
                                customer_name=clean_name[:256],
                                platform="youtube",
                                status=order_status,
                                payment_status=pay_status,
                                items=snapshots,
                                subtotal=subtotal,
                                shipping_fee=shipping_fee,
                                discount_amount=0.0,
                                total_amount=total_amount,
                                currency="USD",
                                shipping_address=ship_addr[:500],
                                notes=f"[comment_id:{result.comment_id}] Auto-order from comment: {payload.text[:200]}",
                            )
                            live_orders.insert(0, order_doc)
                            for prod, qty in order_attempt.items:
                                in_memory_stock_sold[prod.id] = (
                                    in_memory_stock_sold.get(prod.id, 0) + qty
                                )
                            if facade is not None:
                                try:
                                    await facade.orders.upsert_order(tenant_ctx, order_doc)
                                    for prod, qty in order_attempt.items:
                                        await facade.products.adjust_inventory_quantity(
                                            tenant_ctx,
                                            product_id=prod.id,
                                            delta_on_hand=-qty,
                                        )
                                except DatabaseError as exc:
                                    logger.warning(
                                        "Failed to persist auto-order %s: %s", order_id, exc
                                    )

                        items_desc = ", ".join(
                            f"{it.quantity}x {it.product_name}" for it in order_doc.items
                        )
                        if is_vi:
                            status_note = (
                                "Đã xác nhận giao hàng COD."
                                if order_doc.status == "confirmed"
                                else "Vui lòng nhắn SĐT & địa chỉ nhận hàng."
                            )
                            confirm_text = (
                                f"Bản nháp: Đã tạo đơn #{order_doc.order_id} ({items_desc}, tổng ${order_doc.total_amount:.2f}). {status_note}"
                            )
                        else:
                            status_note = (
                                "Confirmed COD."
                                if order_doc.status == "confirmed"
                                else "Please reply with phone & shipping address."
                            )
                            confirm_text = (
                                f"Draft: Order #{order_doc.order_id} created! {items_desc} "
                                f"(total ${order_doc.total_amount:.2f}). {status_note}"
                            )
                        result.auto_order = order_doc.model_dump(mode="json")
                        result.lead_score = 98
                        result.lead_label = "hot"
                        result.needs_human_review = False
                        result.reason = None
                        result.answer_draft = AnswerDraftResponse(
                            answer=confirm_text,
                            intent=result.intent,
                            sources=[
                                AnswerSourceResponse(
                                    source_type="product",
                                    source_id=prod.id,
                                    title=prod.name,
                                    excerpt=prod.description,
                                )
                                for prod, _ in order_attempt.items
                            ],
                            status="answered",
                            needs_human_review=False,
                            reason=None,
                            is_draft=True,
                        )
        elif can_attempt_order:
            # Check if viewer is replying with standalone phone / shipping address for their existing pending order
            follow_phone = _extract_phone_number(payload.text)
            follow_addr = _extract_shipping_address(payload.text)
            if follow_phone and not follow_addr:
                rem = _PHONE_PATTERN.sub(" ", payload.text)
                rem = re.sub(
                    r"\b(?:sđt|sdt|số\s+điện\s+thoại|so\s+dien\s+thoai|điện\s+thoại|dien\s+thoai|phone|tel|của\s+mình|của\s+em)\b\s*[:\-]?",
                    " ",
                    rem,
                    flags=re.IGNORECASE,
                )
                rem = re.sub(r"\s+", " ", rem).strip(" ,;:-.")
                if len(rem) >= 6 and re.search(
                    r"\b(?:\d+\s+\S+|quận|quan|q\.?\d+|phường|phuong|huyện|huyen|đường|duong|ngõ|ngo|tp\b|hcm|hà\s+nội|ha\s+noi)\b",
                    rem,
                    flags=re.IGNORECASE,
                ):
                    follow_addr = rem

            if follow_phone or follow_addr:
                async with order_lock:
                    clean_handle = payload.sender.strip() or "@viewer"
                    pending_list = await _find_viewer_pending_orders(clean_handle)
                    if pending_list:
                        target_ord = _apply_shipping_update_to_order(
                            pending_list[0],
                            follow_phone,
                            follow_addr,
                            result.comment_id,
                        )
                        if facade is not None:
                            try:
                                await facade.orders.upsert_order(tenant_ctx, target_ord)
                            except DatabaseError as exc:
                                logger.warning(
                                    "Failed to persist follow-up shipping update for %s: %s",
                                    target_ord.order_id,
                                    exc,
                                )
                        is_vi = _is_vietnamese_text(payload.text) or bool(follow_phone)
                        items_desc = ", ".join(
                            f"{it.quantity}x {it.product_name}" for it in target_ord.items
                        )
                        if is_vi:
                            status_note = (
                                "Đã xác nhận thông tin giao hàng COD."
                                if target_ord.status == "confirmed"
                                else "Vui lòng bổ sung đủ cả SĐT và địa chỉ nhận hàng."
                            )
                            confirm_text = (
                                f"Bản nháp: Đã cập nhật đơn #{target_ord.order_id} ({items_desc}, tổng ${target_ord.total_amount:.2f}). {status_note}"
                            )
                        else:
                            status_note = (
                                "Confirmed COD with shipping details."
                                if target_ord.status == "confirmed"
                                else "Please provide both phone and shipping address."
                            )
                            confirm_text = (
                                f"Draft: Updated Order #{target_ord.order_id} ({items_desc}, total ${target_ord.total_amount:.2f}). {status_note}"
                            )
                        result.auto_order = target_ord.model_dump(mode="json")
                        result.intent = "consequential_action"
                        result.lead_score = 98
                        result.lead_label = "hot"
                        result.needs_human_review = False
                        result.reason = None
                        result.answer_draft = AnswerDraftResponse(
                            answer=confirm_text,
                            intent="consequential_action",
                            sources=[],
                            status="answered",
                            needs_human_review=False,
                            reason=None,
                            is_draft=True,
                        )

        thread = threads_by_comment.setdefault(result.comment_id, [])
        draft_ready = bool(
            result.answer_draft
            and result.answer_draft.status == "answered"
            and not result.needs_human_review
            and not result.answer_draft.needs_human_review
            and result.answer_draft.answer
        )
        if draft_ready and not any(m.author_role == "ai" for m in thread):
            thread.append(
                ThreadMessage(
                    sender="AI Draft",
                    author_role="ai",
                    text=result.answer_draft.answer,
                )
            )
        result.thread_messages = thread
        _upsert_recent_comment(result)

        if facade is not None:
            try:
                clean_handle = payload.sender.strip() or "@viewer"
                clean_name = clean_handle.lstrip("@").strip() or "Viewer"
                customer_id = _to_safe_id(f"cust-{clean_name.lower()}")
                await facade.customers.upsert_customer_profile(
                    tenant_ctx,
                    CustomerDocument(
                        store_id=store_id,
                        customer_id=customer_id,
                        platform="youtube",
                        platform_user_handle=clean_handle[:256],
                        display_name=clean_name[:200],
                        tags=["livestream_viewer", *(["hot_lead"] if result.lead_label == "hot" else [])],
                    ),
                )
                matched_prod_ids = [
                    _to_safe_id(s.source_id)
                    for s in (result.answer_draft.sources if result.answer_draft else [])
                    if s.source_type == "product"
                ]
                persistence = await facade.record_comment_with_operational_sync(
                    tenant_ctx,
                    LivestreamCommentDocument(
                        store_id=store_id,
                        session_id=session_id,
                        comment_id=result.comment_id,
                        customer_id=customer_id,
                        platform="youtube",
                        user_handle=result.sender[:256],
                        message=result.text[:1000],
                        timestamp=_safe_timestamp_to_datetime(result.timestamp),
                        intent=result.intent,
                        intent_confidence=result.intent_confidence,
                        lead_label=result.lead_label,
                        lead_score=result.lead_score,
                        matched_product_ids=matched_prod_ids,
                        safety_blocked=result.moderation_status == "blocked",
                        safety_reason=result.moderation_reason,
                    ),
                )
                if persistence.inserted_into_mongo and (
                    result.lead_label == "hot" or result.answer_draft is not None
                ):
                    updated_sess = await facade.sessions.increment_metrics(
                        tenant_ctx,
                        session_id,
                        high_lead_comments=1 if result.lead_label == "hot" else 0,
                        drafts_generated=1 if result.answer_draft is not None else 0,
                        drafts_approved=0,
                    )
                    if facade.cache is not None and updated_sess is not None:
                        await facade.cache.cache_session(tenant_ctx, updated_sess)
                        await facade.cache.publish_dashboard_notification(
                            tenant_ctx,
                            session_id,
                            {
                                "event": "comment.ingested",
                                "comment_id": result.comment_id,
                                "intent": result.intent,
                                "lead_label": result.lead_label,
                            },
                        )
                if (
                    persistence.inserted_into_mongo
                    and result.answer_draft
                    and result.answer_draft.answer
                ):
                    await facade.answer_drafts.save_draft(
                        tenant_ctx,
                        AnswerDraftDocument(
                            store_id=store_id,
                            session_id=session_id,
                            draft_id=_to_safe_id(f"draft-{result.comment_id}"),
                            comment_id=result.comment_id,
                            suggested_reply=result.answer_draft.answer[:10000],
                            confidence=1.0 if result.answer_draft.status == "answered" else 0.0,
                            grounding_passed=not result.answer_draft.needs_human_review,
                            fallback_used=result.answer_draft.status != "answered",
                            seller_action="pending",
                            final_reply_text=None,
                            acted_by_user_id=None,
                            acted_at=None,
                            sources=[
                                EvidenceSourceSnapshot(
                                    source_type=s.source_type,
                                    source_id=_to_safe_id(s.source_id),
                                    title=s.title[:256],
                                    snippet=s.excerpt[:1000],
                                )
                                for s in result.answer_draft.sources
                            ],
                        ),
                    )
            except (DatabaseError, ValidationError) as exc:
                logger.warning("Failed to persist comment %s: %s", result.comment_id, exc)

        return result

    @app.get("/api/plugin/comments", response_model=list[ClassifiedLiveComment])
    async def list_comments(
        limit: int = Query(default=100, ge=1, le=MAX_STORED_COMMENTS),
    ) -> list[ClassifiedLiveComment]:
        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        if facade is not None:
            try:
                repo_items = await _load_comments_from_repository(facade, limit=limit)
                for item in reversed(repo_items):
                    _upsert_recent_comment(item)
                return repo_items[:limit]
            except DatabaseError as exc:
                logger.warning("Repository comment read failed, using in-memory fallback: %s", exc)
        return list(reversed(recent_comments))[:limit]

    @app.post(
        "/api/plugin/comments/{comment_id:path}/thread",
        response_model=list[ThreadMessage],
    )
    async def post_comment_thread_message(
        comment_id: str,
        payload: ThreadMessage,
    ) -> list[ThreadMessage]:
        safe_comment_id = _to_safe_id(comment_id)
        thread = threads_by_comment.setdefault(safe_comment_id, [])
        thread.append(payload)

        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        if facade is not None:
            try:
                draft = await facade.answer_drafts.get_latest_for_comment(
                    tenant_ctx, safe_comment_id
                )
                if draft is not None:
                    was_pending = draft.seller_action == "pending"
                    await facade.answer_drafts.record_seller_action(
                        tenant_ctx,
                        draft.draft_id,
                        action="approved" if payload.author_role == "ai" else "edited",
                        final_reply_text=payload.text[:5000],
                    )
                    if was_pending:
                        await facade.sessions.increment_metrics(
                            tenant_ctx,
                            session_id,
                            drafts_approved=1,
                        )
            except DatabaseError as exc:
                logger.warning("Failed to persist thread reply for %s: %s", safe_comment_id, exc)

        return thread

    @app.get("/api/plugin/summary")
    async def summarize_comments() -> dict[str, object]:
        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        source_comments: list[ClassifiedLiveComment] = list(recent_comments)
        storage_mode = "in_memory"
        if facade is not None:
            try:
                source_comments = await _load_comments_from_repository(
                    facade, limit=MAX_STORED_COMMENTS
                )
                storage_mode = "mongodb"
            except DatabaseError as exc:
                logger.warning("Repository summary read failed, using in-memory fallback: %s", exc)

        intent_counts = Counter(comment.intent for comment in source_comments)
        lead_counts = Counter(comment.lead_label for comment in source_comments)
        return {
            "storage_mode": storage_mode,
            "comment_count": len(source_comments),
            "moderated_comment_count": sum(
                comment.moderation_status == "blocked" for comment in source_comments
            ),
            "needs_human_review_count": sum(
                comment.needs_human_review for comment in source_comments
            ),
            "hot_lead_count": lead_counts.get("hot", 0),
            "lead_counts": dict(sorted(lead_counts.items())),
            "intent_counts": dict(sorted(intent_counts.items())),
        }

    @app.get("/api/agent/architecture")
    def get_agent_architecture() -> dict[str, Any]:
        return orchestrator.architecture_summary

    @app.get("/api/agent/tools")
    def list_tools() -> dict[str, Any]:
        return {
            "count": len(get_tool_definitions()),
            "tools": get_tool_definitions(),
        }

    @app.post("/api/agent/chat")
    def run_multi_agent_chat(payload: AgentChatRequest) -> dict[str, Any]:
        exec_result = orchestrator.run(
            text=payload.text,
            session_id=payload.session_id or session_id,
            comment_id=_to_safe_id(f"agent-{int(time.time() * 1000)}"),
            sender=payload.sender,
            author_type=payload.author_type,
        )
        return {
            "orchestrator_backend": exec_result.orchestrator_backend,
            "vector_backend": exec_result.vector_backend,
            "state": exec_result.state,
        }

    @app.post("/api/agent/rag/search")
    def search_vector_rag(payload: VectorRAGSearchRequest) -> dict[str, Any]:
        hits = vector_store.search(
            payload.query,
            top_k=payload.top_k,
            source_type=payload.source_type,
        )
        return {
            "query": payload.query,
            "backend": vector_store.backend,
            "count": len(hits),
            "hits": [
                {
                    "source_type": hit.source_type,
                    "source_id": hit.source_id,
                    "title": hit.title,
                    "excerpt": hit.excerpt,
                    "score": hit.score,
                    "backend": hit.backend,
                }
                for hit in hits
            ],
        }

    @app.get("/api/store/products")
    async def list_store_products() -> dict[str, Any]:
        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        if facade is not None:
            try:
                products = await facade.products.list_active_products(tenant_ctx)
                inventories = await facade.products.list_inventory_for_store(tenant_ctx)
                inv_by_prod = {inv.product_id: inv for inv in inventories}
                items = [
                    {
                        "product_id": p.product_id,
                        "sku_code": p.sku_code,
                        "name": p.name,
                        "category": p.category,
                        "price": p.price,
                        "currency": p.currency,
                        "description": p.short_description,
                        "stock_quantity": inv_by_prod[p.product_id].quantity_available
                        if p.product_id in inv_by_prod
                        else 0,
                        "in_stock": inv_by_prod[p.product_id].in_stock
                        if p.product_id in inv_by_prod
                        else False,
                        "attributes": p.attributes,
                        "storage_mode": "mongodb",
                    }
                    for p in products
                ]
                return {"count": len(items), "storage_mode": "mongodb", "items": items}
            except DatabaseError:
                pass

        dataset = load_synthetic_data()
        items = [
            {
                "product_id": p.id,
                "sku_code": f"SKU-{p.id.replace('SYN-', '')}",
                "name": p.name,
                "category": p.category,
                "price": p.price,
                "currency": "USD",
                "description": p.description,
                "stock_quantity": max(0, p.stock_quantity - in_memory_stock_sold.get(p.id, 0)),
                "in_stock": max(0, p.stock_quantity - in_memory_stock_sold.get(p.id, 0)) > 0,
                "attributes": dict(p.attributes),
                "storage_mode": "synthetic_memory",
            }
            for p in dataset.products
        ]
        return {"count": len(items), "storage_mode": "synthetic_memory", "items": items}

    @app.get("/api/store/orders")
    async def list_store_orders(
        status: OrderStatus | None = None,
        customer_handle: str | None = None,
    ) -> dict[str, Any]:
        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        if facade is not None:
            try:
                orders = await facade.orders.list_orders(
                    tenant_ctx, status=status, customer_handle=customer_handle
                )
                return {
                    "count": len(orders),
                    "storage_mode": "mongodb",
                    "items": [o.model_dump(mode="json") for o in orders],
                }
            except DatabaseError:
                pass

        filtered_orders = list(live_orders)
        if status is not None:
            filtered_orders = [o for o in filtered_orders if o.status == status]
        if customer_handle:
            handle_norm = customer_handle.strip().casefold()
            filtered_orders = [
                o for o in filtered_orders if o.customer_handle.casefold() == handle_norm
            ]
        return {
            "count": len(filtered_orders),
            "storage_mode": "synthetic_memory",
            "items": [o.model_dump(mode="json") for o in filtered_orders],
        }

    @app.post("/api/store/orders/{order_id}/status")
    @app.patch("/api/store/orders/{order_id}/status")
    async def update_store_order_status(
        order_id: str,
        payload: OrderUpdateStatusRequest,
    ) -> dict[str, Any]:
        safe_order_id = _to_safe_id(order_id)
        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        async with order_lock:
            target_order: OrderDocument | None = None
            if facade is not None:
                try:
                    target_order = await facade.orders.get_by_id(tenant_ctx, safe_order_id)
                except DatabaseError:
                    target_order = None
            if target_order is None:
                for o in live_orders:
                    if o.order_id == safe_order_id:
                        target_order = o
                        break
            if target_order is None:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "order_not_found", "message": f"Order {safe_order_id} not found."},
                )

            previous_status = target_order.status
            target_order.status = payload.status
            if payload.payment_status is not None:
                target_order.payment_status = payload.payment_status
            elif payload.status == "confirmed" and target_order.payment_status == "unpaid":
                target_order.payment_status = "cod"
            elif payload.status == "cancelled" and target_order.payment_status == "unpaid":
                target_order.payment_status = "unpaid"

            if payload.shipping_address is not None:
                target_order.shipping_address = payload.shipping_address[:500]
            if payload.notes is not None:
                target_order.notes = payload.notes[:1000]

            # Restore inventory when cancelling a previously active order
            if previous_status != "cancelled" and payload.status == "cancelled":
                for item in target_order.items:
                    in_memory_stock_sold[item.product_id] = max(
                        0, in_memory_stock_sold.get(item.product_id, 0) - item.quantity
                    )
                    if facade is not None:
                        try:
                            await facade.products.adjust_inventory_quantity(
                                tenant_ctx,
                                product_id=item.product_id,
                                delta_on_hand=item.quantity,
                            )
                        except DatabaseError as exc:
                            logger.warning(
                                "Failed to restore inventory for cancelled order %s: %s",
                                safe_order_id,
                                exc,
                            )

            for idx, existing_ord in enumerate(live_orders):
                if existing_ord.order_id == safe_order_id:
                    live_orders[idx] = target_order
                    break
            else:
                live_orders.insert(0, target_order)

            if facade is not None:
                try:
                    target_order = await facade.orders.upsert_order(tenant_ctx, target_order)
                except DatabaseError as exc:
                    logger.warning("Failed to persist status update for order %s: %s", safe_order_id, exc)

            # Also refresh auto_order snapshot on any matching recent comment in memory
            order_dump = target_order.model_dump(mode="json")
            for c in recent_comments:
                if c.auto_order and c.auto_order.get("order_id") == safe_order_id:
                    c.auto_order = order_dump

            return {"updated": True, "order": order_dump}

    @app.post("/api/store/seed")
    async def trigger_seed() -> dict[str, Any]:
        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        if facade is not None:
            counts = await seed_demo_store_data(facade, tenant_ctx, session_id=session_id)
            return {"seeded": True, "storage_mode": "mongodb", "counts": counts}
        dataset = load_synthetic_data()
        orders = load_demo_orders(store_id=store_id, session_id=session_id)
        return {
            "seeded": True,
            "storage_mode": "synthetic_memory",
            "counts": {
                "products": len(dataset.products),
                "faqs": len(dataset.faqs),
                "policies": len(dataset.policies),
                "orders": len(orders),
            },
        }

    @app.get("/api/database/overview")
    async def get_database_overview() -> dict[str, Any]:
        """Return Section 3.2.4 Database Design overview (13 MongoDB collections + 7 Redis structures)."""
        facade: StoreDatabaseFacade | None = getattr(app.state, "db", None)
        collection_schemas: list[dict[str, Any]] = [
            {"name": COLL_USERS, "model": "UserDocument", "fields": list(UserDocument.model_fields.keys())},
            {"name": COLL_STORES, "model": "StoreDocument", "fields": list(StoreDocument.model_fields.keys())},
            {"name": COLL_STORE_MEMBERSHIPS, "model": "StoreMembershipDocument", "fields": list(StoreMembershipDocument.model_fields.keys())},
            {"name": COLL_PRODUCTS, "model": "ProductDocument", "fields": list(ProductDocument.model_fields.keys())},
            {"name": COLL_PRODUCT_VARIANTS, "model": "ProductVariantDocument", "fields": list(ProductVariantDocument.model_fields.keys())},
            {"name": COLL_INVENTORY, "model": "InventoryDocument", "fields": list(InventoryDocument.model_fields.keys())},
            {"name": COLL_FAQS, "model": "FAQDocument", "fields": list(FAQDocument.model_fields.keys())},
            {"name": COLL_SALES_POLICIES, "model": "SalesPolicyDocument", "fields": list(SalesPolicyDocument.model_fields.keys())},
            {"name": COLL_CUSTOMERS, "model": "CustomerDocument", "fields": list(CustomerDocument.model_fields.keys())},
            {"name": COLL_LIVESTREAM_SESSIONS, "model": "LivestreamSessionDocument", "fields": list(LivestreamSessionDocument.model_fields.keys())},
            {"name": COLL_LIVESTREAM_COMMENTS, "model": "LivestreamCommentDocument", "fields": list(LivestreamCommentDocument.model_fields.keys())},
            {"name": COLL_ANSWER_DRAFTS, "model": "AnswerDraftDocument", "fields": list(AnswerDraftDocument.model_fields.keys())},
            {"name": COLL_ORDERS, "model": "OrderDocument", "fields": list(OrderDocument.model_fields.keys())},
        ]
        for entry in collection_schemas:
            coll_name = entry["name"]
            count_val = 0
            if facade is not None:
                try:
                    count_val = int(await facade.mongo.database[coll_name].count_documents({}))
                except Exception:
                    count_val = 0
            entry["document_count"] = count_val

        redis_structures = [
            {
                "key_pattern": "store:{store_id}:session:{session_id}:state",
                "concrete_key": RedisKeys.session_state(store_id, session_id),
                "data_structure": "Hash",
                "purpose": "Stores active livestream session state.",
            },
            {
                "key_pattern": "store:{store_id}:session:{session_id}:comments",
                "concrete_key": RedisKeys.session_comments(store_id, session_id),
                "data_structure": "List",
                "purpose": "Caches recent livestream comments.",
            },
            {
                "key_pattern": "store:{store_id}:session:{session_id}:events",
                "concrete_key": RedisKeys.session_events_stream(store_id, session_id),
                "data_structure": "Stream",
                "purpose": "Stores livestream event records.",
            },
            {
                "key_pattern": "store:{store_id}:session:{session_id}:pubsub",
                "concrete_key": RedisKeys.session_pubsub_channel(store_id, session_id),
                "data_structure": "Pub/Sub",
                "purpose": "Distributes dashboard notifications.",
            },
            {
                "key_pattern": "store:{store_id}:session:{session_id}:dedupe:{event_id}",
                "concrete_key": RedisKeys.session_dedupe(store_id, session_id, "sample-event"),
                "data_structure": "String",
                "purpose": "Prevents duplicate event processing.",
            },
            {
                "key_pattern": "store:{store_id}:cache:product:{product_id}",
                "concrete_key": RedisKeys.product_cache(store_id, "SYN-PROD-001"),
                "data_structure": "String",
                "purpose": "Caches product information.",
            },
            {
                "key_pattern": "store:{store_id}:cache:session:{session_id}",
                "concrete_key": RedisKeys.session_cache(store_id, session_id),
                "data_structure": "String",
                "purpose": "Caches livestream session information.",
            },
        ]

        return {
            "database_name": db_settings.mongo.database,
            "total_collections": len(collection_schemas),
            "collections": collection_schemas,
            "total_redis_patterns": len(redis_structures),
            "redis_structures": redis_structures,
            "in_memory_runtime": {
                "private_thread_conversations": len(threads_by_comment),
                "recent_comments_buffer_size": len(recent_comments),
            },
        }

    return app


app = create_app()