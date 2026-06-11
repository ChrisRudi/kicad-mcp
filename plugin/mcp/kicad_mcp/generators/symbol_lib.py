# SPDX-License-Identifier: GPL-3.0-or-later
"""
KiCad symbol library mapping.

Maps component names/ref-prefixes to standard KiCad library symbols.
When the schematic is opened in KiCad, "Update from Library" replaces
the embedded placeholders with real symbols.
"""

# Standard KiCad library symbol IDs
# Format: "Library:Symbol"

_BY_NAME: dict[str, str] = {
    # Passives (Device library)
    "R": "Device:R",
    "C": "Device:C",
    "L": "Device:L",
    "D": "Device:D",
    "LED": "Device:LED",
    "Fuse": "Device:Fuse",
    "Ferrite_Bead": "Device:FerriteBead",

    # Transistors (KiCad 10: no _BCE/_GDS suffix)
    "Q_NPN": "Device:Q_NPN",
    "Q_PNP": "Device:Q_PNP",
    "Q_NMOS": "Device:Q_NMOS",
    "Q_PMOS": "Device:Q_PMOS",
    "Q_NPN_Darlington": "Device:Q_NPN_Darlington",
    "Q_PNP_Darlington": "Device:Q_PNP_Darlington",
    "Q_NJFET": "Device:Q_NJFET_DGS",
    "Q_PJFET": "Device:Q_PJFET_DGS",

    # Regulators
    "AMS1117": "Regulator_Linear:AMS1117",
    "AP2112": "Regulator_Linear:AP2112K-3.3",
    "MCP1700": "Regulator_Linear:MCP1700x-330xxTT",
    "LM1117": "Regulator_Linear:LM1117DT-3.3",

    # ESP modules
    "ESP32-WROOM-32E": "RF_Module:ESP32-WROOM-32E",
    "ESP32-S3-WROOM-1": "RF_Module:ESP32-S3-WROOM-1",
    "ESP32-C3-MINI-1": "RF_Module:ESP32-C3-WROOM-02",
    "ESP-12F": "RF_Module:ESP-12F",
    "ESP32-C6": "RF_Module:ESP32-C6-MINI-1",

    # Sensors (verified against KiCad 10 libraries)
    "BME280": "Sensor:BME280",
    "BME680": "Sensor:BME680",
    "SHT31-DIS": "Sensor_Humidity:SHT30-DIS",
    # BH1750FVI has no symbol in KiCad 10 — intentionally omitted, will use
    # placeholder.  Do NOT map to an unrelated sensor like BME280.
    "VL53L0X": "Sensor_Distance:VL53L0CXV0DH1",
    "MPU-6050": "Sensor_Motion:MPU-6050",
    "DS18B20": "Sensor_Temperature:DS18B20",
    "DHT22": "Sensor:DHT11",

    # ESPHome-common sensors (4.3 – avoid Placeholder for frequent ESPHome components)
    "AHT10": "Sensor:AHT20",
    "AHT20": "Sensor:AHT20",
    "BMP280": "Sensor:BMP280",
    "SHT31": "Sensor_Humidity:SHT30-DIS",
    "SHT40": "Sensor_Humidity:SHT40-AD1B",
    "INA219": "Sensor_Current:INA219xD",
    "INA226": "Sensor_Current:INA226",

    # ADC
    "ADS1115": "Analog_ADC:ADS1115IDGS",
    "MCP3208": "Analog_ADC:MCP3208",

    # Displays — SSD1306 not in KiCad 10 standard libs
    # "SSD1306": not available

    # Motor drivers
    "TMC2209": "Driver_Motor:TMC2209-LA",
    # DRV8313, DRV10983: not in KiCad 10 standard libs — will use placeholder

    # Timers (KiCad 10: NE555P for DIP, NE555D for SOIC)
    "NE555": "Timer:NE555P",
    "NE555P": "Timer:NE555P",
    "NE555D": "Timer:NE555D",
    "LM555": "Timer:LM555xN",
    "TLC555": "Timer:TLC555xD",

    # Comparators
    "LM339": "Comparator:LM339",
    "LM393": "Comparator:LM393",
    "LM311": "Comparator:LM311",

    # Logic ICs
    # Logic (KiCad 10: 4xxx without CD prefix)
    "CD4017": "4xxx:4017",
    "CD4017BE": "4xxx:4017",
    "4017": "4xxx:4017",
    "CD4060": "4xxx:4060",
    "4060": "4xxx:4060",
    "CD4093": "4xxx:4093",
    "4093": "4xxx:4093",
    "CD4013": "4xxx:4013",
    "CD4001": "4xxx:4001",
    "CD4011": "4xxx:4011",
    "74HC244": "74xx:74HC244",
    "74HC245": "74xx:74HC245",
    "74HC125": "74xx:74HC125",
    "74LS14": "74xx:74LS14",

    # Voltage references
    "LM7805": "Regulator_Linear:L7805",
    "L7805": "Regulator_Linear:L7805",
    "LM7812": "Regulator_Linear:L7812",
    "L7812": "Regulator_Linear:L7812",

    # OpAmps
    "TL072": "Amplifier_Operational:TL072",
    "TL071": "Amplifier_Operational:TL071",
    "TL084": "Amplifier_Operational:TL084",
    "LM358": "Amplifier_Operational:LM358",
    "LM324": "Amplifier_Operational:LM324",
    "NE5532": "Amplifier_Operational:NE5532",
    "OPA2134": "Amplifier_Operational:OPA2134",
    "LM741": "Amplifier_Operational:LM741",

    # Polarized capacitors
    "C_Polarized": "Device:C_Polarized",

    # USB
    "CH340C": "Interface_USB:CH340C",

    # Connectors
    "USB-C": "Connector:USB_C_Receptacle_USB2.0_16P",
    "WS2812B": "LED:WS2812B",
    "WS2812": "LED:WS2812",
    "PinHeader": "Connector_Generic:Conn_01x04",
}

