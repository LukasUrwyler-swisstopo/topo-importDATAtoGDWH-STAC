print("\nVersion GDS_DOP_16: 16BIT , 2.0.0 (Remove FlightYear/ResolutionOfOrigin/Provider | Add NoData | AcquisitionTimes mit Hundertstelsekunden)\n")

import os
import re
import xml.etree.ElementTree as ET
import hashlib
import shutil
from osgeo import gdal
from datetime import datetime
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

# ****************************** Helper Functions ******************************

def calculate_md5(file_path):
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()

def define_gds(path):
    GDS = path.split("\\")[-2]
    log(f"GDS erkannt: {GDS}\n")
    return GDS

def wkt_footprint(full_file_path):
    raster_layer = gdal.Open(full_file_path)
    if raster_layer is None:
        raise FileNotFoundError(f"Raster konnte nicht geöffnet werden: {full_file_path}")
    gt = raster_layer.GetGeoTransform()
    cols = raster_layer.RasterXSize
    rows = raster_layer.RasterYSize
    ulx, uly = gdal.ApplyGeoTransform(gt, 0, 0)
    urx, ury = gdal.ApplyGeoTransform(gt, cols, 0)
    llx, lly = gdal.ApplyGeoTransform(gt, 0, rows)
    lrx, lry = gdal.ApplyGeoTransform(gt, cols, rows)
    return f"POLYGON (({llx} {lly}, {lrx} {lry}, {urx} {ury}, {ulx} {uly}, {llx} {lly}))"

def parse_line_id_to_hundredths(line_id):
    """
    Parst eine LineID und gibt ein dict mit Datums- und Zeitkomponenten zurück.

    Unterstützte Formate im Zeitteil (parts[1]):
        HHMM        -> Sekunden = 00, Hundertstel = 00  -> HH:MM:00.00
        HHMMSS      -> Hundertstel = 00               -> HH:MM:SS.00
        HHMMSSss    -> Hundertstel direkt              -> HH:MM:SS.ss
        HHMMSSsss   -> Millisekunden -> auf Hundertstel runden -> HH:MM:SS.ss
    """
    try:
        parts = line_id.split("_")
        date_str = parts[0]   # z.B. "20230820"
        time_str = parts[1]   # z.B. "0921" oder "092130" etc.

        hh = int(time_str[0:2])
        mm = int(time_str[2:4])
        remaining = time_str[4:]

        if len(remaining) == 0:
            ss = 0
            hundredths = 0
        elif len(remaining) == 2:
            ss = int(remaining[0:2])
            hundredths = 0
        elif len(remaining) == 4:
            ss = int(remaining[0:2])
            hundredths = int(remaining[2:4])
        elif len(remaining) == 5:
            ss = int(remaining[0:2])
            millis = int(remaining[2:5])
            hundredths = round(millis / 10)
            if hundredths >= 100:
                hundredths = 99
        else:
            ss = int(remaining[0:2]) if len(remaining) >= 2 else 0
            hundredths = 0

        return {
            "year": int(date_str[0:4]), "month": int(date_str[4:6]), "day": int(date_str[6:8]),
            "hh": hh, "mm": mm, "ss": ss, "hundredths": hundredths
        }
    except Exception as e:
        log(f"Fehler beim Parsen der LineID '{line_id}': {e}")
        return None

def format_iso8601_hundredths(parsed):
    """Gibt ISO8601 mit Hundertstelsekunden zurück: z.B. 2023-08-20T09:21:00.00"""
    if parsed is None:
        return "UNKNOWN"
    return (f"{parsed['year']:04d}-{parsed['month']:02d}-{parsed['day']:02d}"
            f"T{parsed['hh']:02d}:{parsed['mm']:02d}:{parsed['ss']:02d}"
            f".{parsed['hundredths']:02d}")

