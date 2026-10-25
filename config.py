"""Central configuration for CodeScope AI.

All settings come from environment variables (optionally loaded from a local
``.env`` file) with safe defaults.  The Gemini API key is never printed:
``Settings.__repr__`` hides it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:  # python-dotenv is optional at import time so tests can run without it
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env", override=False)

_PLACEHOLDER_KEYS = {"", "your_gemini_api_key_here", "your-api-key", "changeme"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _clean_key(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    key = raw.strip().strip('"').strip("'")
    return None if key.lower() in _PLACEHOLDER_KEYS else key


@dataclass(frozen=True)
class Settings:
    # --- Gemini -----------------------------------------------------------
    gemini_api_key: Optional[str] = field(default=None, repr=False)
    gemini_model: str = "gemini-2.5-flash"
    gemini_timeout_s: float = 60.0
    # --- Embeddings -------------------------------------------------------
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embed_batch_size: int = 64
    # --- Storage ----------------------------------------------------------
    data_dir: Path = BASE_DIR / "data"
    # --- Repository limits ------------------------------------------------
    max_repo_mb: int = 200            # abort the clone if it grows beyond this
    clone_timeout_s: float = 120.0
    max_file_bytes: int = 300_000     # skip individual files larger than this
    max_data_file_bytes: int = 60_000  # smaller cap for json/xml/yaml/txt
    max_files: int = 3000
    max_chunks: int = 20_000
    # --- Chunking / retrieval --------------------------------------------
    max_chunk_lines: int = 80
    top_k: int = 8
    max_context_chars: int = 24_000
    min_relevance: float = 0.15

    @property
    def has_api_key(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def repos_dir(self) -> Path:
        return self.data_dir / "repos"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "indexes"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.repos_dir, self.index_dir, self.chroma_dir):
            path.mkdir(parents=True, exist_ok=True)


def get_settings(data_dir: Optional[Path] = None) -> Settings:
    """Build Settings from the current environment (read fresh on every call)."""
    return Settings(
        gemini_api_key=_clean_key(os.environ.get("GEMINI_API_KEY")),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash",
        gemini_timeout_s=_float_env("GEMINI_TIMEOUT_SECONDS", 60.0),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2").strip(),
        data_dir=Path(data_dir) if data_dir else Path(os.environ.get("CODESCOPE_DATA_DIR", BASE_DIR / "data")),
        max_repo_mb=_int_env("MAX_REPO_MB", 200),
        clone_timeout_s=_float_env("CLONE_TIMEOUT_SECONDS", 120.0),
        max_files=_int_env("MAX_FILES", 3000),
    )
