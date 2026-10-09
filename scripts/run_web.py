#!/usr/bin/env python3
"""Run the Streamlit LAN web app bound to the local network (ADR-006).

The app is launched through Streamlit's CLI so all of Streamlit's lifecycle
and flag parsing is honored. By default it binds to the LAN interface so
operators on the same network (mobile/tablet) can reach it, and the firewall
rule that restricts inbound access to the LAN is documented in the README and
in the code comments below.

Usage:
    python scripts/run_web.py
    python scripts/run_web.py --address 0.0.0.0

Environment:
    PLANT_RAG_QDRANT_URL, PLANT_RAG_OLLAMA_URL, PLANT_RAG_OLLAMA_MODEL,
    and all other PLANT_RAG_* tunables from config.py are honored.

Exit codes:
    0 — the web process exited cleanly (unlikely during serving).
    non-zero — launch failed (missing Streamlit, bad address, etc.).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

# ADR-006: bind every interface so LAN devices (tablet/mobile) can reach the
# UI; the firewall rule documented in the README is the enforcement boundary
# that keeps the service LAN-only ("Request from outside the LAN").
DEFAULT_ADDRESS = "0.0.0.0"
DEFAULT_PORT = 8501


def _repo_root() -> Path:
    """Repo root from this script's location (scripts/ sits next to web/ and src/)."""
    return Path(__file__).resolve().parent.parent


def resolve_bind_address(explicit: str | None) -> str:
    """Bind address: the CLI override, else the ADR-006 LAN-scope default."""
    return explicit or DEFAULT_ADDRESS


def build_env(repo: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """Child env with the repo root and ``src/`` prepended to ``PYTHONPATH``.

    The repo root makes ``web`` (the package) importable inside the app;
    ``src/`` makes ``plant_rag`` importable. An existing ``PYTHONPATH`` is
    preserved, not overwritten.
    """
    env = dict(environ)
    entries = [str(repo), str(repo / "src")]
    existing = env.get("PYTHONPATH", "")
    if existing:
        entries.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(entries)
    return env


def build_command(streamlit: str, app_path: Path, address: str, port: int) -> list[str]:
    """Streamlit CLI invocation for the app with the resolved bind address."""
    return [
        streamlit,
        "run",
        str(app_path),
        f"--server.address={address}",
        f"--server.port={port}",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
    ]


def resolve_streamlit() -> str | None:
    """Locate the Streamlit CLI.

    The README launches ``.venv/bin/python scripts/run_web.py`` without
    activating the virtualenv, so ``PATH`` may not contain ``Scripts/``:
    fall back to the interpreter's own environment (PEP 405 layout), where
    ``pip install -r requirements.txt`` placed the ``streamlit`` entrypoint.
    """
    found = shutil.which("streamlit") or shutil.which("streamlit.exe")
    if found:
        return found
    exe_dir = Path(sys.executable).resolve().parent
    for name in ("streamlit.exe", "streamlit"):
        candidate = exe_dir / name
        if candidate.exists():
            return str(candidate)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Launch the LAN Streamlit web app for the plant RAG system."
    )
    parser.add_argument(
        "--address",
        type=str,
        default=None,
        help="Bind address for Streamlit (default: LAN interface from config).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Port for Streamlit (default: {DEFAULT_PORT}).",
    )
    args = parser.parse_args(argv)

    repo = _repo_root()
    app_path = repo / "web" / "app.py"
    if not app_path.is_file():
        print(f"ERROR: web/app.py not found at {app_path}", file=sys.stderr)
        return 1

    streamlit = resolve_streamlit()
    if not streamlit:
        print(
            "ERROR: streamlit not found — install from requirements.txt",
            file=sys.stderr,
        )
        return 1

    address = resolve_bind_address(args.address)
    port = args.port or DEFAULT_PORT
    env = build_env(repo, os.environ)
    cmd = build_command(streamlit, app_path, address, port)

    print(f"Starting plant RAG web UI on http://{address}:{port}")
    print(f"App: {app_path}")
    print(f"Streamlit: {streamlit}")
    print(f"Env: PYTHONPATH={env['PYTHONPATH']}")
    print("Press Ctrl-C to stop.")
    print("-" * 60)

    try:
        result = subprocess.run(cmd, check=False, cwd=str(repo), env=env)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
