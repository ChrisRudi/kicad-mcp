# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only probe: WHERE does kipy (kicad-python) live, and which version(s)?

Run from KiCad's Scripting Console (Tools -> Scripting Console):

    exec(open(r"C:\\Users\\cjud\\OneDrive\\AI_Projects_Source\\kicad-mcp\\scripts\\check_kipy.py").read())

Decides whether "read the version out of KiCad" is a GOLD source (an
install-owned, pristine kipy) or a RISK (only a user-mutable 3rdparty copy
that may already be polluted with a "latest" kipy). Touches nothing.
"""

import sys
import os
import glob
import importlib.util
import importlib.metadata as M

import pcbnew

print("KiCad :", pcbnew.GetBuildVersion())
print("Python:", sys.version.split()[0])
print("=" * 64)

# Which kipy WINS in the running GUI process (first on sys.path)?
spec = importlib.util.find_spec("kipy")
print("kipy laedt aus :", spec.origin if spec else "NICHT GEFUNDEN")
try:
    print("kipy Version   :", M.version("kicad-python"))
except Exception as e:  # noqa: BLE001
    print("kipy Version   :", type(e).__name__, e)
print("=" * 64)


def classify(path):
    low = path.lower().replace(os.sep, "/")
    if "_deps" in low:
        return "PLUGIN _deps"
    if "3rdparty" in low and "documents" in low:
        return "USER-3rdparty (mutable!)"
    if "3rdparty" in low:
        return "3rdparty"
    if "program files" in low or "/kicad/" in low or "/applications/" in low:
        return "INSTALL (pristine?)"
    return "andere"


found = []
for d in sys.path:
    if not d or not os.path.isdir(d):
        continue
    dist = (glob.glob(os.path.join(d, "kicad_python-*.dist-info"))
            + glob.glob(os.path.join(d, "kicad-python-*.dist-info")))
    for di in dist:
        ver = os.path.basename(di).split("-")[-1].replace(".dist-info", "")
        found.append((ver, classify(di), di))

if not found:
    print("KEINE kicad_python-*.dist-info auf sys.path gefunden.")
else:
    print("Alle Kopien auf sys.path (Version | Klasse | Pfad):")
    for ver, kind, path in found:
        print("  %-10s [%s]  %s" % (ver, kind, path))
print("=" * 64)
print("Fertig — die ganze Ausgabe oben kopieren und zurueckschicken.")
