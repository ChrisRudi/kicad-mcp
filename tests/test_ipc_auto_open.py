# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the Phase-7 auto-open hook in ``ipc_tools``.

The hook (``_require_editor``) launches the missing KiCad editor when a
tool is invoked while the wrong editor (or none) is open. These tests
exercise the contract without ever actually spawning eeschema/pcbnew —
``subprocess.Popen`` is monkeypatched.
"""


# pylint: disable=no-name-in-module  # generated kipy protobuf modules
from __future__ import annotations

from typing import Any

import pytest

from kicad_mcp.tools import ipc_tools


# ---------------------------------------------------------------------------
# _consume_auto_open / _attach_auto_open  (no I/O at all)
# ---------------------------------------------------------------------------


class TestConsumeAutoOpen:
    def setup_method(self) -> None:
        # reset the shared module-level slot
        ipc_tools._AUTO_OPEN_LAST["doc_type"] = None
        ipc_tools._AUTO_OPEN_LAST["binary"] = ""
        ipc_tools._AUTO_OPEN_LAST["project_file"] = ""

    def test_consume_returns_none_when_nothing_launched(self) -> None:
        assert ipc_tools._consume_auto_open() is None

    def test_consume_returns_record_then_clears(self) -> None:
        ipc_tools._AUTO_OPEN_LAST["doc_type"] = "schematic"
        ipc_tools._AUTO_OPEN_LAST["binary"] = "eeschema.exe"
        ipc_tools._AUTO_OPEN_LAST["project_file"] = "/tmp/x.kicad_sch"

        rec = ipc_tools._consume_auto_open()
        assert rec is not None
        assert rec["doc_type"] == "schematic"
        assert rec["binary"] == "eeschema.exe"

        # consume is destructive: a second call sees nothing
        assert ipc_tools._consume_auto_open() is None

    def test_attach_no_op_when_nothing_pending(self) -> None:
        original = {"success": True, "x_mm": 1.0}
        result = ipc_tools._attach_auto_open(original)
        assert "auto_opened" not in result
        assert result is original or result == original

    def test_attach_splices_record(self) -> None:
        ipc_tools._AUTO_OPEN_LAST["doc_type"] = "pcb"
        ipc_tools._AUTO_OPEN_LAST["binary"] = "pcbnew.exe"
        ipc_tools._AUTO_OPEN_LAST["project_file"] = r"C:\proj\x.kicad_pcb"

        result = ipc_tools._attach_auto_open({"success": True})
        assert result["success"] is True
        assert result["auto_opened"]["doc_type"] == "pcb"
        assert result["auto_opened"]["binary"] == "pcbnew.exe"

        # attach also consumes
        assert ipc_tools._consume_auto_open() is None


# ---------------------------------------------------------------------------
# _editor_binary_path — derives sibling-of-kicad-cli
# ---------------------------------------------------------------------------


class TestEditorBinaryPath:
    def test_returns_empty_when_cli_unknown(self, monkeypatch) -> None:
        from kicad_mcp.utils import path_env

        monkeypatch.setattr(path_env, "kicad_cli", lambda: "")
        assert ipc_tools._editor_binary_path("schematic") == ""
        assert ipc_tools._editor_binary_path("pcb") == ""

    def test_resolves_sibling_binary(self, tmp_path, monkeypatch) -> None:
        # Build a fake KiCad bin dir with all three executables.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        for name in ("kicad-cli.exe", "eeschema.exe", "pcbnew.exe"):
            (bin_dir / name).write_text("")

        from kicad_mcp.utils import path_env

        monkeypatch.setattr(
            path_env, "kicad_cli", lambda: str(bin_dir / "kicad-cli.exe")
        )

        sch = ipc_tools._editor_binary_path("schematic")
        pcb = ipc_tools._editor_binary_path("pcb")
        assert sch.endswith("eeschema.exe")
        assert pcb.endswith("pcbnew.exe")

    def test_returns_empty_when_sibling_missing(self, tmp_path, monkeypatch) -> None:
        # Only kicad-cli exists — eeschema/pcbnew do not.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "kicad-cli").write_text("")

        from kicad_mcp.utils import path_env

        monkeypatch.setattr(
            path_env, "kicad_cli", lambda: str(bin_dir / "kicad-cli")
        )
        assert ipc_tools._editor_binary_path("schematic") == ""
        assert ipc_tools._editor_binary_path("pcb") == ""


# ---------------------------------------------------------------------------
# _require_editor — branch coverage without spawning real editors
# ---------------------------------------------------------------------------


class _FakeProject:
    def __init__(self, name: str = "demo", path: str = "/tmp/demo"):
        self.name = name
        self.path = path


class _FakeDoc:
    """Mimic kipy's DocumentSpecifier for the fields _require_editor reads."""

    def __init__(
        self,
        *,
        board_filename: str = "",
        project: _FakeProject | None = None,
        has_project: bool = True,
    ):
        self.board_filename = board_filename
        self.project = project or _FakeProject()
        self._has_project = has_project

    def HasField(self, name: str) -> bool:  # noqa: N802 — mimics protobuf
        if name == "project":
            return self._has_project
        return False


