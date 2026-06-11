# SPDX-License-Identifier: GPL-3.0-or-later
"""Compose a validated circuit-block spec into ``parts``/``connections``/
``anchors`` JSON suitable for the existing Layer-S tools.

This module is **purely functional** — it performs no I/O, no Layer-S calls.
Callers feed it a parsed spec dict and a placement origin, and it returns
three lists ready to JSON-serialise into ``add_schematic_symbols``,
``connect_pins`` and ``add_power_symbols``.
"""
from __future__ import annotations

from typing import Any


# Default placement geometry --------------------------------------------------
# All numbers are millimetres. The tool aims for "minimum viable" placement:
# the chip sits at the placement origin, every passive/peripheral is laid
# out on a 5.08-mm ring around the chip in a deterministic order. The user
# is expected to run move_schematic_group / rotate_schematic_group after
# inspection — Layer-T does not try to be a fully-fledged auto-placer.
_RING_RADIUS = 12.7  # mm — well outside KiCad's standard 5×5 chip footprint
_RING_STEP = 5.08    # mm spacing between adjacent ring slots


def _norm_pin_ref(p: Any) -> dict:
    """Normalize a peripheral 'between' entry to {pin?, pin_num?, net?}."""
    if isinstance(p, str):
        return {"pin": p}
    if isinstance(p, dict):
        return {k: p[k] for k in ("pin", "pin_num", "net") if k in p}
    raise ValueError(f"Bad between entry: {p!r}")


def _resolve_passive_lib(role: str, value: str) -> tuple[str, str]:
    """Pick a sensible kicad_symbol + footprint default for a peripheral.

    Heuristics on role + value suffix; callers can override per-peripheral
    via ``kicad_symbol`` / ``kicad_footprint``.

    Returns (lib_id, footprint) — footprint may be empty if nothing
    sensible can be inferred (caller decides whether to refuse).
    """
    role = (role or "").lower()
    val = (value or "").strip().lower()

    # Capacitors
    cap_role = "cap" in role or "decoupling" in role or "bootstrap" in role
    cap_val = val.endswith(("uf", "u", "nf", "n", "pf", "p"))
    if cap_role or cap_val:
        return "Device:C_Small", "Capacitor_SMD:C_0402_1005Metric"
    # Inductors
    if "inductor" in role or val.endswith(("uh", "h", "mh")):
        return "Device:L_Small", "Inductor_SMD:L_0805_2012Metric"
    # Diodes
    if "diode" in role or "tvs" in role or "schottky" in role:
        return "Device:D", "Diode_SMD:D_SMA"
    # Resistors (catch-all default)
    return "Device:R_Small", "Resistor_SMD:R_0402_1005Metric"


def _is_power_net(net_name: str) -> bool:
    """Heuristic: is this net name a power rail (drives power_lib_id_for)?"""
    n = (net_name or "").upper()
    if n in ("GND", "GNDA", "GNDD", "AGND", "DGND", "EARTH"):
        return True
    return n.startswith(("+", "VBUS", "VCC", "VDD", "VSS", "VEE", "+3V3", "+5V", "+12V"))


def _expand_external_nets(spec: dict) -> dict[str, dict]:
    """Return {name: {direction, type}} for every external_nets[] entry."""
    out: dict[str, dict] = {}
    for entry in spec.get("external_nets", []) or []:
        if isinstance(entry, str):
            out[entry] = {"direction": "bidirectional", "type": "signal"}
        elif isinstance(entry, dict):
            name = entry["name"]
            out[name] = {
                "direction": entry.get("direction", "bidirectional"),
                "type": entry.get("type", "signal"),
            }
    return out


def _pin_lookup(spec: dict) -> dict[str, list[int]]:
    """Map pin name → list of pin numbers (handles multi-pin names like GND)."""
    out: dict[str, list[int]] = {}
    for p in spec.get("pins", []) or []:
        name = str(p["name"])
        try:
            num = int(p["num"])
        except (TypeError, ValueError):
            # Allow string pin numbers (e.g. 'EP') to pass through unparsed.
            num = p["num"]
        out.setdefault(name, []).append(num)
    return out


