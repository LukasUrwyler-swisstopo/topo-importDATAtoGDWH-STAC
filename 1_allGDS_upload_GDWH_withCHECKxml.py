print("\nVersion 2.4.0 (Remove FlightYear/ResolutionOfOrigin/Provider | Add NoData | AcquisitionTimes mit Hundertstelsekunden)\n")

import os
import re
import hashlib
import shutil
from osgeo import gdal
from datetime import datetime
import xml.etree.ElementTree as ET
from xml.dom import minidom
import sys

# ****************************** Log-Funktion ******************************
LOG_DIR = r"\\v0t0020a.adr.admin.ch\prod\topo\tbk\tbkn\BAFUprod\GDWH_STAC_imports\upload_GDWH\scrip_logs"
os.makedirs(LOG_DIR, exist_ok=True)
log_file = None

def log(message):
    print(message)
    if log_file:
        log_file.write(message + "\n")

# ****************************** Helper ******************************
def calculate_md5(file_path):
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()

def define_gds(path):
    gds = path.split("\\")[-2]
    log(f"GDS erkannt: {gds}\n")
    return gds

def parse_line_id_to_hundredths(line_id):
    """
    Parst eine LineID und gibt (date_str, time_str_with_hundredths) zurück.
    
    Unterstützte Formate im Zeitteil (parts[1]):
        HHMM        -> Sekunden = 00, Hundertstel = 00  -> HH:MM:00.00
        HHMMSS      -> Hundertstel = 00               -> HH:MM:SS.00
        HHMMSSss    -> Hundertstel direkt              -> HH:MM:SS.ss  (2 Stellen Hundertstel)
        HHMMSSsss   -> Millisekunden -> auf Hundertstel runden -> HH:MM:SS.ss
    
    Gibt zurück: datetime-Objekt mit Hundertstelsekunden (als float-Attribut .hundredths: int 0-99)
    """
    try:
        parts = line_id.split("_")
        date_str = parts[0]   # z.B. "20230820"
        time_str = parts[1]   # z.B. "0921" oder "092130" oder "09213045" etc.

        hh = int(time_str[0:2])
        mm = int(time_str[2:4])

        remaining = time_str[4:]  # alles nach HHMM

        if len(remaining) == 0:
            # nur HHMM
            ss = 0
            hundredths = 0
        elif len(remaining) == 2:
            # HHMMSS
            ss = int(remaining[0:2])
            hundredths = 0
        elif len(remaining) == 4:
            # HHMMSSss (2 Hundertstelstellen)
            ss = int(remaining[0:2])
            hundredths = int(remaining[2:4])
        elif len(remaining) == 5:
            # HHMMSSsss (3 Millisekunden-Stellen) -> auf Hundertstel runden
            ss = int(remaining[0:2])
            millis = int(remaining[2:5])
            hundredths = round(millis / 10)
            if hundredths >= 100:
                hundredths = 99  # Clamp bei Rundungsüberlauf
        else:
            # Unbekanntes Format: nur HHMMSS nehmen, Rest ignorieren
            ss = int(remaining[0:2]) if len(remaining) >= 2 else 0
            hundredths = 0

        year  = int(date_str[0:4])
        month = int(date_str[4:6])
        day   = int(date_str[6:8])

        return {
            "year": year, "month": month, "day": day,
            "hh": hh, "mm": mm, "ss": ss, "hundredths": hundredths
        }
    except Exception as e:
        log(f"Fehler beim Parsen der LineID '{line_id}': {e}")
        return None

def format_iso8601_hundredths(parsed):
    """
    Gibt ISO8601 mit Hundertstelsekunden zurück:
    z.B. 2023-08-20T09:21:00.00
    """
    if parsed is None:
        return "UNKNOWN"
    return (f"{parsed['year']:04d}-{parsed['month']:02d}-{parsed['day']:02d}"
            f"T{parsed['hh']:02d}:{parsed['mm']:02d}:{parsed['ss']:02d}"
            f".{parsed['hundredths']:02d}")

