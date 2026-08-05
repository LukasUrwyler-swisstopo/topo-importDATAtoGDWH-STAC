# GDWH & STAC Import Pipeline

Python-Scripts zur automatisierten XML-Generierung, Datenvalidierung und Import-Pipeline nach GDWH und STAC.

---

## Starten

### GUI

```
python 0_main_GDWH_import_GUI.py
```
open CMD-Terminal> *python "...\0_main_GDWH_import_GUI.py"*

<img width="452" height="617" alt="image" src="https://github.com/user-attachments/assets/1153f60c-7824-451e-a4dd-6f8c2e023e9b" />


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
| `3_fix_false_nodata_dop.py` | Sub-Script (optional, nur `SB_DOP`) – korrigiert falsche NoData-Pixel (einzelne/kleine 0,0,0- bzw. 255,255,255-Gruppen innerhalb der Nutzdaten) und setzt dabei gleich die Flag Mask; läuft **in-place** auf den Quelldateien, **vor** Script 1 | ✓ (eigenständiges CLI, siehe Skript-Docstring) |
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
        ├─ Datenpacket in GDWH erstellen  (Button "GDWH-PROD" / "GDWH-INT" –
        │   öffnet den GDWH-Catalog-Import-Link des gewählten GDS im
        │   Standardbrowser, zum Anlegen des Datenpakets im Portal)
        ├─ Meta-Informationen eingeben  (Dropdown / Auswahl)
        ├─ Pfade eingeben  (Quelle / Ziel)
        │
        ├─ [SB_DOP_16 only]  Script 2_1: Line_ID im Quellordner prüfen,
        │                     Altdateien bereinigen, nach LineID in Unterordner sortieren
        │
        ├─ Sicherheitscheck  (Dialog: Metadaten, Pfade, Kontrollfragen bestätigen)
        │
        ├─ [SB_DOP only, optional – Checkbox]  Script 3: falsche NoData-Pixel
        │   (kleine 0,0,0- / 255,255,255-Gruppen INNERHALB der Nutzdaten)
        │   korrigieren + Flag Mask direkt setzen – in-place auf den
        │   Quelldateien, noch vor der Quellordner-Bereinigung
        │   (bei NoData 255,255,255: echte NoData-Pixel zusaetzlich auf
        │   0,0,0 normalisiert, siehe unten)
        │
        ├─ Quellordner bereinigen  (nur Nutzdaten behalten – Whitelist pro GDS)
        ├─ XML-Generierung  (pro .tif / .laz)
        ├─ NoData-Tag im TIFF setzen  (GDAL SetNoDataValue, pro Band)
        ├─ Interne Maske im TIFF setzen  (GDAL_TIFF_INTERNAL_MASK, 1-bit DEFLATE –
        │   bleibt auch bei späterer JPEG-COG-Ableitung erhalten, da NoData bei
        │   verlustbehafteter Kompression vom COG-Treiber ignoriert wird.
        │   Fail-safe: Maske wird zuerst vollständig im Speicher berechnet,
        │   erst bei Erfolg geschrieben – kein Risiko einer halbfertigen
        │   "alles ungültig"-Maske bei einem GDAL/NumPy-Fehler.
        │   AUSNAHME 1: SB_DSM DSM-Raster [nicht Hillshade] – dort NUR NoData-Tag,
        │   keine Maske, siehe Vorfall 23.7.2026.
        │   AUSNAHME 2: SB_DOP mit aktivierter Vorkorrektur – die Maske wurde
        │   bereits von Script 3 gesetzt und wird hier übersprungen; der
        │   NoData-Tag wird trotzdem wie gehabt gesetzt.
        │   Bei NoData 255,255,255 (und Checkbox NICHT aktiviert): echte
        │   NoData-Pixel werden hier ebenfalls zusaetzlich auf 0,0,0
        │   normalisiert, siehe unten.)
        ├─ Daten ins Bucket kopieren  (NV-Ordner; PUNKTWOLKE: +PrecalculatedFormats)
        └─ files.csv erstellen  (MD5-Hash, TileKey, WKT-Footprint)
                │
                ▼
        GDWH CHECK  (Portal: Datenpaket prüfen)
                │
                ▼
        GDWH Import  →  STAC-Integration (automatisch)
