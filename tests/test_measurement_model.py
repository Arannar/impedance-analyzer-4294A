import math
import unittest

from impedance_analyzer.app import (
    SampleInfo,
    SweepSettings,
    build_measurement,
    export_filename,
    format_impedspec_text,
    parse_impedspec_text,
    read_uploaded_content,
)


class MeasurementModelTests(unittest.TestCase):
    def test_build_measurement_calculates_derived_columns(self) -> None:
        measurement = build_measurement(
            SampleInfo(sample_id="rc", diameter_mm=10, thickness_mm=1),
            SweepSettings(points=2),
            [100, 1000],
            [1000, 500],
            [-45, -60],
        )

        self.assertEqual(len(measurement.zr_ohm), 2)
        self.assertEqual(len(measurement.er), 2)
        self.assertTrue(all(math.isfinite(value) for value in measurement.er))

    def test_import_export_round_trip_keeps_metadata_and_rows(self) -> None:
        text = """#sample\t RC_RC_0
#d(mm)\t 1.2
#D(mm)\t 3.4
#freq_start\t 40
#freq_stop\t 1000
#sweep\t LOG
#num_points\t 2
#bandwidth\t 4
#voltage\t 0.5
#point_average\t ON
#measurements_per_point\t 64
#notes\t fixture A
#Freq(Hz)\t Z(ohms)\t Phase(degrees)\t er_Re\t er_Im
4.0000e+01\t4.6568e+03\t-8.0054e+00\t1.0\t2.0
1.0000e+03\t2.0000e+03\t-4.0000e+01\t3.0\t4.0
"""
        measurement = parse_impedspec_text(text)
        exported = format_impedspec_text(measurement)
        imported = parse_impedspec_text(exported)

        self.assertEqual(imported.sample.sample_id, "RC_RC_0")
        self.assertEqual(imported.sample.notes, "fixture A")
        self.assertAlmostEqual(imported.sample.thickness_mm, 1.2)
        self.assertAlmostEqual(imported.sample.diameter_mm, 3.4)
        self.assertEqual(len(imported.frequency_hz), 2)
        self.assertAlmostEqual(imported.impedance_ohm[0], 4656.8)

    def test_zero_division_inputs_become_nan_or_finite_without_crashing(self) -> None:
        measurement = build_measurement(
            SampleInfo(diameter_mm=1, thickness_mm=1),
            SweepSettings(points=1),
            [100],
            [0],
            [90],
        )

        self.assertEqual(len(measurement.er), 1)
        self.assertTrue(math.isnan(measurement.er[0]) or math.isfinite(measurement.er[0]))

    def test_export_filename_sanitizes_and_adds_txt_suffix(self) -> None:
        self.assertEqual(export_filename("sample", "my sample"), "my_sample.txt")
        self.assertEqual(export_filename("sample", "already.txt"), "already.txt")
        self.assertEqual(export_filename("sample", " ... "), "measurement.txt")

    def test_read_uploaded_content_supports_small_upload_content_attribute(self) -> None:
        class SmallUpload:
            content = b"1 2 3\n"

        self.assertEqual(read_uploaded_content(SmallUpload()), b"1 2 3\n")


if __name__ == "__main__":
    unittest.main()