def format_stac_datetime(parsed):
    """Gibt StacItemIdDatetime zurück: z.B. 2023-08-20t09210000"""
    if parsed is None:
        return "UNKNOWN"
    return (f"{parsed['year']:04d}-{parsed['month']:02d}-{parsed['day']:02d}"
            f"t{parsed['hh']:02d}{parsed['mm']:02d}{parsed['ss']:02d}{parsed['hundredths']:02d}")

def format_first_acquisition(line_id):
    """Formatiert FirstAcquisitionTime mit Hundertstelsekunden (ISO8601)."""
    return format_iso8601_hundredths(parse_line_id_to_hundredths(line_id))

def format_acquisition_times(line_id):
    """Formatiert AcquisitionTimes mit Hundertstelsekunden (ISO8601)."""
    return format_iso8601_hundredths(parse_line_id_to_hundredths(line_id))

def get_raster_attributes(file_path):
    raster_layer = gdal.Open(file_path)
    if raster_layer is None:
        raise FileNotFoundError(f"Raster konnte nicht geöffnet werden: {file_path}")
    gt = raster_layer.GetGeoTransform()
    band = raster_layer.GetRasterBand(1)
    px, py = abs(gt[1]), abs(gt[5])
    cols, rows = raster_layer.RasterXSize, raster_layer.RasterYSize
    bx, by = band.GetBlockSize()
    return {
        "CellSize": f"{(px+py)/2}",
        "BlockSizeX": str(bx),
        "BlockSizeY": str(by),
        "CellCountWidth": str(cols),
        "CellCountHeight": str(rows)
    }

def extract_area(filename):
    """Extrahiert AREA zwischen Jahr (202X_) und _DOP im Dateinamen.
    Beispiel: 2025_PLAINE_MORTE_DOP_... -> 'PLAINE_MORTE'
    Funktioniert auch bei einwortigen Areas wie 'BERN'.
    """
    match = re.search(r'20\d{2}_(.+?)_DOP', filename)
    return match.group(1) if match else "UNKNOWN"

def extract_tile(filename):
    """Extrahiert TileKey als die zwei Parts vor '_LV95' im Dateinamen.
    Beispiel: ..._2602_1145_LV95.tif -> '2602_1145'
    """
    base = filename.rsplit('.', 1)[0]
    parts = base.split('_')
    try:
        lv95_idx = parts.index('LV95')
        return parts[lv95_idx - 2] + "_" + parts[lv95_idx - 1]
    except (ValueError, IndexError) as e:
        log(f"Fehler beim Extrahieren des TileKey aus '{filename}': {e}")
        return "UNKNOWN"

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

    AOI = extract_area(example_file)
    example_tilekey = extract_tile(example_file)

    print("CHECK-ref.SYS.; ReferenzSystem OK?: ", Quelle)
    print(meta_info.get("Auftragstyp", ""))
    print(AOI)
    print(f"TileKey (Beispiel aus '{example_file}'): {example_tilekey}")
    print(meta_info.get("CustomAttribute", ""))
    print(meta_info.get("CameraSystem", ""))
    print(meta_info.get("SourceReferenceSystem", ""))
    print(meta_info.get("TerrainModel", ""))
    print(f"NoData: {meta_info.get('NoData', '')}")

    print(meta_info.get("allAreaLineIDs", ""))

    all_area_ids = meta_info.get("allAreaLineIDs", [])
    acq_times = [format_acquisition_times(l) for l in all_area_ids]
    print(",".join(acq_times))

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

# ****************************** XML & CSV Functions ******************************

