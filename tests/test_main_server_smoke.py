"""Smoke test: ``python main.py`` reaches Uvicorn application startup (port override for CI)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


def _pick_ephemeral_listen_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        _host, port = probe.getsockname()
        return int(port)


def test_main_py_reaches_uvicorn_application_startup_complete() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    listen_port = _pick_ephemeral_listen_port()
    env = os.environ.copy()
    env["ETERNAL_CITIES_HTTP_PORT_OVERRIDE"] = str(listen_port)
    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    startup_seen = threading.Event()
    lines_out: list[str] = []

    def _drain_stdout() -> None:
        try:
            for line in iter(proc.stdout.readline, ""):
                lines_out.append(line)
                if "Application startup complete" in line:
                    startup_seen.set()
                    break
        except Exception:
            startup_seen.set()

    reader = threading.Thread(target=_drain_stdout, name="main-smoke-stdout", daemon=True)
    reader.start()
    try:
        assert startup_seen.wait(45.0), (
            "main.py did not report Uvicorn startup within 45s; last lines:\n"
            + "".join(lines_out[-40:])
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=12.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)
        reader.join(timeout=2.0)
