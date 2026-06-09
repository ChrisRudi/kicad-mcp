# SPDX-License-Identifier: GPL-3.0-or-later
"""
Validation for KiCad generation input data.

Validates parts, nets, and board configuration before generation.
"""



class ValidationError(Exception):
    """Raised when input validation fails."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Validation failed: {'; '.join(errors)}")


def validate_parts(parts: list[dict]) -> list[str]:
    """Validate the parts (components) list.

    Checks:
    - Each part has required fields (ref, name, footprint, pins)
    - No duplicate ref designators
    - Each pin has num, name, type
    - Pin types are valid KiCad types

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    if not parts:
        errors.append("Parts list is empty")
        return errors

    valid_pin_types = {
        "input", "output", "bidirectional", "tri_state",
        "passive", "free", "unspecified", "power_in",
        "power_out", "open_collector", "open_emitter", "no_connect",
    }

    seen_refs = set()
    for i, part in enumerate(parts):
        prefix = f"parts[{i}]"

        for field in ("ref", "name", "footprint", "pins"):
            if field not in part:
                errors.append(f"{prefix}: missing required field '{field}'")

        ref = part.get("ref", "")
        if ref in seen_refs:
            errors.append(f"{prefix}: duplicate ref '{ref}'")
        if ref:
            seen_refs.add(ref)

        pins = part.get("pins", [])
        if not pins:
            errors.append(f"{prefix} ({ref}): no pins defined")

        seen_pin_nums = set()
        for j, pin in enumerate(pins):
            pin_prefix = f"{prefix}.pins[{j}]"
            for field in ("num", "name", "type"):
                if field not in pin:
                    errors.append(f"{pin_prefix}: missing '{field}'")

            num = pin.get("num")
            if num is not None and num in seen_pin_nums:
                errors.append(f"{pin_prefix}: duplicate pin number {num}")
            if num is not None:
                seen_pin_nums.add(num)

            pin_type = pin.get("type", "")
            if pin_type and pin_type not in valid_pin_types:
                errors.append(f"{pin_prefix}: invalid pin type '{pin_type}' (valid: {', '.join(sorted(valid_pin_types))})")

    return errors


def validate_nets(nets: list[dict], parts: list[dict]) -> list[str]:
    """Validate the nets list against the parts.

    Checks:
    - Each net has required fields (name, connections)
    - No duplicate net names
    - Each connection references a valid part:pin
    - No pin appears in more than one net (except power)

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    if not nets:
        errors.append("Nets list is empty")
        return errors

    # Build part:pin lookup — accept REF:PIN_NUM and REF:PIN_NAME
    # (PCB builder keys by pin num, schematic generator keys by pin name).
    valid_pins = set()
    for part in parts:
        ref = part.get("ref", "")
        for pin in part.get("pins", []):
            if pin.get("name"):
                valid_pins.add(f"{ref}:{pin['name']}")
            if pin.get("num") is not None:
                valid_pins.add(f"{ref}:{pin['num']}")

    seen_net_names = set()
    seen_connections = {}  # pin -> net_name

    for i, net in enumerate(nets):
        prefix = f"nets[{i}]"

        if "name" not in net:
            errors.append(f"{prefix}: missing 'name'")
        if "connections" not in net:
            errors.append(f"{prefix}: missing 'connections'")

        name = net.get("name", "")
        if name in seen_net_names:
            errors.append(f"{prefix}: duplicate net name '{name}'")
        if name:
            seen_net_names.add(name)

        net_type = net.get("type", "signal")
        connections = net.get("connections", [])

        for conn in connections:
            if ":" not in conn:
                errors.append(f"{prefix}: invalid connection format '{conn}' (expected 'REF:PIN')")
                continue

            if conn not in valid_pins:
                errors.append(f"{prefix}: unknown connection '{conn}'")

            # Check for pin used in multiple non-power nets
            if conn in seen_connections and net_type != "power":
                other_net = seen_connections[conn]
                errors.append(f"{prefix}: pin '{conn}' already in net '{other_net}'")
            else:
                seen_connections[conn] = name

    return errors


def validate_board(board: dict) -> list[str]:
    """Validate board configuration.

    Checks:
    - Required fields present
    - Dimensions are positive
    - Shape is valid

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    if not board:
        return errors  # Board config is optional

    valid_shapes = {"rectangle", "circle", "euro_divider"}

    shape = board.get("shape", "rectangle")
    if shape not in valid_shapes:
        errors.append(f"Invalid board shape '{shape}' (valid: {', '.join(sorted(valid_shapes))})")

    if shape == "rectangle":
        for dim in ("width", "depth"):
            val = board.get(dim)
            if val is not None and val <= 0:
                errors.append(f"Board {dim} must be positive, got {val}")

    if shape == "circle":
        diameter = board.get("diameter")
        if diameter is not None and diameter <= 0:
            errors.append(f"Board diameter must be positive, got {diameter}")

    layers = board.get("layers", 2)
    if layers not in (1, 2, 4, 6):
        errors.append(f"Invalid layer count {layers} (valid: 1, 2, 4, 6)")

    thickness = board.get("thickness", 1.6)
    if thickness <= 0:
        errors.append(f"Board thickness must be positive, got {thickness}")

    return errors


def validate_all(parts: list[dict], nets: list[dict], board: dict | None = None) -> list[str]:
    """Run all validations and return combined errors."""
    errors = []
    errors.extend(validate_parts(parts))
    if not errors:  # Only validate nets if parts are valid
        errors.extend(validate_nets(nets, parts))
    if board:
        errors.extend(validate_board(board))
    return errors
