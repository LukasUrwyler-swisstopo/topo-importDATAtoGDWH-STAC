# GDWH & STAC Import Pipeline

Python-Scripts zur automatisierten XML-Generierung, Datenvalidierung und Import-Pipeline nach GDWH und STAC.

---

## Starten

### GUI

```
python 0_main_GDWH_import_GUI.py
```
open CMD-Terminal> *python "...\0_main_GDWH_import_GUI.py"*

<img width="1276" height="1519" alt="grafik" src="https://github.com/user-attachments/assets/c7212c9c-3f47-4227-9646-ab0120e3b903" />

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
| `test_functions.py` | **Unit-Tests** – prüft die reinen Python-Funktionen ohne externe Abhängigkeiten (kein OSGeo4W nötig) | ✓ (normales Python) |

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
        ├─ NoData-Tag im TIFF setzen  (GDAL SetNoDataValue, pro Band)
        ├─ Interne Maske im TIFF setzen  (GDAL_TIFF_INTERNAL_MASK, 1-bit DEFLATE –
        │   bleibt auch bei späterer JPEG-COG-Ableitung erhalten, da NoData bei
        │   verlustbehafteter Kompression vom COG-Treiber ignoriert wird.
        │   Fail-safe: Maske wird zuerst vollständig im Speicher berechnet,
        │   erst bei Erfolg geschrieben – kein Risiko einer halbfertigen
        │   "alles ungültig"-Maske bei einem GDAL/NumPy-Fehler.)
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
| `Area` | AOI-Name – wird live aus der ersten passenden Datei im Quellordner abgeleitet, ist aber **editierbar**. Ein hier gesetzter Wert überschreibt für den ganzen Lauf die pro-Datei-Ableitung aus dem Dateinamen (`extract_area()`) – wichtig als Absicherung, falls das Dateinamen-Format nicht passt und "Check - NameFormat" übersehen wurde | Freitext, z.B. `PLAINE_MORTE` |
| *(TileKey-Vorschau)* | Reine Diagnoseanzeige (nicht editierbar): TileKey-Beispiel aus der ersten Datei. Wird pro Datei einzeln neu berechnet, nicht überschreibbar. Rote Schrift + Hinweis, wenn das Format nicht `XXXX_YYYY` entspricht (ausser SB_DSM, dort fix `1000`) | – |
| `CustomAttribute` | Beschreibung des Datenprodukts | siehe Auswahlliste |
| `Line_ID` | Befliegungslinien-IDs | `["YYYYMMDD_HHMM_QQQQQ", ...]` – wird von der GUI automatisch chronologisch sortiert (älteste zuoberst) |
| `allAreaLineIDs` | Alle LineIDs des Gebiets *(nur SB_DOP_16)* | `["YYYYMMDD_HHMM_QQQQQ", ...]` |
| `NoData` | NoData-Wert – wird ins XML geschrieben, als GDAL-Tag auf jedes Band des TIFF gesetzt (`SetNoDataValue`) **und** zusätzlich als interne per-Dataset-Maske geschrieben (`tag_mask_on_raster`, siehe unten) | DOP 8BIT RGB: `"0 0 0"` / `"255 255 255"` , DOP 16BIT NRGB: `"0 0 0 0"` / `"65535 65535 65535 65535"` |
| `TerrainModel` | Verwendetes Geländemodell | siehe Auswahlliste |
| `SourceReferenceSystem` | Koordinatensystem | `"(EPSG:2056) CH1903+ / LV95_LN02"` *(fix)* |
| `CameraSystem` | Kamerasystem | `"Leica ADS100"` / `"Leica ADS80"` / `"Leica DMC-4"` |