class _FakeKiCad:
    """Mimic the kipy KiCad client for _require_editor's branches."""

    def __init__(self, sch_docs: list, pcb_docs: list):
        self._sch_docs = sch_docs
        self._pcb_docs = pcb_docs

    def get_open_documents(self, doc_type) -> list:
        # 1 = DOCTYPE_SCHEMATIC, 3 = DOCTYPE_PCB in base_types_pb2
        return self._sch_docs if int(doc_type) == 1 else self._pcb_docs


class TestRequireEditor:
    def setup_method(self) -> None:
        # _require_editor now reuses the central, *cached* ipc_session client,
        # so each test must drop the cache or it would inherit the previous
        # test's fake KiCad instead of its own monkeypatched kipy.KiCad.
        from kicad_mcp.utils import ipc_session
        ipc_session.reset_client()
        ipc_tools._AUTO_OPEN_LAST["doc_type"] = None
        ipc_tools._AUTO_OPEN_LAST["binary"] = ""
        ipc_tools._AUTO_OPEN_LAST["project_file"] = ""

    def teardown_method(self) -> None:
        # don't leak a cached fake into other test modules
        from kicad_mcp.utils import ipc_session
        ipc_session.reset_client()

    def test_unknown_doc_type(self) -> None:
        out = ipc_tools._require_editor("not-a-real-type")
        assert out is not None
        assert out["success"] is False
        assert "doc_type" in out["error"]

    def test_kipy_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(ipc_tools, "_kipy_available", lambda: False)
        out = ipc_tools._require_editor("pcb")
        assert out is not None
        assert "kipy" in out["error"].lower()

    def test_already_open_short_circuits(self, monkeypatch) -> None:
        pytest.importorskip("kipy")
        from kipy.proto.common.types.base_types_pb2 import DocumentType  # type: ignore

        monkeypatch.setattr(ipc_tools, "_kipy_available", lambda: True)
        fake = _FakeKiCad(sch_docs=[], pcb_docs=[_FakeDoc(board_filename="b.kicad_pcb")])
        # patch the KiCad constructor used inside _require_editor
        import kipy  # type: ignore
        monkeypatch.setattr(kipy, "KiCad", lambda *_a, **_kw: fake)
        # And patch DocumentType so _FakeKiCad's branch logic matches
        _ = DocumentType  # silences unused import — DocumentType is used by the helper

        # PCB is open → no error, no auto-open record
        assert ipc_tools._require_editor("pcb") is None
        assert ipc_tools._consume_auto_open() is None

    def test_no_project_to_derive_from(self, monkeypatch) -> None:
        pytest.importorskip("kipy")
        monkeypatch.setattr(ipc_tools, "_kipy_available", lambda: True)
        # Neither editor open → no project to derive a path from.
        fake = _FakeKiCad(sch_docs=[], pcb_docs=[])
        import kipy  # type: ignore
        monkeypatch.setattr(kipy, "KiCad", lambda *_a, **_kw: fake)

        out = ipc_tools._require_editor("schematic")
        assert out is not None
        assert "No KiCad project" in out["error"]

    def test_launches_and_polls(self, monkeypatch, tmp_path) -> None:
        pytest.importorskip("kipy")
        # Simulate: PCB is open, SCH is missing. Auto-launch SCH.
        sch_file = tmp_path / "demo.kicad_sch"
        sch_file.write_text("")
        pcb_doc = _FakeDoc(
            board_filename="demo.kicad_pcb",
            project=_FakeProject(name="demo", path=str(tmp_path)),
        )

        # The fake client toggles SCH availability after the first poll
        # tick, simulating eeschema.exe registering on the IPC bus.
        poll_count = {"n": 0}

        class _ToggleClient:
            def get_open_documents(self, dt):
                if int(dt) == 1:  # schematic
                    poll_count["n"] += 1
                    return [_FakeDoc()] if poll_count["n"] >= 2 else []
                return [pcb_doc]

        import kipy  # type: ignore
        monkeypatch.setattr(ipc_tools, "_kipy_available", lambda: True)
        monkeypatch.setattr(kipy, "KiCad", lambda *_a, **_kw: _ToggleClient())

        # Pretend eeschema.exe exists and intercept the spawn
        monkeypatch.setattr(
            ipc_tools, "_editor_binary_path", lambda dt: "/fake/eeschema"
        )
        monkeypatch.setattr(ipc_tools, "_editor_process_running", lambda dt: False)
        spawned: dict[str, Any] = {}

        class _FakePopen:
            def __init__(self, args, **kwargs):
                spawned["args"] = args
                spawned["kwargs"] = kwargs

        import subprocess
        monkeypatch.setattr(subprocess, "Popen", _FakePopen)

        out = ipc_tools._require_editor("schematic", timeout_s=2.0)
        assert out is None, f"expected success, got {out!r}"
        assert spawned["args"][0] == "/fake/eeschema"
        assert str(sch_file) in spawned["args"][1]

        rec = ipc_tools._consume_auto_open()
        assert rec is not None
        assert rec["doc_type"] == "schematic"
        assert rec["binary"] == "eeschema"

    def test_launch_timeout(self, monkeypatch, tmp_path) -> None:
        pytest.importorskip("kipy")
        sch_file = tmp_path / "demo.kicad_sch"
        sch_file.write_text("")
        pcb_doc = _FakeDoc(
            board_filename="demo.kicad_pcb",
            project=_FakeProject(name="demo", path=str(tmp_path)),
        )

        class _AlwaysEmpty:
            def get_open_documents(self, dt):
                return [pcb_doc] if int(dt) == 3 else []

        import kipy  # type: ignore
        monkeypatch.setattr(ipc_tools, "_kipy_available", lambda: True)
        monkeypatch.setattr(kipy, "KiCad", lambda *_a, **_kw: _AlwaysEmpty())
        monkeypatch.setattr(
            ipc_tools, "_editor_binary_path", lambda dt: "/fake/eeschema"
        )
        monkeypatch.setattr(ipc_tools, "_editor_process_running", lambda dt: False)

        import subprocess
        monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_kw: None)

        out = ipc_tools._require_editor("schematic", timeout_s=0.5)
        assert out is not None
        assert "did not register" in out["error"]
        # binary + project_file echoed back for diagnostics
        assert "binary" in out
        assert "project_file" in out
        assert ipc_tools._consume_auto_open() is None

    def test_no_handler_falls_through_to_launch(self, monkeypatch, tmp_path) -> None:
        """When SCH IPC is dead but PCB still answers, still launch.

        Regression for the 10.0.1 case where Eeschema's handler is
        deregistered after a CreateItems hang and ``get_open_documents
        (SCHEMATIC)`` raises ``no handler available``. The hook should
        treat that as "editor missing", not "IPC bus down".
        """
        pytest.importorskip("kipy")
        sch_file = tmp_path / "demo.kicad_sch"
        sch_file.write_text("")
        pcb_doc = _FakeDoc(
            board_filename="demo.kicad_pcb",
            project=_FakeProject(name="demo", path=str(tmp_path)),
        )

        class _SchDeadPcbAlive:
            calls = {"n": 0}

            def get_open_documents(self, dt):
                if int(dt) == 1:
                    self.calls["n"] += 1
                    if self.calls["n"] == 1:
                        raise RuntimeError("no handler available for request")
                    return [_FakeDoc()]  # registers on second poll
                return [pcb_doc]

        import kipy  # type: ignore
        monkeypatch.setattr(ipc_tools, "_kipy_available", lambda: True)
        monkeypatch.setattr(kipy, "KiCad", lambda *_a, **_kw: _SchDeadPcbAlive())
        monkeypatch.setattr(
            ipc_tools, "_editor_binary_path", lambda dt: "/fake/eeschema"
        )
        monkeypatch.setattr(ipc_tools, "_editor_process_running", lambda dt: False)

        import subprocess
        monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_kw: None)

        out = ipc_tools._require_editor("schematic", timeout_s=2.0)
        assert out is None, f"expected fall-through-to-launch, got {out!r}"

    def test_real_ipc_error_short_circuits(self, monkeypatch) -> None:
        """Genuine IPC bus failure (Connection refused) → no launch."""
        pytest.importorskip("kipy")

        class _BusDown:
            def get_open_documents(self, dt):
                raise RuntimeError("Connection refused")

        import kipy  # type: ignore
        monkeypatch.setattr(ipc_tools, "_kipy_available", lambda: True)
        monkeypatch.setattr(kipy, "KiCad", lambda *_a, **_kw: _BusDown())

        out = ipc_tools._require_editor("pcb")
        assert out is not None
        assert "not reachable" in out["error"]

    def test_already_running_does_not_relaunch(self, monkeypatch, tmp_path) -> None:
        """Eeschema runs as a process but its IPC handler is silent →
        do NOT spawn a second instance, just poll until timeout.

        Regression: prior to the process-list check, an unresponsive
        Eeschema (KiCad 10.0.x SCH-IPC fragility) caused the auto-open
        hook to launch a duplicate window because
        ``get_open_documents(SCHEMATIC)`` returns empty.
        """
        pytest.importorskip("kipy")
        sch_file = tmp_path / "demo.kicad_sch"
        sch_file.write_text("")
        pcb_doc = _FakeDoc(
            board_filename="demo.kicad_pcb",
            project=_FakeProject(name="demo", path=str(tmp_path)),
        )

        class _SchSilent:
            def get_open_documents(self, dt):
                return [pcb_doc] if int(dt) == 3 else []

        import kipy  # type: ignore
        monkeypatch.setattr(ipc_tools, "_kipy_available", lambda: True)
        monkeypatch.setattr(kipy, "KiCad", lambda *_a, **_kw: _SchSilent())
        monkeypatch.setattr(
            ipc_tools, "_editor_binary_path", lambda dt: "/fake/eeschema"
        )
        monkeypatch.setattr(ipc_tools, "_editor_process_running", lambda dt: True)

        spawn_calls = {"n": 0}

        class _BoomPopen:
            def __init__(self, *_a, **_kw):
                spawn_calls["n"] += 1

        import subprocess
        monkeypatch.setattr(subprocess, "Popen", _BoomPopen)

        out = ipc_tools._require_editor("schematic", timeout_s=0.3)
        assert out is not None
        assert spawn_calls["n"] == 0, "must not spawn a second editor instance"
        assert out.get("already_running") is True
        assert "already running" in out["error"]


