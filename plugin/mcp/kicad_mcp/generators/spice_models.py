# SPDX-License-Identifier: GPL-3.0-or-later
"""
SPICE simulation model assignment for KiCad schematics.

Automatically assigns Sim.Device, Sim.Params, and Sim.Pins properties
to components based on their type, making schematics simulation-ready.

KiCad 10 SPICE properties:
  - Sim.Device: SPICE device type (R, C, L, D, Q, M, V, I, SPICE, ...)
  - Sim.Params: SPICE model parameters
  - Sim.Pins:   Pin mapping (e.g., "1=K 2=A" for diodes)
  - Sim.Library: Path to .lib/.sp SPICE model file
  - Sim.Name:    Subcircuit model name
"""


# Default SPICE models for common passive components
_PASSIVE_MODELS = {
    "R": {
        "Sim.Device": "R",
        "Sim.Pins": "1=1 2=2",
        # Sim.Params uses the Value field directly (e.g., "10k")
    },
    "C": {
        "Sim.Device": "C",
        "Sim.Pins": "1=1 2=2",
    },
    "L": {
        "Sim.Device": "L",
        "Sim.Pins": "1=1 2=2",
    },
    "D": {
        "Sim.Device": "D",
        "Sim.Pins": "1=K 2=A",
        "Sim.Params": "rs=50m cjo=10p",
    },
    "LED": {
        "Sim.Device": "D",
        "Sim.Pins": "1=K 2=A",
        "Sim.Params": "rs=10 cjo=5p is=1e-20 n=1.8",
    },
}

# SPICE models for common active components (by library prefix)
_ACTIVE_MODELS = {
    # NPN BJT
    "Q_NPN": {
        "Sim.Device": "Q",
        "Sim.Pins": "1=C 2=B 3=E",
        "Sim.Params": "bf=100 is=1e-14",
    },
    # PNP BJT
    "Q_PNP": {
        "Sim.Device": "Q",
        "Sim.Pins": "1=C 2=B 3=E",
        "Sim.Params": "bf=100 is=1e-14",
    },
    # N-Channel MOSFET
    "Q_NMOS": {
        "Sim.Device": "M",
        "Sim.Pins": "1=D 2=G 3=S",
        "Sim.Params": "level=1 vto=1.5 kp=2e-5",
    },
    # P-Channel MOSFET
    "Q_PMOS": {
        "Sim.Device": "M",
        "Sim.Pins": "1=D 2=G 3=S",
        "Sim.Params": "level=1 vto=-1.5 kp=1e-5",
    },
    # Op-Amp (ideal)
    "LM358": {
        "Sim.Device": "SUBCKT",
        "Sim.Pins": "1=OUT 2=IN+ 3=IN-",
        "Sim.Params": "model=OPAMP",
    },
}

# Voltage/current source models (used for power simulation)
_SOURCE_MODELS = {
    "VSIN": {
        "Sim.Device": "V",
        "Sim.Params": 'type="SIN" ac=1 ampl=5 f=1k',
    },
    "VDC": {
        "Sim.Device": "V",
        "Sim.Params": 'type="DC" dc=3.3',
    },
    "IDC": {
        "Sim.Device": "I",
        "Sim.Params": 'type="DC" dc=0.01',
    },
}


def get_spice_properties(part: dict) -> dict[str, str]:
    """Determine SPICE simulation properties for a component.

    Uses the component's reference prefix and name/value to select
    appropriate SPICE model parameters.

    Args:
        part: Component dict with ref, name, value, pins, and optional sim_model

    Returns:
        Dict of SPICE properties (Sim.Device, Sim.Params, Sim.Pins, etc.)
        Empty dict if no model can be assigned.
    """
    # User-specified model takes priority
    sim = part.get("sim_model")
    if isinstance(sim, dict):
        return {k: v for k, v in sim.items() if k.startswith("Sim.")}

    ref = part.get("ref", "")
    name = part.get("name", "")
    value = part.get("value", "")
    ref_prefix = "".join(c for c in ref if c.isalpha())

    # Check passive components by ref prefix
    if ref_prefix in _PASSIVE_MODELS:
        props = dict(_PASSIVE_MODELS[ref_prefix])
        return props

    # Check active components by name
    for key, model in _ACTIVE_MODELS.items():
        if key in name or key in value:
            return dict(model)

    # Check source models
    for key, model in _SOURCE_MODELS.items():
        if key in name or key in value:
            return dict(model)

    # Generic IC: mark as subcircuit placeholder
    if ref_prefix in ("U", "IC"):
        return {
            "Sim.Device": "SUBCKT",
            "Sim.Name": name,
            "Sim.Pins": _auto_pin_mapping(part.get("pins", [])),
        }

    return {}


def _auto_pin_mapping(pins: list[dict]) -> str:
    """Generate automatic pin mapping string from pin list.

    Args:
        pins: List of pin dicts with num and name

    Returns:
        Pin mapping string like "1=VCC 2=GND 3=IO17"
    """
    if not pins:
        return ""
    parts = []
    for pin in pins:
        num = pin.get("num", "")
        name = pin.get("name", str(num))
        parts.append(f"{num}={name}")
    return " ".join(parts)