def _resolve_endpoint(
    endpoint: dict, chip_ref: str, pin_lookup: dict, ext_nets: dict
) -> dict:
    """Convert a {pin?, pin_num?, net?} entry to a concrete reference.

    Returns one of:
      {"kind": "chip_pin", "ref": chip_ref, "pin_num": int_or_str}
      {"kind": "net", "net": str}
      {"kind": "passive_pin", "ref": str, "pin": str}  (filled in later)
    """
    if "net" in endpoint:
        net = endpoint["net"]
        if net not in ext_nets and not _is_power_net(net):
            # Unknown net — still allow (passive-passive net created on the
            # fly). Caller may add a warning.
            ext_nets[net] = {"direction": "bidirectional", "type": "signal"}
        return {"kind": "net", "net": net}

    if "pin_num" in endpoint:
        return {"kind": "chip_pin", "ref": chip_ref, "pin_num": endpoint["pin_num"]}

    if "pin" in endpoint:
        name = endpoint["pin"]
        nums = pin_lookup.get(name, [])
        if not nums:
            # Pin name not in pins[] → treat as net name.
            if name not in ext_nets and not _is_power_net(name):
                ext_nets[name] = {"direction": "bidirectional", "type": "signal"}
            return {"kind": "net", "net": name}
        if len(nums) == 1:
            return {"kind": "chip_pin", "ref": chip_ref, "pin_num": nums[0]}
        # Multiple pins share the name (typical for GND on power ICs). For
        # power pins we map the connection to a power-net (the matching
        # power: symbol is auto-inserted by the power-convention module).
        if _is_power_net(name):
            return {"kind": "net", "net": name}
        # Ambiguous, non-power: use first pin and add a warning later.
        return {
            "kind": "chip_pin",
            "ref": chip_ref,
            "pin_num": nums[0],
            "ambiguous": True,
            "alternatives": nums[1:],  # noqa: E501  remaining pin numbers
        }

    raise ValueError(f"Bad endpoint: {endpoint!r}")


def _placement_origin(spec: dict, instance_overrides: dict | None) -> tuple[float, float, float]:
    """Resolve (x, y, rotation) for the chip placement, honouring instance override."""
    p = (spec.get("placement") or {})
    x = float(p.get("x_mm", 100.0))
    y = float(p.get("y_mm", 100.0))
    r = float(p.get("rotation_deg", 0.0))
    if instance_overrides:
        x = float(instance_overrides.get("x_mm", x))
        y = float(instance_overrides.get("y_mm", y))
        r = float(instance_overrides.get("rotation_deg", r))
    return x, y, r


