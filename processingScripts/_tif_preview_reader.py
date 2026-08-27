"""
_tif_preview_reader.py  –  Wird via OSGeo4W Python aufgerufen (NICHT direkt starten).

Liest ein Tiff (dezimiert, ohne volle Auflösung zu laden) und schreibt eine
einfache 8BIT-RGB-Vorschau als PPM (P6), damit das Haupt-GUI (Standard-Python,
ohne GDAL-Bindings) sie mit tk.PhotoImage anzeigen kann - ohne Pillow.

Zweck: rein visuelle Kontrolle der Rand-NoData-Pixel (schwarz/weiss) im GUI-
Button "check input-NoData". Keine Georeferenzierung, keine radiometrische
Korrektur - nur Darstellung.

Aufruf:
    <osgeo_python> _tif_preview_reader.py <input.tif> <output.ppm> <max_dim_px>
"""

import sys

import numpy as np
from osgeo import gdal

gdal.UseExceptions()


def build_preview(in_path, out_path, max_dim):
    ds = gdal.Open(in_path, gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f"Datei konnte nicht geöffnet werden: {in_path}")

    xsize, ysize = ds.RasterXSize, ds.RasterYSize
    scale = min(1.0, max_dim / float(max(xsize, ysize)))
    out_w = max(1, round(xsize * scale))
    out_h = max(1, round(ysize * scale))

    # Nur die ersten 3 Bänder (RGB) für die Darstellung – der NoData-Wert
    # ("0 0 0[ 0]" bzw. "255…" / "65535…") ist laut GUI-Auswahl über ALLE
    # Bänder identisch, ein evtl. 4. Band (NIR/Alpha bei SB_DOP_16) ist für
    # den reinen Schwarz/Weiss-Randcheck nicht relevant.
    n_bands = min(3, ds.RasterCount)
    try:
        resample = gdal.GRIORA_Average
    except AttributeError:
        resample = None

    bands = []
    for i in range(1, n_bands + 1):
        band = ds.GetRasterBand(i)
        kwargs = dict(buf_xsize=out_w, buf_ysize=out_h)
        if resample is not None:
            kwargs["resample_alg"] = resample
        bands.append(band.ReadAsArray(**kwargs))

    if n_bands == 1:
        bands = bands * 3  # Graustufen -> RGB dupliziert

    stack = np.stack(bands, axis=-1)

    if ds.GetRasterBand(1).DataType != gdal.GDT_Byte:
        # 16BIT (SB_DOP_16 NRGB): Perzentil-Stretch für eine sichtbare
        # Darstellung. Reine 0- bzw. Maximalwert-NoData-Pixel liegen ausserhalb
        # der 2/98%-Spanne und werden dabei exakt auf schwarz/weiss geclippt,
        # der Randcheck bleibt also korrekt.
        lo, hi = np.percentile(stack, (2, 98))
        if hi <= lo:
            hi = lo + 1
        stack = np.clip((stack.astype(np.float32) - lo) * (255.0 / (hi - lo)), 0, 255)

    stack = stack.astype(np.uint8)

    with open(out_path, "wb") as f:
        f.write(f"P6\n{out_w} {out_h}\n255\n".encode("ascii"))
        f.write(stack.tobytes())


def main():
    if len(sys.argv) != 4:
        print("[FEHLER] Aufruf: _tif_preview_reader.py <input.tif> <output.ppm> <max_dim_px>", flush=True)
        sys.exit(1)

    in_path, out_path, max_dim = sys.argv[1], sys.argv[2], int(sys.argv[3])
    try:
        build_preview(in_path, out_path, max_dim)
        print("OK", flush=True)
    except Exception as e:
        print(f"[FEHLER] {e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
