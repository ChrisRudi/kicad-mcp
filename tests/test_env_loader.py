# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for kicad_mcp.utils.env.load_dotenv.

The non-override behavior is a regression guard: launcher scripts
(start_mcp.bat / start_mcp_wsl.sh) set KICAD_CLI_PATH to a platform-correct
value, and .env must not silently clobber it.
"""
import os
from pathlib import Path

import pytest

from kicad_mcp.utils.env import find_env_file, get_env_list, load_dotenv


def _write_env(path: Path, content: str) -> None:
    (path / ".env").write_text(content)


def test_sets_new_var(isolated_cwd, monkeypatch):
    monkeypatch.delenv("KICAD_MCP_TEST_NEW", raising=False)
    _write_env(isolated_cwd, "KICAD_MCP_TEST_NEW=hello\n")

    result = load_dotenv()

    assert result["KICAD_MCP_TEST_NEW"] == "hello"
    assert os.environ["KICAD_MCP_TEST_NEW"] == "hello"


def test_does_not_override_existing_var(isolated_cwd, monkeypatch):
    """Launcher values must win over .env."""
    monkeypatch.setenv("KICAD_MCP_TEST_OVERRIDE", "from-launcher")
    _write_env(isolated_cwd, "KICAD_MCP_TEST_OVERRIDE=from-dotenv\n")

    result = load_dotenv()

    assert os.environ["KICAD_MCP_TEST_OVERRIDE"] == "from-launcher"
    assert result["KICAD_MCP_TEST_OVERRIDE"] == "from-launcher"


def test_skips_comments_and_blank_lines(isolated_cwd, monkeypatch):
    for k in ("KICAD_MCP_TEST_A", "KICAD_MCP_TEST_B"):
        monkeypatch.delenv(k, raising=False)
    _write_env(
        isolated_cwd,
        "# header comment\n"
        "\n"
        "KICAD_MCP_TEST_A=1\n"
        "   \n"
        "# inline note\n"
        "KICAD_MCP_TEST_B=2\n",
    )

    result = load_dotenv()

    assert result == {"KICAD_MCP_TEST_A": "1", "KICAD_MCP_TEST_B": "2"}


def test_no_file_returns_empty(isolated_cwd):
    assert load_dotenv("nonexistent.env") == {}


def test_absolute_path_loads_regardless_of_cwd(tmp_path, monkeypatch):
    """main.py passes the absolute path of its sibling .env — it must load
    even when the server's cwd is elsewhere and has no .env itself."""
    monkeypatch.delenv("KICAD_MCP_TEST_ABS", raising=False)
    env_file = tmp_path / "app" / ".env"
    env_file.parent.mkdir()
    env_file.write_text("KICAD_MCP_TEST_ABS=from-abs\n")

    # cwd has no .env and no .env in parents within search depth
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = load_dotenv(str(env_file))

    assert result["KICAD_MCP_TEST_ABS"] == "from-abs"
    assert os.environ["KICAD_MCP_TEST_ABS"] == "from-abs"


def test_absolute_path_nonexistent_returns_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    missing = tmp_path / "does" / "not" / "exist" / ".env"
    assert load_dotenv(str(missing)) == {}


def test_strips_surrounding_quotes(isolated_cwd, monkeypatch):
    monkeypatch.delenv("KICAD_MCP_TEST_QUOTED", raising=False)
    _write_env(isolated_cwd, 'KICAD_MCP_TEST_QUOTED="value with spaces"\n')

    result = load_dotenv()

    assert result["KICAD_MCP_TEST_QUOTED"] == "value with spaces"


def test_expands_tilde(isolated_cwd, monkeypatch):
    monkeypatch.delenv("KICAD_MCP_TEST_HOME", raising=False)
    _write_env(isolated_cwd, "KICAD_MCP_TEST_HOME=~/mydir\n")

    result = load_dotenv()

    assert result["KICAD_MCP_TEST_HOME"] == os.path.expanduser("~/mydir")
    assert "~" not in result["KICAD_MCP_TEST_HOME"]


def test_find_env_file_walks_up(tmp_path, monkeypatch):
    outer = tmp_path / "outer"
    inner = outer / "inner" / "deeper"
    inner.mkdir(parents=True)
    (outer / ".env").write_text("")
    monkeypatch.chdir(inner)

    found = find_env_file(".env")

    assert found is not None
    assert Path(found).resolve() == (outer / ".env").resolve()


@pytest.mark.parametrize(
    "value,expected",
    [
        ("a,b,c", ["a", "b", "c"]),
        (" a , b ,  c ", ["a", "b", "c"]),
        ("", []),
        ("single", ["single"]),
        ("a,,b", ["a", "b"]),
    ],
)
def test_get_env_list(monkeypatch, value, expected):
    monkeypatch.setenv("KICAD_MCP_TEST_LIST", value)
    assert get_env_list("KICAD_MCP_TEST_LIST") == expected
