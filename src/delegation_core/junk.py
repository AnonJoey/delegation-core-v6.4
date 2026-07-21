"""
junk.py — Boilerplate file detection.

Catches license files, READMEs, changelogs, requirements, and similar noise
before they reach the classifier or the vault. The model classifier has
repeatedly misfiled these into decisions/research — deterministic filtering
here is cheaper and more reliable than prompt-tuning.

SAAD deployment introduced JUNK_STEM_RE to replace the original set-based approach.
"""

import re
from pathlib import Path

# Matches the filename stem (after stripping any chunk-staging prefix).
JUNK_STEM_RE = re.compile(
    r"^(license|licence|copying|notice|readme|changelog|changes|contributing|"
    r"contributors|authors|requirements|install|installation|help|todo|"
    r"version|manifest|makefile|dockerfile|codeowners|gitignore|gitattributes|"
    r"editorconfig|pylintrc|flake8|mypy)([._\-].*)?$",
    re.IGNORECASE,
)

# Office temporary lock files (prefix ~$).
_OFFICE_LOCK_RE = re.compile(r"^~\$")

# License/boilerplate content signals — matched against the first 500 chars.
_CONTENT_MARKERS = (
    "permission is hereby granted, free of charge",  # MIT
    "apache license",
    "gnu general public license",
    "bsd 2-clause",
    "bsd 3-clause",
    "mozilla public license",
    "creative commons",
)


def is_junk(filename: str, content: str = "") -> str | None:
    """Return a skip reason string if the file looks like boilerplate, else None.

    Checks filename stem first (fast path), then content markers if text is provided.
    """
    name = Path(filename).name
    stem = Path(filename).stem.lower()

    if _OFFICE_LOCK_RE.match(name):
        return f"Office lock file ({name})"

    if JUNK_STEM_RE.match(stem):
        return f"matches boilerplate filename pattern ({stem})"

    if content:
        head = content[:500].lower()
        for marker in _CONTENT_MARKERS:
            if marker in head:
                return f"content matches license/boilerplate text ({marker!r})"

    return None