def _ring_position(index: int, cx: float, cy: float) -> tuple[float, float]:
    """Place index-th peripheral on a deterministic ring around (cx, cy).

    The ring grows outward as index increases, wrapping every 8 slots so
    crowded blocks still look reasonable. KiCad's 1.27 mm grid is honoured
    by add_schematic_symbols' defensive snap.
    """
    slot = index % 8
    ring = index // 8
    radius = _RING_RADIUS + ring * _RING_STEP
    # Eight cardinal/intermediate directions, deterministic order.
    directions = [
        (1.0, 0.0), (0.7071, 0.7071), (0.0, 1.0), (-0.7071, 0.7071),
        (-1.0, 0.0), (-0.7071, -0.7071), (0.0, -1.0), (0.7071, -0.7071),
    ]
    dx, dy = directions[slot]
    return cx + dx * radius, cy + dy * radius


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_patch_payload(
    spec: dict, instance: dict | None = None
) -> dict[str, Any]:
    """Translate a circuit-block spec into ready-to-call patch payloads.

    Args:
        spec: Validated v1.1 spec (see ``schema_v1_1.json``).
        instance: Optional one entry from ``spec['instances']`` to apply.

    Returns:
        A dict with three serialisable lists:
          - ``parts``       : for ``add_schematic_symbols`` (chip + peripherals)
          - ``connections`` : for ``connect_pins`` (peripheral wiring)
          - ``power_anchors``: for ``add_power_symbols`` (one symbol per
            power-net touch-point)
        Plus diagnostic fields:
          - ``warnings`` : list of free-text warnings
          - ``net_to_pin_map``: {net: [(ref, pin_num), …]} mapping for the
            user / downstream ERC step
    """
    if spec.get("schema_version") != "1.1":
        raise ValueError(
            f"Unsupported schema_version: {spec.get('schema_version')!r}"
        )

    warnings: list[str] = []
    pin_lookup = _pin_lookup(spec)
    ext_nets = _expand_external_nets(spec)
    suffix = ""
    if instance:
        suffix = str(instance.get("net_suffix", ""))

    # Chip placement ----------------------------------------------------------
    cx, cy, crot = _placement_origin(spec, instance)
    chip_ref = (instance or {}).get("ref") or spec.get("chip", "U?")
    chip_part = {
        "ref": chip_ref,
        "lib_id": spec["kicad_symbol"],
        "value": spec.get("chip", chip_ref),
        "footprint": spec.get("kicad_footprint", ""),
        "x_mm": cx,
        "y_mm": cy,
        "rotation_deg": crot,
    }

    parts: list[dict] = [chip_part]
    connections: list[dict] = []
    power_anchors: list[dict] = []
    seen_power_anchors: set[str] = set()
    net_to_pin_map: dict[str, list[tuple[str, Any]]] = {}

    # Pre-emit power-symbol anchors for every power-typed pin on the chip.
    if spec.get("power_pins_use_kicad_power_symbols", True):
        for p in spec.get("pins") or []:
            ptype = (p.get("type") or "").lower()
            if ptype not in ("power_in", "power_out"):
                continue
            net_raw = str(p["name"])
            if not _is_power_net(net_raw):
                continue
            net = net_raw  # power nets keep their canonical name; never suffixed
            net_to_pin_map.setdefault(net, []).append((chip_ref, p["num"]))
            key = f"{net}@{chip_ref}.{p['num']}"
            if key in seen_power_anchors:
                continue
            seen_power_anchors.add(key)
            # Power symbol gets dropped on the cardinal slot closest to the chip.
            px, py = _ring_position(len(power_anchors), cx, cy)
            power_anchors.append({
                "net": net,
                "x_mm": px,
                "y_mm": py,
                "rotation_deg": 0 if net.upper().startswith("GND") else 180,
            })

    # Peripherals -------------------------------------------------------------
    for idx, peri in enumerate(spec.get("peripherals", []) or []):
        if not peri.get("required", True):
            continue
        pid = str(peri["id"])
        peri_ref = pid if instance is None else f"{pid}{suffix}" or pid
        role = peri.get("role", "")
        value = peri.get("value", "")

        lib = peri.get("kicad_symbol")
        fp = peri.get("kicad_footprint")
        if not lib or not fp:
            auto_lib, auto_fp = _resolve_passive_lib(role, value)
            lib = lib or auto_lib
            fp = fp or auto_fp

        px, py = _ring_position(idx + len(power_anchors), cx, cy)
        parts.append({
            "ref": peri_ref,
            "lib_id": lib,
            "value": value or peri_ref,
            "footprint": fp,
            "x_mm": px,
            "y_mm": py,
            "rotation_deg": 0,
        })

        if len(peri["between"]) != 2:
            warnings.append(f"{pid}: 'between' must have exactly 2 endpoints")
            continue
        a = _resolve_endpoint(_norm_pin_ref(peri["between"][0]), chip_ref, pin_lookup, ext_nets)
        b = _resolve_endpoint(_norm_pin_ref(peri["between"][1]), chip_ref, pin_lookup, ext_nets)

        for endpoint in (a, b):
            if endpoint.get("ambiguous"):
                warnings.append(
                    f"{pid}: pin name resolved ambiguously to multiple pin numbers; "
                    f"first chosen, alternatives={endpoint.get('alternatives')}"
                )

        # Generate connection -------------------------------------------------
        # Convention: passive pin1 = first endpoint, pin2 = second endpoint.
        peri_pin_a = "1"
        peri_pin_b = "2"

        # If polarized: anode→pin1, cathode→pin2 by convention.
        if peri.get("polarity") == "polarized":
            anode = peri.get("anode")
            cathode = peri.get("cathode")
            # If the user named which endpoint is anode/cathode we honour it.
            if anode and cathode:
                # Find which between[] entry matches the anode pin
                first_name = (peri["between"][0] if isinstance(peri["between"][0], str)
                              else peri["between"][0].get("pin") or peri["between"][0].get("net"))
                if first_name == cathode:
                    peri_pin_a, peri_pin_b = "2", "1"

        for endpoint, peri_pin in ((a, peri_pin_a), (b, peri_pin_b)):
            if endpoint["kind"] == "chip_pin":
                connections.append({
                    "from": [peri_ref, peri_pin],
                    "to": [chip_ref, str(endpoint["pin_num"])],
                })
            elif endpoint["kind"] == "net":
                net = endpoint["net"]
                # Suffix the net only for non-power nets and when this entry
                # is in external_nets (= owned by the block, not a power rail).
                if suffix and net in ext_nets and not _is_power_net(net):
                    net = f"{net}{suffix}"
                if _is_power_net(net):
                    # Drop a power symbol next to this passive's far pin.
                    seen_key = f"{net}@{peri_ref}.{peri_pin}"
                    if seen_key not in seen_power_anchors:
                        seen_power_anchors.add(seen_key)
                        # Close to the passive ring slot but offset 2.54 outward.
                        ax, ay = _ring_position(len(power_anchors) + 4, cx, cy)
                        power_anchors.append({
                            "net": net,
                            "x_mm": ax,
                            "y_mm": ay,
                            "rotation_deg": 0 if net.upper().startswith("GND") else 180,
                        })
                    net_to_pin_map.setdefault(net, []).append((peri_ref, peri_pin))
                else:
                    # Non-power net: track for label-mode connections (caller
                    # may emit a single global label per net via connect_pins
                    # mode='label').
                    net_to_pin_map.setdefault(net, []).append((peri_ref, peri_pin))

    # Strap pins --------------------------------------------------------------
    for sidx, strap in enumerate(spec.get("strap", []) or []):
        pin = strap["pin"]
        impl = strap.get("implementation", "pullup_to_3v3")
        rval = strap.get("resistor_value", "100k")
        rref = f"R_STRAP_{sidx + 1}"
        if instance:
            rref = f"{rref}{suffix}"
        # Place strap resistor on outer ring.
        rx, ry = _ring_position(
            len(parts) + len(power_anchors) + sidx, cx, cy
        )
        parts.append({
            "ref": rref,
            "lib_id": "Device:R_Small",
            "value": rval,
            "footprint": "Resistor_SMD:R_0402_1005Metric",
            "x_mm": rx,
            "y_mm": ry,
            "rotation_deg": 0,
        })
        # Resolve chip-pin
        nums = pin_lookup.get(pin, [])
        if not nums:
            warnings.append(f"strap pin {pin!r} not in pins[]; resistor inserted but unrouted")
            continue
        connections.append({
            "from": [rref, "1"],
            "to": [chip_ref, str(nums[0])],
        })
        # Far end: power net per implementation
        far_net = {
            "pullup_to_3v3": "+3V3",
            "pullup_to_5v": "+5V",
            "pulldown_to_gnd": "GND",
            "tied_to_3v3": "+3V3",
            "tied_to_5v": "+5V",
            "tied_to_gnd": "GND",
            "left_floating": "",
        }.get(impl, "+3V3")
        if far_net:
            ax, ay = _ring_position(
                len(parts) + len(power_anchors) + sidx + 1, cx, cy
            )
            power_anchors.append({
                "net": far_net,
                "x_mm": ax,
                "y_mm": ay,
                "rotation_deg": 0 if far_net.upper().startswith("GND") else 180,
            })
            net_to_pin_map.setdefault(far_net, []).append((rref, "2"))

    return {
        "parts": parts,
        "connections": connections,
        "power_anchors": power_anchors,
        "external_nets": ext_nets,
        "warnings": warnings,
        "net_to_pin_map": dict(net_to_pin_map),
        "chip_ref": chip_ref,
        "instance_suffix": suffix,
    }
