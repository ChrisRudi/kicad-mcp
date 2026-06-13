# SPDX-License-Identifier: GPL-3.0-or-later
"""
ESPHome YAML to KiCad parts/nets converter.

Parses ESPHome configuration YAML and extracts:
- ESP chip type and pinout
- I2C/SPI/UART bus definitions
- Sensor/actuator platforms → KiCad components
- GPIO assignments → net connections
- Standard peripherals (decoupling, pull-ups, USB)
"""

import re
from typing import Any

import yaml

# ── ESP chip database ────────────────────────────────────────────────────────

_ESP_CHIPS = {
    "esp32": {
        "name": "ESP32-WROOM-32E",
        "footprint": "RF_Module:ESP32-WROOM-32E",
        "pins": [
            {"num": 1, "name": "GND", "type": "power_in"},
            {"num": 2, "name": "3V3", "type": "power_in"},
            {"num": 3, "name": "EN", "type": "input"},
            {"num": 4, "name": "IO36", "type": "input"},
            {"num": 5, "name": "IO39", "type": "input"},
            {"num": 6, "name": "IO34", "type": "input"},
            {"num": 7, "name": "IO35", "type": "input"},
            {"num": 8, "name": "IO32", "type": "bidirectional"},
            {"num": 9, "name": "IO33", "type": "bidirectional"},
            {"num": 10, "name": "IO25", "type": "bidirectional"},
            {"num": 11, "name": "IO26", "type": "bidirectional"},
            {"num": 12, "name": "IO27", "type": "bidirectional"},
            {"num": 13, "name": "IO14", "type": "bidirectional"},
            {"num": 14, "name": "IO12", "type": "bidirectional"},
            {"num": 15, "name": "IO13", "type": "bidirectional"},
            {"num": 16, "name": "IO15", "type": "bidirectional"},
            {"num": 17, "name": "IO2", "type": "bidirectional"},
            {"num": 18, "name": "IO4", "type": "bidirectional"},
            {"num": 19, "name": "IO16", "type": "bidirectional"},
            {"num": 20, "name": "IO17", "type": "bidirectional"},
            {"num": 21, "name": "IO5", "type": "bidirectional"},
            {"num": 22, "name": "IO18", "type": "bidirectional"},
            {"num": 23, "name": "IO19", "type": "bidirectional"},
            {"num": 24, "name": "IO21", "type": "bidirectional"},
            {"num": 25, "name": "IO22", "type": "bidirectional"},
            {"num": 26, "name": "IO23", "type": "bidirectional"},
        ],
    },
    "esp32s3": {
        "name": "ESP32-S3-WROOM-1",
        "footprint": "RF_Module:ESP32-S3-WROOM-1",
        "pins": [
            {"num": 1, "name": "GND", "type": "power_in"},
            {"num": 2, "name": "3V3", "type": "power_in"},
            {"num": 3, "name": "EN", "type": "input"},
            *[{"num": i + 4, "name": f"IO{i}", "type": "bidirectional"} for i in range(49)],
        ],
    },
    "esp32c3": {
        "name": "ESP32-C3-MINI-1",
        "footprint": "RF_Module:ESP32-C3-MINI-1",
        "pins": [
            {"num": 1, "name": "GND", "type": "power_in"},
            {"num": 2, "name": "3V3", "type": "power_in"},
            {"num": 3, "name": "EN", "type": "input"},
            *[{"num": i + 4, "name": f"IO{i}", "type": "bidirectional"} for i in range(22)],
        ],
    },
    "esp8266": {
        "name": "ESP-12F",
        "footprint": "RF_Module:ESP-12F",
        "pins": [
            {"num": 1, "name": "GND", "type": "power_in"},
            {"num": 2, "name": "3V3", "type": "power_in"},
            {"num": 3, "name": "EN", "type": "input"},
            {"num": 4, "name": "GPIO0", "type": "bidirectional"},
            {"num": 5, "name": "GPIO2", "type": "bidirectional"},
            {"num": 6, "name": "GPIO4", "type": "bidirectional"},
            {"num": 7, "name": "GPIO5", "type": "bidirectional"},
            {"num": 8, "name": "GPIO12", "type": "bidirectional"},
            {"num": 9, "name": "GPIO13", "type": "bidirectional"},
            {"num": 10, "name": "GPIO14", "type": "bidirectional"},
            {"num": 11, "name": "GPIO15", "type": "bidirectional"},
            {"num": 12, "name": "GPIO16", "type": "bidirectional"},
            {"num": 13, "name": "ADC0", "type": "input"},
            {"num": 14, "name": "TX", "type": "output"},
            {"num": 15, "name": "RX", "type": "input"},
        ],
    },
}

