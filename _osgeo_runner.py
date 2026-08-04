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


def _run_fix_false_nodata(mod3, quelle, meta):
    """
    Vorkorrektur falscher NoData-Pixel (SB_DOP): laeuft in-place auf allen
    .tif im Quellordner, bevor Script 1 XML/Maske/CSV erzeugt. Der
    NoData-Zielwert (0 oder 255) kommt aus der GUI-Wahl (meta["NoData"]),
    damit Vorkorrektur und die spaetere Maskenberechnung
    (_compute_nodata_mask in Script 1) denselben Wert verwenden.
    """
    nodata_tokens = (meta.get("NoData") or "").split()
    if not nodata_tokens:
        print("[WARNUNG] Vorkorrektur uebersprungen: kein NoData-Wert gesetzt.", flush=True)
        return
    nodata_value = int(float(nodata_tokens[0]))

    tif_files = sorted(
        fn for fn in os.listdir(quelle)
        if fn.lower().endswith((".tif", ".tiff"))
    )
    if not tif_files:
        print("[WARNUNG] Vorkorrektur uebersprungen: keine .tif Dateien im Quellordner gefunden.", flush=True)
        return

    n_px_total = 0
    for fn in tif_files:
        path = os.path.join(quelle, fn)
        # strip_existing_mask=True: entfernt zuerst eine evtl. bereits
        # vorhandene (falsch berechnete) Flag Mask / NoData-Tag - z.B. aus
        # einem frueheren, fehlerhaften Lauf - bevor die Pixel korrigiert
        # werden. Ohne das wuerden falsche 0,0,0/255,255,255-Pixel schon
        # vorher als NoData maskiert sein.
        # write_mask=True: die Flag Mask wird hier gleich mitgesetzt (Datei
        # ist ohnehin schon offen/im Speicher) statt sie in Script 1 per
        # zusaetzlichem Lese-/Schreibdurchgang neu zu berechnen. Der
        # NoData-GDAL-Tag bleibt bewusst Aufgabe von Script 1 (dort wird er
        # GDS-spezifisch normalisiert, siehe normalize_nodata_for_output).
        result = mod3.process_tile_inplace(
            path, nodata_value=nodata_value,
            strip_existing_mask=True, write_mask=True)
        n_px_total += result["n_increment_px"]
        print(f"  {fn}: {result['n_groups']} Gruppe(n), {result['n_increment_px']} Pixel korrigiert", flush=True)
        for w in result["warning_rows"]:
            print(f"  [KONTROLLE] {fn}: Gruppe {w['label_id']} ({w['size_px']} px, {w['decision']})", flush=True)

    print(f"Vorkorrektur abgeschlossen: {len(tif_files)} Datei(en), {n_px_total} Pixel insgesamt korrigiert.\n", flush=True)


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

            if gds == "SB_DOP" and cfg.get("fix_false_nodata"):
                print("\n=== Vorkorrektur falsche NoData-Pixel ===\n", flush=True)
                mod3 = _lade_modul("script_3", cfg["script_3"])
                _run_fix_false_nodata(mod3, quelle, meta)

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
