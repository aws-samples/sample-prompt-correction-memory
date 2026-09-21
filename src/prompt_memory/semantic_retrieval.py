"""Semantic correction retrieval: finds relevant corrections by text similarity.

Instead of retrieving corrections only by exact field_name match, this module
uses TF-IDF cosine similarity on document excerpts to find corrections that
are semantically relevant — even for new document types or field names not
previously seen.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from src.extraction.models import CorrectionRecord


class SemanticRetriever:
    """Retrieves corrections by semantic similarity to query text.

    Uses TF-IDF with cosine similarity (no external dependencies).
    Operates over the document_excerpt + correction_reason fields of
    stored corrections.

    This enables:
    - Cross-document-type transfer (correction for "payment_terms" in MSAs
      helps with "payment_terms" in leases)
    - Handling unseen field names through content similarity
    - Better ranking when multiple corrections exist for a field
    """

    def __init__(self, corrections: Optional[List[CorrectionRecord]] = None):
        self._corrections: List[CorrectionRecord] = []
        self._idf: Dict[str, float] = {}
        self._doc_vectors: List[Dict[str, float]] = []
        if corrections:
            self.index(corrections)

    def index(self, corrections: List[CorrectionRecord]) -> None:
        """Build the TF-IDF index over correction excerpts and reasons."""
        self._corrections = corrections
        if not corrections:
            self._idf = {}
            self._doc_vectors = []
            return

        # Build document texts (excerpt + reason combined)
        doc_texts = [self._build_text(c) for c in corrections]

        # Compute IDF
        n_docs = len(doc_texts)
        doc_freq: Dict[str, int] = defaultdict(int)
        tokenized_docs = [self._tokenize(text) for text in doc_texts]

        for tokens in tokenized_docs:
            unique_tokens = set(tokens)
            for token in unique_tokens:
                doc_freq[token] += 1

        self._idf = {
            token: math.log((n_docs + 1) / (freq + 1)) + 1
            for token, freq in doc_freq.items()
        }

        # Build TF-IDF vectors for each correction
        self._doc_vectors = []
        for tokens in tokenized_docs:
            tf = Counter(tokens)
            total = len(tokens) if tokens else 1
            vector = {
                token: (count / total) * self._idf.get(token, 0.0)
                for token, count in tf.items()
            }
            self._doc_vectors.append(vector)

    def retrieve(
        self,
        query_text: str,
        field_name: Optional[str] = None,
        limit: int = 3,
        min_similarity: float = 0.1,
    ) -> List[Tuple[CorrectionRecord, float]]:
        """Retrieve corrections ranked by semantic similarity to query text.

        Args:
            query_text: The document text or excerpt to match against.
            field_name: Optional field name to boost matching corrections.
            limit: Maximum number of corrections to return.
            min_similarity: Minimum cosine similarity threshold.

        Returns:
            List of (CorrectionRecord, similarity_score) tuples, sorted descending.
        """
        if not self._corrections or not self._doc_vectors:
            return []

        # Build query vector
        query_tokens = self._tokenize(query_text)
        query_tf = Counter(query_tokens)
        total = len(query_tokens) if query_tokens else 1
        query_vector = {
            token: (count / total) * self._idf.get(token, 0.0)
            for token, count in query_tf.items()
        }

        # Compute cosine similarity against all indexed corrections
        scored: List[Tuple[int, float]] = []
        for i, doc_vector in enumerate(self._doc_vectors):
            sim = self._cosine_similarity(query_vector, doc_vector)

            # Boost score if field_name matches
            if field_name and self._corrections[i].field_name == field_name:
                sim = sim * 1.5  # 50% boost for matching field name

            if sim >= min_similarity:
                scored.append((i, sim))

        # Sort by similarity descending
        scored.sort(key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in scored[:limit]:
            results.append((self._corrections[idx], min(score, 1.0)))
        return results

    def retrieve_for_field(
        self,
        document_text: str,
        field_name: str,
        document_type: str = "",
        limit: int = 3,
    ) -> List[CorrectionRecord]:
        """Retrieve corrections relevant to a field extraction.

        Combines field_name filtering with semantic similarity of document text.
        This is the primary interface for the confidence router.

        Args:
            document_text: Full document text (will be truncated for efficiency).
            field_name: The target field being extracted.
            document_type: Optional document type for boosting.
            limit: Maximum corrections to return.

        Returns:
            List of CorrectionRecords ranked by relevance.
        """
        # Use first 1000 chars for efficiency (preamble usually contains key info)
        query = document_text[:1000]
        results = self.retrieve(query, field_name=field_name, limit=limit)

        # If we got results, apply document_type boost reranking
        if document_type and results:
            reranked = []
            for correction, score in results:
                if correction.document_type == document_type:
                    reranked.append((correction, score * 1.2))
                else:
                    reranked.append((correction, score))
            reranked.sort(key=lambda x: x[1], reverse=True)
            return [c for c, _ in reranked[:limit]]

        return [c for c, _ in results]

    @staticmethod
    def _build_text(correction: CorrectionRecord) -> str:
        """Combine correction fields into a single searchable text."""
        parts = [
            correction.document_excerpt,
            correction.correction_reason,
            correction.field_name,
            correction.document_type,
        ]
        return " ".join(p for p in parts if p)

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Simple whitespace + punctuation tokenizer with lowercasing."""
        text = text.lower()
        # Split on non-alphanumeric, keep tokens of length >= 2
        tokens = re.findall(r"[a-z0-9]+", text)
        return [t for t in tokens if len(t) >= 2]

    @staticmethod
    def _cosine_similarity(vec_a: Dict[str, float], vec_b: Dict[str, float]) -> float:
        """Compute cosine similarity between two sparse vectors."""
        if not vec_a or not vec_b:
            return 0.0

        # Dot product (only over shared keys)
        shared_keys = set(vec_a.keys()) & set(vec_b.keys())
        dot_product = sum(vec_a[k] * vec_b[k] for k in shared_keys)

        # Magnitudes
        mag_a = math.sqrt(sum(v * v for v in vec_a.values()))
        mag_b = math.sqrt(sum(v * v for v in vec_b.values()))

        if mag_a == 0 or mag_b == 0:
            return 0.0

        return dot_product / (mag_a * mag_b)
