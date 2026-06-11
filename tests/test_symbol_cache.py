# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for symbol_cache user-sym-lib-table fallback."""
from __future__ import annotations

import pytest

from kicad_mcp.generators import symbol_cache


_MINIMAL_LIB_TEMPLATE = """\
(kicad_symbol_lib (version 20231120) (generator kicad_symbol_editor)
  (symbol "{sym}"
    (pin_names (offset 1.016))
    (in_bom yes)
    (on_board yes)
    (property "Reference" "U" (at 0 5.08 0))
    (property "Value" "{sym}" (at 0 -5.08 0))
    (symbol "{sym}_0_1"
      (rectangle (start -5.08 5.08) (end 5.08 -5.08)
        (stroke (width 0.254) (type default))
        (fill (type background))
      )
    )
    (symbol "{sym}_1_1"
      (pin power_in line (at -7.62 2.54 0) (length 2.54)
        (name "VCC" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
    )
  )
)
"""


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    """Reset internal caches between tests so each test sees a clean slate."""
    symbol_cache._reset_user_sym_libs_cache()
    symbol_cache._KICAD_SYM_DIRS.clear()
    symbol_cache._read_lib_file.cache_clear()
    yield
    symbol_cache._reset_user_sym_libs_cache()
    symbol_cache._KICAD_SYM_DIRS.clear()
    symbol_cache._read_lib_file.cache_clear()


def _write_custom_lib(tmp_path, lib_filename: str, sym_name: str) -> str:
    """Create a minimal .kicad_sym file in tmp_path. Returns absolute path."""
    lib_path = tmp_path / lib_filename
    lib_path.write_text(_MINIMAL_LIB_TEMPLATE.format(sym=sym_name), encoding="utf-8")
    return str(lib_path)


def _write_sym_lib_table(tmp_path, entries: list[tuple[str, str]]) -> str:
    """Create a sym-lib-table at tmp_path/config/sym-lib-table.

    Each entry is (lib_name, uri). Returns the config dir path (parent of file).
    """
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    table = cfg_dir / "sym-lib-table"
    body = "(sym_lib_table\n  (version 7)\n"
    for name, uri in entries:
        body += f'  (lib (name "{name}") (type "KiCad") (uri "{uri}") (options "") (descr ""))\n'
    body += ")\n"
    table.write_text(body, encoding="utf-8")
    return str(cfg_dir)


def test_custom_lib_resolved_via_sym_lib_table(tmp_path, monkeypatch):
    """Happy path: a lib registered in the user's sym-lib-table is resolvable."""
    lib_path = _write_custom_lib(tmp_path, "MyCustom.kicad_sym", "MCU_X")
    cfg_dir = _write_sym_lib_table(tmp_path, [("MyCustom", lib_path)])
    monkeypatch.setenv("KICAD_CONFIG_DIR", cfg_dir)

    result = symbol_cache.get_real_symbol("MyCustom:MCU_X")

    assert result is not None
    assert '(symbol "MyCustom:MCU_X"' in result
    # Pin from the template body must come through.
    assert '"VCC"' in result


def test_missing_sym_lib_table_is_silent(tmp_path, monkeypatch):
    """Edge case: no user sym-lib-table → fallback silently returns None.

    The stock-only path must keep working when KICAD_CONFIG_DIR points at an
    empty directory (mimicking a fresh KiCad install with no custom libs).
    """
    empty_cfg = tmp_path / "empty_config"
    empty_cfg.mkdir()
    monkeypatch.setenv("KICAD_CONFIG_DIR", str(empty_cfg))

    libs = symbol_cache._load_user_sym_libs()
    assert not libs

    # Idempotency: second call hits the cache, still empty, no exception.
    libs_again = symbol_cache._load_user_sym_libs()
    assert not libs_again
    assert libs_again is libs  # same cached dict instance


def test_broken_uri_is_skipped(tmp_path, monkeypatch, caplog):
    """Error path: a sym-lib-table entry pointing at a non-existent file is
    skipped, valid entries in the same table still load."""
    good_path = _write_custom_lib(tmp_path, "GoodLib.kicad_sym", "Part_A")
    missing_path = str(tmp_path / "this_file_does_not_exist.kicad_sym")

    cfg_dir = _write_sym_lib_table(
        tmp_path,
        [
            ("BadLib", missing_path),
            ("GoodLib", good_path),
        ],
    )
    monkeypatch.setenv("KICAD_CONFIG_DIR", cfg_dir)

    with caplog.at_level("DEBUG", logger="kicad_mcp.generators.symbol_cache"):
        libs = symbol_cache._load_user_sym_libs()

    assert "BadLib" not in libs
    assert libs.get("GoodLib") == good_path
    assert any("BadLib" in rec.message for rec in caplog.records)


