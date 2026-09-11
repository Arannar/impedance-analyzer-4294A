import math
import unittest

from impedance_analyzer.app import (
    SampleInfo,
    PLOT_MODES,
    SweepSettings,
    build_measurement,
    export_filename,
    figure_for,
    format_impedspec_text,
    healthz,
    normalize_root_path,
    parse_impedspec_text,
    read_uploaded_content,
    table_rows,
)


class MeasurementModelTests(unittest.TestCase):
    def test_series_values_plots_table_and_export(self) -> None:
        # Z = 3 - 4j and 3 + 4j at omega = 1 rad/s.
        measurement = build_measurement(
            SampleInfo(), SweepSettings(points=2),
            [1 / (2 * math.pi)] * 2, [5, 5],
            [-math.degrees(math.atan2(4, 3)), math.degrees(math.atan2(4, 3))],
        )
        for actual, expected in zip(measurement.series_capacitance_f, [0.25, -0.25]):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(measurement.series_inductance_h, [-4, 4]):
            self.assertAlmostEqual(actual, expected)
        for actual in measurement.series_resistance_ohm:
            self.assertAlmostEqual(actual, 3)
        self.assertEqual(PLOT_MODES, ["|Z| + θ", "Cs + Rs", "Ls + Rs", "Ls + Q", "Zᵣ vs Zᵢ"])
        for value in measurement.quality_factor:
            self.assertAlmostEqual(value, 4 / 3)
        quality_plot = figure_for(measurement, "Ls + Q")
        self.assertEqual(list(quality_plot.data[0].y), measurement.series_inductance_h)
        self.assertEqual(list(quality_plot.data[1].y), measurement.quality_factor)
        self.assertEqual(quality_plot.data[1].yaxis, "y2")
        self.assertEqual(table_rows(measurement)[0]["q"], "1.33333")
        for mode, values in [("Cs + Rs", measurement.series_capacitance_f),
                             ("Ls + Rs", measurement.series_inductance_h)]:
            fig = figure_for(measurement, mode)
            self.assertEqual(list(fig.data[0].y), values)
            self.assertEqual(list(fig.data[1].y), measurement.series_resistance_ohm)
            self.assertEqual(fig.layout.yaxis.type, "linear")
        self.assertEqual(table_rows(measurement)[0]["cs"], "0.25")
        self.assertEqual(table_rows(measurement)[1]["ls"], "4")
        exported = format_impedspec_text(measurement)
        self.assertIn("Cs(F)\t Rs(ohms)\t Ls(H)\t Q", exported)
        numeric_rows = [line.split() for line in exported.splitlines() if not line.startswith("#")]
        self.assertEqual(len(numeric_rows[0]), 11)
        self.assertEqual([float(value) for value in numeric_rows[0][7:10]], [0.25, 3, -4])
        self.assertAlmostEqual(float(numeric_rows[0][10]), 4 / 3, places=4)
        imported = parse_impedspec_text(exported)
        self.assertAlmostEqual(imported.series_capacitance_f[0], 0.25, places=4)
        self.assertAlmostEqual(imported.series_inductance_h[1], 4, places=3)
        self.assertAlmostEqual(imported.quality_factor[1], 4 / 3, places=4)

    def test_series_singularities_and_truncation(self) -> None:
        measurement = build_measurement(
            SampleInfo(), SweepSettings(), [0, 100, 200], [5, 0], [0, 0],
        )
        self.assertEqual(len(measurement.series_inductance_h), 2)
        self.assertTrue(all(math.isnan(value) for value in measurement.series_capacitance_f))
        self.assertTrue(math.isnan(measurement.series_inductance_h[0]))
        self.assertEqual(measurement.series_inductance_h[1], 0)
        self.assertEqual(measurement.quality_factor[0], 0)
        self.assertTrue(math.isnan(measurement.quality_factor[1]))

    def test_healthz_reports_web_service_readiness(self) -> None:
        self.assertEqual(healthz(), {"status": "ok", "service": "impedance-analyzer"})

    def test_normalize_root_path(self) -> None:
        self.assertEqual(normalize_root_path(None), "")
        self.assertEqual(normalize_root_path(""), "")
        self.assertEqual(normalize_root_path("impedance-analyzer"), "/impedance-analyzer")
        self.assertEqual(normalize_root_path("/impedance-analyzer"), "/impedance-analyzer")
        self.assertEqual(normalize_root_path("/impedance-analyzer/"), "/impedance-analyzer")

    def test_normalize_root_path_rejects_malformed_paths(self) -> None:
        for value in ("/impedance//analyzer", r"\impedance-analyzer", "/../impedance-analyzer"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_root_path(value)

    def test_build_measurement_calculates_derived_columns(self) -> None:
        measurement = build_measurement(
            SampleInfo(sample_id="rc"),
            SweepSettings(points=2),
            [100, 1000],
            [1000, 500],
            [-45, -60],
        )

        self.assertEqual(len(measurement.zr_ohm), 2)
        self.assertEqual(len(measurement.capacitance_f), 2)
        self.assertTrue(all(math.isfinite(value) for value in measurement.zr_ohm))

    def test_import_export_round_trip_keeps_metadata_and_rows(self) -> None:
        text = """#sample\t RC_RC_0
#freq_start\t 40
#freq_stop\t 1000
#sweep\t LOG
#num_points\t 2
#bandwidth\t 4
#voltage\t 0.5
#point_average\t ON
#measurements_per_point\t 64
#notes\t fixture A
#Freq(Hz)\t Z(ohms)\t Phase(degrees)\t Zr(ohms)\t Zi(ohms)\t R(ohms)\t C(F)
4.0000e+01\t4.6568e+03\t-8.0054e+00\t1.0\t2.0\t3.0\t4.0
1.0000e+03\t2.0000e+03\t-4.0000e+01\t3.0\t4.0\t5.0\t6.0
"""
        measurement = parse_impedspec_text(text)
        exported = format_impedspec_text(measurement)
        imported = parse_impedspec_text(exported)

        self.assertEqual(imported.sample.sample_id, "RC_RC_0")
        self.assertEqual(imported.sample.notes, "fixture A")
        self.assertEqual(len(imported.frequency_hz), 2)
        self.assertAlmostEqual(imported.impedance_ohm[0], 4656.8)

    def test_zero_division_inputs_become_nan_or_finite_without_crashing(self) -> None:
        measurement = build_measurement(
            SampleInfo(),
            SweepSettings(points=1),
            [100],
            [0],
            [90],
        )

        self.assertEqual(len(measurement.capacitance_f), 1)
        self.assertTrue(math.isnan(measurement.capacitance_f[0]) or math.isfinite(measurement.capacitance_f[0]))

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
