"""
sidecar.py — Sidecar YAML metadata files for inbox ingestion.

A sidecar is a `<stem>.meta.yaml` file dropped alongside a main inbox file.
It can carry routing hints and content metadata that bypass the LLM classifier
and enrich the synthesis prompt.

Introduced in the MAURICIO deployment.

Supported sidecar keys:
  folder_hint   vault folder path to route to (bypasses classifier)
  no_merge      true → never merge this file into an existing note (SAAD, ported from 0.1.0)
  type          document type hint for synthesis (meeting, research, decision, …)
  client        client/project name injected into the note frontmatter
  topics        list of topic tags
  council_session  (meeting-specific) council session identifier
"""

import logging
from pathlib import Path

logger = logging.getLogger("sidecar")

_SUFFIXES = (".meta.yaml", ".meta.yml")


def is_sidecar(path: Path) -> bool:
    """Return True if the path is a sidecar metadata file (not a main content file)."""
    return any(path.name.endswith(s) for s in _SUFFIXES)


def sidecar_for(main_path: Path) -> Path | None:
    """Return the sidecar path for main_path if one exists on disk, else None."""
    for suffix in _SUFFIXES:
        candidate = main_path.parent / f"{main_path.stem}{suffix}"
        if candidate.exists():
            return candidate
    return None


def load(main_path: Path) -> dict:
    """Load and parse the sidecar for main_path. Returns {} on missing or invalid YAML."""
    sc = sidecar_for(main_path)
    if not sc:
        return {}
    try:
        import yaml
        data = yaml.safe_load(sc.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("Failed to parse sidecar %s: %s", sc.name, e)
        return {}


def is_valid_folder_hint(hint, vault_folders: list) -> bool:
    """Return True if hint's root segment names a configured vault folder.

    folder_hint may be a subpath like 'meetings/Gazin/2026-2027' — only the
    root segment needs to match a configured vault folder.
    """
    if not hint or not isinstance(hint, str):
        return False
    head = hint.strip("/").split("/", 1)[0]
    return head in vault_folders


def format_block(sidecar: dict) -> str:
    """Render sidecar as a bullet list for injection into a synthesis prompt.
    Excludes routing-only keys (folder_hint) that aren't content hints."""
    if not sidecar:
        return "(none)"
    skip_keys = {"folder_hint"}
    lines = []
    for k, v in sidecar.items():
        if k in skip_keys or v is None or v == "":
            continue
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        lines.append(f"- {k}: {v}")
    return "\n".join(lines) if lines else "(none)"