def format_stac_datetime(parsed):
    """
    Gibt StacItemIdDatetime zurück:
    z.B. 2023-08-20t09210000
    Format: YYYY-MM-DDtHHMMSSss  (Datum mit Bindestrich, kein Doppelpunkt, keine Punkte)
    """
    if parsed is None:
        return "UNKNOWN"
    return (f"{parsed['year']:04d}-{parsed['month']:02d}-{parsed['day']:02d}"
            f"t{parsed['hh']:02d}{parsed['mm']:02d}{parsed['ss']:02d}{parsed['hundredths']:02d}")

def format_first_acquisition(line_id):
    """Formatiert FirstAcquisitionTime mit Hundertstelsekunden (ISO8601)."""
    parsed = parse_line_id_to_hundredths(line_id)
    return format_iso8601_hundredths(parsed)

def wkt_footprint(full_file_path):
    if not full_file_path.lower().endswith(('.tif', '.tiff')):
        return ""
    raster = gdal.Open(full_file_path)
    if raster is None:
        return ""
    gt = raster.GetGeoTransform()
    cols, rows = raster.RasterXSize, raster.RasterYSize
    ulx, uly = gdal.ApplyGeoTransform(gt, 0, 0)
    urx, ury = gdal.ApplyGeoTransform(gt, cols, 0)
    llx, lly = gdal.ApplyGeoTransform(gt, 0, rows)
    lrx, lry = gdal.ApplyGeoTransform(gt, cols, rows)
    return f"POLYGON (({llx} {lly}, {lrx} {lly}, {urx} {ury}, {ulx} {uly}, {llx} {lly}))"

def get_raster_attributes(file_path):
    raster = gdal.Open(file_path)
    if raster is None:
        raise FileNotFoundError(f"Konnte Raster nicht öffnen: {file_path}")
    gt = raster.GetGeoTransform()
    band = raster.GetRasterBand(1)
    px, py = abs(gt[1]), abs(gt[5])
    cols, rows = raster.RasterXSize, raster.RasterYSize
    bx, by = band.GetBlockSize()
    return {
        "CellSize": f"{(px+py)/2}",
        "BlockSizeX": str(bx),
        "BlockSizeY": str(by),
        "CellCountWidth": str(cols),
        "CellCountHeight": str(rows)
    }

def extract_area(filename, GDS):
    """
    Extrahiert den AREA-Namen robust per Regex aus dem Dateinamen.

    SB_DOP:              zwischen '202X_' und '_DOP'
                         Bsp: 2025_PLAINE_MORTE_DOP_...  -> 'PLAINE_MORTE'

    SB_DSM (hillshade):  zwischen '202X_' und '_hillshade'
                         Bsp: 2025_PLAINE_MORTE_hillshade_... -> 'PLAINE_MORTE'

    SB_DSM (DSM):        zwischen '202X_' und '_DSM'
                         Bsp: 2025_PLAINE_MORTE_DSM_...  -> 'PLAINE_MORTE'

    SB_DSM_PUNKTWOLKE:   zwischen '202X_' und '_TIN'
                         Bsp: 2025_PLAINE_MORTE_TIN_...  -> 'PLAINE_MORTE'
    """
    if GDS == "SB_DOP" or GDS == "SB_DOP_16":
        match = re.search(r'20\d{2}_(.+?)_DOP', filename, re.IGNORECASE)

    elif GDS == "SB_DSM":
        if "hillshade" in filename.lower():
            match = re.search(r'20\d{2}_(.+?)_hillshade', filename, re.IGNORECASE)
        else:
            match = re.search(r'20\d{2}_(.+?)_DSM', filename, re.IGNORECASE)

    elif GDS == "SB_DSM_PUNKTWOLKE":
        match = re.search(r'20\d{2}_(.+?)_TIN', filename, re.IGNORECASE)

    else:
        match = None

    if match:
        return match.group(1)
    else:
        log(f"Warnung: AREA konnte nicht aus '{filename}' ermittelt werden (GDS={GDS})")
        return "UNKNOWN"

