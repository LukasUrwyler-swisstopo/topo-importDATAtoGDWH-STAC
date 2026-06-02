# GDWH & STAC Import Pipeline

Python-Scripts zur automatisierten XML-Generierung, Datenvalidierung und Import-Pipeline nach GDWH und STAC.

---

## Starten

### GUI

<img width="1273" height="1524" alt="grafik" src="https://github.com/user-attachments/assets/77cdad84-0a11-4841-83e2-fd443ddf19ed" />


```
python 0_main_GDWH_import_GUI.py
```
open CMD-Terminal> *python "...\0_main_GDWH_import_GUI.py"*

GUI – alle Felder werden interaktiv ausgefüllt, kein manuelles Script-Editieren nötig.

---

## Beschreibung

Die Scripts automatisieren den gesamten Prozess von der Datenvorbereitung bis zur Integration in STAC. Je nach GDS und Datenformat werden XML-Metadaten generiert, Daten ins korrekte GDWH-Bucket kopiert und nach erfolgreicher Validierung automatisiert nach STAC importiert.

### Script-Übersicht

| Script | Rolle | Direkt ausführbar |
|--------|-------|:-----------------:|
| `0_main_GDWH_import_GUI.py` | **Hauptscript (GUI)** – steuert alle Sub-Scripts über eine Tkinter-Oberfläche | ✓ (normales Python) |
| `1_allGDS_upload_GDWH_withCHECKxml.py` | Sub-Script für `SB_DOP`, `SB_DSM`, `SB_DSM_PUNKTWOLKE` | (direkt möglich, Working Part anpassen) |
| `2_1_SB_DOP_16_FOLDERorganize_by_lineID.py` | Sub-Script – prüft die Line_ID im Quellordner, bereinigt Altdateien und sortiert 16BIT-DOP-Dateien nach LineID in Unterordner | (direkt möglich, Pfad anpassen) |
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
        ├─ [SB_DOP_16 only]  Script 2_1: Line_ID im Quellordner prüfen,
        │                     Altdateien bereinigen, nach LineID in Unterordner sortieren
        │
        ├─ Sicherheitscheck  (Dialog: Metadaten, Pfade, Kontrollfragen bestätigen)
        │
        ├─ Quellordner bereinigen  (nur Nutzdaten behalten – Whitelist pro GDS)
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
> 
> **SB_DSM_PUNKTWOLKE:** kein NoData-Value.
> 
> **`Auftragstyp`:**
> 
>         `kry`: Kryosphäre
> 
>         `ram`: Rapid Mapping
> 
>         `bim`: Biotop Monitoring
> 
>         `mom`: Moor Monitoring
> 
>         `wam`: Wald Monitoring
> 

---

## Unterstützte GDS-Typen

| GDS | Datenformat | Besonderheiten |
|-----|-------------|----------------|
| `SB_DOP` | `.tif` / `.tfw` | TileKey aus Dateiname (zwei Parts vor `_LV95`) |
| `SB_DOP_16` | `.tif` / `.tfw` | 16BIT NRGB; Dateien werden vor Import nach LineID in Unterordner sortiert |
| `SB_DSM` | `.tif` / `.tfw` (DSM + Hillshade) | NoData automatisch per Dateiname; TileKey fix `1000` |
| `SB_DSM_PUNKTWOLKE` | `.laz` | Kein NoData; Kopie in `PrecalculatedFormats\SB_DSM_PUNKTWOLKE` |

---

## Datenbereinigung & Prüfungen

Vor der XML-Generierung wird der Quellordner automatisch bereinigt, damit keine Altbestände (z.B. alte XML, Pyramiden, Indexdateien) mitverarbeitet oder mitkopiert werden. Es gilt eine **Whitelist pro GDS** – alles, was nicht auf der Liste steht, wird gelöscht:

| GDS | Behalten | Gelöscht (Beispiele) |
|-----|----------|----------------------|
| `SB_DOP` / `SB_DOP_16` | `.tif` / `.tiff` / `.tfw` | `.xml`, `.pyr`, `.rdx`, `.ovr`, … |
| `SB_DSM` | `.tif` / `.tiff` / `.tfw` | `.xml`, `.ovr`, `.cpg`, `.dbf`, `.lock`, … |
| `SB_DSM_PUNKTWOLKE` | `.laz` / `.ascii` | `.xml`, `.lax`, `.lasx`, … |

