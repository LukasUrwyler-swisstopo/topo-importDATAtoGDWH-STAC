#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
5_1_LAS12_FolderCopy_4_BatchProcessing.py

Standalone Tool: kopiert Archiv-Ordner (mit LAS-1.2 .laz-Kacheln) von einem
Quelllaufwerk (z.B. A:\...) gespiegelt in eine Kopie unter
Y:\01_GDWH-STAC_ArchivCopy\..., damit das anschliessende Batch-Upgrade
(siehe 5_LAS12_LAS14_batch_inplace_upgrade.py) NICHT auf den Original-
Archivdaten laeuft, sondern auf dieser Kopie - die LAS-1.2-Originale auf
dem Quelllaufwerk bleiben dadurch unangetastet.

Ablauf:
  1. Pfadliste aus einer .txt-Datei einlesen (ein Quellordner-Pfad pro
     Zeile, '#'-Kommentare und Leerzeilen werden ignoriert) - gleiches
     Format wie bei 5_LAS12_LAS14_batch_inplace_upgrade.py.
  2. Pro Zeile wird aus dem Pfad das "Jahr" ermittelt (= erster Ordnername
     nach dem Laufwerksbuchstaben, z.B. "2024" bei
     "A:\2024\ALETSCH\DSM\LV95_LN02\TIN\thinned_out_04") und der
     Zielordner unter --dest-root nach demselben Unterpfad-Muster
     gespiegelt (Jahresordner wird dabei automatisch mit angelegt):
       A:\2024\ALETSCH\DSM\LV95_LN02\TIN\thinned_out_04
       -> Y:\01_GDWH-STAC_ArchivCopy\2024\ALETSCH\DSM\LV95_LN02\TIN\thinned_out_04
  3. Alle .laz-Dateien (nur oberste Ebene, keine Rekursion) werden von
     Quelle nach Ziel kopiert (shutil.copy2, Zeitstempel bleibt erhalten).
     Dateien, die im Ziel bereits mit identischer Groesse vorliegen,
     werden uebersprungen (Resume nach abgebrochenem Lauf) - ausser
     --overwrite ist gesetzt.
  4. Am Ende wird eine Pfadliste der erzeugten Zielordner geschrieben nach
     --dest-root\Liste_for_Batch_script_LAS12-LAS14\
     <JAHR_1>_..._<JAHR_n>_Liste_for_Batch_scriptLAS12-LAS14.txt
     (Jahre aufsteigend sortiert, eindeutig, mit '_' verbunden). Diese
     Liste kann direkt als --folder-list fuer
     5_LAS12_LAS14_batch_inplace_upgrade.py verwendet werden.

Verwendung (aus dem Projekt-Hauptverzeichnis):
  Testlauf ohne Schreibzugriff (zeigt geplante Zielordner + Dateianzahl):
    python standaloneTools\5_1_LAS12_FolderCopy_4_BatchProcessing.py --pfad-liste LAS12_PfadListe.txt --dry-run

  Kopieren (Default-Ziel: Y:\01_GDWH-STAC_ArchivCopy):
    python standaloneTools\5_1_LAS12_FolderCopy_4_BatchProcessing.py --pfad-liste LAS12_PfadListe.txt

  LAS12_PfadListe.txt Beispiel (ein Quellordner-Pfad pro Zeile):
    A:\2024\ALETSCH\DSM\LV95_LN02\TIN\thinned_out_04
    A:\2024\RANDA\DSM\LV95_LN02\TIN\thinned_out_04
    A:\2025\ANDERE_AOIs1\DSM\LV95_LN02\TIN\thinned_out_04
    # Kommentarzeilen und Leerzeilen werden ignoriert

Konkret im OSGeo4W-Terminal, im Projekt-Hauptverzeichnis ausgefuehrt (Beispiel mit "--dry-run"):

