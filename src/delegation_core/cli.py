"""
cli.py — delegation-core v0.4 command-line interface.

Commands:
  setup    Interactive setup wizard (run once per machine).
  run      Start the MCP server (used in Claude Desktop config).
  status   Check vault, binary, model, llama.cpp health, and feature config.
  reindex  Rebuild the ChromaDB search index from vault folders.
  maintain Run inbox maintenance once and exit (used by the SessionStart hook).
  ingest   Index files from an external folder without moving them.
  relink   Add wikilinks to notes in a vault subfolder.
"""

import argparse
import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def cmd_setup(_args):
    from .wizard import run_wizard
    run_wizard()


def cmd_run(args):
    import asyncio
    import os
    from .config import Config, CONFIG_DIR

    cfg = Config.load()
    if not cfg.is_configured():
        sys.stderr.write("delegation-core is not configured.\nRun: delegation-core setup\n")
        sys.exit(1)

    if getattr(args, "recalibrate", False):
        cfg.tok_sec = 0.0
        cfg.save()
        sys.stderr.write("Calibration reset — will recalibrate on startup.\n")

    # Use cached model weights only — suppress HuggingFace Hub network checks
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    # Suppress FastMCP update check on startup
    os.environ.setdefault("FASTMCP_DISABLE_UPDATE_CHECK", "1")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            RotatingFileHandler(
                str(cfg.log_path),
                maxBytes=5 * 1024 * 1024,   # 5 MB per file
                backupCount=3,
                encoding="utf-8",
            ),
            logging.StreamHandler(sys.stderr),
        ],
    )

    if cfg.budget_mode == "auto" and cfg.tok_sec == 0.0:
        from .engine import DelegationEngine

        async def _calibrate():
            engine = DelegationEngine(cfg)
            try:
                logging.info("Auto-calibration: starting llama.cpp and measuring tok/sec...")
                await engine.ensure_running()
                await engine.calibrate()
            finally:
                await engine.aclose()

        asyncio.run(_calibrate())

    from .server import run_server
    run_server(cfg)


def cmd_status(_args):
    from rich.console import Console
    from rich.table import Table
    from .config import Config

    console = Console()
    cfg = Config.load()

    if not cfg.is_configured():
        console.print("[yellow]Not configured.[/yellow] Run: delegation-core setup")
        return

    ok   = "[green]✓[/green]"
    fail = "[red]✗[/red]"

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("key", style="dim", min_width=16)
    table.add_column("value")

    vault_ok  = Path(cfg.vault_path).exists()
    binary_ok = Path(cfg.llama_binary).exists() if cfg.llama_binary else False
    model_ok  = Path(cfg.llama_model).exists()  if cfg.llama_model  else False

    table.add_row("Vault",   f"{ok if vault_ok  else fail}  {cfg.vault_path}")
    table.add_row("Binary",  f"{ok if binary_ok else fail}  {cfg.llama_binary}")
    table.add_row("Model",   f"{ok if model_ok  else fail}  {cfg.llama_model}")
    table.add_row("Folders", ", ".join(cfg.vault_folders))

    import requests
    try:
        r = requests.get(f"{cfg.llama_url}/health", timeout=3)
        llama_str = (
            f"[green]online[/green]  ({cfg.llama_url})"
            if r.status_code == 200
            else f"[yellow]unhealthy[/yellow]  ({cfg.llama_url})"
        )
    except Exception:
        llama_str = f"[dim]offline — will start on first tool call[/dim]  ({cfg.llama_url})"
    table.add_row("llama.cpp", llama_str)

    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(cfg.chroma_path))
        col = client.get_collection("vault_bge")
        table.add_row("ChromaDB", f"[green]✓[/green]  {col.count()} notes indexed")
    except Exception:
        table.add_row("ChromaDB", "[dim]not initialized — run: delegation-core reindex[/dim]")

    # v0.2 feature flags
    table.add_row("budget_mode",  cfg.budget_mode)
    table.add_row("synthesis",
                  f"{'on' if cfg.synthesis_enabled else 'off'} ({cfg.synthesis_lang})")

    console.print()
    console.print(table)
    console.print()


def cmd_reindex(_args):
    from rich.console import Console
    from .config import Config
    from .vault import VaultManager

    console = Console()
    cfg = Config.load()
    if not cfg.is_configured():
        console.print("[yellow]Not configured.[/yellow] Run: delegation-core setup")
        return

    console.print(f"Reindexing [bold]{cfg.vault_path}[/bold] ...")
    vault = VaultManager(cfg)
    count = vault.reindex_vault()
    console.print(f"[green]✓[/green]  {count} notes indexed.")