def create_xml(file_path, GDS, meta_info):
    filename = os.path.basename(file_path)

    # AREA: robust aus Dateiname extrahieren (zwischen Jahr und _DOP)
    AOI = extract_area(filename)

    root = ET.Element("MetaObject", {"xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance"})

    # Standardfelder
    fields = {
        "Auftragstyp": meta_info.get("Auftragstyp", ""),
        "Area": AOI,
        "TerrainModel": meta_info.get("TerrainModel", ""),
        "CameraSystem": meta_info.get("CameraSystem", ""),
        "CoordinateReferenceSystem": meta_info.get("CoordinateReferenceSystem", meta_info.get("SourceReferenceSystem", "")),
        "Commentary": meta_info.get("Commentary", meta_info.get("CustomAttribute", ""))
    }

    for k, v in fields.items():
        ET.SubElement(root, k).text = v

    # NoData aus meta_info
    ET.SubElement(root, "NoData").text = meta_info.get("NoData", "")

    line_ids = meta_info.get("Line_ID", [])
    if not line_ids:
        raise ValueError("Mindestens eine Line_ID muss angegeben werden!")
    ET.SubElement(root, "LineID").text = ",".join(line_ids)

    # AcquisitionTimes aus allAreaLineIDs mit Hundertstelsekunden
    all_area_ids = meta_info.get("allAreaLineIDs", [])
    if not all_area_ids:
        raise ValueError("Meta-Attribut 'allAreaLineIDs' muss mindestens eine LineID enthalten!")

    formatted_times = [format_acquisition_times(x) for x in all_area_ids]
    ET.SubElement(root, "AcquisitionTimes").text = ",".join(formatted_times)

    # FirstAcquisitionTime aus erster Line_ID mit Hundertstelsekunden
    first_parsed = parse_line_id_to_hundredths(line_ids[0])
    first_time = format_iso8601_hundredths(first_parsed)
    ET.SubElement(root, "FirstAcquisitionTime").text = first_time

    # StacItemIdDatetime: z.B. 2023-08-20t09210000
    ET.SubElement(root, "StacItemIdDatetime").text = format_stac_datetime(first_parsed)

    # BandID aus Line_ID
    if len(line_ids[0]) >= 13:
        ET.SubElement(root, "BandID").text = line_ids[0][9:13]

    # Raster-Attribute
    if file_path.lower().endswith(('.tif', '.tiff')):
        attrs = get_raster_attributes(file_path)
        for k, v in attrs.items():
            ET.SubElement(root, k).text = v

    xml_filename = file_path.rsplit('.', 1)[0] + ".xml"
    rough_string = ET.tostring(root, 'utf-8')
    reparsed = minidom.parseString(rough_string)
    formatted_xml = reparsed.toprettyxml(indent="    ")
    formatted_xml = "\n".join([line for line in formatted_xml.split("\n") if line.strip() != ""])
    with open(xml_filename, 'w', encoding="utf-8") as f:
        f.write(formatted_xml)

    return AOI, format_acquisition_times(all_area_ids[0])

def update_file_csv(output_path, full_file_path, GDS):
    csv_file_path = os.path.join(output_path, 'files.csv')
    filename = os.path.basename(full_file_path)
    name_parts = filename.rsplit('.', 1)[0].split('_')

    if GDS == "SB_DOP":
        tile = name_parts[4] + "_" + name_parts[5]
    elif GDS == "SB_DOP_16":
        tile = extract_tile(filename)
    elif GDS == "SB_DSM":
        tile = "1000"
        footprint = wkt_footprint(full_file_path)
    elif GDS == "SB_DSM_PUNKTWOLKE":
        tile = name_parts[-4] + "_" + name_parts[-3]
    else:
        tile = ""
        footprint = ""

    md5_hash = calculate_md5(full_file_path)
    row_data = f"NV\\{filename};{md5_hash};{tile};add;"
    with open(csv_file_path, 'a', encoding='utf-8') as f:
        f.write("\n" + row_data)

# ****************************** Main Functions ******************************

def files_in_order(path, output_path, GDS, meta_info):
    global log_file

    log_name = os.path.basename(output_path.rstrip("/\\")) + ".log"
    log_path = os.path.join(LOG_DIR, log_name)
    log_file = open(log_path, 'w', encoding='utf-8')
    log(f"Log-Datei erstellt: {log_path}")

    for fn in os.listdir(path):
        full_file_path = os.path.join(path, fn)
        if os.path.isfile(full_file_path) and fn.lower().endswith(('.tif', '.tiff', '.las', '.laz')):
            log(f"Verarbeite Datei: {fn}")
            try:
                AOI, first_time_tmp = create_xml(full_file_path, GDS, meta_info)
                update_file_csv(output_path, full_file_path, GDS)
            except Exception as e:
                log(f"Fehler bei Datei {fn}: {e}")