> Die Bereinigung läuft **nach** dem Sicherheitscheck – bei einem Abbruch wird nichts gelöscht. Für unbekannte GDS wird sicherheitshalber nichts entfernt.

**Line_ID-Prüfung (nur SB_DOP_16, Script 2_1):** Bevor Dateien sortiert oder gelöscht werden, prüft das Script, ob die erwartete Line_ID im Quellordner überhaupt vorkommt. Fehlt sie, bricht die Verarbeitung mit einer Meldung ab – der Ordner bleibt unverändert. So fällt ein versehentlich falscher Input-Pfad sofort auf, statt erst später im Ablauf.

---

## Log

Die GUI schreibt bei jedem Import eine Logdatei in den Ordner `logs\` neben dem Script:
```
logs\GDWHimport_{GDS}_{AREA}_{Line_ID}_{YYYYMMDD_HHMMSS}.log
```
`AREA` wird aus dem Zielordner abgeleitet, `Line_ID` ist die erste eingegebene Line_ID.
Der Ordner wird beim ersten Import automatisch erstellt.

Zusätzlich wird ein fortlaufendes **Archiv-Log** geführt – eine einzige Datei, die bei jedem Import um eine Zeile erweitert wird:
```
logs\GDWHimport_archived_AREA_proGDS.log
```
Eintrag pro Import (Zeitstempel + `{GDS}_{AREA}_{Line_ID}`), z.B.:
```
2025-08-16 09:52:30  SB_DOP_GRIES_20250816_0952_12501
2025-08-16 10:14:05  SB_DOP_16_GRIES_20250816_0952_12501
```
So bleibt nachvollziehbar, welche AREAS für welches GDS importiert wurden.

> Die Sub-Scripts (1, 2_2) führen kein eigenes Logfile mehr – ihre Konsolenausgabe wird vollständig von der GUI mitgeschrieben.

---

## Hinweise

**Line_IDs**
- Die erste `Line_ID` **muss** die frühste Befliegungslinie (frühester Aufnahmezeitpunkt) des AOIs sein – sie bestimmt `FirstAcquisitionTime` und `StacItemIdDatetime`. (ausgenommen SB_DOP_16, siehe weiter unten*)
- Mehrere LineIDs auf einmal eingeben: Spalte in Excel markieren → Ctrl+C → ins LineID-Feld klicken → Ctrl+V (jede Zeile wird einzeln validiert).
- *Bei `SB_DOP_16` ist genau **eine** Line_ID erlaubt; alle Fluglinien des Gebiets kommen separat ins Feld `allAreaLineIDs`.

**Pfade**
- Zielpfad muss den GDS-Namen als vorletzten Ordner enthalten (z.B. `…\SB_DSM\2025_AREA_DSM`) – die GUI warnt bei Abweichung.
- Der `AREA`-Name im Log wird aus dem letzten Zielordner abgeleitet (Muster `202X_AREA_TYP`).
- Bei `SB_DOP_16` wird der effektive Quellpfad automatisch um den HHMM-Teil der Line_ID ergänzt (`INPUT_FOLDER\<HHMM>`).

**Vor dem Import prüfen**
- Im Sicherheitscheck müssen **alle** Kontrollfragen bestätigt werden, sonst bleibt der Import-Button gesperrt. Je nach GDS gehören dazu: korrekter Input-Pfad, korrekte Line_IDs, und die NoData-Werte (vorgängig visuell kontrollieren in ApplicationsMaster / ArcGIS / QGIS).
- Bei `SB_DOP_16`: Script 2_1 kann übersprungen werden, falls die Unterordner bereits existieren.

**Nach dem Import**
- Die Validierung via `GDWH/CHECK` (Portal) muss erfolgreich abgeschlossen sein, bevor der Import gestartet wird.
- Der STAC-Import erfolgt automatisch nach erfolgreichem GDWH-Import.