def extract_tile_lv95(filename):
    """
    Extrahiert TileKey als die zwei Parts direkt vor '_LV95' im Dateinamen.
    Bsp: ..._2601_1136_LV95_LN02.laz -> '2601_1136'
    """
    base = filename.rsplit('.', 1)[0]
    parts = base.split('_')
    try:
        lv95_idx = parts.index('LV95')
        return parts[lv95_idx - 2] + "_" + parts[lv95_idx - 1]
    except (ValueError, IndexError) as e:
        log(f"Fehler beim Extrahieren des TileKey aus '{filename}': {e}")
        return "UNKNOWN"

def get_nodata_value(filename, GDS, meta_info):
    """
    Gibt den NoData-Wert zurück:
    - SB_DSM + '_hillshade_' im Dateinamen -> immer '255 255 255'
    - SB_DSM + '_DSM_' im Dateinamen       -> immer '-3.4028235e+38'
    - Alle anderen GDS                     -> aus meta_info["NoData"]
    """
    if GDS == "SB_DSM":
        fn_lower = filename.lower()
        if "_hillshade_" in fn_lower:
            return "255 255 255"
        elif "_dsm_" in fn_lower:
            return "-3.4028235e+38"
    return meta_info.get("NoData", "")

# ****************************** Sicherheits-Check ******************************
def preview_xml_attributes(src, GDS, meta_info):
    """
    Zeigt eine Vorschau der XML-Attribute an (nur Anzeige, keine Datei-Erstellung).
    Gibt alle Attributwerte jeweils in einer separaten Zeile aus.
    Danach Benutzerabfrage Y/N.
    """

    print("\n==================== SICHERHEITS-CHECK ====================\n")
    print("Beispiel-XML Attribute (nur Vorschau, keine Datei wird erzeugt):\n")

    example_file = None
    for fn in os.listdir(src):
        if fn.lower().endswith(('.tif', '.tiff', '.laz')):
            example_file = fn
            break

    if not example_file:
        print("Keine geeignete Datei für Vorschau gefunden!")
        sys.exit(1)

    AOI = extract_area(example_file, GDS)

    # TileKey aus Beispieldatei ermitteln
    if GDS == "SB_DSM_PUNKTWOLKE":
        example_tilekey = extract_tile_lv95(example_file)
    elif GDS in ["SB_DOP", "SB_DOP_16"]:
        _parts = example_file.rsplit('.', 1)[0].split('_')
        if "LV95" in _parts:
            _idx = _parts.index("LV95")
            example_tilekey = _parts[_idx - 2] + "_" + _parts[_idx - 1]
        else:
            example_tilekey = "NICHT GEFUNDEN – 'LV95' fehlt im Dateinamen!"
    elif GDS == "SB_DSM":
        example_tilekey = "1000  (fix für SB_DSM)"
    else:
        _parts = example_file.rsplit('.', 1)[0].split('_')
        example_tilekey = _parts[-2] + "_" + _parts[-1] if len(_parts) >= 2 else "UNBEKANNT"

    print("CHECK-ref.SYS.; ReferenzSystem OK?: ", Quelle)
    print(meta_info.get("Auftragstyp", ""))
    print(AOI)
    print(f"TileKey (Beispiel aus '{example_file}'): {example_tilekey}")
    print(meta_info.get("CustomAttribute", ""))    
    print(meta_info.get("CameraSystem", ""))    
    print(meta_info.get("SourceReferenceSystem", ""))
    print(meta_info.get("TerrainModel", ""))
    if GDS != "SB_DSM_PUNKTWOLKE":
        print(f"NoData: {get_nodata_value(example_file, GDS, meta_info)}")
    else:
        print("NoData: <LAZ hat kein noData-Value>")

    line_ids = meta_info.get("Line_ID", [])
    print(",".join(line_ids))

    acq_times = [format_first_acquisition(l) for l in line_ids]
    # print(",".join(acq_times))

    if line_ids:
        print(format_first_acquisition(line_ids[0]))

    print("\n============================================================\n")

    decision = input("Script wirklich starten? (Y/N): ").strip().upper()

    if decision != "Y":
        print("Script wurde vom Benutzer abgebrochen.\n")
        sys.exit(0)

