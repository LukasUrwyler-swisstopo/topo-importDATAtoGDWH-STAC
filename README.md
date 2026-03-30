# GDWH & STAC Import Pipeline

Python-Script zur automatisierten XML-Generierung, Datenvalidierung und Import-Pipeline nach GDWH und STAC.

---

## Beschreibung

Diese Scripts automatisieren den gesamten Prozess von der Datenvorbereitung bis zur Integration in STAC. Je nach GDS und Datenformat werden XML-Metadaten generiert, Daten ins korrekte GDWH-Bucket kopiert und nach erfolgreicher Validierung automatisiert nach STAC importiert.

### Verfügbare Scripts

| Script | Verwendung |
|--------|------------|
| `1_allGDS_upload_GDWH_withCHECKxml.py` | Universalscript für alle GDS-Typen (SB_DOP, SB_DSM, SB_DSM_PUNKTWOLKE) |
| `2_1_SB_DOP_16_FOLDERorganize_by_lineID.py` | Vorbereitung für das "2_2_SB_DOP_16..."-Script; Organisiert und verschiebt die 16BIT Daten im Quell-Pfad, in einzelne Import-Ordner (nach Aufnahmezeitpunkt) |
| `2_2_SB_DOP_16_GDS_upload_GDWH_withCHECKxml.py` | Spezialisiert für GDS `SB_DOP_16` (SB_DOP_16) |

---

## Ablauf

1. **GDWH-Datenpacket** – im GDWH Portal (swisstopo) ein Datenpaket für jedes GDS erstellen, Bucket-Pfad kopieren.
2. **edit-pyScript** – Bucket-Pfad, Quell-Pfad und Meta-Informationen im Script entsprechend anpassen, Script mit osgeo4shell/python  ausführen:
3. **Script** - Script enthält folgende Funktionen:
  4. **Sicherheits-Check** – Vorschau der XML-Attribute anhand einer Beispieldatei; Benutzer bestätigt mit `Y/N`
  5. **XML-Generierung** – Für jede `.tif`/`.tiff`/`.laz`-Datei wird ein XML mit Metadaten erstellt (abhängig von GDS und Dateiname)
  6. **Daten ins Bucket kopieren** – Dateien und XMLs werden in das korrekte GDWH-Bucket (NV-Ordner) kopiert; bei `SB_DSM_PUNKTWOLKE` zusätzlich in `PrecalculatedFormats`
  7. **files.csv erstellen** – Pro Datei wird ein Eintrag mit MD5-Hash, TileKey und WKT-Footprint in `files.csv` geschrieben
  8. **Validierung** – Mit `GDWH/CHECK` werden alle bereitgestellten Daten geprüft
  9. **Import nach GDWH** – Nach erfolgreicher Validierung werden die Daten in GDWH importiert
  10. **Integration nach STAC** – Die importierten Daten werden automatisiert in STAC integriert

---

## Voraussetzungen

- Python 3.x
- **GDAL** (`osgeo`) via OSGeo4Shell
- Zugriff auf GDWH-Bucket (`\\v0t0020a.adr.admin.ch\...\BUCKET_INT\...`)
- Zugriff auf Log-Verzeichnis (`\\v0t0020a.adr.admin.ch\...\scrip_logs`)
- **Korrektes Dateinamen-Format** (wird für XML-Generierung zwingend benötigt):

| GDS | Dateinamen-Format (Beispiel) |
|-----|-----------------------------|
| `SB_DOP` | `202X_AREANAME_DOP_..._XXXX_YYYY_LV95.tif` / `.tfw` |
| `SB_DOP_16` | `202X_AREANAME_DOP_..._XXXX_YYYY_LV95.tif` / `.tfw` |
| `SB_DSM` | `202X_AREANAME_DSM_..._LV95_LN02.tif` / `.tfw` / `202X_AREANAME_hillshade_..._LV95_LN02.tif` / `.tfw` |
| `SB_DSM_PUNKTWOLKE` | `202X_AREANAME_TIN_..._XXXX_YYYY_LV95_LN02.laz` |

> `XXXX_YYYY` entspricht dem TileKey (z.B. `2601_1136`). `_LV95` muss im Dateinamen vorhanden sein (was danach folgt, z.B. `_LN02`, ist für das Script irrelevant).

---

## Konfiguration (Working Part im Script)

Vor dem Ausführen müssen folgende Variablen im Script angepasst werden:

```python
Quelle = r"A:\2025\PROJEKTNAME\DOP\LV95\..."   # Pfad zu den Quelldaten
Ziel   = r"\\server\...\BUCKET_INT\RASTER\SB_DOP\2025_PROJEKTNAME_DOP"  # Zielbucket in GDWH
sowie
meta_info = (Attribut-/ Meta-Informationen korrekt ausfüllen!)
```

### meta_info

| Parameter | Beschreibung | Typische Werte |
|-----------|-------------|----------------|
| `Auftragstyp` | Art des Auftrags | `kry`, `ram`, `bim`, `mom`, `wam` |
| `Line_ID` | Liste der Befliegungslinien-IDs | `["20250919_0947_12501", ...]` |
| `allAreaLineIDs` | Alle LineIDs des Mosaiks (nur SB_DOP_16) | `["20250919_0947_12501", ...]` |
| `NoData` | NoData-Wert des Rasters | `"0 0 0"` (RGB), `"0 0 0 0"` (NRGB 16BIT) |
| `CustomAttribute` | Beschreibung des Datenprodukts | `"Digital OrthoPhoto - Mosaic RGB 8BIT"` |
| `SourceReferenceSystem` | Koordinatensystem | `"(EPSG:2056) CH1903+ / LV95_LN02"` |
| `CameraSystem` | Kamerasystem | `"Leica ADS100"`, `"Leica DMC-4"` |
| `TerrainModel` | Verwendetes Geländemodell | `"Digital Surface Model (DSM photogrammetric autocorrelation)"` |

> **Hinweis:** Bei GDS `SB_DSM` wird NoData automatisch gesetzt (`255 255 255` für Hillshade, `-3.4028235e+38` für DSM). Bei `SB_DSM_PUNKTWOLKE` gibt es kein NoData-Value.

---

## Unterstützte GDS-Typen

| GDS | Datenformat | Besonderheiten |
|-----|------------|----------------|
| `SB_DOP` | `.tif` / `.tfw` | TileKey aus Dateiname (vor `_LV95`) |
| `SB_DOP_16` | `.tif` | 16BIT; separate AcquisitionTimes da kein Mosaik aus versch. Linien |
| `SB_DSM` | `.tif` (DSM + Hillshade) | NoData automatisch; TileKey fix `1000` |
| `SB_DSM_PUNKTWOLKE` | `.laz` | Kein NoData; Kopie in `PrecalculatedFormats` |

---

## Log

Logs werden automatisch erstellt unter:
```
\\v0t0020a.adr.admin.ch\...\GDWH_STAC_imports\upload_GDWH\scrip_logs\
```
Der Log-Dateiname entspricht dem Zielbucket-Ordnernamen (aka. Name des GDWH Datenpackets).

---

## Hinweise

- Der Sicherheits-Check muss mit `Y` bestätigt werden, sonst bricht das Script ab
- Die erste `Line_ID` **muss** die erste Befliegungslinie (frühester Aufnahmezeitpunkt) des AOIs sein
- Die Validierung via `GDWH/CHECK` muss erfolgreich abgeschlossen sein, bevor der Import gestartet wird
- Der STAC-Import erfolgt automatisch nach erfolgreichem GDWH-Import
