import os
import shutil
import re
from pathlib import Path
from typing import Optional

# ============================================================
#  KONFIGURATION – hier den Inputordner anpassen, immer prüfen
# ============================================================
INPUT_FOLDER = r"A:\2025\BIRCH\DOP\LV95\DOP_NRGB_16BITS"   # <-- anpassen
EXPECTED_LINE_ID = "1005"   # <-- 4-stellige Line_ID, die im INPUT_FOLDER erwartet wird
# ============================================================


def extract_line_id(filename: str) -> Optional[str]:
    match = re.search(r'(\d{4})NRGB', filename, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def check_line_id_present(input_folder: str, expected_line_id: str) -> bool:
    """Prüft, ob mindestens eine Datei im INPUT_FOLDER die erwartete Line_ID trägt.

    Gibt True zurück wenn gefunden, sonst False. Wirft FileNotFoundError,
    wenn der INPUT_FOLDER selbst nicht existiert.
    """
    input_path = Path(input_folder)

    if not input_path.exists():
        raise FileNotFoundError(f"INPUT_FOLDER nicht gefunden: {input_path}")

    for file in input_path.iterdir():
        if file.is_file() and extract_line_id(file.name) == expected_line_id:
            return True
    return False


def delete_unwanted_files(input_folder: str) -> None:
    """Loescht alle .pyr und .xml Dateien im Inputordner."""
    input_path = Path(input_folder)
    extensions = {".pyr", ".xml", ".rdx"}
    deleted = 0

    for file in input_path.iterdir():
        if file.is_file() and file.suffix.lower() in extensions:
            file.unlink()
            print(f"[GELOESCHT] {file.name}")
            deleted += 1

    if deleted == 0:
        print("Keine .pyr oder .xml Dateien gefunden.")
    else:
        print(f"-> {deleted} Datei(en) geloescht.")
    print()


def organize_files(input_folder: str, dry_run: bool = False) -> None:
    input_path = Path(input_folder)

    if not input_path.exists():
        print(f"[FEHLER] Ordner nicht gefunden: {input_path}")
        return

    files = [f for f in input_path.iterdir() if f.is_file()]

    if not files:
        print("Keine Dateien im Inputordner gefunden.")
        return

    moved   = 0
    skipped = 0
    unknown = 0

    for file in sorted(files):
        line_id = extract_line_id(file.name)

        if line_id is None:
            print(f"[UEBERSPRUNGEN - kein LineID gefunden] {file.name}")
            unknown += 1
            continue

        target_dir = input_path / line_id

        if not dry_run:
            target_dir.mkdir(exist_ok=True)

        target_file = target_dir / file.name

        if target_file.exists():
            print(f"[BEREITS VORHANDEN - uebersprungen] {file.name}  -->  {line_id}/")
            skipped += 1
            continue

        if dry_run:
            print(f"[VORSCHAU] {file.name}  -->  {line_id}/")
        else:
            shutil.move(str(file), str(target_file))
            print(f"[VERSCHOBEN] {file.name}  -->  {line_id}/")

        moved += 1

    print()
    print("=" * 60)
    if dry_run:
        print(f"VORSCHAU abgeschlossen - {moved} Dateien wuerden verschoben")
    else:
        print(f"Fertig - {moved} Dateien verschoben, "
              f"{skipped} uebersprungen, {unknown} ohne LineID")
    print("=" * 60)


if __name__ == "__main__":
    try:
        print("=" * 60)
        print("   LineID Organizer - Dateien nach LineID sortieren")
        print("=" * 60)
        print(f"Inputordner: {INPUT_FOLDER}")
        print()

        # -- Schritt 0: Line_ID-Pruefung ------------------------
        # Abbruch, wenn die erwartete Line_ID nicht im INPUT_FOLDER vorkommt.
        # Laeuft vor dem Loeschen, damit bei falschem Pfad nichts veraendert wird.
        print(f">>> Pruefe, ob Line_ID '{EXPECTED_LINE_ID}' im INPUT_FOLDER vorhanden ist <<<")
        print()
        if not check_line_id_present(INPUT_FOLDER, EXPECTED_LINE_ID):
            print(f"[ABBRUCH] Die line_ID '{EXPECTED_LINE_ID}' wurde im angegebenen "
                  f"INPUT_FOLDER nicht gefunden. Bitte pruefen Sie den gewaehlten Pfad.")
            raise SystemExit(1)
        print(f"-> Line_ID '{EXPECTED_LINE_ID}' gefunden. Verarbeitung wird fortgesetzt.")
        print()

        # -- Schritt 1: .pyr und .xml loeschen ------------------
        print(">>> .pyr und .xml Dateien werden geloescht <<<")
        print()
        delete_unwanted_files(INPUT_FOLDER)

        # -- Schritt 2: Vorschau --------------------------------
        print(">>> VORSCHAU (keine Dateien werden verschoben) <<<")
        print()
        organize_files(INPUT_FOLDER, dry_run=True)
        print()

        # -- Schritt 3: Bestaetigung ----------------------------
        antwort = input("Dateien jetzt wirklich verschieben? (ja/nein): ").strip().lower()
        print()
        if antwort in ("ja", "j", "yes", "y"):
            organize_files(INPUT_FOLDER, dry_run=False)
        else:
            print("Abgebrochen - es wurden keine Dateien verschoben.")

    except Exception as e:
        print()
        print(f"[FEHLER] {e}")

    finally:
        # Fenster bleibt offen bis Enter gedrueckt wird
        print()
        input(">>> Druecke ENTER um das Fenster zu schliessen <<<")
