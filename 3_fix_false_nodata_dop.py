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
    Pixelgruppe (>= THRESHOLD Pixel), deren Aussenkontur (Befliegungs-/
    Mosaik-Perimeter) ausserdem ueber eine laengere Strecke entlang eines
    Tile-Rands verlaeuft (>= MIN_BORDER_CONTACT Pixel Randkontakt).
  - "Falsches" NoData sind einzelne Pixel oder kleine Gruppen (< THRESHOLD
    Pixel) innerhalb der Nutzdaten, die durch die Radiometrie zufaellig auf
    0,0,0 gefallen sind (dunkle Schattenzonen).
  - Sonderfall: grosse ueberstrahlte ("ausgebrannte") Gletscherflaechen
    koennen zufaellig an einen Tile-Rand grenzen, teils sogar ueber eine
    laengere Strecke, und waeren nach Groesse und Randkontakt allein nicht
    immer von echtem NoData zu unterscheiden. Der entscheidende Unterschied
    liegt am inneren Rand der Gruppe (dem Teil, der NICHT auf dem Tile-Rand
    liegt, sondern in die Nutzdaten uebergeht): echtes NoData ist ein
    harter Schnitt (Maskierung), die Nutzdaten-Pixel direkt daneben haben
    normale, vom NoData-Wert klar verschiedene Werte. Ueberstrahlung ist ein
    photometrischer Effekt mit weichem Uebergang - die Pixel direkt an der
    Gruppe liegen selbst schon nahe am NoData-Wert (z.B. 220-224 statt exakt
    255,255,255 bei weissem NoData). Dieser Randverlauf-Gradient ist die
    vierte, abschliessende Bedingung.

Vorgehen:
  1. Maske bilden: alle Pixel, bei denen R, G und B gleichzeitig 0 sind
     (Background Value).
  2. Connected-Component-Labeling auf dieser Maske (Standard: 8-Nachbarschaft).
  3. Pro Gruppe: Groesse (Pixelanzahl) und Randkontakt bestimmen. Randkontakt
     ist die Summe der Pixel der Gruppe, die auf einer der vier Tile-Kanten
     (Zeile/Spalte 0 bzw. letzte Zeile/Spalte) liegen, ueber alle Kanten
     zusammengezaehlt (deckt auch Eck-Faelle ab, die zwei Kanten je nur
     kurz beruehren).
  4. Klassifikation, vier Bedingungen nacheinander (jede nur geprueft, wenn
     die vorherige erfuellt ist - spart die teureren Stufen im Regelfall):
       A) Groesse >= THRESHOLD (Default 25000 Pixel)?
       B) Randkontakt vorhanden (> 0 Pixel)?
       C) Randkontakt >= MIN_BORDER_CONTACT (Default 100 Pixel)?
       D) Nur fuer den nicht auf dem Tile-Rand liegenden Teil des inneren
          Gruppenrands: sind die direkt angrenzenden Nutzdaten-Pixel
          mehrheitlich NAHE am NoData-Wert (weicher Uebergang, Default-
          Toleranz 40, Default-Anteil 50%)? Wenn ja -> trotz A-C "falsch"
          (Ueberstrahlung/Schatten-Clipping). Wenn nein (harter Schnitt)
          -> "echt".
       A, B oder C nicht erfuellt -> sofort "falsch", D wird nicht geprueft.
  5. Nur die Baender 1-3 (RGB) werden veraendert. Ein evtl. vorhandenes 4.
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