# ****************************** XML Creation ******************************
def create_xml(file_path, GDS, meta_info):
    filename = os.path.basename(file_path)

    # AREA: robust aus Dateiname extrahieren
    AOI = extract_area(filename, GDS)

    root = ET.Element("MetaObject", {"xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance"})

    for key, val in {
        "Auftragstyp": meta_info.get("Auftragstyp", ""),
        "Area": AOI,
        "TerrainModel": meta_info.get("TerrainModel", ""),
        "CameraSystem": meta_info.get("CameraSystem", ""),
        "CoordinateReferenceSystem": meta_info.get("CoordinateReferenceSystem", meta_info.get("SourceReferenceSystem", "")),
        "Commentary": meta_info.get("Commentary", meta_info.get("CustomAttribute", ""))
    }.items():
        ET.SubElement(root, key).text = val

    # NoData: automatisch für SB_DSM, sonst aus meta_info
    # NoData nur schreiben, wenn NICHT SB_DSM_PUNKTWOLKE
    if GDS != "SB_DSM_PUNKTWOLKE":
        ET.SubElement(root, "NoData").text = get_nodata_value(filename, GDS, meta_info)

    line_ids = meta_info.get("Line_ID", [])
    if not line_ids:
        raise ValueError("Keine Line_ID angegeben!")
    ET.SubElement(root, "LineID").text = ",".join(line_ids)

    # AcquisitionTimes mit Hundertstelsekunden
    acq_times = []
    for l in line_ids:
        parsed = parse_line_id_to_hundredths(l)
        acq_times.append(format_iso8601_hundredths(parsed))
    ET.SubElement(root, "AcquisitionTimes").text = ",".join(acq_times)

    # FirstAcquisitionTime mit Hundertstelsekunden
    first_line = line_ids[0]
    first_parsed = parse_line_id_to_hundredths(first_line)
    first_time = format_iso8601_hundredths(first_parsed)
    ET.SubElement(root, "FirstAcquisitionTime").text = first_time

    # StacItemIdDatetime: z.B. 2023-08-20t09210000
    ET.SubElement(root, "StacItemIdDatetime").text = format_stac_datetime(first_parsed)

    if len(first_line) >= 13:
        ET.SubElement(root, "BandID").text = first_line[9:13]

    if file_path.lower().endswith(('.tif', '.tiff')):
        for k, v in get_raster_attributes(file_path).items():
            ET.SubElement(root, k).text = v
    elif file_path.lower().endswith('.laz'):
        # Year aus Line_ID ableiten (erste 4 Ziffern = Jahr)
        ET.SubElement(root, "Year").text = first_line[0:4] if first_line else "UNKNOWN"

    xml_path = file_path.rsplit('.', 1)[0] + ".xml"
    pretty = minidom.parseString(ET.tostring(root, 'utf-8')).toprettyxml(indent="    ")
    pretty = "\n".join([line for line in pretty.split("\n") if line.strip() != ""])
    with open(xml_path, 'w', encoding='utf-8') as f:
        f.write(pretty)
    return xml_path, AOI, first_time

