"""Vector RAG index with FAISS / ChromaDB support and zero-dependency cosine fallback.

Implements vector similarity retrieval over catalog products, FAQs, and active sales
policies so the system fulfills the RAG + Vector Store (FAISS / Chroma / LangChain)
architecture requirement while remaining 100% reproducible offline.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Literal

from ai.customer_qa.schemas import SourceReference, SourceType
from ai.data import get_effective_policies, load_synthetic_data
from ai.data.models import SyntheticDataset

VectorBackendType = Literal["faiss", "chroma", "local_vector"]


@dataclass(frozen=True, slots=True)
class VectorDocument:
    doc_id: str
    source_type: SourceType
    source_id: str
    title: str
    text: str
    excerpt: str
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class VectorSearchHit:
    source_type: SourceType
    source_id: str
    title: str
    excerpt: str
    score: float
    backend: VectorBackendType

    def to_source_reference(self) -> SourceReference:
        return SourceReference(
            source_type=self.source_type,
            source_id=self.source_id,
            title=self.title,
            excerpt=self.excerpt,
        )


def _tokenize_with_ngrams(text: str) -> list[str]:
    lowered = text.casefold()
    words = re.findall(r"[a-z0-9àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]+", lowered)
    compact = re.sub(r"\s+", " ", lowered).strip()
    char_ngrams = [compact[i : i + 3] for i in range(max(0, len(compact) - 2))]
    return [f"w:{w}" for w in words] + [f"c:{g}" for g in char_ngrams]


def build_corpus_documents(dataset: SyntheticDataset | None = None) -> tuple[VectorDocument, ...]:
    records = dataset if dataset is not None else load_synthetic_data()
    docs: list[VectorDocument] = []

    for product in records.products:
        attrs = ", ".join(f"{k}={v}" for k, v in sorted(product.attributes.items()))
        excerpt = (
            f"Name: {product.name}. Description: {product.description} "
            f"Price: {product.price:.2f} (synthetic demo). "
            f"Stock quantity: {product.stock_quantity} (simulated). "
            f"Category: {product.category}. Attributes: {attrs}."
        )
        full_text = f"{product.id} {product.name} {product.category} {product.description} {attrs} price {product.price} stock {product.stock_quantity}"
        docs.append(
            VectorDocument(
                doc_id=f"product:{product.id}",
                source_type="product",
                source_id=product.id,
                title=product.name,
                text=full_text,
                excerpt=excerpt,
                metadata={"category": product.category, "price": product.price, "stock": product.stock_quantity},
            )
        )

    for faq in records.faqs:
        full_text = f"{faq.id} {faq.category} {faq.question} {faq.answer} {' '.join(faq.product_ids)}"
        docs.append(
            VectorDocument(
                doc_id=f"faq:{faq.id}",
                source_type="faq",
                source_id=faq.id,
                title=faq.question,
                text=full_text,
                excerpt=faq.answer,
                metadata={"category": faq.category, "product_ids": list(faq.product_ids)},
            )
        )

    for policy in get_effective_policies(records.policies):
        full_text = f"{policy.id} {policy.category} policy {policy.content}"
        docs.append(
            VectorDocument(
                doc_id=f"policy:{policy.id}",
                source_type="policy",
                source_id=policy.id,
                title=policy.category,
                text=full_text,
                excerpt=policy.content,
                metadata={"category": policy.category, "status": policy.status},
            )
        )

    return tuple(docs)


class VectorRAGStore:
    """Hybrid Vector Store supporting FAISS, ChromaDB, and pure-Python TF-IDF embeddings."""

    def __init__(self, dataset: SyntheticDataset | None = None) -> None:
        self._documents = build_corpus_documents(dataset)
        self._vocab: dict[str, int] = {}
        self._idf: list[float] = []
        self._doc_vectors: list[list[float]] = []
        self._backend: VectorBackendType = "local_vector"
        self._faiss_index: Any = None
        self._chroma_collection: Any = None
        self._build_index()

    @property
    def backend(self) -> VectorBackendType:
        return self._backend

    @property
    def document_count(self) -> int:
        return len(self._documents)

    def _build_index(self) -> None:
        tokenized_docs = [_tokenize_with_ngrams(doc.text) for doc in self._documents]
        df: dict[str, int] = {}
        for tokens in tokenized_docs:
            for token in set(tokens):
                df[token] = df.get(token, 0) + 1

        vocab_tokens = sorted(df.keys())
        self._vocab = {tok: idx for idx, tok in enumerate(vocab_tokens)}
        n_docs = max(1, len(self._documents))
        self._idf = [math.log((1.0 + n_docs) / (1.0 + df[tok])) + 1.0 for tok in vocab_tokens]

        self._doc_vectors = [self._embed_tokens(tokens) for tokens in tokenized_docs]

        # Optional FAISS backend acceleration if installed
        try:
            import faiss  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]

            if self._doc_vectors and self._doc_vectors[0]:
                matrix = np.array(self._doc_vectors, dtype="float32")
                index = faiss.IndexFlatIP(matrix.shape[1])
                index.add(matrix)
                self._faiss_index = (index, np)
                self._backend = "faiss"
                return
        except Exception:
            pass

        # Optional ChromaDB in-memory collection if installed
        try:
            import chromadb  # type: ignore[import-not-found]

            if self._doc_vectors and self._doc_vectors[0]:
                client = chromadb.Client()
                collection = client.get_or_create_collection(
                    name="livestream_rag_store",
                    metadata={"hnsw:space": "cosine"},
                )
                collection.upsert(
                    ids=[doc.doc_id for doc in self._documents],
                    embeddings=self._doc_vectors,
                    documents=[doc.excerpt for doc in self._documents],
                    metadatas=[
                        {
                            "source_type": doc.source_type,
                            "source_id": doc.source_id,
                            "title": doc.title,
                        }
                        for doc in self._documents
                    ],
                )
                self._chroma_collection = collection
                self._backend = "chroma"
                return
        except Exception:
            pass

        self._backend = "local_vector"

    def _embed_tokens(self, tokens: list[str]) -> list[float]:
        if not self._vocab:
            return []
        vec = [0.0] * len(self._vocab)
        counts: dict[int, int] = {}
        for token in tokens:
            idx = self._vocab.get(token)
            if idx is not None:
                counts[idx] = counts.get(idx, 0) + 1
        if not counts:
            return vec
        norm_sq = 0.0
        for idx, count in counts.items():
            val = (1.0 + math.log(count)) * self._idf[idx]
            vec[idx] = val
            norm_sq += val * val
        norm = math.sqrt(norm_sq)
        if norm > 0:
            for idx in counts:
                vec[idx] /= norm
        return vec

    def embed_query(self, query: str) -> list[float]:
        return self._embed_tokens(_tokenize_with_ngrams(query))

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        source_type: SourceType | None = None,
        min_score: float = 0.08,
    ) -> list[VectorSearchHit]:
        if not isinstance(query, str) or not query.strip():
            return []
        q_vec = self.embed_query(query)
        if not q_vec or not any(q_vec):
            return []

        scored: list[tuple[float, VectorDocument]] = []
        if self._backend == "faiss" and self._faiss_index is not None:
            index, np = self._faiss_index
            q_arr = np.array([q_vec], dtype="float32")
            scores, indices = index.search(q_arr, len(self._documents))
            for score, doc_idx in zip(scores[0], indices[0]):
                if 0 <= int(doc_idx) < len(self._documents):
                    scored.append((float(score), self._documents[int(doc_idx)]))
        else:
            for doc, d_vec in zip(self._documents, self._doc_vectors):
                dot = sum(a * b for a, b in zip(q_vec, d_vec) if a and b)
                scored.append((dot, doc))
            scored.sort(key=lambda item: item[0], reverse=True)

        hits: list[VectorSearchHit] = []
        for score, doc in scored:
            if score < min_score:
                continue
            if source_type is not None and doc.source_type != source_type:
                continue
            hits.append(
                VectorSearchHit(
                    source_type=doc.source_type,
                    source_id=doc.source_id,
                    title=doc.title,
                    excerpt=doc.excerpt,
                    score=round(float(score), 4),
                    backend=self._backend,
                )
            )
            if len(hits) >= max(1, top_k):
                break
        return hits


_DEFAULT_VECTOR_STORE: VectorRAGStore | None = None


def get_vector_rag_store(dataset: SyntheticDataset | None = None) -> VectorRAGStore:
    global _DEFAULT_VECTOR_STORE
    if dataset is not None:
        return VectorRAGStore(dataset=dataset)
    if _DEFAULT_VECTOR_STORE is None:
        _DEFAULT_VECTOR_STORE = VectorRAGStore()
    return _DEFAULT_VECTOR_STORE
