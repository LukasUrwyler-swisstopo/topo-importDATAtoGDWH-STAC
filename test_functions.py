"""
Unit-Tests fuer die reinen Python-Funktionen der GDWH-Import-Scripts.
Getestet werden nur Funktionen ohne externe Abhaengigkeiten (kein GDAL, keine echten Dateien).

Ausfuehren:
    python test_functions.py
    python -m pytest test_functions.py -v   (falls pytest installiert)
"""

import importlib.util
import os
import sys
import tempfile
import unittest
import unittest.mock
from unittest.mock import MagicMock

import numpy as np

# ============================================================
#  osgeo/gdal als Mock registrieren, damit die Scripts ohne
#  OSGeo4W-Installation importierbar sind.
#  Nur Funktionen, die in den getesteten Funktionen NICHT
#  verwendet werden, muessen nicht exakt simuliert werden.
# ============================================================
_gdal_mock = MagicMock()
_gdal_mock.UseExceptions = MagicMock()
sys.modules.setdefault("osgeo", MagicMock())
sys.modules.setdefault("osgeo.gdal", _gdal_mock)


# ============================================================
#  Hilfsfunktion: Script per Pfad importieren
#  (noetig weil Dateinamen mit Ziffern beginnen)
# ============================================================
def _import_script(filename):
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, filename)
    spec = importlib.util.spec_from_file_location(filename, path)
    mod  = importlib.util.module_from_spec(spec)
    # Ausgabe des version-print unterdruecken
    old_stdout = sys.stdout
    sys.stdout = open(os.devnull, "w")
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.stdout.close()
        sys.stdout = old_stdout
    return mod

allGDS  = _import_script("1_allGDS_upload_GDWH_withCHECKxml.py")
dop16   = _import_script("2_2_SB_DOP_16_GDS_upload_GDWH_withCHECKxml.py")
organiz = _import_script("2_1_SB_DOP_16_FOLDERorganize_by_lineID.py")


# ============================================================
#  parse_line_id_to_hundredths  (aus allGDS und dop16)
# ============================================================
class TestParseLineId(unittest.TestCase):

    def _check(self, fn, line_id, expected):
        result = fn(line_id)
        self.assertIsNotNone(result, f"Ergebnis fuer '{line_id}' sollte nicht None sein")
        self.assertEqual(result, expected)

    # -- Format HHMM (keine Sekunden) --
    def test_hhmm_format(self):
        erwartet = {"year": 2023, "month": 8, "day": 20,
                    "hh": 9, "mm": 21, "ss": 0, "hundredths": 0}
        self._check(allGDS.parse_line_id_to_hundredths, "20230820_0921", erwartet)
        self._check(dop16.parse_line_id_to_hundredths,  "20230820_0921", erwartet)

    # -- Format HHMMSS --
    def test_hhmmss_format(self):
        erwartet = {"year": 2023, "month": 8, "day": 20,
                    "hh": 9, "mm": 21, "ss": 30, "hundredths": 0}
        self._check(allGDS.parse_line_id_to_hundredths, "20230820_092130", erwartet)
        self._check(dop16.parse_line_id_to_hundredths,  "20230820_092130", erwartet)

    # -- Format HHMMSSss (4 Stellen nach HHMM: Sekunden + Hundertstel) --
    def test_hhmmssss_format(self):
        erwartet = {"year": 2023, "month": 8, "day": 20,
                    "hh": 9, "mm": 21, "ss": 30, "hundredths": 45}
        self._check(allGDS.parse_line_id_to_hundredths, "20230820_09213045", erwartet)
        self._check(dop16.parse_line_id_to_hundredths,  "20230820_09213045", erwartet)

    # -- Format HHMMSSsss (5 Stellen: Millisekunden -> runden) --
    def test_hhmmsssss_millis_runden(self):
        # 456ms -> round(456/10) = 46
        erwartet = {"year": 2023, "month": 8, "day": 20,
                    "hh": 9, "mm": 21, "ss": 30, "hundredths": 46}
        self._check(allGDS.parse_line_id_to_hundredths, "20230820_092130456", erwartet)

    # -- Rundungsclamp: 999ms -> round(999/10)=100 -> clamped auf 99 --
    def test_millis_clamp_bei_99(self):
        result = allGDS.parse_line_id_to_hundredths("20230820_092130999")
        self.assertIsNotNone(result)
        self.assertEqual(result["hundredths"], 99)

    # -- Reales Beispiel aus den Scripts --
    def test_reales_beispiel(self):
        result = allGDS.parse_line_id_to_hundredths("20200913_1054_12501")
        self.assertIsNotNone(result)
        self.assertEqual(result["year"], 2020)
        self.assertEqual(result["month"], 9)
        self.assertEqual(result["day"], 13)
        self.assertEqual(result["hh"], 10)
        self.assertEqual(result["mm"], 54)

    # -- Ungueltige Eingaben liefern None --
    def test_ungueltige_eingabe_kein_unterstrich(self):
        self.assertIsNone(allGDS.parse_line_id_to_hundredths("20230820"))

    def test_leerer_string(self):
        self.assertIsNone(allGDS.parse_line_id_to_hundredths(""))