> **SB_DSM:** NoData wird automatisch gesetzt (`"255"` für Hillshade [1-Band Grayscale], `"-3.4028235e+38"` für DSM-Raster).
> 
> **SB_DSM_PUNKTWOLKE:** kein NoData-Value.
>
> **Warum zusätzlich eine interne Maske?** Die spätere COG-Ableitung im GDWH-Catalog nutzt JPEG-Kompression (verlustbehaftet). Der GDAL-COG-Treiber schreibt dabei bewusst **keinen** NoData-Wert, da ein exakter Pixelwert nach der Kompression nicht mehr garantiert ist. Eine interne per-Dataset-Maske (`GDAL_TIFF_INTERNAL_MASK`, 1-bit DEFLATE) bleibt dagegen verlustfrei erhalten und wird vom COG-Treiber auch bei JPEG korrekt übernommen – daher setzt `tag_mask_on_raster()` diese zusätzlich zum klassischen NoData-Tag.
>
> **Fail-safe-Design (wichtig, siehe Vorfall 22.7.2026):** Ein GDAL/NumPy-ABI-Konflikt in der Ausführungsumgebung liess `ReadAsArray()` mit einer Exception abbrechen, *nachdem* `CreateMaskBand()` bereits eine leere (per Default komplett ungültige) Maske angelegt hatte – Ergebnis war ein COG, der auf map.geo.admin.ch vollständig transparent gerendert wurde. Seither wird die Maske zuerst **vollständig im Speicher** berechnet (`_compute_nodata_mask`) und `CreateMaskBand()`/`WriteArray()` erst bei garantiertem Erfolg aufgerufen. Schlägt die Berechnung fehl, bleibt die TIFF-Datei unverändert. Zusätzlich setzt die GUI beim OSGeo4W-Subprocess `PYTHONNOUSERSITE=1`, damit private User-Site-Packages (z.B. eine per `pip install --user` installierte NumPy-Version) nicht die zur OSGeo4W-`gdal_array`-Bindung passende NumPy-Version überschatten.
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

## Tests

`test_functions.py` prüft die reinen Python-Funktionen der Sub-Scripts, ohne dass OSGeo4W oder echte Dateien benötigt werden. `osgeo`/`gdal` wird als Mock registriert.

```bash
python test_functions.py
python -m pytest test_functions.py -v   # falls pytest installiert
```

| Testklasse | Getestete Funktion | Script(s) |
|---|---|---|
| `TestParseLineId` | `parse_line_id_to_hundredths` | Script 1, Script 2_2 |
| `TestFormatIso8601` | `format_iso8601_hundredths` | Script 1, Script 2_2 |
| `TestFormatStacDatetime` | `format_stac_datetime` | Script 1, Script 2_2 |
| `TestExtractAreaAllGDS` | `extract_area` | Script 1 |
| `TestExtractAreaDop16` | `extract_area` | Script 2_2 |
| `TestExtractTileLv95AllGDS` | `extract_tile_lv95` | Script 1 |
| `TestExtractTileDop16` | `extract_tile` | Script 2_2 |
| `TestGetNodataValue` | `get_nodata_value` | Script 1 |
| `TestTagMaskOnRaster` | `tag_mask_on_raster` (Masken-Logik + Fail-Safe-Verhalten, GDAL via Fake-Dataset simuliert) | Script 1 |
| `TestCreateXmlAreaOverride` | `create_xml` – Area-Override aus GUI-Feld übersteuert dateinamen-basierte Ableitung | Script 1 |
| `TestCsvAppend` | `_csv_append` | Script 1 |
| `TestExtractLineId` | `extract_line_id` | Script 2_1 |
| `TestParseUndFormatKombiniert` | Parse + Format End-zu-End | Script 1 |

---

## Hinweise

**Line_IDs**
- Die erste `Line_ID` bestimmt `FirstAcquisitionTime` und `StacItemIdDatetime` und muss daher die frühste Befliegungslinie (frühester Aufnahmezeitpunkt) des AOIs sein. Die GUI sortiert eingegebene/eingefügte Line_IDs automatisch chronologisch (älteste zuoberst) – manuelles Sortieren vor dem Einfügen ist nicht mehr nötig. (ausgenommen SB_DOP_16, siehe weiter unten*)
- Mehrere LineIDs auf einmal eingeben: Spalte in Excel markieren → Ctrl+C → ins LineID-Feld klicken → Ctrl+V (jede Zeile wird einzeln validiert und die Liste automatisch neu sortiert).
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