# ****************************** CSV & Kopieren ******************************
def update_file_csv(output_path, full_file_path, GDS):
    csv_path = os.path.join(output_path, 'files.csv')
    name = os.path.basename(full_file_path)
    name_parts = name.rsplit('.', 1)[0].split('_')
    ext = os.path.splitext(name)[1].lower()

    # === SB_DSM ===
    if GDS == "SB_DSM":
        subfolder = "HILLSHADE" if "hillshade" in name.lower() else "DSM"
        dst_folder = os.path.join(output_path, "NV", subfolder)
        os.makedirs(dst_folder, exist_ok=True)
        for fext in [ext, ".xml", ".tfw"]:
            src = full_file_path.rsplit('.', 1)[0] + fext
            if os.path.exists(src):
                shutil.copy(src, os.path.join(dst_folder, os.path.basename(src)))
        md5 = calculate_md5(full_file_path)
        tilekey = "1000"
        row = f"NV\\{subfolder}\\{name};{md5};{tilekey};add;{wkt_footprint(full_file_path)}"

    # === SB_DSM_PUNKTWOLKE ===
    elif GDS == "SB_DSM_PUNKTWOLKE" and ext == ".laz":
        # TileKey: die zwei Parts direkt vor '_LV95' (robust, von hinten)
        tilekey = extract_tile_lv95(name)
        md5 = calculate_md5(full_file_path)

        # Hauptziel: NV\SB_DSM_PUNKTWOLKE
        dst_nv = os.path.join(output_path, "NV", "SB_DSM_PUNKTWOLKE")
        os.makedirs(dst_nv, exist_ok=True)
        shutil.copy(full_file_path, os.path.join(dst_nv, name))

        xml_src = full_file_path.rsplit('.', 1)[0] + ".xml"
        if os.path.exists(xml_src):
            shutil.copy(xml_src, os.path.join(dst_nv, os.path.basename(xml_src)))

        # Zweites Ziel: PrecalculatedFormats\SB_DSM_PUNKTWOLKE
        dst_pre = os.path.join(output_path, "PrecalculatedFormats", "SB_DSM_PUNKTWOLKE")
        os.makedirs(dst_pre, exist_ok=True)
        new_name = f"SB_DSM_PUNKTWOLKE_LAZ_CHLV95_LN02_{tilekey}.laz"
        shutil.copy(full_file_path, os.path.join(dst_pre, new_name))

        row_nv  = f"NV\\SB_DSM_PUNKTWOLKE\\{name};{md5};{tilekey};add;"
        row_pre = f"PrecalculatedFormats\\SB_DSM_PUNKTWOLKE\\{new_name};{md5};{tilekey};add;"

        with open(csv_path, 'a', encoding='utf-8') as f:
            f.write("\n" + row_nv)
            f.write("\n" + row_pre)
        return  # fertig für diesen Datentyp

    # === SB_DOP / SB_DOP_16 ===
    elif GDS in ["SB_DOP", "SB_DOP_16"]:
        # TileKey: die zwei Parts direkt vor '_LV95' (robust, von hinten)
        if "LV95" in name_parts:
            lv95_index = name_parts.index("LV95")
            tile = name_parts[lv95_index - 2] + "_" + name_parts[lv95_index - 1]
        else:
            raise ValueError(f"LV95 nicht im Dateinamen gefunden: {name}")

        md5 = calculate_md5(full_file_path)
        row = f"NV\\{name};{md5};{tile};add;"

        xml_src = full_file_path.rsplit('.', 1)[0] + ".xml"
        if os.path.exists(xml_src):
            nv_path = os.path.join(output_path, "NV")
            os.makedirs(nv_path, exist_ok=True)
            shutil.copy(xml_src, os.path.join(nv_path, os.path.basename(xml_src)))

    # === Default ===
    else:
        tile = name_parts[-2] + "_" + name_parts[-1]
        md5 = calculate_md5(full_file_path)
        row = f"NV\\{name};{md5};{tile};add;"

    with open(csv_path, 'a', encoding='utf-8') as f:
        f.write("\n" + row)

# ****************************** Hauptlogik ******************************
def files_in_order(src, out, GDS, meta):
    global log_file
    missing_xml = []

    log_name = os.path.basename(out.rstrip("/\\")) + ".log"
    log_path = os.path.join(LOG_DIR, log_name)
    log_file = open(log_path, 'w', encoding='utf-8')
    log(f"Log-Datei: {log_path}")

    for fn in os.listdir(src):
        fp = os.path.join(src, fn)
        if os.path.isfile(fp) and fn.lower().endswith(('.tif', '.tiff', '.laz')):
            log(f"Verarbeite: {fn}")
            try:
                xml_path, _, _ = create_xml(fp, GDS, meta)
                update_file_csv(out, fp, GDS)
            except Exception as e:
                log(f"Fehler bei {fn}: {e}")
                missing_xml.append(fn)

    if missing_xml:
        log("Fehler: Einige XML wurden nicht erstellt:")
        for f in missing_xml:
            log("   - " + f)
        sys.exit(1)
    else:
        log("Alle XML-Dateien erfolgreich erstellt und kopiert.\n")