# ── Peripheral component database ────────────────────────────────────────────
# Maps ESPHome platform names to KiCad components

_SENSOR_DB: dict[str, dict[str, Any]] = {
    # Temperature / Humidity / Pressure
    "bme280": {
        "ref_prefix": "U", "name": "BME280", "value": "BME280",
        "footprint": "Package_LGA:Bosch_LGA-8_2.5x2.5mm_P0.65mm_ClockwisePinNumbering",
        "interface": "i2c", "address": "0x76",
        "pins": [
            {"num": 1, "name": "VDD", "type": "power_in"},
            {"num": 2, "name": "GND", "type": "power_in"},
            {"num": 3, "name": "SDI", "type": "bidirectional"},
            {"num": 4, "name": "SCK", "type": "input"},
        ],
    },
    "bme680": {
        "ref_prefix": "U", "name": "BME680", "value": "BME680",
        "footprint": "Package_LGA:Bosch_LGA-8_3x3mm_P0.8mm_ClockwisePinNumbering",
        "interface": "i2c", "address": "0x76",
        "pins": [
            {"num": 1, "name": "VDD", "type": "power_in"},
            {"num": 2, "name": "GND", "type": "power_in"},
            {"num": 3, "name": "SDI", "type": "bidirectional"},
            {"num": 4, "name": "SCK", "type": "input"},
        ],
    },
    "dht22": {
        "ref_prefix": "U", "name": "DHT22", "value": "DHT22",
        "footprint": "Sensor:Aosong_DHT11_5.5x12.0_P2.54mm",
        "interface": "gpio",
        "pins": [
            {"num": 1, "name": "VDD", "type": "power_in"},
            {"num": 2, "name": "DATA", "type": "bidirectional"},
            {"num": 3, "name": "NC", "type": "no_connect"},
            {"num": 4, "name": "GND", "type": "power_in"},
        ],
    },
    "dallas": {
        "ref_prefix": "U", "name": "DS18B20", "value": "DS18B20",
        "footprint": "Package_TO_SOT_THT:TO-92_Inline",
        "interface": "onewire",
        "pins": [
            {"num": 1, "name": "GND", "type": "power_in"},
            {"num": 2, "name": "DQ", "type": "bidirectional"},
            {"num": 3, "name": "VDD", "type": "power_in"},
        ],
    },
    "sht3xd": {
        "ref_prefix": "U", "name": "SHT31-DIS", "value": "SHT31",
        "footprint": "Package_DFN_QFN:DFN-8-1EP_2.5x2.5mm_P0.5mm_EP1.1x1.7mm",
        "interface": "i2c", "address": "0x44",
        "pins": [
            {"num": 1, "name": "SDA", "type": "bidirectional"},
            {"num": 2, "name": "ADDR", "type": "input"},
            {"num": 3, "name": "ALERT", "type": "output"},
            {"num": 4, "name": "SCL", "type": "input"},
            {"num": 5, "name": "VDD", "type": "power_in"},
            {"num": 6, "name": "nRESET", "type": "input"},
            {"num": 7, "name": "R", "type": "passive"},
            {"num": 8, "name": "VSS", "type": "power_in"},
        ],
    },
    # Light
    "bh1750": {
        "ref_prefix": "U", "name": "BH1750FVI", "value": "BH1750",
        "footprint": "Package_SO:WSOF-6_1.4x1.1mm_P0.4mm",
        "interface": "i2c", "address": "0x23",
        "pins": [
            {"num": 1, "name": "VCC", "type": "power_in"},
            {"num": 2, "name": "ADDR", "type": "input"},
            {"num": 3, "name": "GND", "type": "power_in"},
            {"num": 4, "name": "SDA", "type": "bidirectional"},
            {"num": 5, "name": "DVI", "type": "input"},
            {"num": 6, "name": "SCL", "type": "input"},
        ],
    },
    # Distance
    "vl53l0x": {
        "ref_prefix": "U", "name": "VL53L0X", "value": "VL53L0X",
        "footprint": "Sensor_Optical:ST_VL53L0X",
        "interface": "i2c", "address": "0x29",
        "pins": [
            {"num": 1, "name": "VDD", "type": "power_in"},
            {"num": 2, "name": "GND", "type": "power_in"},
            {"num": 3, "name": "SDA", "type": "bidirectional"},
            {"num": 4, "name": "SCL", "type": "input"},
            {"num": 5, "name": "XSHUT", "type": "input"},
            {"num": 6, "name": "GPIO1", "type": "output"},
        ],
    },
    # IMU
    "mpu6050": {
        "ref_prefix": "U", "name": "MPU-6050", "value": "MPU6050",
        "footprint": "Sensor_Motion:InvenSense_QFN-24_4x4mm_P0.5mm",
        "interface": "i2c", "address": "0x68",
        "pins": [
            {"num": 1, "name": "VCC", "type": "power_in"},
            {"num": 2, "name": "GND", "type": "power_in"},
            {"num": 3, "name": "SDA", "type": "bidirectional"},
            {"num": 4, "name": "SCL", "type": "input"},
            {"num": 5, "name": "INT", "type": "output"},
        ],
    },
    # ADC
    "ads1115": {
        "ref_prefix": "U", "name": "ADS1115", "value": "ADS1115",
        "footprint": "Package_SO:MSOP-10_3x3mm_P0.5mm",
        "interface": "i2c", "address": "0x48",
        "pins": [
            {"num": 1, "name": "ADDR", "type": "input"},
            {"num": 2, "name": "ALERT", "type": "output"},
            {"num": 3, "name": "GND", "type": "power_in"},
            {"num": 4, "name": "AIN0", "type": "input"},
            {"num": 5, "name": "AIN1", "type": "input"},
            {"num": 6, "name": "AIN2", "type": "input"},
            {"num": 7, "name": "AIN3", "type": "input"},
            {"num": 8, "name": "VDD", "type": "power_in"},
            {"num": 9, "name": "SDA", "type": "bidirectional"},
            {"num": 10, "name": "SCL", "type": "input"},
        ],
    },
}

