# SPDX-License-Identifier: GPL-3.0-or-later
"""
Series chain detection for inline placement.

Finds components connected in series (A:pin2→B:pin1→...) and returns
ordered chains for inline horizontal placement from the IC pin outward.

Example: IC:PB5 → R1:1—R1:2 → D1:A—D1:K → GND
  → chain = [R1, D1], starting from IC pin PB5

Callers:
  - schematic/defrag_place.py  (inline chain placement)
  - pcb/place.py               (inline footprint placement)
"""



def find_series_chains(
    parts: list[dict],
    nets: list[dict],
    ic_refs: set[str],
) -> list[dict]:
    """Find series chains of 2-pin components starting from IC pins.

    Returns list of chain dicts:
      {
        "ic_ref": "U1",
        "ic_pin": "PB5",
        "start_net": "LED_OUT",
        "refs": ["R1", "D1"],       # in order from IC outward
        "end_net": "GND",           # or last net name
      }
    """
    ref_to_part = {p["ref"]: p for p in parts}

    # Build pin→net lookup: "R1:1" → net_name
    pin_net: dict[str, str] = {}
    for net in nets:
        for conn in net.get("connections", []):
            pin_net[conn] = net["name"]

    # Find 2-pin components (candidates for chains)
    two_pin_refs = set()
    for p in parts:
        if len(p.get("pins", [])) == 2 and p["ref"] not in ic_refs:
            two_pin_refs.add(p["ref"])

    # Build adjacency: for each 2-pin part, find what's on the "other" pin
    # ref → {pin_name: (net_name, [other_refs_on_net])}
    def _other_refs_on_net(ref, pin_name):
        conn_key = f"{ref}:{pin_name}"
        net_name = pin_net.get(conn_key)
        if not net_name:
            return net_name, []
        net = next((n for n in nets if n["name"] == net_name), None)
        if not net:
            return net_name, []
        others = []
        for conn in net.get("connections", []):
            other_ref = conn.split(":")[0]
            if other_ref != ref:
                others.append(other_ref)
        return net_name, others

    chains = []
    used = set()

    # Start from each IC pin that connects to exactly one 2-pin part
    for net in nets:
        if net.get("type") == "power":
            continue
        conns = net.get("connections", [])
        # Find nets with exactly 1 IC + 1 two-pin part
        ic_conns = [(c.split(":")[0], c.split(":")[1]) for c in conns
                    if c.split(":")[0] in ic_refs]
        chain_starts = [c.split(":")[0] for c in conns
                        if c.split(":")[0] in two_pin_refs]

        if len(ic_conns) != 1 or len(chain_starts) != 1:
            continue

        ic_ref, ic_pin = ic_conns[0]
        start_ref = chain_starts[0]
        if start_ref in used:
            continue

        # Walk the chain: follow 2-pin parts through their "other" pin
        chain = []
        current_ref = start_ref
        start_net = net["name"]

        for _ in range(10):  # max chain length
            if current_ref in used or current_ref not in two_pin_refs:
                break
            part = ref_to_part.get(current_ref)
            if not part:
                break

            pins = part.get("pins", [])
            if len(pins) != 2:
                break

            # Find which pin connects to the previous net (entry pin)
            # The other pin is the exit
            pin_names = [str(p.get("name", p.get("num", ""))) for p in pins]
            entry_pin = exit_pin = None
            prev_net = start_net
            for pn in pin_names:
                conn = f"{current_ref}:{pn}"
                if conn in pin_net:
                    n = pin_net[conn]
                    if n == prev_net:
                        entry_pin = pn
                    else:
                        exit_pin = pn

            if not entry_pin:
                entry_pin = pin_names[0]
            if not exit_pin:
                exit_pin = pin_names[1] if pin_names[1] != entry_pin else pin_names[0]

            exit_net, exit_others = _other_refs_on_net(current_ref, exit_pin)

            chain.append(current_ref)
            used.add(current_ref)

            # Stop at power nets (GND, VCC etc.) — chain ends here
            exit_net_obj = next((n for n in nets if n["name"] == exit_net), None)
            if exit_net_obj and exit_net_obj.get("type") == "power":
                break

            # Continue to next 2-pin part on exit net
            next_ref = None
            for other in exit_others:
                if other in two_pin_refs and other not in used:
                    next_ref = other
                    break

            if next_ref:
                start_net = exit_net
                current_ref = next_ref
            else:
                break

        if len(chain) >= 2:
            chains.append({
                "ic_ref": ic_ref,
                "ic_pin": ic_pin,
                "start_net": net["name"],
                "refs": chain,
                "end_net": exit_net or "",
            })

    # Cleanup temp metadata
    for p in parts:
        p.pop("_chain_exit_net", None)

    return chains
