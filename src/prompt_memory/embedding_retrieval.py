"""Embedding-based correction retrieval using sentence transformers or Bedrock embeddings.

Upgrades TF-IDF retrieval with dense vector similarity for higher-quality
semantic matching. Falls back to TF-IDF when embedding dependencies unavailable.

Supports:
- Local sentence-transformers (all-MiniLM-L6-v2, zero API calls)
- Amazon Bedrock Titan Embeddings (API-based, no local model)
- Auto-fallback to TF-IDF if neither is available
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.extraction.models import CorrectionRecord
from src.prompt_memory.semantic_retrieval import SemanticRetriever

logger = logging.getLogger(__name__)


class EmbeddingRetriever:
    """Semantic retrieval using dense embeddings for higher-quality matching.

    Architecture:
    - Embeds correction (excerpt + reason) into vectors at index time
    - Embeds query (document text section) into vector at retrieval time
    - Computes cosine similarity between query and all corrections
    - Applies field_name and document_type boosting

    Falls back to TF-IDF SemanticRetriever if embedding backend is unavailable.
    """

    def __init__(
        self,
        backend: str = "auto",
        model_name: str = "all-MiniLM-L6-v2",
        bedrock_client: Optional[Any] = None,
        bedrock_model_id: str = "amazon.titan-embed-text-v2:0",
        cache_path: Optional[Path] = None,
    ):
        """Initialize the embedding retriever.

        Args:
            backend: "sentence-transformers", "bedrock", "auto" (try ST then Bedrock then TF-IDF)
            model_name: Sentence transformer model name (if using local).
            bedrock_client: boto3 bedrock-runtime client (if using Bedrock).
            bedrock_model_id: Bedrock embedding model ID.
            cache_path: Optional directory to cache embeddings.
        """
        self._backend = backend
        self._model_name = model_name
        self._bedrock_client = bedrock_client
        self._bedrock_model_id = bedrock_model_id
        self._cache_path = cache_path

        self._encoder = None
        self._corrections: List[CorrectionRecord] = []
        self._embeddings: List[List[float]] = []
        self._fallback: Optional[SemanticRetriever] = None

        self._initialize_backend()

    def _initialize_backend(self) -> None:
        """Attempt to initialize the embedding backend."""
        if self._backend in ("sentence-transformers", "auto"):
            try:
                from sentence_transformers import SentenceTransformer

                self._encoder = SentenceTransformer(self._model_name)
                self._backend = "sentence-transformers"
                logger.info("Using sentence-transformers backend: %s", self._model_name)
                return
            except ImportError:
                if self._backend == "sentence-transformers":
                    raise
                logger.debug("sentence-transformers not available, trying next backend")

        if self._backend in ("bedrock", "auto"):
            if self._bedrock_client is not None:
                self._backend = "bedrock"
                logger.info(
                    "Using Bedrock embeddings backend: %s", self._bedrock_model_id
                )
                return
            elif self._backend == "bedrock":
                raise ValueError("Bedrock backend requested but no client provided")

        # Fall back to TF-IDF
        self._backend = "tfidf-fallback"
        self._fallback = SemanticRetriever()
        logger.info(
            "Falling back to TF-IDF semantic retrieval (no embedding backend available)"
        )

    def index(self, corrections: List[CorrectionRecord]) -> None:
        """Build embedding index over corrections."""
        self._corrections = corrections

        if self._backend == "tfidf-fallback":
            self._fallback.index(corrections)
            return

        # Build text representations
        texts = [self._build_text(c) for c in corrections]

        # Generate embeddings
        self._embeddings = self._embed_batch(texts)

        # Optionally cache
        if self._cache_path:
            self._save_cache()

    def retrieve(
        self,
        query_text: str,
        field_name: Optional[str] = None,
        document_type: Optional[str] = None,
        limit: int = 3,
        min_similarity: float = 0.2,
    ) -> List[Tuple[CorrectionRecord, float]]:
        """Retrieve corrections by embedding similarity.

        Args:
            query_text: Document text to match against.
            field_name: Optional field name for boosting.
            document_type: Optional document type for boosting.
            limit: Max results.
            min_similarity: Minimum cosine similarity threshold.

        Returns:
            List of (CorrectionRecord, similarity_score) sorted descending.
        """
        if self._backend == "tfidf-fallback":
            return self._fallback.retrieve(
                query_text,
                field_name=field_name,
                limit=limit,
                min_similarity=min_similarity,
            )

        if not self._corrections or not self._embeddings:
            return []

        # Embed query
        query_embedding = self._embed_single(query_text)
        if not query_embedding:
            return []

        # Compute similarities
        scored: List[Tuple[int, float]] = []
        for i, doc_emb in enumerate(self._embeddings):
            sim = self._cosine_similarity(query_embedding, doc_emb)

            # Apply boosts
            if field_name and self._corrections[i].field_name == field_name:
                sim *= 1.3  # 30% boost for matching field
            if document_type and self._corrections[i].document_type == document_type:
                sim *= 1.15  # 15% boost for matching doc type

            if sim >= min_similarity:
                scored.append((i, sim))

        scored.sort(key=lambda x: x[1], reverse=True)

        return [
            (self._corrections[idx], min(score, 1.0)) for idx, score in scored[:limit]
        ]

    def retrieve_for_field(
        self,
        document_text: str,
        field_name: str,
        document_type: str = "",
        limit: int = 3,
    ) -> List[CorrectionRecord]:
        """Convenience method matching SemanticRetriever interface."""
        query = document_text[:1000]
        results = self.retrieve(
            query, field_name=field_name, document_type=document_type, limit=limit
        )
        return [c for c, _ in results]

    def _embed_single(self, text: str) -> List[float]:
        """Embed a single text string."""
        if self._backend == "sentence-transformers" and self._encoder:
            embedding = self._encoder.encode(text, show_progress_bar=False)
            return embedding.tolist()
        elif self._backend == "bedrock":
            return self._bedrock_embed(text)
        return []

    def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of texts."""
        if self._backend == "sentence-transformers" and self._encoder:
            embeddings = self._encoder.encode(texts, show_progress_bar=False)
            return [emb.tolist() for emb in embeddings]
        elif self._backend == "bedrock":
            return [self._bedrock_embed(t) for t in texts]
        return []

    def _bedrock_embed(self, text: str) -> List[float]:
        """Get embedding from Bedrock Titan Embeddings."""
        try:
            response = self._bedrock_client.invoke_model(
                modelId=self._bedrock_model_id,
                body=json.dumps({"inputText": text[:8000]}),
                contentType="application/json",
            )
            body = json.loads(response["body"].read())
            return body.get("embedding", [])
        except Exception as exc:
            logger.error("Bedrock embedding failed: %s", exc)
            return []

    @staticmethod
    def _build_text(correction: CorrectionRecord) -> str:
        """Combine correction fields into embeddable text."""
        parts = [
            f"Field: {correction.field_name}",
            f"Error: {correction.original_value} → {correction.corrected_value}",
            f"Reason: {correction.correction_reason}",
            f"Context: {correction.document_excerpt}",
        ]
        return " | ".join(parts)

    @staticmethod
    def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        """Compute cosine similarity between two dense vectors."""
        if len(vec_a) != len(vec_b) or not vec_a:
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        mag_a = math.sqrt(sum(a * a for a in vec_a))
        mag_b = math.sqrt(sum(b * b for b in vec_b))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    def _save_cache(self) -> None:
        """Cache embeddings to disk."""
        if not self._cache_path:
            return
        self._cache_path.mkdir(parents=True, exist_ok=True)
        cache_file = self._cache_path / "embeddings_cache.json"
        data = {
            "backend": self._backend,
            "model": (
                self._model_name
                if self._backend == "sentence-transformers"
                else self._bedrock_model_id
            ),
            "embeddings": self._embeddings,
            "correction_count": len(self._corrections),
        }
        with open(cache_file, "w") as f:
            json.dump(data, f)

    @property
    def backend_name(self) -> str:
        """Return the active backend name."""
        return self._backend
