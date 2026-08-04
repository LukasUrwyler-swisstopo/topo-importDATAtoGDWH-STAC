#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_false_nodata_dop.py

Vorkorrektur-Skript fuer DOP-Tiles (RGB 8bit, GeoTIFF mit .tfw-Begleitdatei).
Korrigiert "falsche" NoData-Pixel (0,0,0 oder 255,255,255, je nach
--nodata-value), welche durch zu starkes Histogramm-Stretch in dunklen
Schatten- bzw. hellen Ueberstrahlungsbereichen entstanden sind.

Dieses Skript korrigiert ausschliesslich die Pixelwerte im Tiff-Tile. Es
schreibt keine Flag Mask und macht keine GDWH-/STAC-Vorbereitung, das
passiert in nachgelagerten Skripten, auf Basis der hier bereits bereinigten
0,0,0-Werte. Es geht nicht um COG, sondern um die regulaeren GeoTIFF-Tiles
(.tif + .tfw), wie sie vor der eigentlichen COG-/Flag-Mask-Erstellung
vorliegen.

Hintergrund:
  - Echtes NoData in einem DOP-Tile besteht aus einer grossen zusammenhaengenden
    Pixelgruppe (>= THRESHOLD Pixel) und beruehrt immer mindestens einen Rand
    des Tiles.
  - "Falsches" NoData sind einzelne Pixel oder kleine Gruppen (< THRESHOLD
    Pixel) innerhalb der Nutzdaten, die durch die Radiometrie zufaellig auf
    0,0,0 gefallen sind (dunkle Schattenzonen).

Vorgehen:
  1. Maske bilden: alle Pixel, bei denen R, G und B gleichzeitig 0 sind
     (Background Value).
  2. Connected-Component-Labeling auf dieser Maske (Standard: 8-Nachbarschaft).
  3. Pro Gruppe: Groesse (Pixelanzahl) bestimmen.
  4. Klassifikation (einzige Schwelle, THRESHOLD = 10000 Pixel):
       - Groesse >= THRESHOLD -> "echt"   -> bleibt 0,0,0
       - Groesse <  THRESHOLD -> "falsch" -> anheben (+INCREMENT)
  5. Randkontakt (beruehrt Zeile/Spalte 0 oder die letzte Zeile/Spalte des
     Tiles) wird zusaetzlich geprueft, ist aber NICHT entscheidend fuer die
     Klassifikation. Es dient nur als Kontrollhinweis: eine als "echt"
     eingestufte Gruppe ohne Randkontakt ist untypisch und wird im Log/Report
     markiert, damit sie manuell geprueft werden kann.
  6. Nur die Baender 1-3 (RGB) werden veraendert. Ein evtl. vorhandenes 4.
     Band (z.B. NIR/Alpha) wird unveraendert uebernommen.

Die Kernlogik (classify_mask) ist von der GDAL-I/O getrennt und wurde separat
mit synthetischen Testfaellen geprueft (Schwellenwert exakt, kleine Gruppe am
Rand, grosse Gruppe ohne Randkontakt, gemischtes Tile).

Benoetigt: GDAL Python-Bindings (osgeo.gdal), numpy, scipy
  -> im OSGeo4W/QGIS-Python-Environment normalerweise bereits vorhanden.

Verwendung:
  Einzelnes Tile:
    python fix_false_nodata_dop.py --input tile_001.tif --output tile_001_fixed.tif

  Ganzer Ordner (alle .tif):
    python fix_false_nodata_dop.py --input-dir ./dop_tiles --output-dir ./dop_tiles_fixed

  Tiles mit bereits vorhandener, falsch berechneter Flag Mask (Alpha-Band,
  internes Mask Band oder NoData-Tag) zuerst bereinigen und dann korrigieren:
    python fix_false_nodata_dop.py --input-dir ./dop_tiles_alt_maskiert \
        --output-dir ./dop_tiles_fixed --strip-existing-mask

  In place: Originale (tif + tfw) direkt durch die korrigierte Version
  ersetzen, mit Backup der Originale vorher:
    python fix_false_nodata_dop.py --input-dir ./dop_tiles --in-place --backup-dir ./dop_tiles_backup

  Optional CSV-Report der Kontroll-/Warnfaelle:
    python fix_false_nodata_dop.py --input-dir ./dop_tiles --output-dir ./dop_tiles_fixed --report report.csv
