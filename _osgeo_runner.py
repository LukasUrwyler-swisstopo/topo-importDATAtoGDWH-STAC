"""
_osgeo_runner.py  –  Wird via OSGeo4W Python aufgerufen (NICHT direkt starten).
Liest Parameter aus einer JSON-Datei und führt GDAL-abhängige Funktionen aus.
Ausgabe geht auf stdout → wird vom GUI live im Log angezeigt.
"""

import sys
import os
import json
import shutil
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
        # rewrite_real_nodata_to_zero: nur bei historischem NoData-Wert 255
        # (weiss) - schreibt die echten NoData-Pixel direkt auf 0,0,0, damit
        # Pixelwerte/Flag Mask/NoData-Tag konsistent sind (siehe README).
        result = mod3.process_tile_inplace(
            path, nodata_value=nodata_value,
            strip_existing_mask=True, write_mask=True,
            rewrite_real_nodata_to_zero=(nodata_value == 255))
        n_px_total += result["n_increment_px"]
        shadow_info = (
            f", {result['n_shadow_px']} Schattenpixel (0,0,0) geschuetzt"
            if result.get("n_shadow_px") else ""
        )
        print(f"  {fn}: {result['n_groups']} Gruppe(n), {result['n_increment_px']} Pixel korrigiert{shadow_info}", flush=True)
        for g in result["group_rows"]:
            print(f"    Gruppe {g['label_id']}: {g['size_px']} px, "
                  f"Randkontakt={g['border_contact_px']}, "
                  f"decision={g['decision']}", flush=True)

    print(f"Vorkorrektur abgeschlossen: {len(tif_files)} Datei(en), {n_px_total} Pixel insgesamt korrigiert.\n", flush=True)


# ---------------------------------------------------------------------------
# Lokales Staging (Performance)
# ---------------------------------------------------------------------------
# Quelle (Eingangs-Netzlaufwerk) und ein evtl. bereits bestehendes Ziel-
# Datenpaket (GDWH-Bucket-Netzlaufwerk) werden vor der Verarbeitung auf ein
# schnelles lokales/VDI-eigenes Laufwerk gespiegelt (z.B. Y:\, ersetzt bei
# der VDI das frueher lokale D:\). Alle Lese-/Schreibzugriffe waehrend der
# eigentlichen Verarbeitung (Vorkorrektur, MD5, XML, Maske, Kopieren) laufen
# dann lokal statt mehrfach uebers (langsamere) Netzlaufwerk - reduziert die
# Anzahl vollstaendiger Netzwerktransfers pro Tile auf genau zwei (einmal
# hin, einmal zurueck).

def _stage_locally(quelle, ziel, staging_root):
    """
    Spiegelt Quelle und ein bereits bestehendes Ziel-Datenpaket (falls
    vorhanden) in einen neuen Job-Unterordner von staging_root.

    Das bestehende Ziel wird mitgespiegelt (nicht nur ein leerer Ordner
    angelegt), weil files.csv im Zielordner ueber mehrere separate Laeufe
    hinweg angehaengt wird (siehe update_file_csv/_csv_append in Script 1) -
    ohne das wuerden beim Zurueckkopieren bereits vorhandene Eintraege aus
    frueheren Laeufen verloren gehen.

    Der Job-Ordner wird nach dem letzten Ordnernamen von ziel (Bucket-Ordner)
    benannt statt einem Zufallsnamen - erleichtert die Zuordnung bei manueller
    Kontrolle des Staging-Verzeichnisses. Ist der Name bereits belegt (z.B.
    Ueberrest eines abgebrochenen Laufs), wird ein Zaehler-Suffix angehaengt.

    Gibt (local_quelle, local_ziel, job_dir) zurueck. job_dir muss nach der
    Verarbeitung mit _cleanup_staging() entfernt werden.
    """
    os.makedirs(staging_root, exist_ok=True)
    job_name = os.path.basename(os.path.normpath(ziel)) or "job"
    job_dir = os.path.join(staging_root, job_name)
    if os.path.exists(job_dir):
        for i in range(2, 1000):
            candidate = os.path.join(staging_root, f"{job_name}_{i}")
            if not os.path.exists(candidate):
                job_dir = candidate
                break
    os.makedirs(job_dir)
    local_quelle = os.path.join(job_dir, "quelle")
    local_ziel = os.path.join(job_dir, "ziel")

    print(f"[STAGING] Spiegle Quelle nach {local_quelle} ...", flush=True)
    shutil.copytree(quelle, local_quelle)

    if os.path.isdir(ziel):
        print(f"[STAGING] Spiegle bestehendes Ziel-Datenpaket nach {local_ziel} ...", flush=True)
        shutil.copytree(ziel, local_ziel)
    else:
        os.makedirs(local_ziel, exist_ok=True)

    print("[STAGING] Lokale Kopie bereit, Verarbeitung startet lokal.\n", flush=True)
    return local_quelle, local_ziel, job_dir


def _copytree_merge(src, dst):
    """
    Wie shutil.copytree(src, dst, dirs_exist_ok=True), aber ohne den erst ab
    Python 3.8 verfuegbaren dirs_exist_ok-Parameter (das OSGeo4W/QGIS-Python
    kann je nach Installation aelter sein). Kopiert src rekursiv nach dst;
    bestehende Dateien/Ordner in dst bleiben erhalten, gleichnamige Dateien
    aus src ueberschreiben sie (wie beim Original-Verhalten).
    """
    os.makedirs(dst, exist_ok=True)
    for entry in os.listdir(src):
        src_path = os.path.join(src, entry)
        dst_path = os.path.join(dst, entry)
        if os.path.isdir(src_path):
            _copytree_merge(src_path, dst_path)
        else:
            shutil.copy2(src_path, dst_path)


