"""
ingest.py — External folder ingestion (ABNER).

Index files from any path without moving or modifying them.
Uses embeddings.chunk_text for long documents and persists an ingestion registry
so re-runs are safe (upsert semantics — no duplicates).

New in v0.2.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from .config import CONFIG_DIR
from .embeddings import chunk_text

logger = logging.getLogger("ingest")

_REGISTRY_FILE = CONFIG_DIR / "ingested_sources.json"


def _load_registry() -> dict:
    try:
        return json.loads(_REGISTRY_FILE.read_text(encoding="utf-8")) if _REGISTRY_FILE.exists() else {}
    except Exception:
        return {}


def _save_registry(registry: dict):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _REGISTRY_FILE.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Could not save ingest registry: %s", e)


class IngestManager:
    """Index external files into the vault's ChromaDB without touching them on disk.

    External results are tagged folder='_external' so search_vault can distinguish
    them from vault notes. Each file's absolute path is the ChromaDB document ID,
    so re-indexing the same path is safe.
    """

    def __init__(self, vault_manager):
        self._vault = vault_manager
        self._cfg = vault_manager.cfg

    def ingest(self, source_path: str, recursive: bool = True) -> dict:
        """Index all supported files under source_path.

        source_path: absolute path to a file or directory.
        recursive: walk subdirectories (default True).
        """
        from .extractor import SUPPORTED, extract

        source = Path(source_path).expanduser().resolve()
        if not source.exists():
            return {"error": f"Path not found: {source_path}"}

        candidates: list[Path]
        if source.is_file():
            candidates = [source] if source.suffix.lower() in SUPPORTED else []
        else:
            pattern = "**/*" if recursive else "*"
            candidates = [
                f for f in source.glob(pattern)
                if f.is_file() and f.suffix.lower() in SUPPORTED
            ]

        indexed: list[str] = []
        errors: list[str] = []
        skipped: list[str] = []
        now = datetime.now().isoformat()

        max_chars = self._cfg.ingest_chunk_size
        overlap   = self._cfg.ingest_chunk_overlap

        for f in candidates:
            try:
                content = extract(f)
                if not content or not content.strip():
                    skipped.append(f.name)
                    continue
                chunks = chunk_text(content, max_chars=max_chars, overlap=overlap)
                for i, chunk in enumerate(chunks):
                    chunk_id = f"{f}::chunk_{i}" if len(chunks) > 1 else str(f)
                    self._vault.index_note(
                        chunk,
                        {
                            "title":         f.stem,
                            "path":          str(f),
                            "folder":        "_external",
                            "source_folder": str(source),
                            "ingested_at":   now,
                            "is_external":   "true",
                            "chunk":         str(i),
                            "total_chunks":  str(len(chunks)),
                        },
                        doc_id=chunk_id,
                    )
                indexed.append(str(f))
            except Exception as e:
                logger.warning("Ingest error %s: %s", f.name, e)
                errors.append(f"{f.name}: {e}")

        registry = _load_registry()
        registry[str(source)] = {
            "last_indexed":  now,
            "indexed_count": len(indexed),
            "error_count":   len(errors),
            "recursive":     recursive,
        }
        _save_registry(registry)

        return {"source": str(source), "indexed": len(indexed),
                "skipped": len(skipped), "errors": errors}

    def status(self) -> dict:
        """Return the ingestion registry: which paths have been indexed and when."""
        registry = _load_registry()
        return {"sources": registry, "count": len(registry)}
