# GDWH & STAC Import Pipeline

Python-Scripts zur automatisierten XML-Generierung, Datenvalidierung und Import-Pipeline nach GDWH und STAC.

---

## Starten

### Empfohlen: GUI

```
python 0_main_GDWH_import_GUI.py
```
cmd>python 0_main_GDWH_import_GUI.py

(Tkinter-Oberfläche – alle Felder werden interaktiv ausgefüllt, kein manuelles Script-Editieren nötig.)

---

## Beschreibung

Die Scripts automatisieren den gesamten Prozess von der Datenvorbereitung bis zur Integration in STAC. Je nach GDS und Datenformat werden XML-Metadaten generiert, Daten ins korrekte GDWH-Bucket kopiert und nach erfolgreicher Validierung automatisiert nach STAC importiert.

### Script-Übersicht

| Script | Rolle | Direkt ausführbar |
|--------|-------|:-----------------:|
| `0_main_GDWH_import_GUI.py` | **Hauptscript (GUI)** – steuert alle Sub-Scripts über eine Tkinter-Oberfläche | ✓ (normales Python) |
| `1_allGDS_upload_GDWH_withCHECKxml.py` | Sub-Script für `SB_DOP`, `SB_DSM`, `SB_DSM_PUNKTWOLKE` | (direkt möglich, Working Part anpassen) |
| `2_1_SB_DOP_16_FOLDERorganize_by_lineID.py` | Sub-Script – organisiert 16BIT-DOP-Dateien nach LineID in Unterordner | (direkt möglich, Pfad anpassen) |
| `2_2_SB_DOP_16_GDS_upload_GDWH_withCHECKxml.py` | Sub-Script für `SB_DOP_16` | (direkt möglich, Working Part anpassen) |
| `_osgeo_runner.py` | **Interner Subprocess-Runner** – wird von der GUI via OSGeo4W Python aufgerufen; nicht direkt starten | – |

> Alle Scripts müssen **im selben Ordner** liegen. `_gdwh_config.json` wird beim ersten Start der GUI automatisch erstellt und speichert den OSGeo4W Python-Pfad.

---

## Ablauf

```
GDWH-Datenpacket erstellen (Portal)
        │
        ▼
Hauptscript starten  (GUI)
        │
        ├─ GDS wählen
        ├─ Meta-Informationen eingeben  (Dropdown / Auswahl)
        ├─ Pfade eingeben  (Quelle / Ziel)
        │
        ├─ [SB_DOP_16 only]  Script 2_1: Dateien nach LineID in Unterordner sortieren
        │
        ├─ Sicherheitscheck  (Dialog: Metadaten, Pfade, CRS-Prüfung bestätigen)
        │
        ├─ XML-Generierung  (pro .tif / .laz)
        ├─ Daten ins Bucket kopieren  (NV-Ordner; PUNKTWOLKE: +PrecalculatedFormats)
        └─ files.csv erstellen  (MD5-Hash, TileKey, WKT-Footprint)
                │
                ▼
        GDWH CHECK  (Portal: Datenpaket prüfen)
                │
                ▼
        GDWH Import  →  STAC-Integration (automatisch)
```

### GDS-Routing

| GDS | Sub-Scripts |
|-----|-------------|
| `SB_DOP` | Script 1 |
| `SB_DOP_16` | Script 2_1 → Script 2_2 |
| `SB_DSM` | Script 1 |
| `SB_DSM_PUNKTWOLKE` | Script 1 |

---

## Voraussetzungen

- **normales Python 3.x** – kein OSGeo4W-Start nötig
  - Die GUI erkennt den OSGeo4W Python-Pfad automatisch (OSGeo4W, QGIS-Installation)
  - Pfad kann über den Button **Ändern…** im GUI manuell gesetzt und wird in `_gdwh_config.json` gespeichert
  - GDAL-abhängige Sub-Scripts (1 und 2_2) werden intern als Subprocess im OSGeo4W Python ausgeführt
- **tkinter** (in Python-Standardbibliothek enthalten)
- Netzwerkzugriff auf GDWH-Bucket (`\\v0t0020a.adr.admin.ch\...\BUCKET_INT\...`)
- Netzwerkzugriff auf Log-Verzeichnis (`\\v0t0020a.adr.admin.ch\...\scrip_logs`)
- **Korrektes Dateinamen-Format** (wird für XML-Generierung zwingend benötigt):