# ============================================================
#  format_iso8601_hundredths
# ============================================================
class TestFormatIso8601(unittest.TestCase):

    def _parsed(self):
        return {"year": 2023, "month": 8, "day": 20,
                "hh": 9, "mm": 21, "ss": 30, "hundredths": 45}

    def test_normalformat(self):
        self.assertEqual(allGDS.format_iso8601_hundredths(self._parsed()),
                         "2023-08-20T09:21:30.45")
        self.assertEqual(dop16.format_iso8601_hundredths(self._parsed()),
                         "2023-08-20T09:21:30.45")

    def test_null_liefert_unknown(self):
        self.assertEqual(allGDS.format_iso8601_hundredths(None), "UNKNOWN")
        self.assertEqual(dop16.format_iso8601_hundredths(None),  "UNKNOWN")

    def test_fuehrende_nullen(self):
        parsed = {"year": 2023, "month": 1, "day": 5,
                  "hh": 7, "mm": 3, "ss": 9, "hundredths": 4}
        self.assertEqual(allGDS.format_iso8601_hundredths(parsed),
                         "2023-01-05T07:03:09.04")


# ============================================================
#  format_stac_datetime
# ============================================================
class TestFormatStacDatetime(unittest.TestCase):

    def _parsed(self):
        return {"year": 2023, "month": 8, "day": 20,
                "hh": 9, "mm": 21, "ss": 0, "hundredths": 0}

    def test_normalformat(self):
        self.assertEqual(allGDS.format_stac_datetime(self._parsed()),
                         "2023-08-20t09210000")
        self.assertEqual(dop16.format_stac_datetime(self._parsed()),
                         "2023-08-20t09210000")

    def test_null_liefert_unknown(self):
        self.assertEqual(allGDS.format_stac_datetime(None), "UNKNOWN")

    def test_mit_hundertstelsekunden(self):
        parsed = {"year": 2023, "month": 8, "day": 20,
                  "hh": 9, "mm": 21, "ss": 30, "hundredths": 45}
        self.assertEqual(allGDS.format_stac_datetime(parsed), "2023-08-20t09213045")


# ============================================================
#  extract_area  (aus allGDS – GDS-spezifisch)
# ============================================================
class TestExtractAreaAllGDS(unittest.TestCase):

    def test_sb_dop_einwort(self):
        self.assertEqual(allGDS.extract_area("2025_BERN_DOP_0001_LV95.tif", "SB_DOP"), "BERN")

    def test_sb_dop_mehrere_woerter(self):
        self.assertEqual(
            allGDS.extract_area("2025_PLAINE_MORTE_DOP_1001_LV95.tif", "SB_DOP"),
            "PLAINE_MORTE")

    def test_sb_dop_16(self):
        self.assertEqual(
            allGDS.extract_area("2025_PLAINE_MORTE_DOP_1005NRGB_2601_1136_LV95.tif", "SB_DOP_16"),
            "PLAINE_MORTE")

    def test_sb_dsm(self):
        self.assertEqual(
            allGDS.extract_area("2025_PLAINE_MORTE_DSM_1000_LV95.tif", "SB_DSM"),
            "PLAINE_MORTE")

    def test_sb_dsm_hillshade(self):
        self.assertEqual(
            allGDS.extract_area("2025_PLAINE_MORTE_hillshade_1000.tif", "SB_DSM"),
            "PLAINE_MORTE")

    def test_sb_dsm_punktwolke(self):
        self.assertEqual(
            allGDS.extract_area("2025_PLAINE_MORTE_TIN_2601_1136_LV95_LN02.laz", "SB_DSM_PUNKTWOLKE"),
            "PLAINE_MORTE")

    def test_kein_match_liefert_unknown(self):
        result = allGDS.extract_area("kein_jahres_prefix.tif", "SB_DOP")
        self.assertEqual(result, "UNKNOWN")


# ============================================================
#  extract_area  (aus dop16 – nur _DOP)
# ============================================================
class TestExtractAreaDop16(unittest.TestCase):

    def test_einwort(self):
        self.assertEqual(dop16.extract_area("2025_BERN_DOP_10cm_20250802_1005_12501NRGB_LV95.tif"), "BERN")

    def test_mehrere_woerter(self):
        self.assertEqual(
            dop16.extract_area("2025_PLAINE_MORTE_DOP_10cm_2601_1136_LV95.tif"),
            "PLAINE_MORTE")

    def test_kein_match(self):
        self.assertEqual(dop16.extract_area("irgendwas.tif"), "UNKNOWN")