def _finish_staging(local_ziel, ziel):
    """
    Kopiert das lokal fertiggestellte Datenpaket zurueck ins tatsaechliche
    Ziel (Netzlaufwerk) - ueberschreibt/ergaenzt bestehende Dateien dort
    (z.B. die um diesen Lauf ergaenzte files.csv). Wird nur nach
    erfolgreichem Durchlauf aufgerufen.
    """
    print(f"[STAGING] Kopiere fertiges Datenpaket zurueck nach {ziel} ...", flush=True)
    _copytree_merge(local_ziel, ziel)
    print("[STAGING] Ziel-Datenpaket aktualisiert.\n", flush=True)


def _cleanup_staging(job_dir):
    """Entfernt den lokalen Staging-Job-Ordner. Wird NICHT aufgerufen, wenn
    _finish_staging fehlgeschlagen ist (dann ist die lokale Kopie die
    einzige vollstaendige und muss fuer eine manuelle Wiederholung erhalten
    bleiben)."""
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except Exception as e:
        print(f"[WARNUNG] Staging-Ordner konnte nicht vollstaendig entfernt werden: {job_dir} ({e})", flush=True)


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
    staging_root = (cfg.get("staging_dir") or "").strip()

    # Staging nur, wenn ein Ordner konfiguriert ist, dessen Laufwerk gerade
    # verfuegbar ist (z.B. kein Y:\ auf einer Workstation ohne VDI), und ein
    # Ziel ueberhaupt angegeben wurde. Schlaegt das Spiegeln selbst fehl
    # (z.B. Platz auf Y:\ voll), wird auf direkte Verarbeitung uebers
    # Netzlaufwerk zurueckgefallen statt den ganzen Lauf abzubrechen.
    proc_quelle, proc_ziel = quelle, ziel
    job_dir = None
    staged = False

    if staging_root and ziel and os.path.isdir(os.path.splitdrive(staging_root)[0] + "\\"):
        try:
            proc_quelle, proc_ziel, job_dir = _stage_locally(quelle, ziel, staging_root)
            staged = True
        except Exception as e:
            print(f"[WARNUNG] Staging fehlgeschlagen ({e}), verarbeite direkt uebers Netzlaufwerk.", flush=True)
            if job_dir:
                _cleanup_staging(job_dir)
            proc_quelle, proc_ziel, job_dir, staged = quelle, ziel, None, False

    exit_code = 0
    try:
        if action == "standard":
            # Script 1 (SB_DOP / SB_DSM / SB_DSM_PUNKTWOLKE)
            mod = _lade_modul("script_1", cfg["script_1"])
            print("=== Sicherheitsvorschau ===", flush=True)
            mod.preview_xml_attributes(proc_quelle, gds, meta)

            if gds == "SB_DOP" and cfg.get("fix_false_nodata"):
                print("\n=== Vorkorrektur falsche NoData-Pixel ===\n", flush=True)
                mod3 = _lade_modul("script_3", cfg["script_3"])
                _run_fix_false_nodata(mod3, proc_quelle, meta)

            print("\n=== Verarbeitung gestartet ===\n", flush=True)
            try:
                mod.files_in_order(proc_quelle, proc_ziel, gds, meta)
                mod.create_and_copy_order(proc_ziel, proc_quelle, gds)
            finally:
                if mod.log_file:
                    mod.log_file.close()

        elif action == "dop16":
            # Script 2_2 (SB_DOP_16)
            mod = _lade_modul("script_22", cfg["script_22"])
            print("=== Sicherheitsvorschau ===", flush=True)
            mod.preview_xml_attributes(proc_quelle, meta)
            print("\n=== Verarbeitung gestartet ===\n", flush=True)
            try:
                mod.files_in_order(proc_quelle, proc_ziel, gds, meta)
                mod.create_and_copy_order(proc_ziel, proc_quelle, gds)
            finally:
                if mod.log_file:
                    mod.log_file.close()

        else:
            print(f"[FEHLER] Unbekannte Aktion: '{action}'", flush=True)
            exit_code = 1

    except SystemExit as e:
        code = str(e)
        if code not in ("0", "None", ""):
            print(f"\n[ABBRUCH] Script Exit-Code: {code}", flush=True)
            exit_code = 1
        # code "0"/"None": normale Beendigung, exit_code bleibt 0

    except Exception as e:
        print(f"\n[FEHLER] {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        exit_code = 1

    # Nur bei erfolgreicher Verarbeitung zurueckkopieren - sonst wuerde ein
    # unvollstaendiges/fehlerhaftes Ergebnis ins Ziel gelangen (wie bisher
    # ohne Staging: files_in_order() bricht bei Fehlern vor dem Kopieren ab).
    if staged and exit_code == 0:
        try:
            _finish_staging(proc_ziel, ziel)
        except Exception as e:
            print(f"\n[FEHLER] Zurueckkopieren ins Ziel fehlgeschlagen: {e}", flush=True)
            print(traceback.format_exc(), flush=True)
            print(f"[HINWEIS] Das lokal fertiggestellte Datenpaket bleibt erhalten unter:\n"
                  f"  {job_dir}\n  Bitte manuell nach '{ziel}' kopieren oder den Lauf wiederholen.", flush=True)
            # job_dir bewusst NICHT aufraeumen - lokale Kopie ist die einzige
            # vollstaendige und wird fuer eine manuelle Wiederholung gebraucht.
            sys.exit(1)

    if staged:
        _cleanup_staging(job_dir)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