| GDS | Dateinamen-Format |
|-----|-------------------|
| `SB_DOP` | `202X_AREANAME_DOP_..._XXXX_YYYY_LV95.tif` / `.tfw` |
| `SB_DOP_16` | `202X_AREANAME_DOP_..._XXXX_YYYY_LV95.tif` / `.tfw` |
| `SB_DSM` | `202X_AREANAME_DSM_..._LV95_LN02.tif` / `.tfw`  und/oder  `202X_AREANAME_hillshade_..._LV95_LN02.tif` |
| `SB_DSM_PUNKTWOLKE` | `202X_AREANAME_TIN_..._XXXX_YYYY_LV95_LN02.laz` |

> `XXXX_YYYY` = TileKey (z.B. `2601_1136`). `_LV95` muss im Dateinamen vorhanden sein.

---

## Meta-Informationen

Alle Meta-Informationen werden **interaktiv** über das Haupt-Script eingegeben – kein manuelles Editieren der Sub-Scripts nötig.

| Parameter | Beschreibung | Mögliche Werte |
|-----------|-------------|----------------|
| `Auftragstyp` | Art des Auftrags | `kry` / `ram` / `bim` / `mom` / `wam` |
| `CustomAttribute` | Beschreibung des Datenprodukts | siehe Auswahlliste |
| `Line_ID` | Befliegungslinien-IDs | `["YYYYMMDD_HHMM_QQQQQ", ...]` – erste ID = frühste Linie |
| `allAreaLineIDs` | Alle LineIDs des Gebiets *(nur SB_DOP_16)* | `["YYYYMMDD_HHMM_QQQQQ", ...]` |
| `NoData` | NoData-Wert | `"0 0 0"` / `"255 255 255"` (8BIT RGB) · `"0 0 0 0"` / `"65535 65535 65535 65535"` (16BIT NRGB) |
| `TerrainModel` | Verwendetes Geländemodell | siehe Auswahlliste |
| `SourceReferenceSystem` | Koordinatensystem | `"(EPSG:2056) CH1903+ / LV95_LN02"` *(fix)* |
| `CameraSystem` | Kamerasystem | `"Leica ADS100"` / `"Leica ADS80"` / `"Leica DMC-4"` |

> **SB_DSM:** NoData wird automatisch gesetzt (`"255 255 255"` für Hillshade, `"-3.4028235e+38"` für DSM-Raster).
> **SB_DSM_PUNKTWOLKE:** kein NoData-Value.

---

## Unterstützte GDS-Typen

| GDS | Datenformat | Besonderheiten |
|-----|-------------|----------------|
| `SB_DOP` | `.tif` / `.tfw` | TileKey aus Dateiname (zwei Parts vor `_LV95`) |
| `SB_DOP_16` | `.tif` / `.tfw` | 16BIT NRGB; Dateien werden vor Import nach LineID in Unterordner sortiert |
| `SB_DSM` | `.tif` / `.tfw` (DSM + Hillshade) | NoData automatisch per Dateiname; TileKey fix `1000` |
| `SB_DSM_PUNKTWOLKE` | `.laz` | Kein NoData; Kopie in `PrecalculatedFormats\SB_DSM_PUNKTWOLKE` |

---

## Log

Die GUI schreibt bei jedem Import eine Logdatei in den Ordner `logs\` neben dem Script:
```
logs\GDWH_{GDS}_{YYYYMMDD_HHMMSS}.log
```
Der Ordner wird beim ersten Import automatisch erstellt.
Die Sub-Scripts schreiben zusätzlich in den zentralen Netzwerk-Log:
```
\\...adr.admin.ch\...\GDWH_STAC_imports\upload_GDWH\scrip_logs\
```

---

## Hinweise

- Die erste `Line_ID` **muss** die frühste Befliegungslinie (frühester Aufnahmezeitpunkt) des AOIs sein
- **Mehrere LineIDs auf einmal eingeben**: Spalte in Excel markieren → Ctrl+C → ins LineID-Feld klicken → Ctrl+V (jede Zeile wird einzeln validiert)
- Zielpfad muss den GDS-Namen als vorletzten Ordner enthalten (z.B. `…\SB_DSM\2025_AREA_DSM`) – das Haupt-Script warnt bei Abweichung
- Bei `SB_DOP_16`: Script 2_1 kann übersprungen werden, falls Unterordner bereits existieren
- Die Validierung via `GDWH/CHECK` muss erfolgreich abgeschlossen sein, bevor der Import gestartet wird
- Der STAC-Import erfolgt automatisch nach erfolgreichem GDWH-Import