# ============================================================
#  extract_tile_lv95  (aus allGDS)
# ============================================================
class TestExtractTileLv95AllGDS(unittest.TestCase):

    def test_laz_datei(self):
        self.assertEqual(
            allGDS.extract_tile_lv95("2025_PLAINE_MORTE_TIN_2601_1136_LV95_LN02.laz"),
            "2601_1136")

    def test_tif_datei(self):
        self.assertEqual(
            allGDS.extract_tile_lv95("2025_PLAINE_MORTE_DOP_1005NRGB_2602_1145_LV95_LN02.tif"),
            "2602_1145")

    def test_kein_lv95_liefert_unknown(self):
        result = allGDS.extract_tile_lv95("kein_lv95_hier.laz")
        self.assertEqual(result, "UNKNOWN")


# ============================================================
#  extract_tile  (aus dop16)
# ============================================================
class TestExtractTileDop16(unittest.TestCase):

    def test_normal(self):
        self.assertEqual(
            dop16.extract_tile("2025_PLAINE_MORTE_DOP_10cm_2602_1145_LV95.tif"),
            "2602_1145")

    def test_kein_lv95(self):
        result = dop16.extract_tile("kein_lv95.tif")
        self.assertEqual(result, "UNKNOWN")


# ============================================================
#  get_nodata_value  (aus allGDS)
# ============================================================
class TestGetNodataValue(unittest.TestCase):

    def test_hillshade_immer_weiss(self):
        meta = {"NoData": "255 255 255"}
        self.assertEqual(
            allGDS.get_nodata_value("2025_AOI_hillshade_1000.tif", "SB_DSM", meta),
            "255 255 255")

    def test_dsm_immer_float_min(self):
        meta = {"NoData": "-3.4028235e+38"}
        self.assertEqual(
            allGDS.get_nodata_value("2025_AOI_DSM_1000.tif", "SB_DSM", meta),
            "-3.4028235e+38")

    def test_sb_dop_aus_meta(self):
        meta = {"NoData": "0 0 0"}
        self.assertEqual(
            allGDS.get_nodata_value("2025_AOI_DOP_2601_1136_LV95.tif", "SB_DOP", meta),
            "0 0 0")

    def test_sb_dop_16_aus_meta(self):
        meta = {"NoData": "0 0 0 0"}
        self.assertEqual(
            allGDS.get_nodata_value("2025_AOI_DOP_1005NRGB_2601_1136_LV95.tif", "SB_DOP_16", meta),
            "0 0 0 0")

    def test_fehlender_meta_wert(self):
        self.assertEqual(allGDS.get_nodata_value("datei.tif", "SB_DOP", {}), "")


# ============================================================
#  tag_mask_on_raster  (aus allGDS)
#  GDAL wird durch ein minimales Fake-Dataset simuliert, damit nur die
#  reine Masken-Logik (numpy-Vergleich ueber alle Baender) geprueft wird.
# ============================================================
class _FakeBand:
    def __init__(self, array, mask_band):
        self._array = array
        self._mask_band = mask_band

    def ReadAsArray(self, xoff, yoff, xsize, ysize):
        return self._array[yoff:yoff + ysize, xoff:xoff + xsize]

    def GetMaskBand(self):
        return self._mask_band


class _FakeMaskBand:
    def __init__(self, shape):
        self.written = np.zeros(shape, dtype=np.uint8)

    def WriteArray(self, array, xoff=0, yoff=0):
        ysize, xsize = array.shape
        self.written[yoff:yoff + ysize, xoff:xoff + xsize] = array


class _FakeDataset:
    def __init__(self, band_arrays):
        self.RasterCount = len(band_arrays)
        self.RasterYSize, self.RasterXSize = band_arrays[0].shape
        self._mask_band = _FakeMaskBand((self.RasterYSize, self.RasterXSize))
        self._bands = [_FakeBand(arr, self._mask_band) for arr in band_arrays]

    def GetRasterBand(self, i):
        return self._bands[i - 1]

    def CreateMaskBand(self, flags):
        pass

    def FlushCache(self):
        pass