def classify_mask(mask_zero, band_arrays_rgb, nodata_value, threshold=25000,
                   connectivity=8, min_border_contact=100,
                   gradient_tolerance=None, gradient_ring_fraction=0.5,
                   min_fill_ratio=0.15, enable_gradient_check=False,
                   enable_fill_ratio_check=False):
    """
    Klassifiziert zusammenhaengende Gruppen von True-Werten in mask_zero
    als "echtes NoData" (bleibt) oder "falsches NoData" (wird angehoben).

    Fuenfstufige Pruefung, jede Stufe nur, wenn die vorherige erfuellt ist
    (spart die teureren Stufen im Regelfall):
      A) Groesse >= threshold?        Sonst sofort "falsch".
      B) Beruehrt die Gruppe ueberhaupt einen Tile-Rand?  Sonst "falsch".
      C) Randkontakt (Summe der Gruppenpixel auf allen vier Tile-Kanten
         zusammen, nicht nur ein Ja/Nein pro Kante - deckt auch Eck-Faelle
         ab) >= min_border_contact?   Sonst "falsch".
      D) [STANDARDMAESSIG DEAKTIVIERT, siehe unten] Nur fuer den Teil des
         Gruppenrands, der NICHT auf dem Tile-Rand liegt (also in die
         Nutzdaten uebergeht): sind die direkt angrenzenden Pixel
         mehrheitlich nahe am NoData-Wert (weicher Uebergang)? Wenn ja ->
         trotz A-C "falsch", sonst weiter zu E.
      E) [STANDARDMAESSIG DEAKTIVIERT, siehe unten] Nur wenn D "echt"
         ergeben hat: Bounding-Box-Fuellgrad (Groesse der Gruppe / Flaeche
         ihrer Bounding-Box) >= min_fill_ratio? Kompakte, block-/
         keilfoermige Flaechen (typisch fuer einen Perimeter-Schnitt) haben
         einen hohen Fuellgrad. Duenne, verzweigte Formen (typisch fuer
         Gletscherspalten/Grate) haben einen niedrigen Fuellgrad. Wenn zu
         niedrig -> trotz A-D "falsch", sonst "echt".

    enable_gradient_check / enable_fill_ratio_check (Default: False):
      Stufe D bzw. E sind seit einem Vorfall (WALLIS_SAASTAL, 05.08.2026)
      standardmaessig DEAKTIVIERT: Stufe D nahm faelschlich riesige, echte
      NoData-Flaechen als "falsch" an, weil deren innerer Rand bei diesem
      Mosaik (13 Befliegungslinien) vermutlich weich ausgeblendet
      (Feathering aus der Photogrammetrie-Produktion) statt hart
      geschnitten ist - die Annahme "weicher Uebergang = Ueberstrahlung"
      gilt also nicht fuer jeden Datensatz. Klassifikation basiert bis auf
      Weiteres nur auf A-C (Groesse + Randkontakt), wie vor dieser
      Erweiterung. Auf True setzen nur zu Testzwecken, bis eine fuer
      Feathering robuste Alternative gefunden ist.

    Grund fuer Stufe C: eine grosse, ueberstrahlte Gletscherflaeche kann
    zufaellig an einen Tile-Rand grenzen, teils sogar ueber eine laengere
    Strecke, und waere nach Groesse und Randkontakt allein nicht immer von
    echtem Mosaik-NoData zu unterscheiden.

    Grund fuer Stufe D: der eigentliche Unterschied liegt am inneren
    Gruppenrand. Echtes NoData ist ein harter Maskierungs-Schnitt - die
    Nutzdaten-Pixel direkt daneben sind normale, klar vom NoData-Wert
    verschiedene Werte. Ueberstrahlung/Schatten-Clipping ist dagegen ein
    photometrischer Effekt mit weichem Uebergang - die angrenzenden Pixel
    liegen selbst schon nahe am NoData-Wert (Ueberstrahlung bei weiss:
    Werte 220-254; Schatten-Clipping bei schwarz: Werte 1-20 - beide
    Bereiche ausdruecklich ohne den exakten NoData-Wert selbst). Ein direkt
    angrenzendes Pixel mit exaktem NoData-Wert gehoert per
    Connected-Component-Labeling ohnehin schon zur Gruppe selbst, deshalb
    kann der Ring nur "nahe, aber nicht exakt gleich" enthalten.

    band_arrays_rgb:
      Liste der drei RGB-Baender (2D-Arrays, gleiche Form wie mask_zero),
      fuer die Randverlauf-Pruefung in Stufe D.

    gradient_tolerance:
      None (Default) -> automatisch anhand nodata_value gewaehlt: 35 bei
      255 (deckt 220-254 ab, Ueberstrahlung/Sensorsaettigung), 20 bei 0
      (deckt 1-20 ab, Schatten-Clipping). Die beiden Effekte sind
      photometrisch nicht symmetrisch, deshalb kein gemeinsamer Wert.
      Explizit gesetzter Wert (int) uebersteuert die Automatik.

    min_fill_ratio:
      Schwelle fuer Stufe E (Default 0.15 = 15%). Bewusst konservativ
      niedrig angesetzt: nur sehr duenne/verzweigte Formen sollen dadurch
      als falsch erkannt werden, nicht jede unregelmaessige, aber
      plausible Perimeterform.

    Rueckgabe:
        increment_mask : bool-Array, True = diese Pixel sollen angehoben werden
        log_rows        : Liste von Dicts mit Infos pro Gruppe (fuer Report/Debug)
    """
    if gradient_tolerance is None:
        gradient_tolerance = 35 if nodata_value == 255 else 20

    structure = np.ones((3, 3), dtype=int) if connectivity == 8 else None
    labeled, n_features = ndimage.label(mask_zero, structure=structure)

    increment_mask = np.zeros_like(mask_zero, dtype=bool)
    log_rows = []

    if n_features == 0:
        return increment_mask, log_rows

    sizes = ndimage.sum(mask_zero, labeled, index=np.arange(1, n_features + 1))

    # Stufe A zuerst fuer alle Gruppen pruefen. Der Randkontakt (Stufe B/C)
    # wird erst berechnet, wenn ueberhaupt eine Gruppe die Groesse-Schwelle
    # erreicht - im Regelfall (keine Gruppe auch nur annaehernd so gross)
    # entfaellt dieser Schritt komplett.
    candidate_label_ids = {
        label_id for label_id, size in enumerate(sizes, start=1) if size >= threshold
    }

    border_contact_counts = None
    if candidate_label_ids:
        border_mask = np.zeros_like(mask_zero, dtype=bool)
        border_mask[0, :] = True
        border_mask[-1, :] = True
        border_mask[:, 0] = True
        border_mask[:, -1] = True

        border_pixel_labels = labeled[border_mask]
        border_contact_counts = np.bincount(
            border_pixel_labels[border_pixel_labels > 0], minlength=n_features + 1
        )

    # Bounding-Boxen fuer Stufe E: ein einziger Aufruf ueber alle Labels,
    # kein zusaetzlicher Pixel-Durchgang (arbeitet auf dem bereits
    # vorliegenden labeled-Array). Nur berechnet, wenn ueberhaupt ein
    # Kandidat existiert UND Stufe E aktiv ist.
    bounding_boxes = (
        ndimage.find_objects(labeled)
        if (candidate_label_ids and enable_fill_ratio_check) else None
    )

    dilation_structure = np.ones((3, 3), dtype=bool)

    for label_id in range(1, n_features + 1):
        size = int(sizes[label_id - 1])
        ring_close_fraction = None

        if label_id not in candidate_label_ids:
            # Stufe A nicht erfuellt -> falsch, B-D werden gar nicht erst
            # geprueft.
            touches_border = None
            decision = "false_nodata"
        else:
            border_contact_px = int(border_contact_counts[label_id])
            touches_border = border_contact_px > 0
            if not (touches_border and border_contact_px >= min_border_contact):
                # Stufe B nicht erfuellt (kein Randkontakt) oder Stufe C
                # nicht erfuellt (Randkontakt zu kurz) -> falsch, Stufe D
                # wird nicht mehr geprueft.
                decision = "false_nodata"
            elif not enable_gradient_check:
                # Stufe D deaktiviert (Default, siehe Docstring) -> A-C
                # reichen fuer "echt", Stufe E folgt nur wenn aktiv.
                decision = "real_nodata"
            else:
                # Stufe D: Randverlauf am inneren (nicht auf dem Tile-Rand
                # liegenden) Teil der Gruppe pruefen. Ein direkt
                # angrenzendes Pixel mit exaktem NoData-Wert waere bereits
                # Teil derselben Gruppe (Connected-Component-Labeling),
                # der Ring enthaelt also nur echte Nutzdaten-Nachbarn.
                group_mask = (labeled == label_id)
                ring = ndimage.binary_dilation(
                    group_mask, structure=dilation_structure
                ) & ~group_mask

                if not ring.any():
                    # Gruppe fuellt das ganze Tile aus - kein innerer Rand
                    # zu pruefen, Stufe D kann nicht widerlegen -> echt.
                    decision = "real_nodata"
                else:
                    diffs = np.abs(
                        np.stack(
                            [b[ring].astype(np.int32) for b in band_arrays_rgb],
                            axis=0,
                        ) - nodata_value
                    )
                    close_px = np.all(diffs <= gradient_tolerance, axis=0)
                    ring_close_fraction = float(close_px.mean())

                    if ring_close_fraction >= gradient_ring_fraction:
                        # Weicher Uebergang -> Ueberstrahlung/Clipping,
                        # trotz Stufe A-C "falsch". Stufe E wird nicht mehr
                        # geprueft.
                        decision = "false_nodata"
                    else:
                        # Harter Schnitt -> Stufe E pruefen.
                        decision = "real_nodata"

        if decision == "real_nodata" and enable_fill_ratio_check:
            # Stufe E: Bounding-Box-Fuellgrad. Nutzt die bereits berechnete
            # Bounding-Box, kein zusaetzlicher Pixel-Durchgang.
            bbox = bounding_boxes[label_id - 1]
            bbox_area = (
                (bbox[0].stop - bbox[0].start) * (bbox[1].stop - bbox[1].start)
            )
            fill_ratio = size / bbox_area
            if fill_ratio < min_fill_ratio:
                # Duenne, verzweigte Form -> trotz A-D "falsch".
                decision = "false_nodata"

        if decision == "false_nodata":
            increment_mask |= (labeled == label_id)

        log_rows.append({
            "label_id": label_id,
            "size_px": size,
            "touches_border": touches_border,
            "ring_close_fraction": ring_close_fraction,
            "decision": decision,
        })

    return increment_mask, log_rows