"""

import argparse
import csv
import os
import shutil
import sys
import tempfile

import numpy as np
from scipy import ndimage

try:
    from osgeo import gdal, osr
    gdal.UseExceptions()
except ImportError:
    gdal = None
    osr = None


# ---------------------------------------------------------------------------
# Kernlogik (ohne GDAL-Abhaengigkeit, separat testbar)
# ---------------------------------------------------------------------------

def classify_mask(mask_zero, threshold=10000, connectivity=8):
    """
    Klassifiziert zusammenhaengende Gruppen von True-Werten in mask_zero
    als "echtes NoData" (bleibt) oder "falsches NoData" (wird angehoben).

    Regel: Groesse >= threshold -> echt, sonst falsch.
    Randkontakt wird nur als Kontrollhinweis mitgeloggt, beeinflusst den
    Entscheid nicht.

    Rueckgabe:
        increment_mask : bool-Array, True = diese Pixel sollen angehoben werden
        log_rows        : Liste von Dicts mit Infos pro Gruppe (fuer Report/Debug)
    """
    structure = np.ones((3, 3), dtype=int) if connectivity == 8 else None
    labeled, n_features = ndimage.label(mask_zero, structure=structure)

    increment_mask = np.zeros_like(mask_zero, dtype=bool)
    log_rows = []

    if n_features == 0:
        return increment_mask, log_rows

    sizes = ndimage.sum(mask_zero, labeled, index=np.arange(1, n_features + 1))

    border_labels = set()
    border_labels.update(np.unique(labeled[0, :]).tolist())
    border_labels.update(np.unique(labeled[-1, :]).tolist())
    border_labels.update(np.unique(labeled[:, 0]).tolist())
    border_labels.update(np.unique(labeled[:, -1]).tolist())
    border_labels.discard(0)

    for label_id in range(1, n_features + 1):
        size = int(sizes[label_id - 1])
        touches_border = label_id in border_labels

        if size >= threshold:
            decision = "real_nodata"
            if not touches_border:
                # Untypisch: grosse Gruppe ohne Randkontakt.
                # Per Definition sollte echtes NoData immer den Rand beruehren.
                # Klassifikation bleibt "echt" (bleibt 0,0,0), wird aber
                # markiert, damit der Fall manuell geprueft werden kann.
                decision = "real_nodata_no_border_CHECK"
        else:
            decision = "false_nodata"
            increment_mask |= (labeled == label_id)

        log_rows.append({
            "label_id": label_id,
            "size_px": size,
            "touches_border": touches_border,
            "decision": decision,
        })

    return increment_mask, log_rows


# ---------------------------------------------------------------------------
# GDAL I/O
# ---------------------------------------------------------------------------

def _copy_sidecar_tfw(src_path, dst_path):
    """
    Kopiert eine vorhandene .tfw-Begleitdatei vom Input 1:1 zum Output
    (gleicher Basisname wie dst_path). Original-Werte bleiben so exakt
    erhalten, statt aus den internen GDAL-Tags neu berechnet zu werden.
    Gibt den Zielpfad zurueck, oder None falls keine .tfw gefunden wurde.
    """
    base_src, _ = os.path.splitext(src_path)
    base_dst, _ = os.path.splitext(dst_path)
    for ext in (".tfw", ".TFW"):
        tfw_src = base_src + ext
        if os.path.isfile(tfw_src):
            tfw_dst = base_dst + ".tfw"
            dst_dir = os.path.dirname(tfw_dst)
            if dst_dir:
                os.makedirs(dst_dir, exist_ok=True)
            shutil.copyfile(tfw_src, tfw_dst)
            return tfw_dst
    return None


def process_tile(src_path, dst_path, threshold=10000, increment=3,
                  connectivity=8, write_tfw=False,
                  strip_existing_mask=False, fallback_epsg=2056,
                  nodata_value=0, write_mask=False):
    """
    Liest ein RGB-Tile, korrigiert falsche NoData-Pixel und schreibt das
    Ergebnis nach dst_path. Gibt Zusammenfassungszahlen und allfaellige
    Kontroll-/Warnfaelle zurueck (fuer den CSV-Report).

    nodata_value:
      Der zu korrigierende NoData-Zielwert (0 -> schwarz, 255 -> weiss),
      z.B. aus der GUI-Wahl "NoData der Quelldaten" uebernommen. Falsche
      Pixel werden von diesem Wert weg verschoben: bei 0 um +increment,
      bei 255 um -increment (symmetrisch).

    write_mask:
      Nur zusammen mit strip_existing_mask=True unterstuetzt. Schreibt direkt
      im selben Schreibvorgang eine interne Flag Mask (GDAL_TIFF_INTERNAL_MASK,
      analog tag_mask_on_raster in Script 1), statt das nachgelagerten Skripten
      zu ueberlassen. Die Maske ist aequivalent zu einer Neuberechnung von
      _compute_nodata_mask() auf der bereits korrigierten Datei (echtes
      NoData = Pixel, die nach der Korrektur weiterhin nodata_value sind),
      spart aber einen zusaetzlichen vollstaendigen Lese-/Schreibdurchgang,
      weil die Pixel hier schon im Speicher vorliegen. Der NoData-GDAL-Tag
      wird bewusst NICHT gesetzt (bleibt Aufgabe des nachgelagerten Skripts,
      z.B. wegen GDS-spezifischer Normalisierung des Tag-Werts).

    .tfw-Handling:
      - Existiert neben src_path eine .tfw-Datei, wird diese unveraendert
        zum Output kopiert (bevorzugt, exakte Werte).
      - Existiert keine .tfw beim Input und write_tfw=True, wird stattdessen
        via GDAL-Creation-Option eine .tfw aus den internen Tags erzeugt.

    strip_existing_mask:
      Fuer Tiles, die bereits eine (falsch berechnete) Flag Mask enthalten,
      z.B. ein Alpha-/4. Band, ein internes GDAL Mask Band oder einen
      NoData-Metadaten-Eintrag. In diesem Modus wird die Ausgabedatei NICHT
      per CreateCopy geklont (das wuerde die alte Maske mitkopieren),
      sondern komplett neu aufgebaut: nur die 3 RGB-Baender, kein NoData-Tag,
      kein Mask Band. Das entfernt jede Art von altem Flag-Mask-Mechanismus,
      unabhaengig davon wie er gespeichert war, weil er schlicht nicht
      mitgenommen wird. Georeferenzierung (Geotransform, Projektion) wird
      manuell vom Quellfile uebernommen; falls keine Projektion im Quellfile
      steht, wird ersatzweise fallback_epsg gesetzt (Default: 2056) und das
      wird geloggt.
    """
    if gdal is None:
        raise RuntimeError(
            "GDAL Python-Bindings (osgeo.gdal) nicht gefunden. "
            "Im OSGeo4W-Shell-Python bzw. QGIS-Python-Environment ausfuehren."
        )

    ds = gdal.Open(src_path, gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f"Kann Datei nicht oeffnen: {src_path}")

    n_bands = ds.RasterCount
    if n_bands < 3:
        raise RuntimeError(
            f"{src_path}: erwarte mindestens 3 Baender (RGB), gefunden: {n_bands}"
        )

    xsize = ds.RasterXSize
    ysize = ds.RasterYSize

    band_arrays = [ds.GetRasterBand(i).ReadAsArray() for i in range(1, n_bands + 1)]
    dtype = band_arrays[0].dtype
    gdal_dtype = ds.GetRasterBand(1).DataType

    mask_zero = np.ones((ysize, xsize), dtype=bool)
    for b in band_arrays[:3]:
        mask_zero &= (b == nodata_value)

    increment_mask, log_rows = classify_mask(
        mask_zero,
        threshold=threshold,
        connectivity=connectivity,
    )
    warning_rows = [r for r in log_rows if "CHECK" in r["decision"]]

    # Richtung: bei 0 (schwarz) nach oben, bei 255 (weiss) nach unten -
    # falsche Pixel bewegen sich immer vom NoData-Zielwert weg.
    signed_increment = increment if nodata_value == 0 else -increment

    driver = gdal.GetDriverByName("GTiff")
    sidecar_copied = _copy_sidecar_tfw(src_path, dst_path)

    if strip_existing_mask:
        # Komplett neu aufbauen, nur 3 RGB-Baender, keine alte Maske/NoData
        geotransform = ds.GetGeoTransform()
        projection_wkt = ds.GetProjection()
        used_fallback_epsg = False
        if not projection_wkt:
            if osr is None:
                raise RuntimeError("osgeo.osr nicht verfuegbar, fuer EPSG-Fallback benoetigt.")
            srs = osr.SpatialReference()
            srs.ImportFromEPSG(fallback_epsg)
            projection_wkt = srs.ExportToWkt()
            used_fallback_epsg = True

        if write_mask:
            # Muss VOR driver.Create() gesetzt werden, damit die Maske als
            # interne 1-bit-DEFLATE-Maske im TIFF selbst landet (analog
            # tag_mask_on_raster in Script 1), statt als externe .msk-Datei.
            gdal.SetConfigOption("GDAL_TIFF_INTERNAL_MASK", "YES")

        create_options = ["TFW=YES"] if (write_tfw and not sidecar_copied) else []
        out_ds = driver.Create(dst_path, xsize, ysize, 3, gdal_dtype, options=create_options)
        out_ds.SetGeoTransform(geotransform)
        out_ds.SetProjection(projection_wkt)

        for i in range(3):
            arr = band_arrays[i].copy()
            if increment_mask.any():
                new_vals = arr[increment_mask].astype(np.int32) + signed_increment
                new_vals = np.clip(new_vals, 0, 255).astype(dtype)
                arr[increment_mask] = new_vals
            out_ds.GetRasterBand(i + 1).WriteArray(arr)
            # sicherstellen, dass kein NoData-Tag gesetzt ist
            out_ds.GetRasterBand(i + 1).DeleteNoDataValue()

        if write_mask:
            # Echtes NoData nach der Korrektur = Pixel, die weiterhin
            # nodata_value sind (mask_zero abzueglich der soeben
            # hochgesetzten "falschen" Pixel). Aequivalent zu einer
            # Neuberechnung von _compute_nodata_mask() auf der korrigierten
            # Datei, aber ohne zusaetzlichen Lesedurchgang.
            real_nodata_mask = mask_zero & ~increment_mask
            out_ds.CreateMaskBand(gdal.GMF_PER_DATASET)
            mask_band = out_ds.GetRasterBand(1).GetMaskBand()
            mask_band.WriteArray(np.where(real_nodata_mask, 0, 255).astype(np.uint8))

        out_ds.FlushCache()
        out_ds = None
        ds = None

        return {
            "n_groups": len(log_rows),
            "n_increment_px": int(increment_mask.sum()),
            "warning_rows": warning_rows,
            "tfw": "kopiert" if sidecar_copied else ("erzeugt" if create_options else "keine"),
            "epsg_fallback": fallback_epsg if used_fallback_epsg else None,
            "alte_baender_verworfen": n_bands - 3,
            "mask": "gesetzt" if write_mask else "keine",
        }

    # Standardmodus: Struktur per CreateCopy uebernehmen (fuer Tiles ohne
    # vorbestehende falsche Maske)
    create_options = ["TFW=YES"] if (write_tfw and not sidecar_copied) else []

    if int(mask_zero.sum()) == 0:
        driver.CreateCopy(dst_path, ds, options=create_options)
        ds = None
        return {
            "n_groups": 0,
            "n_increment_px": 0,
            "warning_rows": [],
            "tfw": "kopiert" if sidecar_copied else ("erzeugt" if create_options else "keine"),
            "epsg_fallback": None,
            "alte_baender_verworfen": 0,
        }

    out_ds = driver.CreateCopy(dst_path, ds, options=create_options)

    for i in range(3):
        arr = band_arrays[i].copy()
        if increment_mask.any():
            new_vals = arr[increment_mask].astype(np.int32) + signed_increment
            new_vals = np.clip(new_vals, 0, 255).astype(dtype)
            arr[increment_mask] = new_vals
        out_ds.GetRasterBand(i + 1).WriteArray(arr)

    # Weitere Baender (z.B. 4. Kanal) unveraendert uebernehmen
    for i in range(3, n_bands):
        out_ds.GetRasterBand(i + 1).WriteArray(band_arrays[i])

    out_ds.FlushCache()
    out_ds = None
    ds = None

    return {
        "n_groups": len(log_rows),
        "n_increment_px": int(increment_mask.sum()),
        "warning_rows": warning_rows,
        "tfw": "kopiert" if sidecar_copied else ("erzeugt" if create_options else "keine"),
        "epsg_fallback": None,
        "alte_baender_verworfen": 0,
    }


def process_tile_inplace(path, backup_dir=None, **kwargs):
    """
    Verarbeitet ein Tile "in place": Input und Output sind dieselbe Datei
    (tiff + tfw). Schreibt dazu zuerst in eine temporaere Datei im selben
    Ordner (wichtig fuer os.replace, damit der finale Schritt atomar ist
    und nicht ueber Laufwerksgrenzen kopiert), und ersetzt das Original erst
    danach. Bei einem Fehler bleibt das Original unangetastet, die
    Temp-Datei wird aufgeraeumt.

    Falls backup_dir gesetzt ist, wird das unveraenderte Original (tif + tfw)
    vorher dorthin kopiert, als Sicherheitsnetz bei Produktionsdaten.

    **kwargs werden 1:1 an process_tile() weitergereicht (threshold,
    increment, connectivity, write_tfw, strip_existing_mask, fallback_epsg).
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    base = os.path.basename(path)

    fd, tmp_tif = tempfile.mkstemp(suffix=".tif", prefix=base + "_tmp_", dir=directory)
    os.close(fd)

    original_tfw = os.path.splitext(path)[0] + ".tfw"
    tmp_tfw = os.path.splitext(tmp_tif)[0] + ".tfw"

    try:
        result = process_tile(path, tmp_tif, **kwargs)

        if backup_dir:
            os.makedirs(backup_dir, exist_ok=True)
            shutil.copyfile(path, os.path.join(backup_dir, base))
            if os.path.isfile(original_tfw):
                shutil.copyfile(original_tfw, os.path.join(backup_dir, os.path.basename(original_tfw)))

        os.replace(tmp_tif, path)
        if os.path.isfile(tmp_tfw):
            os.replace(tmp_tfw, original_tfw)

        return result
    except Exception:
        if os.path.isfile(tmp_tif):
            os.remove(tmp_tif)
        if os.path.isfile(tmp_tfw):
            os.remove(tmp_tfw)
        raise