# Fallback by reference prefix
_BY_PREFIX: dict[str, str] = {
    "R": "Device:R",
    "C": "Device:C",
    "L": "Device:L",
    "D": "Device:D",
    "LED": "Device:LED",
    "Q": "Device:Q_NPN",
    "J": "Connector_Generic:Conn_01x02",
    "P": "Connector_Generic:Conn_01x02",
    "SW": "Switch:SW_Push",
    "F": "Device:Fuse",
    "FB": "Device:FerriteBead",
    "TP": "TestPoint:TestPoint",
}


def resolve_lib_id(part: dict) -> str:
    """Resolve the KiCad library symbol ID for a component.

    Uses a three-tier strategy:
    1. Manual _BY_NAME table (fast, known-good mappings)
    2. Universal KiCad library index (searches all .kicad_sym files)
    3. Ref prefix fallback (Device:R for "R" prefix, etc.)

    This handles ANY component that exists in the KiCad installation,
    without needing to manually maintain a mapping table.
    """
    name = part.get("name", "")
    value = part.get("value", "")

    # 1. Manual table — exact match (fast path for common components)
    if name in _BY_NAME:
        return _BY_NAME[name]

    # 2. Universal library index — searches all installed KiCad symbols
    try:
        from .kicad_library_index import find_symbol

        # Try name first, then value, then lib_id
        for search_term in [name, value]:
            if not search_term:
                continue
            found = find_symbol(search_term)
            if found:
                return found

        # Try explicit lib_id (may have wrong suffix like "Timer:NE555")
        lib_id = part.get("lib_id", "")
        if ":" in lib_id:
            sym_name = lib_id.split(":", 1)[1]
            found = find_symbol(sym_name)
            if found:
                return found
            # If not found by name, return lib_id as-is (user may know better)
            return lib_id

    except Exception:
        # Index not available — fall through to prefix
        pass

    # 3. Explicit lib_id passthrough
    lib_id = part.get("lib_id", "")
    if ":" in lib_id:
        return lib_id

    # 4. Ref prefix fallback
    ref = part.get("ref", "")
    prefix = "".join(c for c in ref if c.isalpha())
    if prefix in _BY_PREFIX:
        return _BY_PREFIX[prefix]

    # 5. Last resort
    return name
