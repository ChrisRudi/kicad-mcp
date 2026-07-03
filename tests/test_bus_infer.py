# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for pure bus inference (utils/bus_infer) behind Bus-Radar. Headless —
net-name analysis only, no KiCad."""

from __future__ import annotations

from kicad_mcp.utils import bus_infer as bi


def _bus(buses, label):
    return next((b for b in buses if b["bus"] == label), None)


def test_bare_i2c():
    buses = bi.group_buses(["SDA", "SCL", "VCC", "GND"])
    b = _bus(buses, "I2C")
    assert b and b["kind"] == "I2C" and set(b["nets"]) == {"SDA", "SCL"}


def test_prefixed_spi_bus():
    buses = bi.group_buses(["SPI1_MOSI", "SPI1_MISO", "SPI1_SCK", "SPI1_CS"])
    b = _bus(buses, "SPI1:SPI")
    assert b and b["kind"] == "SPI"
    assert set(b["nets"]) == {"SPI1_MOSI", "SPI1_MISO", "SPI1_SCK", "SPI1_CS"}


def test_single_signal_is_not_a_bus():
    # only SDA present → not enough for an I²C bus
    assert bi.group_buses(["SDA", "GND", "VCC"]) == []


def test_numbered_data_bus():
    nets = [f"LCD_D{i}" for i in range(8)]
    buses = bi.group_buses(nets)
    b = _bus(buses, "LCD_D")
    assert b and b["kind"] == "numbered" and len(b["nets"]) == 8


def test_numbered_needs_at_least_three():
    assert bi.group_buses(["A0", "A1"]) == []  # 2 is not a bus


def test_diff_pair_underscore():
    buses = bi.group_buses(["USB_DP", "USB_DM"])
    # USB is also a protocol (DP/DM) → recognised as the USB bus
    b = _bus(buses, "USB") or _bus(buses, "USB_D")
    assert b is not None
    assert set(b["nets"]) == {"USB_DP", "USB_DM"}


def test_diff_pair_plus_minus():
    buses = bi.group_buses(["LVDS0+", "LVDS0-"])
    b = _bus(buses, "LVDS0")
    assert b and b["kind"] == "diffpair"
    assert set(b["nets"]) == {"LVDS0+", "LVDS0-"}


def test_uart_bus():
    buses = bi.group_buses(["UART2_TX", "UART2_RX"])
    b = _bus(buses, "UART2:UART")
    assert b and b["kind"] == "UART"


def test_leading_slash_and_case_folded():
    buses = bi.group_buses(["/sda", "/scl"])
    b = _bus(buses, "I2C")
    assert b and set(b["nets"]) == {"/sda", "/scl"}


def test_net_belongs_to_at_most_one_bus():
    # SPI1_SCK could look numbered (ends in no digit) — ensure protocol wins and
    # no net is double-counted
    buses = bi.group_buses(["SPI1_MOSI", "SPI1_MISO", "SPI1_SCK",
                            "D0", "D1", "D2"])
    seen = [n for b in buses for n in b["nets"]]
    assert len(seen) == len(set(seen))