# ---------------------------------------------------------------------------
# CLI / Batch-Verarbeitung
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Korrigiert falsche NoData-Pixel (0,0,0) in DOP-Tiles."
    )
    parser.add_argument("--input", help="Einzelnes Input-Tile (.tif)")
    parser.add_argument("--output", help="Output-Pfad fuer Einzeltile")
    parser.add_argument("--input-dir", help="Ordner mit Input-Tiles (.tif)")
    parser.add_argument("--output-dir", help="Zielordner fuer korrigierte Tiles")
    parser.add_argument("--threshold", type=int, default=10000,
                         help="Gruppen ab dieser Groesse gelten als echtes NoData, darunter als falsch (Default: 10000)")
    parser.add_argument("--increment", type=int, default=3,
                         help="Wert, um den falsche NoData-Pixel vom NoData-Zielwert weg verschoben werden (Default: 3)")
    parser.add_argument("--nodata-value", type=int, choices=[0, 255], default=0,
                         help="NoData-Zielwert der Quelldaten: 0 = schwarz (Default), 255 = weiss")
    parser.add_argument("--connectivity", type=int, choices=[4, 8], default=8,
                         help="Nachbarschaft fuer Connected-Component-Labeling (Default: 8)")
    parser.add_argument("--write-tfw", action="store_true",
                         help="Zusaetzlich .tfw Worldfile schreiben statt nur interner Georeferenzierung")
    parser.add_argument("--strip-existing-mask", action="store_true",
                         help="Fuer Tiles mit bereits vorhandener, falsch berechneter Flag Mask: "
                              "Ausgabe komplett neu aufbauen (nur 3 RGB-Baender, kein NoData-Tag, "
                              "kein Mask Band), statt die Struktur zu klonen.")
    parser.add_argument("--epsg", type=int, default=2056,
                         help="Fallback-EPSG-Code, falls im Quellfile keine Projektion steht "
                              "(nur relevant mit --strip-existing-mask, Default: 2056)")
    parser.add_argument("--in-place", action="store_true",
                         help="Originaldateien (tif + tfw) direkt durch die korrigierte Version "
                              "ersetzen, statt in einen separaten Ordner zu schreiben. "
                              "Schreibt intern zuerst in eine Temp-Datei und ersetzt danach atomar.")
    parser.add_argument("--backup-dir",
                         help="Nur zusammen mit --in-place: Ordner, in den die unveraenderten "
                              "Originale (tif + tfw) vor dem Ueberschreiben kopiert werden.")
    parser.add_argument("--report", help="Optional: CSV-Pfad fuer Kontroll-/Warnfaelle")

    args = parser.parse_args()

    if not args.input and not args.input_dir:
        parser.error("Entweder --input oder --input-dir angeben.")

    if args.backup_dir and not args.in_place:
        parser.error("--backup-dir ist nur zusammen mit --in-place sinnvoll.")

    if args.in_place:
        if args.output or args.output_dir:
            parser.error("--in-place kann nicht zusammen mit --output/--output-dir verwendet werden.")
        if args.input:
            tasks = [(args.input, args.input)]
        else:
            tif_files = sorted(
                f for f in os.listdir(args.input_dir)
                if f.lower().endswith((".tif", ".tiff"))
            )
            if not tif_files:
                print(f"Keine .tif Dateien in {args.input_dir} gefunden.")
                sys.exit(1)
            tasks = [
                (os.path.join(args.input_dir, f), os.path.join(args.input_dir, f))
                for f in tif_files
            ]
    elif args.input:
        out_path = args.output or _default_output_path(args.input)
        if os.path.abspath(out_path) == os.path.abspath(args.input):
            parser.error("--output ist identisch mit --input. Dafuer --in-place verwenden.")
        out_dir = os.path.dirname(out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        tasks = [(args.input, out_path)]
    else:
        if not args.output_dir:
            parser.error("--output-dir wird zusammen mit --input-dir benoetigt.")
        if os.path.abspath(args.output_dir) == os.path.abspath(args.input_dir):
            parser.error("--output-dir ist identisch mit --input-dir. Dafuer --in-place verwenden.")
        os.makedirs(args.output_dir, exist_ok=True)
        tif_files = sorted(
            f for f in os.listdir(args.input_dir)
            if f.lower().endswith((".tif", ".tiff"))
        )
        if not tif_files:
            print(f"Keine .tif Dateien in {args.input_dir} gefunden.")
            sys.exit(1)
        tasks = [
            (os.path.join(args.input_dir, f), os.path.join(args.output_dir, f))
            for f in tif_files
        ]

    all_report_rows = []
    n_ok = 0
    n_failed = 0

    for src_path, dst_path in tasks:
        name = os.path.basename(src_path)
        try:
            common_kwargs = dict(
                threshold=args.threshold,
                increment=args.increment,
                connectivity=args.connectivity,
                write_tfw=args.write_tfw,
                strip_existing_mask=args.strip_existing_mask,
                fallback_epsg=args.epsg,
                nodata_value=args.nodata_value,
            )
            if args.in_place:
                result = process_tile_inplace(src_path, backup_dir=args.backup_dir, **common_kwargs)
            else:
                result = process_tile(src_path, dst_path, **common_kwargs)
            extra = ""
            if result.get("epsg_fallback"):
                extra += f", EPSG-Fallback {result['epsg_fallback']} verwendet"
            if result.get("alte_baender_verworfen"):
                extra += f", {result['alte_baender_verworfen']} alte(s) Band/Baender verworfen"
            print(
                f"{name}: {result['n_groups']} Gruppen gefunden, "
                f"{result['n_increment_px']} Pixel angehoben, "
                f"{len(result['warning_rows'])} Kontroll-/Warnfaelle, "
                f"tfw: {result['tfw']}{extra}"
            )
            for row in result["warning_rows"]:
                all_report_rows.append({"tile": name, **row})
            n_ok += 1
        except Exception as exc:
            print(f"{name}: FEHLER - {exc}")
            n_failed += 1

    print(f"\nFertig: {n_ok} Tiles verarbeitet, {n_failed} Fehler.")

    if args.report and all_report_rows:
        with open(args.report, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=["tile", "label_id", "size_px", "touches_border", "decision"]
            )
            writer.writeheader()
            writer.writerows(all_report_rows)
        print(f"Warn-Report geschrieben: {args.report} ({len(all_report_rows)} Zeilen)")


def _default_output_path(input_path):
    base, ext = os.path.splitext(input_path)
    return f"{base}_fixed{ext}"


if __name__ == "__main__":
    main()