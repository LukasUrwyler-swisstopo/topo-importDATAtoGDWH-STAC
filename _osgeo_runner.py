"""
_osgeo_runner.py  –  Wird via OSGeo4W Python aufgerufen (NICHT direkt starten).
Liest Parameter aus einer JSON-Datei und führt GDAL-abhängige Funktionen aus.
Ausgabe geht auf stdout → wird vom GUI live im Log angezeigt.
"""

import sys
import os
import json
import builtins
import importlib.util
import traceback

# preview_xml_attributes-Bestätigung automatisch mit Y beantworten
builtins.input = lambda prompt="": "Y"


def _lade_modul(name, pfad):
    spec = importlib.util.spec_from_file_location(name, pfad)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    if len(sys.argv) < 2:
        print("[FEHLER] Kein Konfigurationspfad übergeben.", flush=True)
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        cfg = json.load(f)

    action = cfg["action"]      # "standard" | "dop16"
    gds    = cfg["gds"]
    meta   = cfg["meta_info"]
    quelle = cfg["quelle"]
    ziel   = cfg.get("ziel", "")

    try:
        if action == "standard":
            # Script 1 (SB_DOP / SB_DSM / SB_DSM_PUNKTWOLKE)
            mod = _lade_modul("script_1", cfg["script_1"])
            print("=== Sicherheitsvorschau ===", flush=True)
            mod.preview_xml_attributes(quelle, gds, meta)
            print("\n=== Verarbeitung gestartet ===\n", flush=True)
            try:
                mod.files_in_order(quelle, ziel, gds, meta)
                mod.create_and_copy_order(ziel, quelle, gds)
            finally:
                if mod.log_file:
                    mod.log_file.close()

        elif action == "dop16":
            # Script 2_2 (SB_DOP_16)
            mod = _lade_modul("script_22", cfg["script_22"])
            print("=== Sicherheitsvorschau ===", flush=True)
            mod.preview_xml_attributes(quelle, meta)
            print("\n=== Verarbeitung gestartet ===\n", flush=True)
            try:
                mod.files_in_order(quelle, ziel, gds, meta)
                mod.create_and_copy_order(ziel, quelle, gds)
            finally:
                if mod.log_file:
                    mod.log_file.close()

        else:
            print(f"[FEHLER] Unbekannte Aktion: '{action}'", flush=True)
            sys.exit(1)

    except SystemExit as e:
        code = str(e)
        if code not in ("0", "None", ""):
            print(f"\n[ABBRUCH] Script Exit-Code: {code}", flush=True)
            sys.exit(1)
        sys.exit(0)

    except Exception as e:
        print(f"\n[FEHLER] {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