def create_and_copy_order(output_path, input_path, GDS):
    new_order_path = os.path.join(output_path, 'NV')
    os.makedirs(new_order_path, exist_ok=True)

    if GDS == "SB_DSM":
        os.makedirs(os.path.join(new_order_path, 'HILLSHADE'), exist_ok=True)
        os.makedirs(os.path.join(new_order_path, 'DSM'), exist_ok=True)
    if GDS == "SB_DSM_PUNKTWOLKE":
        os.makedirs(os.path.join(new_order_path, 'SB_DSM_PUNKTWOLKE'), exist_ok=True)
        os.makedirs(os.path.join(output_path, 'PrecalculatedFormats', 'SB_DSM_PUNKTWOLKE'), exist_ok=True)

    log("\nStarte Kopiervorgang...\n")
    for nb, file_name in enumerate(os.listdir(input_path), 1):
        source_file = os.path.join(input_path, file_name)
        if os.path.isfile(source_file):
            dst = os.path.join(new_order_path, file_name)
            try:
                shutil.copy(source_file, dst)
            except Exception as e:
                log(f"Fehler beim Kopieren {file_name}: {e}")
        log(f"Datei {nb} kopiert")
    log("Kopiervorgang abgeschlossen.")

# ****************************** Working Part ******************************

Quelle = r"A:\2025\BIRCH\DOP\LV95\DOP_NRGB_16BITS\1005"
output_path = r"\\v0t0020a.adr.admin.ch\iprod\gdwh-ingest\BUCKET_INT\RASTER\SB_DOP_16\2025_BIRCH_DOP16_1005"

GDS = define_gds(output_path)

# *********************** Meta-Information *******************************
meta_info = {
    "Auftragstyp": "kry",
        # kontrollieren:
        # "kry"
        # "ram"
        # "bim"
        # "mom"
        # "wam"
    "Line_ID": ["20250919_1005_12501"],
        # (!)NUR die zu importierende Line_ID(!): kontrollieren!
    "allAreaLineIDs": ["20250919_0947_12501", "20250919_0955_12501", "20250919_1005_12501", "20250919_1013_12501"],
        # kontrollieren; (!)Alle LineIDs(!) des Mosaiks angeben!
        # erste LineID (!)muss(!) die erste BefliegungsLinie (AufnahmeZeitpunkt) des AOIs sein!
        # (z.B.: "20200821_0952_12504", "20200821_1009_12504", "20200821_1026_12504")
    "NoData": "0 0 0 0",
        # kontrollieren! Typische Werte (bei 16BIT, 4-Band TIF):
        # "0 0 0 0" (schwarze Background-Pixel) / "65535 65535 65535 65535" (weisse Background-Pixel)!
    "CustomAttribute": "Digital OrthoPhoto - (ADS Line) NRGB 16BIT",
        # kontrollieren; "Digital OrthoPhoto - (ADS Line) NRGB 16BIT"
    "SourceReferenceSystem": "(EPSG:2056) CH1903+ / LV95_LN02",
        # kontrollieren! only possible Value ("EPSG:2056) CH1903+ / LV95_LN02"
    "CameraSystem": "Leica ADS100",
        # kontrollieren;
        # "Leica ADS100"
        # "Leica ADS80"
        # "Leica DMC-4"
    "TerrainModel": "Digital Surface Model (DSM photogrammetric autocorrelation)",
        # kontrollieren;
        # "Digital Surface Model (DSM photogrammetric autocorrelation)"
        # "swissALTI3D"
        # "swissALTI3D/DHM25"
        # "swissSURFACE3D"
}

# Sicherheitsvorschau anzeigen
preview_xml_attributes(Quelle, GDS, meta_info)

files_in_order(Quelle, output_path, GDS, meta_info)
create_and_copy_order(output_path, Quelle, GDS)

if log_file:
    log_file.close()