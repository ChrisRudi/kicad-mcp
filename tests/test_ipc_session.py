# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the central IPC session layer: configurable timeout, client reuse,
busy-retry with backoff, reconnect-on-drop, and log-dir derivation. All pure /
fake-injected — no kipy or running KiCad needed.
"""

from __future__ import annotations

import logging

import pytest

from kicad_mcp.utils import ipc_session


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    saved_hooks = list(ipc_session._reset_hooks)
    ipc_session.reset_client()
    ipc_session._logging_configured = False
    monkeypatch.delenv(ipc_session._TIMEOUT_ENV, raising=False)
    # make backoff instant so retry tests don't sleep
    monkeypatch.setattr(ipc_session.time, "sleep", lambda *_a, **_k: None)
    yield
    ipc_session._reset_hooks[:] = saved_hooks
    ipc_session.reset_client()


def _client_with_ping(ping):
    """A minimal fake KiCad client whose ``ping()`` runs ``ping`` (which may
    raise to simulate a dead / busy socket)."""
    c = type("C", (), {})()
    c.ping = ping
    return c


class TestTimeout:
    def test_default(self):
        assert ipc_session.timeout_ms() == ipc_session.DEFAULT_TIMEOUT_MS
        assert ipc_session.DEFAULT_TIMEOUT_MS > 2000  # above kipy's default

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv(ipc_session._TIMEOUT_ENV, "30000")
        assert ipc_session.timeout_ms() == 30000

    def test_invalid_env_falls_back(self, monkeypatch):
        monkeypatch.setenv(ipc_session._TIMEOUT_ENV, "nonsense")
        assert ipc_session.timeout_ms() == ipc_session.DEFAULT_TIMEOUT_MS


class TestClientReuse:
    def test_get_client_caches(self):
        made = []

        def factory():
            made.append(1)
            return object()

        c1 = ipc_session.get_client(factory)
        c2 = ipc_session.get_client(factory)
        assert c1 is c2 and len(made) == 1  # connected ONCE, reused

    def test_force_new_reconnects(self):
        c1 = ipc_session.get_client(object)
        c2 = ipc_session.get_client(object, force_new=True)
        assert c1 is not c2

    def test_new_client_is_always_fresh(self):
        assert ipc_session.new_client(object) is not ipc_session.new_client(object)

    def test_unreachable_raises_clear_error(self):
        def _boom():
            raise OSError("no socket")
        with pytest.raises(RuntimeError, match="Cannot reach KiCad"):
            ipc_session.get_client(_boom)


class TestWslSocketHint:
    """A WSL-launched server cannot reach KiCad's Windows-native IPC socket; the
    connect error must say so (reusing path_env's single env detection) instead
    of looking like 'KiCad is down'."""

    def test_hint_present_under_wsl(self, monkeypatch):
        from kicad_mcp.utils import path_env
        monkeypatch.setattr(path_env, "is_wsl", lambda: True)
        assert "WSL erkannt" in ipc_session._wsl_socket_hint()

    def test_no_hint_off_wsl(self, monkeypatch):
        from kicad_mcp.utils import path_env
        monkeypatch.setattr(path_env, "is_wsl", lambda: False)
        assert ipc_session._wsl_socket_hint() == ""

    def test_unreachable_error_carries_wsl_hint(self, monkeypatch):
        from kicad_mcp.utils import path_env
        monkeypatch.setattr(path_env, "is_wsl", lambda: True)

        def _boom():
            raise OSError("no socket")
        with pytest.raises(RuntimeError, match="WSL erkannt"):
            ipc_session.new_client(_boom)


class TestHealthCheck:
    """The cached client is ping-validated before reuse (self-heal on a stale
    socket), but a *busy* ping must NOT discard an otherwise-live client."""

    def test_live_client_is_pinged_and_reused(self):
        pings = {"n": 0}

        def factory():
            return _client_with_ping(
                lambda: pings.__setitem__("n", pings["n"] + 1))

        c1 = ipc_session.get_client(factory)  # built (not pinged on build)
        c2 = ipc_session.get_client(factory)  # cached → ping → reuse
        assert c1 is c2 and pings["n"] == 1

    def test_stale_client_is_rebuilt(self):
        made = []

        def factory():
            made.append(1)

            def _dead():
                raise RuntimeError("connection closed")
            return _client_with_ping(_dead)

        c1 = ipc_session.get_client(factory)
        c2 = ipc_session.get_client(factory)  # ping fails (conn) → rebuild
        assert c1 is not c2 and len(made) == 2

    def test_busy_client_is_kept(self):
        made = []

        def factory():
            made.append(1)

            def _busy():
                raise RuntimeError("KiCad is busy and cannot respond")
            return _client_with_ping(_busy)

        c1 = ipc_session.get_client(factory)
        c2 = ipc_session.get_client(factory)  # ping says busy → keep, don't rebuild
        assert c1 is c2 and len(made) == 1

    def test_pingless_client_is_trusted(self):
        # a test double / non-kipy client without ping() is reused as-is
        c1 = ipc_session.get_client(object)
        c2 = ipc_session.get_client(object)
        assert c1 is c2


class TestResetHooks:
    """reset_client() fans out to registered sibling caches in lockstep."""

    def test_reset_fires_registered_hooks(self):
        fired = []
        ipc_session.register_reset_hook(lambda: fired.append(1))
        ipc_session.reset_client()
        assert fired == [1]

    def test_register_is_idempotent(self):
        fired = []
        hook = lambda: fired.append(1)  # noqa: E731
        ipc_session.register_reset_hook(hook)
        ipc_session.register_reset_hook(hook)  # second registration is a no-op
        ipc_session.reset_client()
        assert fired == [1]

    def test_force_new_also_fires_hooks(self):
        fired = []
        ipc_session.register_reset_hook(lambda: fired.append(1))
        ipc_session.get_client(object, force_new=True)
        assert fired == [1]

    def test_bad_hook_does_not_block_reset(self):
        def _boom():
            raise RuntimeError("hook blew up")
        ipc_session.register_reset_hook(_boom)
        ipc_session.get_client(object)
        ipc_session.reset_client()  # must not raise
        assert ipc_session._client is None


class TestErrorClassification:
    def test_busy(self):
        assert ipc_session.is_busy_error(Exception("KiCad is busy and cannot respond"))
        assert not ipc_session.is_busy_error(Exception("something else"))

    def test_connection(self):
        assert ipc_session.is_connection_error(Exception("Broken pipe"))
        assert ipc_session.is_connection_error(Exception("connection reset by peer"))
        assert not ipc_session.is_connection_error(Exception("busy"))


class TestRetry:
    def test_busy_then_success_backs_off_and_retries(self):
        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("KiCad is busy and cannot respond")
            return "ok"

        assert ipc_session.call_with_retry(_fn, "t") == "ok"
        assert calls["n"] == 3

    def test_busy_exhausts_and_reraises(self):
        def _fn():
            raise RuntimeError("KiCad is busy")
        with pytest.raises(Exception, match="busy"):
            ipc_session.call_with_retry(_fn, "t", attempts=3)

    def test_non_retryable_raises_immediately(self):
        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            raise ValueError("hard fail")

        with pytest.raises(ValueError):
            ipc_session.call_with_retry(_fn, "t")
        assert calls["n"] == 1  # no retry on a non-busy/non-conn error

    def test_connection_drop_resets_client_then_retries(self):
        ipc_session.get_client(object)  # prime a cached client
        assert ipc_session._client is not None
        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("Broken pipe")
            return "ok"

        assert ipc_session.call_with_retry(_fn, "t") == "ok"
        # the cached client was dropped on the connection error
        assert calls["n"] == 2


class TestLogging:
    def test_log_dir_falls_back_to_tempdir(self):
        # a client whose get_open_documents raises → temp dir fallback
        class _C:
            def get_open_documents(self, _t):
                raise RuntimeError("no docs")
        import tempfile
        assert ipc_session.board_log_dir(_C()) == tempfile.gettempdir()

    def test_configure_logging_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ipc_session, "board_log_dir",
                            lambda client=None: str(tmp_path))
        before = len(logging.getLogger("kicad_mcp").handlers)
        p1 = ipc_session.configure_logging()
        p2 = ipc_session.configure_logging()
        after = len(logging.getLogger("kicad_mcp").handlers)
        assert p1 == p2 and p1.endswith("kicad_mcp_ipc.log")
        assert after == before + 1  # handler added exactly once
        logging.getLogger("kicad_mcp").handlers = (
            logging.getLogger("kicad_mcp").handlers[:before])


class TestConnectBoard:
    def test_returns_client_and_board(self, monkeypatch):
        board = object()
        client = type("C", (), {"get_board": lambda self: board})()
        monkeypatch.setattr(ipc_session, "get_client", lambda *a, **k: client)
        monkeypatch.setattr(ipc_session, "configure_logging",
                            lambda *a, **k: "/tmp/x.log")
        c, b = ipc_session.connect_board()
        assert c is client and b is board

    def test_no_board_raises_clear_error(self, monkeypatch):
        def _no_board(self):
            raise RuntimeError("no board")
        client = type("C", (), {"get_board": _no_board})()
        monkeypatch.setattr(ipc_session, "get_client", lambda *a, **k: client)
        monkeypatch.setattr(ipc_session, "configure_logging",
                            lambda *a, **k: "/tmp/x.log")
        with pytest.raises(RuntimeError, match="No board accessible"):
            ipc_session.connect_board()
