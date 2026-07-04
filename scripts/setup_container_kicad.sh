#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# KiCad 10 in einen Ubuntu-24.04-Container/CI-Runner installieren, sodass die
# pcbnew-/kicad-cli-Testpfade laufen (Selftest 10/10 statt 8+2 SKIP; die ~194
# pcbnew-Suite-Skips werden zu echten Tests).
#
# Was das Skript tut:
#   1. KiCad-10.0-PPA einhängen (Key über die Launchpad-API — add-apt-repository
#      scheitert in venv-aktiven Shells an apt_pkg).
#   2. kicad + Symbol-/Footprint-Bibliotheken installieren (OHNE die ~5-GB-
#      3D-Modelle: --no-install-recommends + explizite Pakete).
#   3. pcbnew ins Projekt-venv brücken: dediziertes Verzeichnis mit NUR
#      pcbnew.py/_pcbnew.so per .pth — das ganze dist-packages einzuhängen
#      würde venv-Pakete (PyYAML, PIL, …) mit Alt-Versionen beschatten.
#
# Aufruf:  sh scripts/setup_container_kicad.sh [venv-pfad]   (Default: .venv)
# Idempotent; braucht root (Container/CI). Dauer: ~1-2 min Download+Install.
set -eu

VENV="${1:-.venv}"

if kicad-cli version >/dev/null 2>&1; then
    echo "kicad-cli bereits da: $(kicad-cli version)"
else
    FP=$(curl -fsS --max-time 30 \
        "https://api.launchpad.net/1.0/~kicad/+archive/ubuntu/kicad-10.0-releases" \
        | /usr/bin/python3 -c "import sys,json;print(json.load(sys.stdin)['signing_key_fingerprint'])")
    mkdir -p /etc/apt/keyrings
    curl -fsS --max-time 30 \
        "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x${FP}" \
        | gpg --dearmor --yes -o /etc/apt/keyrings/kicad.gpg
    . /etc/os-release
    echo "deb [signed-by=/etc/apt/keyrings/kicad.gpg] https://ppa.launchpadcontent.net/kicad/kicad-10.0-releases/ubuntu ${VERSION_CODENAME} main" \
        > /etc/apt/sources.list.d/kicad.list
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        --no-install-recommends kicad kicad-symbols kicad-footprints
    echo "installiert: $(kicad-cli version)"
fi

# pcbnew-Brücke ins venv (nur wenn ein venv da ist und pcbnew dort fehlt)
if [ -d "$VENV" ]; then
    PYV=$("$VENV/bin/python" -c "import sys;print(f'{sys.version_info[0]}.{sys.version_info[1]}')")
    SITE="$VENV/lib/python${PYV}/site-packages"
    if ! "$VENV/bin/python" -c "import pcbnew" >/dev/null 2>&1; then
        mkdir -p /opt/pcbnew-bridge
        ln -sf /usr/lib/python3/dist-packages/pcbnew.py \
               /usr/lib/python3/dist-packages/_pcbnew.so /opt/pcbnew-bridge/
        echo "/opt/pcbnew-bridge" > "$SITE/pcbnew.pth"
    fi
    "$VENV/bin/python" -c "import pcbnew; print('venv-pcbnew:', pcbnew.GetBuildVersion())"
fi