def cmd_maintain(_args):
    import asyncio
    from .config import Config
    from .engine import DelegationEngine
    from .vault import VaultManager
    from . import organizer

    cfg = Config.load()
    if not cfg.is_configured():
        sys.stderr.write("delegation-core is not configured.\nRun: delegation-core setup\n")
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )

    vault  = VaultManager(cfg)
    engine = DelegationEngine(cfg)
    result = asyncio.run(organizer.run(engine, vault))
    print(json.dumps(result, indent=2))


def cmd_ingest(args):
    from rich.console import Console
    from .config import Config
    from .vault import VaultManager
    from .ingest import IngestManager

    console = Console()
    cfg = Config.load()
    if not cfg.is_configured():
        console.print("[yellow]Not configured.[/yellow] Run: delegation-core setup")
        return

    recursive = not getattr(args, "no_recursive", False)
    console.print(f"Ingesting [bold]{args.path}[/bold] (recursive={recursive}) ...")

    vault  = VaultManager(cfg)
    ingest = IngestManager(vault)
    result = ingest.ingest(args.path, recursive=recursive)

    if "error" in result:
        console.print(f"[red]Error:[/red] {result['error']}")
        return

    console.print(
        f"[green]✓[/green]  {result['indexed']} files indexed, "
        f"{result['skipped']} skipped, {len(result['errors'])} errors."
    )
    if result["errors"]:
        console.print("[dim]Errors:[/dim]")
        for e in result["errors"]:
            console.print(f"  {e}")


def cmd_relink(args):
    from rich.console import Console
    from .config import Config
    from .vault import VaultManager
    from .linker import relink_folder

    console = Console()
    cfg = Config.load()
    if not cfg.is_configured():
        console.print("[yellow]Not configured.[/yellow] Run: delegation-core setup")
        return

    days = getattr(args, "days", None)
    min_sim = getattr(args, "min_similarity", None)
    max_links = getattr(args, "max_links", 8)

    console.print(f"Relinking [bold]{args.folder}[/bold] ...")
    vault  = VaultManager(cfg)
    result = relink_folder(vault, args.folder, days=days,
                           min_similarity=min_sim, max_links_per_note=max_links)
    console.print(f"[green]✓[/green]  {result.get('linked_notes', 0)} notes updated, "
                  f"{result.get('links_added', 0)} links added.")
    if result.get("errors"):
        for e in result["errors"]:
            console.print(f"  [red]{e}[/red]")


def main():
    parser = argparse.ArgumentParser(
        prog="delegation-core",
        description="Local MCP delegation server — llama.cpp + BGE + ChromaDB + Obsidian vault",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    sub.add_parser("setup",    help="Interactive setup wizard (run once per machine)")
    p_run = sub.add_parser("run", help="Start the MCP server (used by Claude Desktop)")
    p_run.add_argument(
        "--recalibrate", action="store_true",
        help="Reset and rerun tok/sec auto-calibration before starting (use after swapping models)",
    )
    sub.add_parser("status",   help="Check vault, model, binary, llama.cpp, and feature config")
    sub.add_parser("reindex",  help="Rebuild ChromaDB search index from vault folders")
    sub.add_parser("maintain", help="Run inbox maintenance once and exit")

    p_ingest = sub.add_parser("ingest", help="Index files from an external folder without moving them")
    p_ingest.add_argument("path",           help="Absolute path to a file or directory to index")
    p_ingest.add_argument("--no-recursive", action="store_true", help="Only index top-level files")

    p_relink = sub.add_parser("relink", help="Add wikilinks to notes in a vault subfolder")
    p_relink.add_argument("folder",                   help="Vault-relative folder path (e.g. meetings)")
    p_relink.add_argument("--days",          type=int, default=None,
                          help="Restrict to notes modified within last N days")
    p_relink.add_argument("--min-similarity", dest="min_similarity", type=float, default=None,
                          help="Override similarity threshold (default from config)")
    p_relink.add_argument("--max-links",      dest="max_links",      type=int, default=8,
                          help="Maximum wikilinks per note (default 8)")

    args = parser.parse_args()

    dispatch = {
        "setup":    cmd_setup,
        "run":      cmd_run,
        "status":   cmd_status,
        "reindex":  cmd_reindex,
        "maintain": cmd_maintain,
        "ingest":   cmd_ingest,
        "relink":   cmd_relink,
    }

    if args.command in dispatch:
        dispatch[args.command](args)
    else:
        parser.print_help()