# ---------------------------------------------------------------------------
# GDAL I/O
# ---------------------------------------------------------------------------

_WRITE_CHUNK_ROWS = 1000


def _write_band_chunked(out_band, arr, chunk_rows=_WRITE_CHUNK_ROWS):
    """
    Schreibt arr zeilenweise in Bloecken statt in einem einzigen WriteArray-
    Aufruf (analog chunk_rows in Script 1s _compute_nodata_mask). Das
    Connected-Component-Labeling selbst braucht zwingend das komplette
    Array (kann nicht gechunkt werden, ohne den Algorithmus neu zu bauen),
    aber das Schreiben danach ist unabhaengig davon und profitiert bei
    sehr grossen Tiles von kleineren, aufeinanderfolgenden Schreibzugriffen
    statt einem einzelnen sehr grossen.
    """
    y_size = arr.shape[0]
    for y_off in range(0, y_size, chunk_rows):
        rows = min(chunk_rows, y_size - y_off)
        out_band.WriteArray(arr[y_off:y_off + rows, :], 0, y_off)


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


def process_tile(src_path, dst_path, threshold=25000, increment=7,
                  connectivity=8, write_tfw=False,
                  strip_existing_mask=False, fallback_epsg=2056,
                  nodata_value=0, write_mask=False,
                  rewrite_real_nodata_to_zero=False, min_border_contact=100,
                  gradient_tolerance=None, gradient_ring_fraction=0.5,
                  min_fill_ratio=0.15, enable_gradient_check=False,
                  enable_fill_ratio_check=False):
    """
    Liest ein RGB-Tile, korrigiert falsche NoData-Pixel und schreibt das
    Ergebnis nach dst_path. Gibt Zusammenfassungszahlen und allfaellige
    Kontroll-/Warnfaelle zurueck (fuer den CSV-Report).

    nodata_value:
      Der zu korrigierende NoData-Zielwert (0 -> schwarz, 255 -> weiss),
      z.B. aus der GUI-Wahl "NoData der Quelldaten" uebernommen. Falsche
      Pixel werden von diesem Wert weg verschoben: bei 0 um +increment,
      bei 255 um -increment (symmetrisch).

    min_border_contact:
      Dritte Bedingung fuer "echtes NoData" (siehe classify_mask): eine
      Gruppe gilt nur dann als echt, wenn sie zusaetzlich zur Groesse auch
      ueber mindestens so viele Pixel den Tile-Rand beruehrt. Verhindert,
      dass grosse, ueberstrahlte Gletscherflaechen, die den Rand nur schmal
      kreuzen, faelschlich als echtes NoData stehen bleiben.

    gradient_tolerance / gradient_ring_fraction:
      Vierte, abschliessende Bedingung (siehe classify_mask): prueft am
      inneren Gruppenrand (nicht auf dem Tile-Rand), ob die angrenzenden
      Nutzdaten-Pixel nahe (gradient_tolerance) am NoData-Wert liegen und
      das bei einem Mindestanteil (gradient_ring_fraction) der Randpixel.
      Erkennt weiche Uebergaenge durch Ueberstrahlung/Schatten-Clipping,
      die trotz Groesse und Randkontakt kein echtes NoData sind.
      gradient_tolerance=None (Default) waehlt automatisch 35 bei
      nodata_value=255 bzw. 20 bei nodata_value=0 (siehe classify_mask).

    min_fill_ratio:
      Fuenfte, abschliessende Bedingung (siehe classify_mask): Bounding-
      Box-Fuellgrad der Gruppe, nur geprueft wenn Stufe A-D "echt" ergeben
      haben. Verwirft duenne, verzweigte Formen (Default 0.15).

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

    rewrite_real_nodata_to_zero:
      Nur wirksam zusammen mit write_mask=True und nodata_value=255 (historische
      DOP-Tiles mit weissem NoData 255,255,255). Setzt die RGB-Baender (1-3)
      an allen als "echtes NoData" klassifizierten Pixeln (real_nodata_mask,
      also NICHT die soeben korrigierten "falschen" Gruppen) zusaetzlich
      direkt auf 0,0,0. Nutzt dieselben, bereits im Speicher vorliegenden
      Pixelarrays - kein zusaetzlicher Lese-/Schreibdurchgang. Ziel: Pixelwerte,
      Flag Mask und NoData-Tag/XML sind danach durchgehend konsistent auf 0
      normalisiert, nicht nur Tag/XML wie bisher.

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
        band_arrays[:3],
        nodata_value,
        threshold=threshold,
        connectivity=connectivity,
        min_border_contact=min_border_contact,
        gradient_tolerance=gradient_tolerance,
        gradient_ring_fraction=gradient_ring_fraction,
        min_fill_ratio=min_fill_ratio,
        enable_gradient_check=enable_gradient_check,
        enable_fill_ratio_check=enable_fill_ratio_check,
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

        # Echtes NoData nach der Korrektur = Pixel, die weiterhin nodata_value
        # sind (mask_zero abzueglich der soeben hochgesetzten "falschen"
        # Pixel). Aequivalent zu einer Neuberechnung von _compute_nodata_mask()
        # auf der korrigierten Datei, aber ohne zusaetzlichen Lesedurchgang.
        real_nodata_mask = mask_zero & ~increment_mask
        do_rewrite = rewrite_real_nodata_to_zero and nodata_value == 255
        has_increment = increment_mask.any()
        has_rewrite = do_rewrite and real_nodata_mask.any()

        for i in range(3):
            # Kein .copy() mehr noetig: band_arrays[i] wird danach nicht
            # mehr im Originalzustand gebraucht, direktes In-Place-Aendern
            # spart eine komplette zusaetzliche Array-Kopie im Speicher.
            arr = band_arrays[i]
            if has_increment:
                new_vals = arr[increment_mask].astype(np.int32) + signed_increment
                arr[increment_mask] = np.clip(new_vals, 0, 255).astype(dtype)
            if has_rewrite:
                arr[real_nodata_mask] = 0
            out_band = out_ds.GetRasterBand(i + 1)
            _write_band_chunked(out_band, arr)
            # sicherstellen, dass kein NoData-Tag gesetzt ist
            out_band.DeleteNoDataValue()

        if write_mask:
            out_ds.CreateMaskBand(gdal.GMF_PER_DATASET)
            mask_band = out_ds.GetRasterBand(1).GetMaskBand()
            mask_arr = np.where(real_nodata_mask, 0, 255).astype(np.uint8)
            _write_band_chunked(mask_band, mask_arr)

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

    has_increment = increment_mask.any()
    for i in range(3):
        # Kein .copy() mehr noetig, siehe strip_existing_mask-Zweig oben.
        arr = band_arrays[i]
        if has_increment:
            new_vals = arr[increment_mask].astype(np.int32) + signed_increment
            arr[increment_mask] = np.clip(new_vals, 0, 255).astype(dtype)
        _write_band_chunked(out_ds.GetRasterBand(i + 1), arr)

    # Weitere Baender (z.B. 4. Kanal) unveraendert uebernehmen
    for i in range(3, n_bands):
        _write_band_chunked(out_ds.GetRasterBand(i + 1), band_arrays[i])

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
    increment, connectivity, write_tfw, strip_existing_mask, fallback_epsg,
    write_mask, rewrite_real_nodata_to_zero, min_border_contact,
    gradient_tolerance, gradient_ring_fraction, min_fill_ratio,
    enable_gradient_check, enable_fill_ratio_check).
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
    parser.add_argument("--threshold", type=int, default=25000,
                         help="Gruppen ab dieser Groesse gelten als echtes NoData, darunter als falsch (Default: 25000)")
    parser.add_argument("--min-border-contact", type=int, default=100,
                         help="Dritte Bedingung fuer echtes NoData: Gruppe muss zusaetzlich zur Groesse "
                              "ueber mindestens so viele Pixel den Tile-Rand beruehren (Summe ueber alle "
                              "Kanten), sonst gilt sie als falsch (Default: 100)")
    parser.add_argument("--gradient-tolerance", type=int, default=None,
                         help="Vierte Bedingung fuer echtes NoData: maximale Differenz zum NoData-Wert, "
                              "ab der ein Randpixel als 'nahe' gilt. Default: automatisch anhand "
                              "--nodata-value (35 bei 255, 20 bei 0)")
    parser.add_argument("--gradient-ring-fraction", type=float, default=0.5,
                         help="Vierte Bedingung fuer echtes NoData: Mindestanteil der inneren Randpixel, "
                              "die nahe am NoData-Wert liegen muessen, damit die Gruppe trotz Groesse und "
                              "Randkontakt als falsch (Ueberstrahlung) gilt (Default: 0.5)")
    parser.add_argument("--min-fill-ratio", type=float, default=0.15,
                         help="Fuenfte Bedingung fuer echtes NoData: Bounding-Box-Fuellgrad der Gruppe "
                              "(nur geprueft, wenn Stufe A-D bereits 'echt' ergeben haben), darunter gilt "
                              "sie trotzdem als falsch (duenne, verzweigte Form) (Default: 0.15)")
    parser.add_argument("--enable-gradient-check", action="store_true",
                         help="Stufe D (Randverlauf-Gradient) aktivieren - seit Vorfall WALLIS_SAASTAL "
                              "(05.08.2026) standardmaessig AUS, siehe Docstring classify_mask. Nur zu "
                              "Testzwecken auf einem Datensatz ohne weiche Mosaikkanten (Feathering) setzen.")
    parser.add_argument("--enable-fill-ratio-check", action="store_true",
                         help="Stufe E (Bounding-Box-Fuellgrad) aktivieren - wirkt nur zusammen mit "
                              "--enable-gradient-check, standardmaessig AUS (siehe Docstring classify_mask).")
    parser.add_argument("--increment", type=int, default=7,
                         help="Wert, um den falsche NoData-Pixel vom NoData-Zielwert weg verschoben werden (Default: 7)")
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
    parser.add_argument("--rewrite-nodata-to-zero", action="store_true",
                         help="Nur zusammen mit --strip-existing-mask und --nodata-value 255: "
                              "setzt die RGB-Baender an allen als echtes NoData erkannten Pixeln "
                              "zusaetzlich direkt auf 0,0,0 (historische 255er-NoData-DOPs).")
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
                rewrite_real_nodata_to_zero=args.rewrite_nodata_to_zero,
                min_border_contact=args.min_border_contact,
                gradient_tolerance=args.gradient_tolerance,
                gradient_ring_fraction=args.gradient_ring_fraction,
                min_fill_ratio=args.min_fill_ratio,
                enable_gradient_check=args.enable_gradient_check,
                enable_fill_ratio_check=args.enable_fill_ratio_check,
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