class TestTagMaskOnRaster(unittest.TestCase):

    def _run(self, band_arrays, nodata_str):
        ds = _FakeDataset(band_arrays)
        with unittest.mock.patch.object(allGDS.gdal, "Open", return_value=ds):
            allGDS.tag_mask_on_raster("dummy.tif", nodata_str)
        return ds._mask_band.written

    def test_einzelnes_band_rand_ist_nodata(self):
        arr = np.full((4, 4), 100, dtype=np.uint8)
        arr[0, :] = 0
        mask = self._run([arr], "0")
        self.assertTrue((mask[0, :] == 0).all())
        self.assertTrue((mask[1:, :] == 255).all())

    def test_rgb_nur_ungueltig_wenn_alle_baender_nodata(self):
        # Pixel (0,0): alle drei Baender 0 -> ungueltig.
        # Pixel (0,1): nur zwei von drei Baendern 0 -> gueltig.
        r = np.array([[0, 0], [100, 100]], dtype=np.uint8)
        g = np.array([[0, 50], [100, 100]], dtype=np.uint8)
        b = np.array([[0, 100], [100, 100]], dtype=np.uint8)
        mask = self._run([r, g, b], "0 0 0")
        expected = np.array([[0, 255], [255, 255]], dtype=np.uint8)
        self.assertTrue((mask == expected).all())

    def test_einzelwert_wird_auf_alle_baender_expandiert(self):
        r = np.full((2, 2), 0, dtype=np.uint8)
        g = np.full((2, 2), 0, dtype=np.uint8)
        mask = self._run([r, g], "0")
        self.assertTrue((mask == 0).all())

    def test_falsche_anzahl_werte_wird_uebersprungen(self):
        arr = np.full((2, 2), 0, dtype=np.uint8)
        # 2 NoData-Werte fuer 3 Baender -> Funktion soll ohne Fehler abbrechen.
        mask = self._run([arr, arr, arr], "0 0")
        # Maske bleibt unberuehrt (Default: alles 0 aus _FakeMaskBand.__init__).
        self.assertTrue((mask == 0).all())


# ============================================================
#  _csv_append  (aus allGDS)
# ============================================================
class TestCsvAppend(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", mode="w")
        self.tmp.close()
        self.csv_path = self.tmp.name

    def tearDown(self):
        os.unlink(self.csv_path)

    def test_erste_zeile_ohne_leerzeile(self):
        allGDS._csv_append(self.csv_path, "zeile1")
        with open(self.csv_path, encoding="utf-8") as f:
            inhalt = f.read()
        self.assertEqual(inhalt, "zeile1")

    def test_zweite_zeile_mit_newline_davor(self):
        allGDS._csv_append(self.csv_path, "zeile1")
        allGDS._csv_append(self.csv_path, "zeile2")
        with open(self.csv_path, encoding="utf-8") as f:
            zeilen = f.read().splitlines()
        self.assertEqual(zeilen, ["zeile1", "zeile2"])

    def test_drei_zeilen_korrekt(self):
        for i in range(1, 4):
            allGDS._csv_append(self.csv_path, f"zeile{i}")
        with open(self.csv_path, encoding="utf-8") as f:
            zeilen = f.read().splitlines()
        self.assertEqual(zeilen, ["zeile1", "zeile2", "zeile3"])


# ============================================================
#  extract_line_id  (aus 2_1 Organizer-Script)
# ============================================================
class TestExtractLineId(unittest.TestCase):

    def test_standard_dateiname(self):
        self.assertEqual(
            organiz.extract_line_id("2025_BIRCH_DOP_1005NRGB_2601_1136_LV95_LN02.tif"),
            "1005")

    def test_anderer_wert(self):
        self.assertEqual(
            organiz.extract_line_id("2025_BIRCH_DOP_0947NRGB_2601_1136_LV95_LN02.tif"),
            "0947")

    def test_case_insensitive(self):
        self.assertEqual(
            organiz.extract_line_id("2025_BIRCH_1005nrgb_LV95.tif"),
            "1005")

    def test_kein_match_liefert_none(self):
        self.assertIsNone(organiz.extract_line_id("kein_lineid_datei.tif"))

    def test_leerer_string(self):
        self.assertIsNone(organiz.extract_line_id(""))


# ============================================================
#  Integrations-Test: parse + format kombiniert (Ende-zu-Ende)
# ============================================================
class TestParseUndFormatKombiniert(unittest.TestCase):

    def test_roundtrip_iso8601(self):
        line_id = "20200913_1054_12501"
        parsed = allGDS.parse_line_id_to_hundredths(line_id)
        iso    = allGDS.format_iso8601_hundredths(parsed)
        self.assertEqual(iso, "2020-09-13T10:54:00.00")

    def test_roundtrip_stac(self):
        line_id = "20250919_1005_12501"
        parsed = allGDS.parse_line_id_to_hundredths(line_id)
        stac   = allGDS.format_stac_datetime(parsed)
        self.assertEqual(stac, "2025-09-19t10050000")

    def test_mehrere_line_ids(self):
        ids = ["20200913_1054_12501", "20200913_1104_12501"]
        times = [allGDS.format_iso8601_hundredths(allGDS.parse_line_id_to_hundredths(l)) for l in ids]
        self.assertEqual(times, ["2020-09-13T10:54:00.00", "2020-09-13T11:04:00.00"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