```

> **Hinweis Verarbeitungsreihenfolge:** NoData-Tag und Maske werden auf den Dateien im **Quellordner** gesetzt (bevor `create_and_copy_order()` sie ins Bucket kopiert) – die Kopie im Bucket ist danach identisch zur (bereits veränderten) Quelldatei. Die Original-Rasterdateien auf dem Quelllaufwerk werden also direkt mitverändert, nicht nur eine Kopie im Bucket. Bei SB_DOP mit aktivierter Vorkorrektur (Script 3) gilt das bereits einen Schritt früher: auch die Pixelwerte selbst werden in-place im Quellordner korrigiert, bevor Script 1 überhaupt startet.

### GDS-Routing

| GDS | Sub-Scripts |
|-----|-------------|
| `SB_DOP` | *(optional, Checkbox)* Script 3 → Script 1 |
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
| Reihenfolge im GUI | Parameter | Beschreibung | Mögliche Werte |
|:---:|-----------|-------------|----------------|
| 1 | `Auftragstyp` | Art des Auftrags | `kry` / `ram` / `bim` / `mom` / `wam` |
| 2 | `Area` | AOI-Name – wird live aus der ersten passenden Datei im Quellordner abgeleitet, ist aber **editierbar**. Ein hier gesetzter Wert überschreibt für den ganzen Lauf die pro-Datei-Ableitung aus dem Dateinamen (`extract_area()`) – wichtig als Absicherung, falls das Dateinamen-Format nicht passt und "Check - NameFormat" übersehen wurde | Freitext, z.B. `PLAINE_MORTE` |
| 3 | *(TileKey-Vorschau)* | Reine Diagnoseanzeige (nicht editierbar): TileKey-Beispiel aus der ersten Datei. Erscheint erst, sobald ein gültiger Quellordner gesetzt ist. Wird pro Datei einzeln neu berechnet, nicht überschreibbar. Rote Schrift + Hinweis, wenn das Format nicht `XXXX_YYYY` entspricht (ausser SB_DSM, dort fix `1000`) | – |
| 4 | `NoData` | NoData-Wert – für SB_DOP/SB_DOP_16 dient der hier gewählte Wert nur noch als **Quellwert für die Maskenberechnung** (`tag_mask_on_raster`, siehe unten). Der tatsächlich als GDAL-Tag (`SetNoDataValue`) **und** im XML `<NoData>` geschriebene Wert wird immer auf `"0 0 0"` / `"0 0 0 0"` normalisiert (`normalize_nodata_for_output`, siehe unten) | DOP 8BIT RGB: `"0 0 0"` / `"255 255 255"` , DOP 16BIT NRGB: `"0 0 0 0"` / `"65535 65535 65535 65535"` |
| 4a | `FixFalseNodata` *(nur SB_DOP, Checkbox neben NoData)* | "Falsche NoData-Pixel in Nutzdaten vorkorrigieren" – siehe eigener Abschnitt [Vorkorrektur falscher NoData-Pixel](#vorkorrektur-falscher-nodata-pixel-sb_dop-optional) unten. Standardmässig **aktiviert** | `True` / `False` |
| 5 | `TerrainModel` | Verwendetes Geländemodell | siehe Auswahlliste |
| 6 | `CameraSystem` | Kamerasystem | `"Leica ADS100"` / `"Leica ADS80"` / `"Leica DMC-4"` |
| 7 | `SourceReferenceSystem` | Koordinatensystem | `"(EPSG:2056) CH1903+ / LV95_LN02"` *(fix)* |
| 8 | `CustomAttribute` | Beschreibung des Datenprodukts | siehe Auswahlliste |
| 9 | `Line_ID` | Befliegungslinien-IDs | `["YYYYMMDD_HHMM_QQQQQ", ...]` – wird von der GUI automatisch chronologisch sortiert (älteste zuoberst) |
| 10 | `allAreaLineIDs` | Alle LineIDs des Gebiets *(nur SB_DOP_16, direkt nach Line_ID)* | `["YYYYMMDD_HHMM_QQQQQ", ...]` |

> **IMPORT STARTEN** bleibt deaktiviert, bis Quellordner, Zielordner, Area, NoData *(ausser SB_DSM/SB_DSM_PUNKTWOLKE)*, TerrainModel, CameraSystem und Line_ID *(bzw. zusätzlich allAreaLineIDs bei SB_DOP_16)* alle einen Eintrag haben.

> **SB_DSM:** NoData wird automatisch gesetzt (`"255"` für Hillshade [1-Band Grayscale], `"-3.4028235e+38"` für DSM-Raster).
> 
> **SB_DSM_PUNKTWOLKE:** kein NoData-Value.
>
> **Warum zusätzlich eine interne Maske?** Die spätere COG-Ableitung im GDWH-Catalog nutzt JPEG-Kompression (verlustbehaftet). Der GDAL-COG-Treiber schreibt dabei bewusst **keinen** NoData-Wert, da ein exakter Pixelwert nach der Kompression nicht mehr garantiert ist. Eine interne per-Dataset-Maske (`GDAL_TIFF_INTERNAL_MASK`, 1-bit DEFLATE) bleibt dagegen verlustfrei erhalten und wird vom COG-Treiber auch bei JPEG korrekt übernommen – daher setzt `tag_mask_on_raster()` diese zusätzlich zum klassischen NoData-Tag.
>
> **Ausnahme SB_DSM DSM-Raster (nicht Hillshade, Vorfall 23.7.2026):** Hier wird `tag_mask_on_raster()` bewusst **nicht** aufgerufen – nur der klassische NoData-Tag wird gesetzt (wie vor Einführung der Maske). Die Maske führte bei diesem Rasterformat zu falscher/inkonsistenter NoData-Darstellung im STAC-Kartenviewer und in QGIS (teils schwarze, teils weisse NoData-Pixel). Für SB_DSM-Hillshade bleibt die Maske weiterhin aktiv, da sie dort visuell korrekt ist.
>
> **NoData-Normalisierung bei SB_DOP/SB_DOP_16 auf 0 (Vorfall 23.7.2026, per Test verifiziert):** Wird im GUI `"weiss"` gewählt, entstand in der STAC-Pipeline dasselbe Problem wie oben, nur mit vertauschten Farben: Der externe VRT-Merge-Schritt (Patriks STAC-Pipeline) füllt Bounding-Box-Lücken zwischen Kacheln (Bereiche ganz ohne Quelldatei) mit dem XML-`<NoData>`-Wert – bei `"weiss"` also weiss. Innerhalb einer Kachel maskierte Pixel werden beim `gdal_translate`-Schritt dagegen unabhängig davon als 0 (schwarz) interpretiert. Resultat: zwei unterschiedliche NoData-Farben im selben Bild (weisse Lücken zwischen Kacheln, schwarze Maske innerhalb). Deshalb: `"weiss"`/`"schwarz"` im GUI bestimmt weiterhin, mit welchem Quellwert die Maske berechnet wird (muss dem tatsächlichen Pixelwert im Ausgangsmaterial entsprechen) – der geschriebene GDAL-Tag und der XML-`<NoData>`-Wert werden davon unabhängig **immer** auf `"0 0 0"` (bzw. `"0 0 0 0"`) normalisiert (`normalize_nodata_for_output()`), damit VRT-Lücken und interne Maske dieselbe Farbe ergeben. Die Original-Quelldateien werden dabei mitverändert (siehe Hinweis zur Verarbeitungsreihenfolge unten) – das gilt als Verbesserung der Quelldaten, nicht als Nebenwirkung.
>
> **`FixFalseNodata` (SB_DOP, optional):** Ist die Checkbox aktiv, läuft vor Script 1 zusätzlich Script 3 (`3_fix_false_nodata_dop.py`) **in-place** auf den Quelldateien. Es korrigiert einzelne/kleine Pixelgruppen mit dem gewählten NoData-Wert (0,0,0 bzw. 255,255,255) INNERHALB der Nutzdaten (radiometrischer Zufallstreffer, z.B. dunkle Schattenzonen oder überstrahlte Flächen), die sonst fälschlich als NoData maskiert würden. Echte, grosse NoData-Flächen (Kachelrand) bleiben unverändert. Details siehe [Vorkorrektur falscher NoData-Pixel](#vorkorrektur-falscher-nodata-pixel-sb_dop-optional) unten.
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

## Vorkorrektur falscher NoData-Pixel (SB_DOP, optional)

DOP-Mosaike enthalten teils vereinzelte Pixel oder kleine Pixelgruppen, die durch die Radiometrie zufällig auf den NoData-Wert (0,0,0 bzw. 255,255,255) fallen – z.B. in sehr dunklen Schattenzonen oder überstrahlten Flächen –, obwohl sie eigentlich gültige Nutzdaten sind. Die normale Maskenberechnung (`_compute_nodata_mask`) vergleicht pro Pixel exakt gegen den NoData-Wert und würde solche Pixel fälschlich als NoData maskieren.

`3_fix_false_nodata_dop.py` unterscheidet "echtes" von "falschem" NoData über bis zu fünf Stufen (Connected-Component-Labeling, jede Stufe nur geprüft, wenn die vorherige erfüllt ist):

  - **A) Grösse** der zusammenhängenden Pixelgruppe ≥ Schwelle (25'000 Pixel, Default). Kleinere Gruppen sind sofort "falsch".
  - **B/C) Randkontakt**: Gruppe muss einen Tile-Rand berühren, und zwar über mindestens `--min-border-contact` Pixel (Default 100, Summe über alle vier Kanten). Verhindert, dass grosse, zufällig an den Rand grenzende Gruppen (z.B. überstrahlte Gletscherflächen) allein wegen der Grösse als echt durchgehen.
  - **D) Randverlauf** (`--enable-gradient-check`, **standardmässig deaktiviert**): am inneren (nicht auf dem Tile-Rand liegenden) Gruppenrand werden die angrenzenden Nutzdaten-Pixel geprüft. Ein harter Übergang spricht für echtes NoData, ein weicher Übergang (Nachbarpixel selbst schon nahe am NoData-Wert) kippt die Gruppe zurück zu "falsch".
  - **E) Bounding-Box-Füllgrad** (`--enable-fill-ratio-check`, **standardmässig deaktiviert**, wirkt nur zusammen mit D): kompakte, block-/keilförmige Gruppen bleiben "echt"; dünne, verzweigte Formen werden trotz A-D als "falsch" verworfen.

> **Bekannte Einschränkung (Vorfall WALLIS_SAASTAL, 05.08.2026):** Stufe D nahm bei einem Mosaik aus 13 Befliegungslinien riesige, echte NoData-Flächen (mehr als die Hälfte eines Tiles) fälschlich als "falsch" an und hob deren Pixelwerte an, statt sie als NoData zu maskieren – vermutlich weil die reale Mosaik-/Perimeterkante dort weich ausgeblendet (Feathering aus der Photogrammetrie-Produktion) statt hart geschnitten ist. Die Annahme "weicher Übergang = Überstrahlung, harter Schnitt = echtes NoData" gilt also nicht für jeden Datensatz. **Stufe D und E sind deshalb bis auf Weiteres deaktiviert**, Klassifikation basiert nur auf A-C. Die beiden CLI-Flags bleiben zu Testzwecken erhalten, bis eine für Feathering robuste Alternative gefunden ist.

Als "falsch" erkannte Gruppen werden um `--increment` Werte (Default **7**) vom NoData-Wert weg verschoben (0,0,0 → 7,7,7 bzw. 255,255,255 → 248,248,248) statt unverändert als NoData maskiert zu bleiben.

### Historische 255er-NoData-DOPs: Pixelwerte auf 0,0,0 normalisieren

Wählt man im GUI-Dropdown `NoData der Quelldaten` = `255 255 255` (GDS `SB_DOP`), werden zusätzlich alle als **echtes** NoData erkannten Pixel (nicht die "falschen" Gruppen von oben) direkt im Raster von 255,255,255 auf 0,0,0 umgeschrieben – im selben Lese-/Schreibdurchgang, der die Flag Mask ohnehin schon berechnet, also ohne zusätzlichen I/O:

- **Checkbox "falsche NoData..." aktiv:** Umschreiben erfolgt in Script 3 (`process_tile`, Parameter `rewrite_real_nodata_to_zero`), direkt neben dem Schreiben der Flag Mask.
- **Checkbox nicht aktiv:** Umschreiben erfolgt in Script 1 (`tag_mask_on_raster`/`_compute_nodata_mask`, gleicher Parametername), während die Maske dort ohnehin frisch berechnet wird.

Damit sind Pixelwerte, Flag Mask und NoData-Tag/XML (dieser ist bei SB_DOP bereits immer auf `0` normalisiert, siehe oben) durchgehend konsistent, unabhängig vom historischen NoData-Wert der Quelldaten. Bei NoData-Wahl `0 0 0` ändert sich nichts (Umschreiben auf denselben Wert wäre wirkungslos).

Aktiviert man im GUI die Checkbox neben `NoData` (nur bei GDS `SB_DOP`, standardmässig **aktiviert**), läuft dieser Ablauf automatisch **vor** Script 1, in-place auf den Quelldateien:

1. **Alte Flag Mask/NoData-Tag entfernen** (`strip_existing_mask`) – falls eine Datei bereits eine (falsch berechnete) Maske trägt, z.B. aus einem früheren Lauf.
2. **Pixel korrigieren** wie oben beschrieben.
3. **Flag Mask direkt mitschreiben** (`write_mask`) – die Maske ist rechnerisch äquivalent zu einer Neuberechnung auf der bereits korrigierten Datei (echtes NoData = Pixel, die nach der Korrektur weiterhin dem NoData-Wert entsprechen), wird aber im selben Schreibvorgang gesetzt statt in Script 1 per zusätzlichem vollständigem Lese-/Schreibdurchgang neu berechnet – spart einen kompletten I/O-Durchgang pro Tile. Der NoData-GDAL-Tag wird bewusst **nicht** hier gesetzt, sondern bleibt wie gehabt Aufgabe von Script 1 (dort erfolgt die GDS-spezifische Normalisierung auf `"0 0 0"`, siehe `normalize_nodata_for_output` oben).

Script 1 erkennt über `meta["FixFalseNodata"]`, dass die Maske bereits gesetzt ist, und überspringt `tag_mask_on_raster()` für diese Dateien (der NoData-Tag wird trotzdem normal gesetzt). Bei deaktivierter Checkbox oder für alle anderen GDS ändert sich nichts am bisherigen Ablauf.

`3_fix_false_nodata_dop.py` ist auch eigenständig per CLI nutzbar (einzelnes Tile, ganzer Ordner, mit/ohne `--in-place`, optionaler CSV-Report aller klassifizierten Gruppen für Diagnose via `--report`) – siehe Docstring im Skript.

---

## Performance: Netzwerk-I/O reduzieren

Bei grossen Lieferungen über ein Netzlaufwerk (Quelle und/oder GDWH-Zielordner) kann die Laufzeit stark vom wiederholten Lesen/Schreiben derselben Datei dominiert werden, nicht von der eigentlichen Verarbeitung:

- **MD5 ohne doppelten Lesevorgang**: `copy_with_retry_md5()` (Script 1) berechnet die Prüfsumme für `files.csv` im selben Lese-/Schreibdurchgang wie das Kopieren, statt die Quelldatei danach separat nochmals komplett einzulesen. Gilt für alle GDS-Typen, unabhängig von der Vorkorrektur-Option.
- **Lokales Staging** (`_osgeo_runner.py`): ist im GUI-Feld "Lokaler Temp-Ordner (Staging)" ein erreichbarer Ordner konfiguriert (Default `Y:\00_GDWH-STAC_TempProcessingFolder\`, falls `Y:\` vorhanden ist), werden Quelle und ein evtl. bereits bestehendes Ziel-Datenpaket (inkl. `files.csv` aus früheren Läufen) zuerst dorthin gespiegelt. Die komplette Verarbeitung läuft dann lokal, erst am Schluss wird das fertige Ergebnis in einem Rutsch zurück ins Netzlaufwerk-Ziel kopiert. Reduziert die Anzahl vollständiger Netzwerktransfers pro Tile auf zwei (hin, zurück) statt mehrerer. Ist kein Staging-Ordner konfiguriert/erreichbar, läuft alles wie bisher direkt übers Netzlaufwerk. Schlägt das abschliessende Zurückkopieren fehl, bleibt die lokale Kopie erhalten (nicht automatisch gelöscht) und der Pfad wird im Log ausgegeben.

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

`test_functions.py` prüft die reinen Python-Funktionen der Sub-Scripts, ohne dass OSGeo4W oder echte Dateien benötigt werden. `osgeo`/`gdal` wird als Mock registriert. Die `classify_mask`-Tests (Script 3, Stufen A-E inkl. Regressionstest für den WALLIS_SAASTAL-Vorfall) brauchen echtes `scipy` (nicht mockbar, da echtes Connected-Component-Labeling geprüft wird) – ist `scipy` nicht installiert, werden nur diese Tests übersprungen, der Rest der Suite läuft normal.

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