class TestTransientHandlerRetry:
    """Der Bug 'Links tot nach Folge-Abfragen': ein TRANSIENTER 'no handler'
    (GUI busy) darf nicht als 'Editor fehlt' gelesen werden und einen
    Geister-Editor spawnen — erst Backoff, dann glauben."""

    def test_transient_then_success_no_launch_signal(self):
        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("ApiError: no handler available for "
                                   "GetOpenDocuments")
            return ["doc"]

        docs, err = ipc_tools._docs_with_transient_retry(
            fetch, _sleep=lambda s: None)
        assert docs == ["doc"] and err is None
        assert calls["n"] == 3

    def test_persistent_handler_error_reported_not_raised(self):
        def fetch():
            raise RuntimeError("no handler available")

        docs, err = ipc_tools._docs_with_transient_retry(
            fetch, _sleep=lambda s: None)
        assert docs is None and "no handler" in str(err)

    def test_bus_down_reraises(self):
        def fetch():
            raise RuntimeError("connection refused")

        with pytest.raises(RuntimeError, match="connection refused"):
            ipc_tools._docs_with_transient_retry(fetch, _sleep=lambda s: None)

    def test_empty_docs_pass_through_without_retry(self):
        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            return []

        docs, err = ipc_tools._docs_with_transient_retry(
            fetch, _sleep=lambda s: None)
        assert docs == [] and err is None and calls["n"] == 1


class TestAutoOpenDisabled:
    def test_env_values(self, monkeypatch):
        for val, want in (("1", True), ("true", True), ("YES", True),
                          ("0", False), ("", False)):
            monkeypatch.setenv(ipc_tools._NO_AUTO_OPEN_ENV, val)
            assert ipc_tools._auto_open_disabled() is want
        monkeypatch.delenv(ipc_tools._NO_AUTO_OPEN_ENV, raising=False)
        assert ipc_tools._auto_open_disabled() is False