_DISPLAY_DB: dict[str, dict[str, Any]] = {
    "ssd1306_i2c": {
        "ref_prefix": "U", "name": "SSD1306", "value": "SSD1306_128x64",
        "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
        "interface": "i2c", "address": "0x3C",
        "pins": [
            {"num": 1, "name": "GND", "type": "power_in"},
            {"num": 2, "name": "VCC", "type": "power_in"},
            {"num": 3, "name": "SCL", "type": "input"},
            {"num": 4, "name": "SDA", "type": "bidirectional"},
        ],
    },
}

_OUTPUT_DB: dict[str, dict[str, Any]] = {
    "neopixelbus": {
        "ref_prefix": "J", "name": "WS2812B", "value": "NeoPixel",
        "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
        "interface": "gpio",
        "pins": [
            {"num": 1, "name": "VDD", "type": "power_in"},
            {"num": 2, "name": "DIN", "type": "input"},
            {"num": 3, "name": "GND", "type": "power_in"},
        ],
    },
}

# Merge all DBs
COMPONENT_DB = {**_SENSOR_DB, **_DISPLAY_DB, **_OUTPUT_DB}


# ── Standard support components ──────────────────────────────────────────────

def _decoupling_cap(ref: str, value: str = "100nF") -> dict:
    return {
        "ref": ref, "name": "C", "value": value,
        "footprint": "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm",
        "pins": [
            {"num": 1, "name": "1", "type": "passive"},
            {"num": 2, "name": "2", "type": "passive"},
        ],
    }


