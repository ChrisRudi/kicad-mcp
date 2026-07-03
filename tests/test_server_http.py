# SPDX-License-Identifier: GPL-3.0-or-later
"""Warm-server Phase 1: the HTTP (streamable-http) transport serves the FULL
tool registry.

Spawns the real server as a subprocess on ``127.0.0.1:<free port>`` — exactly
how ``plugin/server_manager.py`` will launch it — then completes an MCP
``initialize`` + ``tools/list`` over HTTP and checks the tool count against the
registry lock. Also proves the bearer-token gate: a request without the token
is rejected with 401. See docs/warm-server-plan.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.test_tool_audit import EXPECTED_TOOL_COUNT

REPO_ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token-123"
START_TIMEOUT_S = 120.0  # cold import of pandas + 183 tools — be generous


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port: int, proc: subprocess.Popen, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stderr = (proc.stderr.read() or b"").decode("utf-8", "replace")
            pytest.fail(f"server exited early (rc={proc.returncode}):\n"
                        f"{stderr[-2000:]}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.3)
    proc.kill()
    pytest.fail(f"server did not open port {port} within {timeout}s")


@pytest.fixture(scope="module")
def http_server():
    """A real kicad-mcp server on streamable-http, torn down after the tests."""
    port = _pick_free_port()
    bootstrap = (f"import sys; sys.path[:0] = [{str(REPO_ROOT)!r}]; "
                 "from kicad_mcp.server import main; main()")
    env = dict(os.environ)
    env["KICAD_MCP_HTTP_TOKEN"] = TOKEN
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-c", bootstrap,
         "--transport", "streamable-http", "--host", "127.0.0.1",
         "--port", str(port)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, env=env, cwd=str(REPO_ROOT))
    try:
        _wait_for_port(port, proc, START_TIMEOUT_S)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.kill()
        proc.wait(timeout=15)


def test_http_serves_full_tool_registry(http_server):
    """initialize + tools/list over HTTP → the complete locked tool count."""
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    async def _list_tools():
        transport = StreamableHttpTransport(
            http_server, headers={"Authorization": f"Bearer {TOKEN}"})
        async with Client(transport) as client:
            return await client.list_tools()

    tools = asyncio.run(_list_tools())
    assert len(tools) == EXPECTED_TOOL_COUNT


class TestTransportResolution:
    """Pure parsing — no server spawn."""

    def test_unknown_and_empty_fall_back_to_stdio(self):
        from kicad_mcp import server
        assert server.resolve_transport("") == "stdio"
        assert server.resolve_transport("bogus") == "stdio"
        assert server.resolve_transport("stdio") == "stdio"

    def test_http_aliases_normalize(self):
        from kicad_mcp import server
        assert server.resolve_transport("http") == "streamable-http"
        assert server.resolve_transport("HTTP") == "streamable-http"
        assert server.resolve_transport("streamable-http") == "streamable-http"

    def test_parse_args_default_is_stdio(self, monkeypatch):
        from kicad_mcp import server
        monkeypatch.delenv(server.TRANSPORT_ENV, raising=False)
        args = server.parse_args([])
        assert args.transport == "stdio"

    def test_parse_args_env_fallback(self, monkeypatch):
        from kicad_mcp import server
        monkeypatch.setenv(server.TRANSPORT_ENV, "http")
        monkeypatch.setenv(server.HTTP_PORT_ENV, "9123")
        args = server.parse_args([])
        assert args.transport == "streamable-http"
        assert args.host == "127.0.0.1"
        assert args.port == 9123

    def test_parse_args_argv_wins_over_env(self, monkeypatch):
        from kicad_mcp import server
        monkeypatch.setenv(server.TRANSPORT_ENV, "http")
        args = server.parse_args(
            ["--transport", "stdio", "--port", "7000"])
        assert args.transport == "stdio"
        assert args.port == 7000


def test_manager_end_to_end(monkeypatch, tmp_path):
    """server_manager wirklich gegen den echten Server: start → ping →
    reuse → shutdown. Der volle Warm-Server-Lebenszyklus ohne Mocks."""
    from plugin import server_manager, server_probe

    monkeypatch.setenv(server_manager.STATE_DIR_ENV, str(tmp_path))
    try:
        first = server_manager.ensure_running(
            mcp_root=str(REPO_ROOT), python_exe=sys.executable, deps_dir="",
            timeout=START_TIMEOUT_S)
        assert first["ok"], first["error"]
        assert not first["reused"]

        ping = server_probe.probe_http(first["url"], first["token"])
        assert ping["ok"], ping["error"]
        # wrong token must bounce (the gate is armed)
        assert server_probe.probe_http(first["url"], "falsch")["status"] == 401

        second = server_manager.ensure_running(
            mcp_root=str(REPO_ROOT), python_exe=sys.executable, deps_dir="")
        assert second["ok"] and second["reused"]
        assert second["port"] == first["port"]
    finally:
        server_manager.shutdown()
    assert server_manager.read_state() == {}
    deadline = time.monotonic() + 10
    while server_manager.pid_alive(first["pid"]):
        assert time.monotonic() < deadline, "warm server survived shutdown()"
        time.sleep(0.2)


def test_http_rejects_missing_token(http_server):
    """No/wrong Authorization header → 401 (the local-process gate works)."""
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "probe", "version": "0"}},
    }).encode("utf-8")
    req = urllib.request.Request(
        http_server, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=10)
    assert exc_info.value.code == 401
