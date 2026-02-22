"""
Codebase embeddings and semantic search.

Provides a fast, local semantic search over the codebase using lightweight
embedding models (like jina-embeddings-v2-base-code) and Qdrant for vector storage.
"""

import os
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

try:
    from fastembed import TextEmbedding
    from qdrant_client import QdrantClient
    from qdrant_client.http.models import Distance, VectorParams, PointStruct
    HAS_EMBEDDINGS = True
except ImportError:
    HAS_EMBEDDINGS = False

logger = logging.getLogger(__name__)

# Lightweight model, strictly trained for code search, runs comfortably on 8GB Mac
DEFAULT_EMBEDDING_MODEL = "jinaai/jina-embeddings-v2-base-code"
COLLECTION_NAME = "codebase_chunks"

class CodebaseRAG:
    """Retrieval-Augmented Generation context provider for the codebase."""

    def __init__(self, project_root: str, model_name: str = DEFAULT_EMBEDDING_MODEL):
        self.project_root = Path(project_root)
        self.model_name = model_name

        self.db_path = self.project_root / ".noether" / "qdrant"
        self.db_path.mkdir(parents=True, exist_ok=True)

        # Load model and DB lazily to avoid startup blocks
        self._model = None
        self._qdrant = None
        self._dimension = None

    @property
    def model(self):
        if not HAS_EMBEDDINGS:
            raise RuntimeError("Embeddings dependencies not installed. Run `pip install fastembed qdrant-client`.")
        if self._model is None:
            logger.info(f"Loading embedding model: {self.model_name}")
            self._model = TextEmbedding(model_name=self.model_name)
            # Get dimension from a test embedding
            test_vector = list(self._model.embed(["test"]))[0]
            self._dimension = len(test_vector)
        return self._model

    @property
    def qdrant(self):
        if not HAS_EMBEDDINGS:
            raise RuntimeError("Embeddings dependencies not installed.")
        if self._qdrant is None:
            logger.info(f"Initializing Qdrant at {self.db_path}")
            self._qdrant = QdrantClient(path=str(self.db_path))

            # Ensure index exists — access model to get dimension
            _ = self.model
            dim = self._dimension

            try:
                self._qdrant.get_collection(COLLECTION_NAME)
            except Exception:
                logger.info("Creating new Qdrant collection")
                self._qdrant.create_collection(
                    collection_name=COLLECTION_NAME,
                    vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
                )
        return self._qdrant

    SKIP_DIRS = {'.git', '__pycache__', 'node_modules', 'venv', 'env', '.noether'}
    DEFAULT_EXTENSIONS = [".py", ".md", ".json", ".js", ".ts", ".html", ".css"]

    @staticmethod
    def scan_files(
        project_root: str | Path,
        file_extensions: List[str] | None = None,
    ) -> List[Path]:
        """Walk the project and return indexable file paths (no reading/embedding)."""
        root = Path(project_root)
        exts = file_extensions or CodebaseRAG.DEFAULT_EXTENSIONS
        result: List[Path] = []
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in CodebaseRAG.SKIP_DIRS]
            for fname in files:
                p = Path(dirpath) / fname
                if p.suffix in exts:
                    result.append(p)
        return result

    def index_repository(
        self,
        file_extensions: List[str] | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> int:
        """
        Scan and index the repository.

        Args:
            file_extensions: File suffixes to include.
            on_progress: Optional callback(current, total, rel_path) called after each file.

        Returns:
            Number of files indexed.
        """
        exts = file_extensions or self.DEFAULT_EXTENSIONS
        file_list = self.scan_files(self.project_root, exts)
        total = len(file_list)

        logger.info(f"Indexing repository: {self.project_root} ({total} files)")

        points = []
        doc_id = 0

        for idx, path in enumerate(file_list):
            try:
                rel_path = path.relative_to(self.project_root)
                content = path.read_text(encoding='utf-8')
                chunk_text = content[:4000]

                vector = list(self.model.embed([chunk_text]))[0].tolist()

                points.append(PointStruct(
                    id=doc_id,
                    vector=vector,
                    payload={
                        "path": str(rel_path),
                        "content_preview": chunk_text[:500]
                    }
                ))
                doc_id += 1

                if on_progress:
                    on_progress(idx + 1, total, str(rel_path))

            except Exception as e:
                logger.warning(f"Failed to index {path}: {e}")

        if points:
            logger.info(f"Upserting {len(points)} points to Qdrant")
            self.qdrant.upsert(
                collection_name=COLLECTION_NAME,
                points=points
            )
            logger.info("Indexing complete.")

        return doc_id

    def get_index_coverage(self) -> tuple[int, int]:
        """Return (indexed_count, total_files) without loading the embedding model.

        Safe to call from any thread. Uses the existing client if open,
        otherwise reads the Qdrant metadata on disk (no exclusive lock needed).
        """
        total_files = len(self.scan_files(self.project_root))
        if total_files == 0:
            return 0, 0
        try:
            if self._qdrant is not None:
                # Reuse existing client — already holds the lock
                info = self._qdrant.get_collection(COLLECTION_NAME)
                return info.points_count, total_files
            else:
                # No client open — try to open one briefly
                # This will fail if another client holds the lock
                client = QdrantClient(path=str(self.db_path))
                try:
                    info = client.get_collection(COLLECTION_NAME)
                    return info.points_count, total_files
                finally:
                    client.close()
        except Exception:
            return 0, total_files

    def close(self) -> None:
        """Close the Qdrant client and release the directory lock."""
        if self._qdrant is not None:
            try:
                self._qdrant.close()
            except Exception:
                pass
            self._qdrant = None

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search the codebase for the query."""
        logger.info(f"Semantic search for: {query}")

        query_vector = list(self.model.embed([query]))[0].tolist()

        results = self.qdrant.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_vector,
            limit=limit
        )

        formatted_results = []
        for res in results:
            formatted_results.append({
                "path": res.payload.get("path"),
                "score": res.score,
                "preview": res.payload.get("content_preview")
            })

        return formatted_results