def _pull_up_resistor(ref: str, value: str = "4.7k") -> dict:
    return {
        "ref": ref, "name": "R", "value": value,
        "footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
        "pins": [
            {"num": 1, "name": "1", "type": "passive"},
            {"num": 2, "name": "2", "type": "passive"},
        ],
    }


# ── GPIO name normalization ──────────────────────────────────────────────────

def _normalize_gpio(pin_spec: Any, chip: str = "esp32") -> str | None:
    """Convert ESPHome pin spec to IO pin name."""
    if isinstance(pin_spec, int):
        prefix = "GPIO" if chip == "esp8266" else "IO"
        return f"{prefix}{pin_spec}"
    if isinstance(pin_spec, str):
        m = re.match(r"(?:GPIO|IO)?(\d+)", pin_spec, re.IGNORECASE)
        if m:
            prefix = "GPIO" if chip == "esp8266" else "IO"
            return f"{prefix}{m.group(1)}"
    if isinstance(pin_spec, dict):
        return _normalize_gpio(pin_spec.get("number"), chip)
    return None


# ── Main converter ───────────────────────────────────────────────────────────

def esphome_to_parts_nets(yaml_text: str) -> dict[str, Any]:
    """Convert ESPHome YAML to parts and nets for KiCad generation.

    Args:
        yaml_text: ESPHome YAML configuration content

    Returns:
        Dict with 'parts', 'nets', 'board', 'chip', 'warnings'
    """
    config = yaml.safe_load(yaml_text)
    if not isinstance(config, dict):
        return {"parts": [], "nets": [], "board": {}, "chip": None, "warnings": ["Invalid YAML"]}

    warnings = []

    # 1. Detect ESP chip
    chip_key = "esp32"
    for key in ("esp32", "esp32s3", "esp32c3", "esp8266"):
        if key in config:
            chip_key = key
            break

    # Need at least an esphome or chip section to generate parts
    has_chip = any(k in config for k in ("esp32", "esp32s3", "esp32c3", "esp8266", "esphome"))
    if not has_chip:
        return {"parts": [], "nets": [], "board": {}, "chip": chip_key, "warnings": ["No ESPHome or chip configuration found"]}

    chip_def = _ESP_CHIPS.get(chip_key, _ESP_CHIPS["esp32"])
    esp_part = {
        "ref": "U1",
        "name": chip_def["name"],
        "footprint": chip_def["footprint"],
        "value": chip_def["name"],
        "pins": list(chip_def["pins"]),
    }

    parts = [esp_part]
    nets = [
        {"name": "3V3", "type": "power", "connections": ["U1:3V3"]},
        {"name": "GND", "type": "power", "connections": ["U1:GND"]},
    ]

    ref_counters = {"U": 1, "C": 0, "R": 0, "J": 0, "D": 0}  # U1 = ESP
    used_gpios = {}  # gpio_name -> net_name

    # 2. Parse I2C bus
    i2c_config = config.get("i2c")
    if i2c_config:
        if isinstance(i2c_config, list):
            i2c_config = i2c_config[0]
        sda_gpio = _normalize_gpio(i2c_config.get("sda", 21), chip_key)
        scl_gpio = _normalize_gpio(i2c_config.get("scl", 22), chip_key)

        nets.append({"name": "I2C_SDA", "type": "signal", "connections": [f"U1:{sda_gpio}"]})
        nets.append({"name": "I2C_SCL", "type": "signal", "connections": [f"U1:{scl_gpio}"]})
        used_gpios[sda_gpio] = "I2C_SDA"
        used_gpios[scl_gpio] = "I2C_SCL"

        # I2C pull-up resistors
        ref_counters["R"] += 1
        r_sda = _pull_up_resistor(f"R{ref_counters['R']}", "4.7k")
        ref_counters["R"] += 1
        r_scl = _pull_up_resistor(f"R{ref_counters['R']}", "4.7k")
        parts.extend([r_sda, r_scl])

        _net_add(nets, "I2C_SDA", f"{r_sda['ref']}:1")
        _net_add(nets, "3V3", f"{r_sda['ref']}:2")
        _net_add(nets, "I2C_SCL", f"{r_scl['ref']}:1")
        _net_add(nets, "3V3", f"{r_scl['ref']}:2")

    # 3. Parse SPI bus
    spi_config = config.get("spi")
    if spi_config:
        if isinstance(spi_config, list):
            spi_config = spi_config[0]
        for spi_name, spi_key in [("SPI_CLK", "clk_pin"), ("SPI_MOSI", "mosi_pin"), ("SPI_MISO", "miso_pin")]:
            gpio = _normalize_gpio(spi_config.get(spi_key), chip_key)
            if gpio and gpio not in used_gpios:
                nets.append({"name": spi_name, "type": "signal", "connections": [f"U1:{gpio}"]})
                used_gpios[gpio] = spi_name
            elif gpio and gpio in used_gpios:
                warnings.append(f"{spi_name} pin {gpio} conflicts with {used_gpios[gpio]}")

    # 4. Parse UART
    uart_config = config.get("uart")
    if uart_config:
        if isinstance(uart_config, list):
            uart_config = uart_config[0]
        tx = _normalize_gpio(uart_config.get("tx_pin"), chip_key)
        rx = _normalize_gpio(uart_config.get("rx_pin"), chip_key)
        if tx and tx not in used_gpios:
            nets.append({"name": "UART_TX", "type": "signal", "connections": [f"U1:{tx}"]})
            used_gpios[tx] = "UART_TX"
        elif tx and tx in used_gpios:
            warnings.append(f"UART TX pin {tx} conflicts with {used_gpios[tx]}")
        if rx and rx not in used_gpios:
            nets.append({"name": "UART_RX", "type": "signal", "connections": [f"U1:{rx}"]})
            used_gpios[rx] = "UART_RX"
        elif rx and rx in used_gpios:
            warnings.append(f"UART RX pin {rx} conflicts with {used_gpios[rx]}")

    # 5. Parse sensors, displays, outputs
    for section_key in ("sensor", "binary_sensor", "text_sensor", "display", "light", "output", "switch", "fan"):
        items = config.get(section_key, [])
        if not isinstance(items, list):
            items = [items]

        for item in items:
            if not isinstance(item, dict):
                continue
            platform = item.get("platform", "")

            # GPIO-based components
            if platform == "gpio":
                pin = item.get("pin")
                gpio = _normalize_gpio(pin, chip_key)
                if gpio:
                    comp_name = item.get("name", section_key)
                    net_name = f"{comp_name}".upper().replace(" ", "_")
                    if gpio not in used_gpios:
                        nets.append({"name": net_name, "type": "signal", "connections": [f"U1:{gpio}"]})
                        used_gpios[gpio] = net_name
                continue

            # Known platform → component from DB
            if platform in COMPONENT_DB:
                comp_def = COMPONENT_DB[platform]
                prefix = comp_def["ref_prefix"]
                ref_counters[prefix] = ref_counters.get(prefix, 0) + 1
                ref = f"{prefix}{ref_counters[prefix]}"

                comp = {
                    "ref": ref,
                    "name": comp_def["name"],
                    "value": comp_def.get("value", comp_def["name"]),
                    "footprint": comp_def["footprint"],
                    "pins": list(comp_def["pins"]),
                }
                parts.append(comp)

                # Decoupling cap
                ref_counters["C"] += 1
                cap = _decoupling_cap(f"C{ref_counters['C']}")
                parts.append(cap)

                # Connect power
                _connect_power(comp, nets, cap)

                # Connect to bus
                iface = comp_def.get("interface", "")
                if iface == "i2c":
                    _connect_i2c(comp, nets)
                elif iface == "gpio":
                    data_pin = item.get("pin") or item.get("data_pin")
                    gpio = _normalize_gpio(data_pin, chip_key)
                    if gpio and gpio not in used_gpios:
                        data_pin_name = _find_data_pin(comp)
                        if data_pin_name:
                            net_name = f"{comp['name']}_DATA"
                            nets.append({"name": net_name, "type": "signal",
                                         "connections": [f"U1:{gpio}", f"{ref}:{data_pin_name}"]})
                            used_gpios[gpio] = net_name
                    elif gpio and gpio in used_gpios:
                        warnings.append(f"{comp['name']} pin {gpio} conflicts with {used_gpios[gpio]}")
                elif iface == "onewire":
                    data_pin = item.get("pin")
                    gpio = _normalize_gpio(data_pin, chip_key)
                    if gpio:
                        data_pin_name = _find_data_pin(comp)
                        if data_pin_name:
                            net_name = "ONEWIRE"
                            existing = _find_net(nets, net_name)
                            if existing:
                                existing["connections"].append(f"{ref}:{data_pin_name}")
                                existing["connections"].append(f"U1:{gpio}")
                            else:
                                nets.append({"name": net_name, "type": "signal",
                                             "connections": [f"U1:{gpio}", f"{ref}:{data_pin_name}"]})
                            used_gpios[gpio] = net_name
            elif platform and platform != "gpio":
                warnings.append(f"Unknown platform '{platform}' in {section_key} — skipped")

    # 6. Decoupling cap for ESP
    ref_counters["C"] += 1
    esp_cap = _decoupling_cap(f"C{ref_counters['C']}")
    parts.append(esp_cap)
    _net_add(nets, "3V3", f"{esp_cap['ref']}:1")
    _net_add(nets, "GND", f"{esp_cap['ref']}:2")

    # 7. Board defaults
    board = {"shape": "rectangle", "width": 50, "depth": 30, "layers": 2, "thickness": 1.6}

    return {
        "parts": parts,
        "nets": nets,
        "board": board,
        "chip": chip_key,
        "warnings": warnings,
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _net_add(nets: list[dict], net_name: str, connection: str) -> None:
    """Add a connection to an existing net, or warn if not found."""
    for net in nets:
        if net["name"] == net_name:
            if connection not in net["connections"]:
                net["connections"].append(connection)
            return


def _find_net(nets: list[dict], name: str) -> dict | None:
    for net in nets:
        if net["name"] == name:
            return net
    return None


def _connect_power(comp: dict, nets: list[dict], cap: dict) -> None:
    """Connect component power pins to 3V3/GND and add decoupling cap."""
    ref = comp["ref"]
    for pin in comp["pins"]:
        pname = pin["name"].upper()
        if pname in ("VDD", "VCC", "3V3"):
            _net_add(nets, "3V3", f"{ref}:{pin['name']}")
        elif pname in ("GND", "VSS"):
            _net_add(nets, "GND", f"{ref}:{pin['name']}")

    _net_add(nets, "3V3", f"{cap['ref']}:1")
    _net_add(nets, "GND", f"{cap['ref']}:2")


def _connect_i2c(comp: dict, nets: list[dict]) -> None:
    """Connect I2C pins of a component to the I2C bus nets."""
    ref = comp["ref"]
    for pin in comp["pins"]:
        pname = pin["name"].upper()
        if pname in ("SDA", "SDI"):
            _net_add(nets, "I2C_SDA", f"{ref}:{pin['name']}")
        elif pname in ("SCL", "SCK"):
            _net_add(nets, "I2C_SCL", f"{ref}:{pin['name']}")


def _find_data_pin(comp: dict) -> str | None:
    """Find the data pin of a component (DATA, DQ, DIN, etc.)."""
    for pin in comp["pins"]:
        if pin["name"].upper() in ("DATA", "DQ", "DIN", "DOUT"):
            return pin["name"]
        if pin["type"] == "bidirectional" and pin["name"].upper() not in ("SDA", "SCL", "SDI", "SCK"):
            return pin["name"]
    return None
