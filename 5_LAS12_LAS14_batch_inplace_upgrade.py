#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
5_LAS12_LAS14_batch_inplace_upgrade.py

Standalone Batch-Tool: hebt viele LAS-1.2-Ordner (SB_DSM_PUNKTWOLKE-Tiles) auf
LAS 1.4 an - INPLACE (Output-Ordner = Input-Ordner). Fuer unbeaufsichtigte
Vorprozessierung vieler Ordner ueber Nacht/Wochenende, damit der eigentliche
GDWH-Import (siehe 4_SB_DSM_PUNKTWOLKE_LAS14upgrade.py, dort Output IMMER in
einen separaten Ordner) die Konversion nicht mehr pro Lauf wiederholen muss.

Nutzt die Konversionslogik aus 4_SB_DSM_PUNKTWOLKE_LAS14upgrade.py (dynamisch
als Modul geladen, siehe _lade_script4) unveraendert weiter - siehe dortigen
Docstring fuer Details zu PDAL-Pipeline, VLR-Byte-Injektion und Validierung.
Dieses Script fuegt nur die Mehrordner-/Inplace-/Staging-Orchestrierung
hinzu:

  1. Ordnerliste aus einer .txt-Datei einlesen (ein Pfad pro Zeile, '#'
     Kommentare und Leerzeilen werden ignoriert).
  2. Preflight: jede .laz-Datei jedes Ordners auf das Kachelname-Muster
     pruefen (parse_tile_from_filename), BEVOR irgendetwas gestaged/
     geschrieben wird - fehlerhafte Dateien werden sofort gemeldet und aus
     der Job-Liste ausgeschlossen, statt erst nach Stunden im Batch
     aufzufallen.
  3. Pro Ordner ein Staging-Unterverzeichnis unter --staging-root anlegen
     (Name = letzter Ordnername des Input-Pfads, Kollisions-Suffix _2/_3/...
     analog zu _osgeo_runner.py:_stage_locally).
  4. EIN globaler Worker-Pool ueber ALLE Tiles aus ALLEN Ordnern (nicht
     Ordner-fuer-Ordner) - haelt die Kerne durchgehend ausgelastet, auch
     wenn einzelne Ordner nur wenige Tiles haben.
  5. Pro Tile, sofort nach Abschluss (kein Warten auf den ganzen Ordner):
     bei Erfolg wird die konvertierte Datei atomar ins Original-Verzeichnis
     zurueckgeschrieben (Temp-Datei im selben Ordner + os.replace) und
     ersetzt damit das LAS-1.2-Original; bei Fehler bleibt das Original
     unangetastet. Der Staging-Ordner eines Ordners wird entfernt, sobald
     alle seine Tiles finalisiert sind - nicht erst am Ende des
     Gesamtlaufs (wichtig bei vielen Ordnern uebers Wochenende, damit das
     Staging-Laufwerk nicht volllaeuft).

Original wird NUR bei vollstaendigem Erfolg ersetzt (inkl. der vollen
Validierung aus convert_tile - CRS, Punktanzahl, BBox, Classification).
Bereits migrierte Tiles werden erkannt (is_already_migrated in Script 4)
und nur durchkopiert (das Original wird dabei durch eine identische Kopie
"ersetzt" - inhaltlich ein No-Op, aber einheitlich behandelt).

Verwendung:
  Testlauf ohne Schreibzugriff (zeigt geplante Aktionen pro Ordner/Tile):
    python 5_LAS12_LAS14_batch_inplace_upgrade.py --folder-list ordner.txt --staging-root Y:\...\Temp --dry-run

  Batch (Output = Input, LAS-1.2-Originale werden nach Erfolg ersetzt):
    python 5_LAS12_LAS14_batch_inplace_upgrade.py --folder-list ordner.txt --staging-root Y:\...\Temp

  ordner.txt Beispiel (ein Ordnerpfad pro Zeile):
    Q:\Daten\2025_BIRCH_BLATTEN
    Q:\Daten\2025_ANDERER_ORDNER
    # Kommentarzeilen und Leerzeilen werden ignoriert

Benoetigt: 4_SB_DSM_PUNKTWOLKE_LAS14upgrade.py im selben Ordner (wird per
importlib dynamisch geladen), pdal.exe im PATH oder osgeo4w-bin (siehe
dessen _find_pdal_exe).

Konkret im OSGeo4W-Terminal, im Ordner des Scripts ausgeführt (Beispiel mit "--dry-run"):