python "U:\05_pyScripts\01_Tools\1_topo-importDATAtoGDWH-STAC\standaloneTools\5_1_LAS12_FolderCopy_4_BatchProcessing.py" --pfad-liste "Y:\01_GDWH-STAC_ArchivCopy\Archiv_LAZ_input_Pfade_txt\Archiv_input_test.txt" --dry-run
"""

import argparse
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

LISTE_UNTERORDNER = "Liste_for_Batch_script_LAS12-LAS14"

# ****************************** Log-Funktion ******************************
_log_file_handle = None


def log(message):
    print(message, flush=True)
    if _log_file_handle:
        _log_file_handle.write(message + "\n")
        _log_file_handle.flush()


# ****************************** Pfadliste ******************************
def read_pfad_liste(list_path):
    """Liest die .txt-Pfadliste (ein Quellordner-Pfad pro Zeile,
    '#'-Kommentare und Leerzeilen ignoriert). Gibt (gueltige_ordner,
    probleme) zurueck - gueltige_ordner als deduplizierte Liste
    normalisierter Pfade in urspruenglicher Reihenfolge; probleme als
    Liste von Meldungsstrings (fehlender Ordner, Duplikat). Bricht NICHT
    ab - der Aufrufer loggt die Probleme nur und verarbeitet den Rest der
    Liste normal weiter."""
    problems = []
    seen = set()
    folders = []
    # utf-8-sig statt utf-8: entfernt eine evtl. vorhandene BOM (Windows-
    # Notepad speichert .txt standardmaessig als UTF-8-mit-BOM), sonst wuerde
    # der erste Pfad der Liste durch das BOM-Zeichen unauffindbar werden.
    with open(list_path, "r", encoding="utf-8-sig") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            path = os.path.normpath(line)
            if path in seen:
                problems.append(f"Duplikat uebersprungen: {path}")
                continue
            seen.add(path)
            if not os.path.isdir(path):
                problems.append(f"Quellordner nicht gefunden, uebersprungen: {path}")
                continue
            folders.append(path)
    return folders, problems


def jahr_und_relpfad(src_folder):
    """Ermittelt aus einem Quellpfad (z.B. 'A:\\2024\\ALETSCH\\DSM\\...')
    das Jahr (= erster Ordnername nach dem Laufwerksbuchstaben) und den
    relativen Unterpfad ab diesem Ordner (inkl. Jahresordner selbst), der
    unveraendert unter --dest-root gespiegelt wird. Wirft ValueError, wenn
    der Pfad keinen Unterordner nach dem Laufwerk enthaelt."""
    _drive, rest = os.path.splitdrive(src_folder)
    rel = rest.lstrip("\\/")
    if not rel:
        raise ValueError(f"Kein Unterordner nach Laufwerk gefunden: {src_folder}")
    jahr = rel.split(os.sep)[0]
    return jahr, rel


# ****************************** Dateikopie ******************************
def find_laz_files(folder):
    """Listet die .laz-Dateien eines Ordners (nur oberste Ebene, keine
    Rekursion, Gross-/Kleinschreibung der Endung egal)."""
    try:
        entries = os.listdir(folder)
    except OSError as e:
        log(f"  [FEHLER] Ordner nicht lesbar: {folder} ({e})")
        return []
    return sorted(
        os.path.join(folder, n) for n in entries
        if n.lower().endswith(".laz") and os.path.isfile(os.path.join(folder, n))
    )


def copy_one_file(src_path, dest_path, overwrite):
    """Kopiert eine einzelne .laz-Datei (shutil.copy2, erhaelt Zeitstempel).
    Ueberspringt die Kopie, wenn im Ziel bereits eine Datei mit identischer
    Groesse liegt (Resume nach abgebrochenem Lauf) - ausser overwrite=True.
    Gibt 'copied' oder 'skipped_identical' zurueck; wirft eine Exception
    bei Kopierfehlern (vom Aufrufer pro Datei abgefangen)."""
    if not overwrite and os.path.isfile(dest_path):
        if os.path.getsize(dest_path) == os.path.getsize(src_path):
            return "skipped_identical"
    shutil.copy2(src_path, dest_path)
    return "copied"


def _logge_status(tag, status, summary):
    if status == "copied":
        summary["files_copied"] += 1
    else:
        log(f"  [UEBERSPRUNGEN] {tag}: bereits vorhanden (identische Groesse)")
        summary["files_skipped"] += 1


def _kopiere_und_logge(src_path, dest_path, tag, overwrite, summary):
    try:
        status = copy_one_file(src_path, dest_path, overwrite)
        _logge_status(tag, status, summary)
    except Exception as e:
        log(f"  [FEHLER] {tag}: {e}")
        summary["files_failed"] += 1
        summary["failed_files"].append((tag, str(e)))


# ****************************** Batch-Orchestrierung ******************************
def run_copy(folders, dest_root, overwrite, dry_run, workers, summary):
    """Baut pro Quellordner den gespiegelten Zielordner, sammelt alle
    .laz-Dateien ueber ALLE Ordner in EINER Job-Liste (haelt die Kopier-
    Worker durchgehend ausgelastet) und kopiert sie. Gibt die Liste der
    tatsaechlich verarbeiteten Zielordner zurueck (Reihenfolge wie
    folders) - im dry_run werden nur die geplanten Zielordner ermittelt,
    ohne etwas anzulegen oder zu kopieren."""
    processed_dest_folders = []
    jobs = []  # (src_path, dest_path, tag)

    for src_folder in folders:
        try:
            jahr, rel = jahr_und_relpfad(src_folder)
        except ValueError as e:
            log(f"  [FEHLER] {e}")
            summary["folders_failed"] += 1
            continue

        dest_folder = os.path.join(dest_root, rel)
        laz_files = find_laz_files(src_folder)
        if not laz_files:
            log(f"  [WARNUNG] Keine .laz-Dateien gefunden, uebersprungen: {src_folder}")
            summary["folders_failed"] += 1
            continue

        log(f"  {src_folder}  (Jahr: {jahr})")
        log(f"    -> {dest_folder}  [{len(laz_files)} .laz-Datei(en)]")
        processed_dest_folders.append(dest_folder)
        summary["folders_ok"] += 1
        summary["files_total"] += len(laz_files)

        if dry_run:
            continue

        os.makedirs(dest_folder, exist_ok=True)
        for src_path in laz_files:
            dest_path = os.path.join(dest_folder, os.path.basename(src_path))
            tag = f"{os.path.basename(dest_folder)}/{os.path.basename(src_path)}"
            jobs.append((src_path, dest_path, tag))

    if dry_run or not jobs:
        return processed_dest_folders

    workers = max(1, min(workers, len(jobs)))
    if workers == 1:
        for src_path, dest_path, tag in jobs:
            _kopiere_und_logge(src_path, dest_path, tag, overwrite, summary)
    else:
        log(f"Parallelisierung: {workers} gleichzeitige Kopier-Worker ueber {len(jobs)} Dateien.")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_job = {
                executor.submit(copy_one_file, src_path, dest_path, overwrite): (src_path, dest_path, tag)
                for src_path, dest_path, tag in jobs
            }
            for future in as_completed(future_to_job):
                src_path, dest_path, tag = future_to_job[future]
                try:
                    status = future.result()
                    _logge_status(tag, status, summary)
                except Exception as e:
                    log(f"  [FEHLER] {tag}: {e}")
                    summary["files_failed"] += 1
                    summary["failed_files"].append((tag, str(e)))

    return processed_dest_folders


# ****************************** Ergebnisliste schreiben ******************************
def schreibe_ergebnisliste(dest_root, dest_folders):
    """Schreibt die Liste der erzeugten Zielordner nach
    <dest_root>\\Liste_for_Batch_script_LAS12-LAS14\\<Jahre>_Liste_for_Batch_scriptLAS12-LAS14.txt
    (Jahre = aufsteigend sortierte, eindeutige Jahresordner aus
    dest_folders, mit '_' verbunden - unabhaengig von der Reihenfolge in
    der urspruenglichen Pfadliste, damit der Dateiname reproduzierbar
    ist). Gibt den geschriebenen Dateipfad zurueck."""
    jahre = sorted({os.path.relpath(p, dest_root).split(os.sep)[0] for p in dest_folders})
    dateiname = "_".join(jahre) + "_Liste_for_Batch_scriptLAS12-LAS14.txt"
    ziel_ordner = os.path.join(dest_root, LISTE_UNTERORDNER)
    os.makedirs(ziel_ordner, exist_ok=True)
    ziel_datei = os.path.join(ziel_ordner, dateiname)
    with open(ziel_datei, "w", encoding="utf-8") as f:
        for folder in dest_folders:
            f.write(folder + "\n")
    return ziel_datei


def main():
    global _log_file_handle

    parser = argparse.ArgumentParser(
        description="Kopiert Archiv-Ordner mit LAS-1.2 .laz-Kacheln von einem "
                    "Quelllaufwerk (z.B. A:\\...) gespiegelt nach --dest-root, "
                    "als Vorbereitung fuer 5_LAS12_LAS14_batch_inplace_upgrade.py.")
    parser.add_argument("--pfad-liste", required=True,
                        help=".txt-Datei mit einem Quellordner-Pfad pro Zeile "
                             "('#'-Kommentare und Leerzeilen werden ignoriert)")
    parser.add_argument("--dest-root", default=r"Y:\01_GDWH-STAC_ArchivCopy",
                        help=r"Ziel-Root, unter dem die Ordnerstruktur gespiegelt wird "
                             r"(Default: Y:\01_GDWH-STAC_ArchivCopy)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Erzwingt das erneute Kopieren, auch wenn im Ziel bereits "
                             "eine Datei mit identischer Groesse liegt")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur Vorschau - nichts anlegen/kopieren/schreiben")
    parser.add_argument("--workers", type=int, default=4,
                        help="Anzahl gleichzeitiger Kopier-Worker (Default: 4)")
    parser.add_argument("--log-file", help="Pfad fuer die Log-Datei (Default: <dest-root>\\logs\\...)")
    args = parser.parse_args()

    if args.workers < 1:
        parser.error("--workers muss >= 1 sein.")
    if not os.path.isfile(args.pfad_liste):
        parser.error(f"--pfad-liste nicht gefunden: {args.pfad_liste}")

    log_path = args.log_file
    if not log_path:
        log_dir = os.path.join(args.dest_root, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"LAS12_FolderCopy_{datetime.now():%Y%m%d_%H%M%S}.log")
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    _log_file_handle = open(log_path, "w", encoding="utf-8")
    log(f"Log-Datei: {log_path}")

    folders, folder_problems = read_pfad_liste(args.pfad_liste)
    log("=== LAS 1.2 Archiv-Ordnerkopie ===")
    log(f"Pfadliste: {args.pfad_liste}")
    log(f"Ziel-Root: {args.dest_root}")
    log(f"Dry-Run: {args.dry_run}")
    for p in folder_problems:
        log(f"  [WARNUNG] {p}")
    log(f"{len(folders)} gueltige(r) Quellordner.\n")

    if not folders:
        log("Keine gueltigen Quellordner - Abbruch.")
        _log_file_handle.close()
        sys.exit(1)

    summary = {"folders_ok": 0, "folders_failed": len(folder_problems), "files_total": 0,
               "files_copied": 0, "files_skipped": 0, "files_failed": 0, "failed_files": []}
    start = datetime.now()
    dest_folders = run_copy(folders, args.dest_root, args.overwrite, args.dry_run, args.workers, summary)
    duration = datetime.now() - start

    total_quellordner = len(folders) + len(folder_problems)
    log(f"\n=== Zusammenfassung: {total_quellordner} Quellordner, {summary['folders_ok']} verarbeitet, "
        f"{summary['folders_failed']} uebersprungen/fehlgeschlagen, "
        f"{summary['files_total']} .laz-Dateien, {summary['files_copied']} kopiert, "
        f"{summary['files_skipped']} bereits vorhanden, {summary['files_failed']} fehlgeschlagen, "
        f"Laufzeit {duration} ===")

    if summary["failed_files"]:
        log("\nFehlgeschlagene Dateien:")
        for tag, error in summary["failed_files"]:
            log(f"  - {tag}: {error}")

    if args.dry_run:
        log("\n[DRY-RUN] Ergebnisliste wird nicht geschrieben.")
    elif dest_folders:
        liste_pfad = schreibe_ergebnisliste(args.dest_root, dest_folders)
        log(f"\nErgebnisliste fuer Batch-Script geschrieben: {liste_pfad}")
    else:
        log("\nKeine Zielordner erzeugt - Ergebnisliste wird nicht geschrieben.")

    _log_file_handle.close()
    sys.exit(1 if (summary["files_failed"] or summary["folders_failed"]) else 0)


if __name__ == "__main__":
    main()