# ****************************** DOP-Kopieren ******************************
def create_and_copy_order(out, src, GDS):
    if GDS in ["SB_DOP", "SB_DOP_16"]:
        nv_path = os.path.join(out, "NV")
        os.makedirs(nv_path, exist_ok=True)
        for fn in os.listdir(src):
            if fn.lower().endswith(('.tif', '.tfw')):
                shutil.copy(os.path.join(src, fn), os.path.join(nv_path, fn))
        log("DOP-Dateien kopiert.\n")

# ****************************** Working Part ******************************

Quelle = r"A:\20XX\AOI\DSM\LV95_LN02\ORIGINAL"
Ziel = r"\\v0t0020a.adr.admin.ch\iprod\gdwh-ingest\BUCKET_INT\RASTER\SB_DSM\20XX_AOI_DSM"
        # --> für GDS "SB_DSM_PUNKTWOLKE" Settings beim Datenpacket kontrollieren:
            # beim erstellen des Datenpackets in GDWH, nur folgende Attribute Wählen, Rest leerlassen. 
                #(Wählen: "Name", "LayerRealeaseKey", "ReleaseModelKey") (leer: "ReleaseKey" und "FullExportFileNameKey")

GDS = define_gds(Ziel)

# *********************** Meta-Information *******************************

meta_info = {
    "Auftragstyp": "kry", 
        # kontrollieren:
        # "kry"
        # "ram"
        # "bim"
        # "mom"
        # "wam"
    "CustomAttribute": "Digital Surface Model  - Raster Mosaic (DSM photogrammetric autocorrelation)" , 
        # kontrollieren; 
        # "Digital Surface Model  - Raster Mosaic (DSM photogrammetric autocorrelation)" 
        # "Digital Surface Model - PointCloud LAZ (DSM photogrammetric autocorrelation)"
        # "Digital OrthoPhoto - Mosaic RGB 8BIT"
    "Line_ID": ["20200913_1054_12501", "20200913_1104_12501"], 
        # kontrollieren;
        # (!)Alle LineIDs(!) des Mosaiks angeben! 
        # erste LineID (!)muss(!) die erste BefliegungsLinie (AufnahmeZeitpunkt) des AOIs sein! 
        #(z.B.: "20200821_0952_12504", "20200821_1009_12504", "20200821_1026_12504")
    "NoData": "0 0 0",
        # kontrollieren! Typische Werte:
        # "0 0 0"    /   "255 255 255"   (8BIT, 3-Band RGB TIF)
        # Hinweis: Für GDS "SB_DSM" wird NoData automatisch gesetzt:
        #   - '_hillshade_' im Dateinamen -> "255 255 255"
        #   - '_DSM_'       im Dateinamen -> "-3.4028235e+38"
        #   (dieser Wert wird dann ignoriert)
        # Hinweis: Für GDS "SB_DSM_PUNKTWOLKE" gibt es kein NoData-Value		
    "TerrainModel": "Digital Surface Model (DSM photogrammetric autocorrelation)", 
        # kontrollieren;
        # "Digital Surface Model (DSM photogrammetric autocorrelation)"
        # "swissALTI3D"
        # "swissALTI3D/DHM25"
        # "swissSURFACE3D"
    "SourceReferenceSystem": "(EPSG:2056) CH1903+ / LV95_LN02", 
        # INPUT kontrollieren! only possible Value:
        # ("EPSG:2056) CH1903+ / LV95_LN02"
    "CameraSystem": "Leica ADS100", 
        # kontrollieren;
        # "Leica ADS100"
        # "Leica ADS80"
        # "Leica DMC-4"
}

# ======================================================================

# Sicherheitsvorschau anzeigen
preview_xml_attributes(Quelle, GDS, meta_info)
# Processierung wenn YES
files_in_order(Quelle, Ziel, GDS, meta_info)
create_and_copy_order(Ziel, Quelle, GDS)

if log_file:
    log_file.close()