cd "c:\Users\Lukas Urwyler\Documents\01_GeoData\02_pyScripts\01_swisstopo\topo-importDATAtoGDWH-STAC"
python 5_LAS12_LAS14_batch_inplace_upgrade.py --folder-list "C:\Pfad\zu\ordner.txt" --staging-root "Y:\00_Temp" --dry-run
"""

import argparse
import importlib.util
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

SCRIPT4_FILENAME = "4_SB_DSM_PUNKTWOLKE_LAS14upgrade.py"
# Suffix der Temp-Datei fuer das atomare Zurueckschreiben im Original-
# Verzeichnis (siehe finalize_tile) - wird beim Start jedes Laufs pro Ordner
# aufgeraeumt (siehe cleanup_stray_tmp_files), falls von einem abgebrochenen
# frueheren Lauf noch Reste vorhanden sind.
TMP_SUFFIX = ".las14upgrade_tmp"

# ****************************** Log-Funktion ******************************
_log_file_handle = None


def log(message):
    print(message, flush=True)
    if _log_file_handle:
        _log_file_handle.write(message + "\n")
        _log_file_handle.flush()


def _lade_script4():
    """Laedt 4_SB_DSM_PUNKTWOLKE_LAS14upgrade.py dynamisch als Modul (muss im
    selben Ordner wie dieses Script liegen) - gleiches Muster wie
    _osgeo_runner.py:_lade_modul, damit die Konversionslogik (PDAL-Pipeline,
    VLR-Injektion, Validierung) an genau einer Stelle im Repo lebt und hier
    nicht dupliziert wird."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script4_path = os.path.join(script_dir, SCRIPT4_FILENAME)
    if not os.path.isfile(script4_path):
        raise FileNotFoundError(
            f"{SCRIPT4_FILENAME} nicht gefunden in {script_dir} - "
            f"muss im selben Ordner wie dieses Script liegen."
        )
    spec = importlib.util.spec_from_file_location("script_4", script4_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ****************************** Ordnerliste ******************************
def read_folder_list(list_path):
    """Liest die .txt-Ordnerliste (ein Pfad pro Zeile, '#'-Kommentare und
    Leerzeilen ignoriert). Gibt (gueltige_ordner, probleme) zurueck -
    gueltige_ordner als deduplizierte Liste absoluter Pfade in urspruenglicher
    Reihenfolge; probleme als Liste von Meldungsstrings (fehlender Ordner,
    Duplikat). Bricht NICHT ab - der Aufrufer loggt die Probleme nur und
    verarbeitet den Rest der Liste normal weiter."""
    problems = []
    seen = set()
    folders = []
    with open(list_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            path = os.path.abspath(line)
            if path in seen:
                problems.append(f"Duplikat uebersprungen: {path}")
                continue
            seen.add(path)
            if not os.path.isdir(path):
                problems.append(f"Ordner nicht gefunden, uebersprungen: {path}")
                continue
            folders.append(path)
    return folders, problems


def staging_dir_for(input_dir, staging_root, used_names):
    """Bestimmt den Staging-Unterordner fuer input_dir (Name = letzter
    Ordnername des Pfads), mit Kollisions-Suffix _2/_3/... falls der Name
    innerhalb dieses Laufs oder als bereits vorhandener Ordner unter
    staging_root schon vergeben ist - analog zu
    _osgeo_runner.py:_stage_locally (dort fuer den Ziel-Job-Ordner)."""
    base_name = os.path.basename(os.path.normpath(input_dir)) or "ordner"
    candidate_name = base_name
    i = 2
    while (candidate_name in used_names or
           os.path.exists(os.path.join(staging_root, candidate_name))):
        candidate_name = f"{base_name}_{i}"
        i += 1
    used_names.add(candidate_name)
    return os.path.join(staging_root, candidate_name)


# ****************************** Preflight / Aufraeumen ******************************
def preflight_folder(mod4, input_dir):
    """Listet die .laz-Dateien von input_dir (nur oberste Ebene, keine
    Rekursion) und prueft jeden Dateinamen gegen das Kachelname-Muster
    (parse_tile_from_filename aus Script 4). Gibt (gueltige_dateinamen,
    namensfehler) zurueck. Die eigentliche Kachelrahmen-/CRS-Pruefung
    passiert weiterhin erst in convert_tile (braucht die Metadaten aus der
    Datei, nicht nur den Namen) - hier geht es nur um den schnellen
    Namens-Check VOR dem eigentlichen Batch, damit ein falsch benanntes
    Tile nicht erst nach Stunden mitten im Lauf auffaellt."""
    valid_names = []
    errors = []
    for src_path in mod4.find_laz_files(input_dir, recursive=False):
        name = os.path.basename(src_path)
        try:
            mod4.parse_tile_from_filename(name)
            valid_names.append(name)
        except ValueError as e:
            errors.append(str(e))
    return valid_names, errors


def cleanup_stray_tmp_files(input_dir):
    """Entfernt verwaiste Rueckschreibe-Temp-Dateien (Suffix TMP_SUFFIX) aus
    einem fruehen abgebrochenen Lauf (Stromausfall/Ctrl+C zwischen
    shutil.copy2 und os.replace in finalize_tile) - das Zeitfenster dafuer
    ist ein einzelner os.replace-Aufruf, also extrem klein, aber zur
    Sicherheit wird beim Start jedes Laufs pro Ordner aufgeraeumt."""
    try:
        entries = os.listdir(input_dir)
    except OSError as e:
        log(f"  [WARNUNG] Ordner nicht lesbar, Aufraeumen uebersprungen: {input_dir} ({e})")
        return
    for name in entries:
        if name.endswith(TMP_SUFFIX):
            stray_path = os.path.join(input_dir, name)
            try:
                os.remove(stray_path)
                log(f"  [AUFRAEUMEN] verwaiste Temp-Datei entfernt: {stray_path}")
            except OSError:
                pass


# ****************************** Finalisierung pro Tile ******************************
def finalize_tile(name, staging_dir, original_dir):
    """Schreibt eine erfolgreich konvertierte Kachel atomar vom
    Staging-Ordner zurueck ins Original-Verzeichnis (ersetzt damit das
    LAS-1.2-Original) und entfernt die Staging-Kopie danach.

    Ablauf (atomar bzgl. des Original-Verzeichnisses):
      1. shutil.copy2 auf eine Temp-Datei IM Original-Verzeichnis - NICHT
         per shutil.move direkt vom Staging-Laufwerk (Y:), das waere bei
         unterschiedlichen Laufwerken (Y: vs. Original) NICHT atomar,
         sondern Kopie+Loeschen mit Bruchgefahr mittendrin.
      2. Groessen-Check zur Kopie-Vollstaendigkeit (die fachliche
         Validierung - CRS, Punktanzahl, BBox, Classification - ist zu
         diesem Zeitpunkt schon in convert_tile() erfolgt).
      3. os.replace(temp, original_pfad) - atomar, da die Temp-Datei im
         selben Verzeichnis/Laufwerk liegt wie das zu ersetzende Original.
      4. Staging-Kopie entfernen (Platz sofort freigeben, nicht erst am
         Ende des gesamten Ordners/Batches).

    Wirft eine Exception bei jedem Problem - der Aufrufer faengt sie pro
    Tile ab und markiert genau diese Kachel als fehlgeschlagen, ohne den
    Rest des Batches zu gefaehrden.
    """
    staging_path = os.path.join(staging_dir, name)
    original_path = os.path.join(original_dir, name)
    tmp_path = os.path.join(original_dir, name + TMP_SUFFIX)

    shutil.copy2(staging_path, tmp_path)
    try:
        if os.path.getsize(tmp_path) != os.path.getsize(staging_path):
            raise IOError(f"Kopie unvollstaendig (Groesse weicht ab): {tmp_path}")
        os.replace(tmp_path, original_path)
        tmp_path = None
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    os.remove(staging_path)


# ****************************** Ergebnis-Verarbeitung ******************************
def _process_result(name, original_dir, staging_dir, result, dry_run, summary):
    """Loggt das Ergebnis eines einzelnen Tiles und schreibt es bei Erfolg
    atomar zurueck (finalize_tile). Aktualisiert summary in-place.

    Gibt True zurueck, wenn im Staging-Ordner fuer dieses Tile nichts mehr
    zurueckbleibt (sicher fuer die Staging-Aufraeum-Entscheidung in
    run_batch), sonst False - z.B. wenn das Zurueckschreiben selbst
    fehlgeschlagen ist: dann liegt die bereits gueltig konvertierte Datei
    weiterhin im Staging und darf NICHT durch ein rmtree verloren gehen.
    """
    tag = f"{os.path.basename(original_dir)}/{name}"
    for w in result["warnings"]:
        log(f"  [WARNUNG] {tag}: {w}")

    status = result["status"]
    if status == "failed":
        log(f"  [FEHLER] {tag}: {result['error']}")
        summary["failed"] += 1
        summary["failed_files"].append((tag, result["error"]))
        return True  # convert_tile raeumt seine eigene Temp-Datei bei Fehlern selbst auf

    if dry_run:
        log(f"  [DRY-RUN OK] {tag} ({status})")
        if status == "skipped_already_migrated":
            summary["skipped"] += 1
        elif status == "warning":
            summary["warning"] += 1
        else:
            summary["ok"] += 1
        return True

    try:
        finalize_tile(name, staging_dir, original_dir)
    except Exception as e:
        log(f"  [FEHLER beim Zurueckschreiben] {tag}: {e} "
            f"- konvertierte Datei bleibt vorerst in {staging_dir}, Original unveraendert.")
        summary["failed"] += 1
        summary["failed_files"].append((tag, f"Zurueckschreiben fehlgeschlagen: {e}"))
        return False

    if status == "skipped_already_migrated":
        log(f"  {tag}: bereits LAS 1.4 - bestaetigt.")
        summary["skipped"] += 1
    elif status == "warning":
        log(f"  {tag}: OK (mit Warnung)")
        summary["warning"] += 1
    else:
        log(f"  {tag}: OK")
        summary["ok"] += 1
    return True


# ****************************** Batch-Orchestrierung ******************************
def run_batch(mod4, folders, staging_root, target_scale, dry_run, workers, summary):
    """Baut die globale Job-Liste ueber ALLE Ordner und verarbeitet sie in
    EINEM einzigen Worker-Pool (siehe Modul-Docstring, Punkt 4) - haelt die
    Kerne durchgehend ausgelastet, unabhaengig davon, wie viele Tiles ein
    einzelner Ordner beitraegt. Aktualisiert summary in-place.

    Staging-Aufraeumen pro Ordner passiert bereits waehrend des Laufs
    (sobald alle Tiles eines Ordners finalisiert sind), nicht erst am Ende
    des gesamten Batches - wichtig bei vielen Ordnern uebers Wochenende,
    damit das Staging-Laufwerk nicht volllaeuft.
    """
    used_staging_names = set()
    jobs = []  # (src_path, name, staging_dir, original_dir)
    folder_remaining = {}  # original_dir -> Anzahl noch offener Tiles
    folder_staging_dir = {}  # original_dir -> Staging-Pfad
    stranded_folders = set()  # Ordner mit fehlgeschlagenem Zurueckschreiben - Staging NICHT loeschen

    for folder in folders:
        cleanup_stray_tmp_files(folder)
        valid_names, name_errors = preflight_folder(mod4, folder)
        for err in name_errors:
            log(f"  [PREFLIGHT-FEHLER] {folder}: {err}")
            summary["total"] += 1
            summary["failed"] += 1
            summary["failed_files"].append((folder, err))

        if not valid_names:
            continue

        staging_dir = staging_dir_for(folder, staging_root, used_staging_names)
        folder_staging_dir[folder] = staging_dir
        folder_remaining[folder] = len(valid_names)
        for name in valid_names:
            jobs.append((os.path.join(folder, name), name, staging_dir, folder))

    summary["total"] += len(jobs)
    if not jobs:
        return

    if not dry_run:
        for staging_dir in set(folder_staging_dir.values()):
            os.makedirs(staging_dir, exist_ok=True)

    workers = max(1, min(workers, len(jobs)))

    def _finish(name, staging_dir, original_dir, result):
        clean = _process_result(name, original_dir, staging_dir, result, dry_run, summary)
        if not clean:
            stranded_folders.add(original_dir)
        folder_remaining[original_dir] -= 1
        if (not dry_run and folder_remaining[original_dir] == 0
                and original_dir not in stranded_folders):
            shutil.rmtree(staging_dir, ignore_errors=True)

    if dry_run or workers <= 1:
        for src_path, name, staging_dir, original_dir in jobs:
            result = mod4.convert_tile(src_path, staging_dir, target_scale=target_scale, dry_run=dry_run)
            _finish(name, staging_dir, original_dir, result)
    else:
        log(f"Parallelisierung: {workers} gleichzeitige PDAL-Worker ueber "
            f"{len(jobs)} Tiles aus {len(folder_staging_dir)} Ordnern "
            f"(verfuegbare Kerne: {os.cpu_count()}).")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_job = {
                executor.submit(mod4.convert_tile, src_path, staging_dir, target_scale, dry_run):
                    (name, staging_dir, original_dir)
                for src_path, name, staging_dir, original_dir in jobs
            }
            for future in as_completed(future_to_job):
                name, staging_dir, original_dir = future_to_job[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = {"status": "failed", "warnings": [],
                              "error": f"Unerwarteter Fehler im Worker: {e}"}
                _finish(name, staging_dir, original_dir, result)


def main():
    global _log_file_handle

    parser = argparse.ArgumentParser(
        description="Batch-Tool: LAS 1.2 -> LAS 1.4 Inplace-Upgrade ueber viele Ordner "
                    "(Output = Input, Original wird nach Erfolg ersetzt).")
    parser.add_argument("--folder-list", required=True,
                        help=".txt-Datei mit einem Input-/Output-Ordnerpfad pro Zeile "
                             "('#'-Kommentare und Leerzeilen werden ignoriert)")
    parser.add_argument("--staging-root", required=True,
                        help="Staging-Root fuer temporaere Konversions-Ausgabe (z.B. Y:\\...\\Temp) - "
                             "pro Ordner wird ein Unterordner angelegt (Name = letzter Ordnername des Inputs)")
    parser.add_argument("--target-scale", type=float, default=0.01,
                        help="Ziel-Scale in Metern (Default 0.01, siehe Script 4)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur Preflight + Vorschau - nichts stagen/schreiben/verschieben/loeschen")
    parser.add_argument("--workers", type=int, default=None,
                        help="Anzahl gleichzeitiger PDAL-Worker (Default: automatisch, siehe Script 4 "
                             "_default_worker_count). --workers 1 erzwingt seriellen Ablauf.")
    parser.add_argument("--log-file", help="Pfad fuer die Log-Datei (Default: <staging-root>\\logs\\...)")
    args = parser.parse_args()

    if args.workers is not None and args.workers < 1:
        parser.error("--workers muss >= 1 sein.")
    if not os.path.isfile(args.folder_list):
        parser.error(f"--folder-list nicht gefunden: {args.folder_list}")

    mod4 = _lade_script4()

    log_path = args.log_file
    if not log_path:
        log_dir = os.path.join(args.staging_root, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"LAS12_LAS14_inplace_{datetime.now():%Y%m%d_%H%M%S}.log")
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    _log_file_handle = open(log_path, "w", encoding="utf-8")
    # Script-4-eigene log()-Aufrufe (z.B. die Dry-Run-Vorschauzeile in
    # convert_tile) landen dadurch im selben Logfile statt nur auf stdout.
    mod4._log_file_handle = _log_file_handle
    log(f"Log-Datei: {log_path}")

    folders, folder_problems = read_folder_list(args.folder_list)
    log(f"=== LAS 1.2 -> LAS 1.4 Batch-Inplace-Upgrade ===")
    log(f"Ordnerliste: {args.folder_list}")
    log(f"Staging-Root: {args.staging_root}")
    log(f"Dry-Run: {args.dry_run}")
    for p in folder_problems:
        log(f"  [WARNUNG] {p}")
    log(f"{len(folders)} gueltige(r) Ordner.\n")

    if not folders:
        log("Keine gueltigen Ordner - Abbruch.")
        _log_file_handle.close()
        sys.exit(1)

    workers = args.workers or mod4._default_worker_count()
    log(f"Worker: {workers}"
        + (f" (automatisch von {os.cpu_count()} Kernen)" if args.workers is None else " (manuell)") + "\n")

    summary = {"total": 0, "ok": 0, "warning": 0, "skipped": 0, "failed": 0, "failed_files": []}
    start = datetime.now()
    run_batch(mod4, folders, args.staging_root, args.target_scale, args.dry_run, workers, summary)
    duration = datetime.now() - start

    log(f"\n=== Zusammenfassung: {len(folders)} Ordner, {summary['total']} Tiles verarbeitet, "
        f"{summary['ok']} gueltig, {summary['warning']} mit Warnung, "
        f"{summary['skipped']} bereits migriert, {summary['failed']} fehlgeschlagen, "
        f"Laufzeit {duration} ===")

    if summary["failed_files"]:
        log("\nFehlgeschlagene Dateien:")
        for tag, error in summary["failed_files"]:
            log(f"  - {tag}: {error}")

    _log_file_handle.close()
    sys.exit(1 if summary["failed"] else 0)


if __name__ == "__main__":
    main()