def test_unresolvable_uri_variable_is_skipped(tmp_path, monkeypatch):
    """``${KIPRJMOD}`` is project-local and must be skipped without crashing."""
    cfg_dir = _write_sym_lib_table(
        tmp_path,
        [("ProjLib", "${KIPRJMOD}/local.kicad_sym")],
    )
    monkeypatch.setenv("KICAD_CONFIG_DIR", cfg_dir)

    libs = symbol_cache._load_user_sym_libs()
    assert "ProjLib" not in libs


def test_stock_and_user_libs_resolved_independently(tmp_path, monkeypatch):
    """Stock and user libraries coexist under distinct lib_name namespaces.

    A request for ``Device:R`` (stock-style) must hit the stock dir; a
    request for ``MyCustom:MCU_X`` must fall back to the user sym-lib-table.
    """
    stock_dir = tmp_path / "stock"
    stock_dir.mkdir()
    stock_lib = stock_dir / "Device.kicad_sym"
    stock_lib.write_text(_MINIMAL_LIB_TEMPLATE.format(sym="R"), encoding="utf-8")

    user_lib = _write_custom_lib(tmp_path, "MyCustom.kicad_sym", "MCU_X")
    cfg_dir = _write_sym_lib_table(tmp_path, [("MyCustom", user_lib)])

    monkeypatch.setenv("KICAD_SYMBOL_DIR", str(stock_dir))
    monkeypatch.setenv("KICAD_CONFIG_DIR", cfg_dir)

    assert symbol_cache.get_real_symbol("Device:R") is not None
    assert symbol_cache.get_real_symbol("MyCustom:MCU_X") is not None
    # A lib_name absent from both sources is unresolvable.
    assert symbol_cache.get_real_symbol("Ghost:X") is None


def test_unknown_lib_returns_none(tmp_path, monkeypatch):
    """A lib_id not present in either source returns None cleanly."""
    monkeypatch.setenv("KICAD_CONFIG_DIR", str(tmp_path))
    assert symbol_cache.get_real_symbol("NoSuchLib:NoSuchSym") is None
    # Malformed lib_id (no colon) — None, no exception.
    assert symbol_cache.get_real_symbol("malformed_no_colon") is None


# ---------------------------------------------------------------------------
# String-literal-aware paren extraction (audit regression)
# ---------------------------------------------------------------------------


def test_extract_symbol_with_paren_in_string_not_truncated():
    # A stray ')' inside a property string must not end the symbol early.
    content = (
        '(kicad_symbol_lib (version 20231120)\n'
        '  (symbol "A" (property "Description" "smiley :)")\n'
        '    (symbol "A_0_1" (rectangle (start -1 1) (end 1 -1))) )\n'
        '  (symbol "B" (property "Value" "B")) )\n'
    )
    a = symbol_cache._extract_top_level_symbol(content, "A")
    assert a is not None
    assert "(rectangle" in a            # body survived the stray ')'
    assert a.rstrip().endswith(")")
    # the following symbol is still independently extractable
    b = symbol_cache._extract_top_level_symbol(content, "B")
    assert b is not None and '"B"' in b


def test_sym_lib_blocks_with_paren_in_uri_or_descr():
    tbl = (
        '(sym_lib_table '
        '(lib (name "X")(uri "${KIPRJMOD}/a.kicad_sym")(descr "has ) paren")) '
        '(lib (name "Y")(uri "y")) )'
    )
    blocks = list(symbol_cache._iter_sym_lib_blocks(tbl))
    assert len(blocks) == 2
    joined = "".join(blocks)
    assert '"X"' in joined and '"Y"' in joined


def test_extends_overlays_derived_properties(tmp_path):
    # extends inlining must keep the BASE geometry but the DERIVED properties.
    lib = tmp_path / "ext.kicad_sym"
    lib.write_text(
        '(kicad_symbol_lib (version 20231120) (generator "t")\n'
        '  (symbol "Base" (in_bom yes)\n'
        '    (property "Reference" "U" (at 0 0 0))\n'
        '    (property "Value" "Base" (at 0 0 0))\n'
        '    (property "Description" "base desc" (at 0 0 0))\n'
        '    (property "ki_keywords" "base kw" (at 0 0 0))\n'
        '    (symbol "Base_0_1" (rectangle (start -1 1) (end 1 -1)))\n'
        '    (symbol "Base_1_1" (pin passive line (at 0 -2 90) (length 1)\n'
        '      (name "P1" (effects (font (size 1 1)))) (number "1" (effects (font (size 1 1)))))) )\n'
        '  (symbol "Derived" (extends "Base")\n'
        '    (property "Value" "Derived" (at 0 0 0))\n'
        '    (property "Description" "derived desc" (at 0 0 0))\n'
        '    (property "ki_keywords" "derived kw" (at 0 0 0))) )\n',
        encoding="utf-8",
    )
    out = symbol_cache._resolve_symbol_from_lib(
        str(lib), "Derived", "Lib:Derived")
    assert out is not None
    assert "derived desc" in out and "base desc" not in out       # derived props win
    assert "derived kw" in out and "base kw" not in out
    assert "(rectangle" in out and "(pin passive" in out          # base geometry kept
    assert '(symbol "Lib:Derived"' in out
