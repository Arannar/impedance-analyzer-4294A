from __future__ import annotations

import math
import os
import re
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import plotly.graph_objects as go
import pyvisa as visa
from nicegui import run, ui
from nicegui.events import UploadEventArguments


DEFAULT_IP = "10.59.133.242"
E0 = 8.85418782e-12
favicon_path = Path(__file__).with_name("favicon.ico")


def _strip_ieee_block(payload: bytes) -> bytes:
    payload = payload.strip()
    if not payload.startswith(b"#") or len(payload) <= 2 or not chr(payload[1]).isdigit():
        return payload
    digits = int(chr(payload[1]))
    data_start = 2 + digits
    try:
        data_len = int(payload[2:data_start].decode("ascii"))
    except ValueError:
        return payload[data_start:]
    return payload[data_start : data_start + data_len]


def _parse_binary_floats(payload: bytes) -> list[float]:
    for byte_order, size in (("<", 4), (">", 4), ("<", 8), (">", 8)):
        if len(payload) >= size and len(payload) % size == 0:
            count = len(payload) // size
            datatype = "d" if size == 8 else "f"
            try:
                return list(struct.unpack(f"{byte_order}{count}{datatype}", payload))
            except struct.error:
                continue
    return []


def _parse_floats(payload: Any) -> list[float]:
    """Parse HP 4294A ASCII responses or IEEE binary blocks into floats."""
    if payload is None:
        return []
    if isinstance(payload, bytes):
        raw = _strip_ieee_block(payload)
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            return _parse_binary_floats(raw)
    else:
        text = str(payload)

    text = text.strip()
    if not text:
        return []

    if text.startswith("#") and len(text) > 2 and text[1].isdigit():
        digits = int(text[1])
        data_start = 2 + digits
        try:
            data_len = int(text[2:data_start])
        except ValueError:
            data_len = 0
        text = text[data_start : data_start + data_len] if data_len else text[data_start:]

    values = [
        float(match.group(0))
        for match in re.finditer(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?", text)
    ]
    if values:
        return values
    return _parse_binary_floats(text.encode("latin1", errors="ignore"))


def _one_value_per_point(values: list[float], points: int, offset: int = 0) -> list[float]:
    if points <= 0 or not values:
        return []
    if len(values) == points:
        return values
    if len(values) >= points * 2:
        stride = len(values) // points
        if stride > 1:
            return values[offset : stride * points : stride]
    return values[:points]


def _resource_candidates(address: str) -> list[str]:
    address = address.strip()
    if "::" in address:
        return [address]
    return [
        f"TCPIP0::{address}::inst0::INSTR",
        f"TCPIP::{address}::inst0::INSTR",
        f"TCPIP0::{address}::INSTR",
        f"TCPIP::{address}::INSTR",
        f"TCPIP0::{address}::5025::SOCKET",
        f"TCPIP::{address}::5025::SOCKET",
    ]


@dataclass
class SampleInfo:
    sample_id: str = "sample"
    notes: str = ""
    diameter_mm: float = 1.0
    thickness_mm: float = 1.0


@dataclass
class SweepSettings:
    start_hz: float = 40.0
    stop_hz: float = 110_000_000.0
    points: int = 201
    sweep_type: str = "LOG"
    voltage: float = 0.5
    bandwidth_factor: int = 4
    point_average: bool = False
    measurements_per_point: int = 4


@dataclass
class Measurement:
    sample: SampleInfo = field(default_factory=SampleInfo)
    settings: SweepSettings = field(default_factory=SweepSettings)
    frequency_hz: list[float] = field(default_factory=list)
    impedance_ohm: list[float] = field(default_factory=list)
    phase_deg: list[float] = field(default_factory=list)
    zr_ohm: list[float] = field(default_factory=list)
    zi_ohm: list[float] = field(default_factory=list)
    resistance_ohm: list[float] = field(default_factory=list)
    capacitance_f: list[float] = field(default_factory=list)
    er: list[float] = field(default_factory=list)
    ei: list[float] = field(default_factory=list)
    elapsed_s: float | None = None

    @property
    def has_data(self) -> bool:
        return bool(self.frequency_hz and self.impedance_ohm and self.phase_deg)


def _safe_list(values: np.ndarray) -> list[float]:
    return [float(value) if np.isfinite(value) else math.nan for value in values]


def build_measurement(
    sample: SampleInfo,
    settings: SweepSettings,
    frequency_hz: list[float],
    impedance_ohm: list[float],
    phase_deg: list[float],
    elapsed_s: float | None = None,
) -> Measurement:
    count = min(len(frequency_hz), len(impedance_ohm), len(phase_deg))
    freq = np.asarray(frequency_hz[:count], dtype=float)
    impedance = np.asarray(impedance_ohm[:count], dtype=float)
    phase = np.asarray(phase_deg[:count], dtype=float)
    phase_rad = np.deg2rad(phase)
    thickness_m = sample.thickness_mm * 1e-3
    diameter_m = sample.diameter_mm * 1e-3
    area_m2 = math.pi * diameter_m**2 / 4

    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        zr = impedance * np.cos(phase_rad)
        zi = -impedance * np.sin(phase_rad)
        resistance = impedance / np.cos(phase_rad)
        capacitance = zi / (zr * freq * resistance * 2 * math.pi)
        er = (capacitance * thickness_m) / (E0 * area_m2)
        ei = er * np.tan(np.deg2rad(90 + phase))

    return Measurement(
        sample=sample,
        settings=settings,
        frequency_hz=_safe_list(freq),
        impedance_ohm=_safe_list(impedance),
        phase_deg=_safe_list(phase),
        zr_ohm=_safe_list(zr),
        zi_ohm=_safe_list(zi),
        resistance_ohm=_safe_list(resistance),
        capacitance_f=_safe_list(capacitance),
        er=_safe_list(er),
        ei=_safe_list(ei),
        elapsed_s=elapsed_s,
    )


def _parse_metadata_line(line: str) -> tuple[str, str] | None:
    cleaned = line.lstrip("#").strip()
    if not cleaned:
        return None
    if "=" in cleaned:
        key, value = cleaned.split("=", 1)
    else:
        parts = cleaned.split()
        if len(parts) < 2:
            return None
        key, value = parts[0], " ".join(parts[1:])
    return key.strip().lower(), value.strip()


def _float_metadata(metadata: dict[str, str], key: str, default: float) -> float:
    try:
        return float(metadata.get(key, default))
    except (TypeError, ValueError):
        return default


def _int_metadata(metadata: dict[str, str], key: str, default: int) -> int:
    try:
        return int(float(metadata.get(key, default)))
    except (TypeError, ValueError):
        return default


def parse_impedspec_text(text: str) -> Measurement:
    metadata: dict[str, str] = {}
    rows: list[list[float]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            parsed = _parse_metadata_line(stripped)
            if parsed:
                metadata[parsed[0]] = parsed[1]
            continue
        try:
            rows.append([float(item) for item in stripped.split()])
        except ValueError:
            continue

    if not rows:
        raise ValueError("No numeric impedance rows found.")

    data = np.asarray(rows, dtype=float)
    if data.shape[1] < 3:
        raise ValueError("Expected at least frequency, impedance, and phase columns.")

    sample = SampleInfo(
        sample_id=metadata.get("sample", metadata.get("sample_id", "imported")).strip(),
        notes=metadata.get("notes", ""),
        diameter_mm=_float_metadata(metadata, "d", _float_metadata(metadata, "diameter_mm", 1.0)),
        thickness_mm=_float_metadata(metadata, "d(mm)", _float_metadata(metadata, "thickness_mm", 1.0)),
    )
    # impedspec uses #d for thickness and #D for diameter; normalize after lowercase collision handling.
    for line in text.splitlines():
        if line.startswith("#D"):
            sample.diameter_mm = float(line.replace("=", " ").split()[-1])
        elif line.startswith("#d"):
            sample.thickness_mm = float(line.replace("=", " ").split()[-1])

    settings = SweepSettings(
        start_hz=_float_metadata(metadata, "freq_start", float(data[0, 0])),
        stop_hz=_float_metadata(metadata, "freq_stop", float(data[-1, 0])),
        points=_int_metadata(metadata, "num_points", len(data)),
        sweep_type=metadata.get("sweep", "LOG").upper(),
        voltage=_float_metadata(metadata, "voltage", 0.5),
        bandwidth_factor=_int_metadata(metadata, "bandwidth", 4),
        point_average=metadata.get("point_average", "ON").upper() == "ON",
        measurements_per_point=_int_metadata(metadata, "measurements_per_point", 64),
    )
    return build_measurement(
        sample,
        settings,
        data[:, 0].tolist(),
        data[:, 1].tolist(),
        data[:, 2].tolist(),
        elapsed_s=_float_metadata(metadata, "time_of_analysis", math.nan),
    )


def format_impedspec_text(measurement: Measurement) -> str:
    sample = measurement.sample
    settings = measurement.settings
    elapsed = "" if measurement.elapsed_s is None else f"{measurement.elapsed_s:.6g}"
    lines = [
        f"#sample\t {sample.sample_id}",
        f"#d(mm)\t {sample.thickness_mm}",
        f"#D(mm)\t {sample.diameter_mm}",
        f"#freq_start\t {settings.start_hz:.12g}",
        f"#freq_stop\t {settings.stop_hz:.12g}",
        f"#sweep\t {settings.sweep_type}",
        f"#num_points\t {len(measurement.frequency_hz)}",
        f"#bandwidth\t {settings.bandwidth_factor}",
        f"#voltage\t {settings.voltage}",
        f"#point_average\t {'ON' if settings.point_average else 'OFF'}",
        f"#measurements_per_point\t {settings.measurements_per_point}",
        f"#notes\t {sample.notes}",
        f"#time_of_analysis\t {elapsed}",
        "#Freq(Hz)\t Z(ohms)\t Phase(degrees)\t er_Re\t er_Im",
    ]
    for row in zip(
        measurement.frequency_hz,
        measurement.impedance_ohm,
        measurement.phase_deg,
        measurement.er,
        measurement.ei,
        strict=False,
    ):
        lines.append("\t".join(f"{value:.4e}" for value in row))
    return "\n".join(lines) + "\n"


def read_uploaded_content(upload: Any) -> bytes | str:
    if hasattr(upload, "seek"):
        upload.seek(0)
    if hasattr(upload, "read"):
        return upload.read()
    if hasattr(upload, "content"):
        return upload.content
    raise TypeError("Uploaded file object does not provide read() or content.")


def export_filename(sample_id: str, requested_name: str | None = None) -> str:
    base_name = requested_name or sample_id or "measurement"
    filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", base_name).strip("._")
    if not filename:
        filename = "measurement"
    if not filename.lower().endswith(".txt"):
        filename = f"{filename}.txt"
    return filename


class HP4294A:
    def __init__(self) -> None:
        self._rm: visa.ResourceManager | None = None
        self._instrument: Any | None = None
        self._lock = threading.Lock()

    def connect(self, ip_address: str) -> str:
        resources = _resource_candidates(ip_address)
        if not resources or not ip_address.strip():
            raise RuntimeError("Enter an IP address or a VISA resource string.")

        with self._lock:
            if self._instrument is not None:
                self._instrument.close()
                self._instrument = None

            errors: list[str] = []
            for backend in (None, "@py"):
                try:
                    rm = visa.ResourceManager() if backend is None else visa.ResourceManager(backend)
                except Exception as exc:
                    errors.append(f"{backend or 'default backend'}: {exc}")
                    continue

                for resource in resources:
                    try:
                        instrument = rm.open_resource(resource)
                        instrument.write_termination = "\n"
                        instrument.read_termination = "\n"
                        identity = self._identify(instrument, resource)
                        instrument.timeout = 180_000
                        self._rm = rm
                        self._instrument = instrument
                        return identity
                    except visa.VisaIOError as exc:
                        errors.append(f"{backend or 'default backend'} {resource}: {exc}")

            raise RuntimeError("Could not open analyzer. Tried:\n" + "\n".join(errors[-8:]))

    def _identify(self, instrument: Any, resource: str) -> str:
        instrument.timeout = 3_000
        try:
            identity = str(instrument.query("*IDN?")).strip()
            return f"{identity} [{resource}]"
        except visa.VisaIOError:
            return resource

    def disconnect(self) -> None:
        with self._lock:
            if self._instrument is not None:
                self._instrument.close()
                self._instrument = None

    def compensate(self, kind: str) -> None:
        command = {"open": "COMA", "short": "COMB"}[kind]
        with self._locked_instrument() as instrument:
            instrument.write("HOLD")
            instrument.write("E4TP OFF")
            instrument.write("TRGS INT")
            instrument.write("ESNB 1")
            instrument.write("*SRE 4")
            instrument.write("*CLS")
            instrument.write(command)
            instrument.write("*WAI")
            instrument.query("*OPC?")

    def apply_sweep_settings(self, settings: SweepSettings) -> None:
        paver = "ON" if settings.point_average else "OFF"
        averages = settings.measurements_per_point if settings.point_average else 1
        sweep_timeout_ms = max(180_000, min(3_600_000, int(settings.points * averages * 80 + 60_000)))
        with self._locked_instrument() as instrument:
            instrument.timeout = sweep_timeout_ms
            instrument.write(f"STAR {settings.start_hz}")
            instrument.write(f"STOP {settings.stop_hz}")
            instrument.write(f"POIN {settings.points}")
            instrument.write("POWMOD VOLT")
            instrument.write(f"POWE {settings.voltage}")
            instrument.write(f"SWPT {settings.sweep_type}")
            instrument.write(f"BWFACT {settings.bandwidth_factor}")
            instrument.write(f"PAVERFACT {settings.measurements_per_point}")
            instrument.write(f"PAVER {paver}")
            instrument.write(f"MEASTAT {paver}")
            instrument.write("MEAS IMPH")

    def single_sweep(self) -> None:
        with self._locked_instrument() as instrument:
            instrument.write("*CLS")
            instrument.write("SING")
            instrument.query("*OPC?")
            instrument.write("TRAC A")
            instrument.write("AUTO")
            instrument.write("TRAC B")
            instrument.write("AUTO")

    def frequency_list(self, points: int) -> list[float]:
        with self._locked_instrument() as instrument:
            instrument.write("FORM5")
            instrument.write("OUTPSWPRM?")
            return _one_value_per_point(self._read_form5_values(instrument), points)

    def display_trace_data(self, trace: str, points: int) -> list[float]:
        with self._locked_instrument() as instrument:
            instrument.write("FORM5")
            instrument.write(f"TRAC {trace.upper()}")
            instrument.write("OUTPDTRC?")
            values = self._read_form5_values(instrument)
        return _one_value_per_point(values, points)

    def _read_form5_values(self, instrument: Any) -> list[float]:
        values = list(instrument.read_binary_values(datatype="f", is_big_endian=False, expect_termination=False))
        old_timeout = instrument.timeout
        instrument.timeout = 200
        try:
            instrument.read_bytes(1, break_on_termchar=False)
        except visa.VisaIOError:
            pass
        finally:
            instrument.timeout = old_timeout
        return values

    def sweep_and_read(self, sample: SampleInfo, settings: SweepSettings) -> Measurement:
        started = time.perf_counter()
        self.apply_sweep_settings(settings)
        self.single_sweep()
        frequencies = self.frequency_list(settings.points)
        magnitude = self.display_trace_data("A", settings.points)
        phase = self.display_trace_data("B", settings.points)
        with self._locked_instrument() as instrument:
            instrument.write("TRAC A")
        if not frequencies or not magnitude or not phase:
            raise RuntimeError(
                "Analyzer returned no sweep data "
                f"(frequency={len(frequencies)}, magnitude={len(magnitude)}, phase={len(phase)})."
            )
        return build_measurement(
            sample,
            settings,
            frequencies,
            magnitude,
            phase,
            elapsed_s=time.perf_counter() - started,
        )

    def _locked_instrument(self) -> "_InstrumentContext":
        return _InstrumentContext(self)


class _InstrumentContext:
    def __init__(self, analyzer: HP4294A) -> None:
        self._analyzer = analyzer

    def __enter__(self) -> Any:
        self._analyzer._lock.acquire()
        instrument = self._analyzer._instrument
        if instrument is None:
            self._analyzer._lock.release()
            raise RuntimeError("Not connected to an HP 4294A.")
        return instrument

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._analyzer._lock.release()


analyzer = HP4294A()
current_measurement = Measurement()


def _dual_axis_figure(
    measurement: Measurement,
    y1: list[float],
    y1_name: str,
    y1_title: str,
    y2: list[float],
    y2_name: str,
    y2_title: str,
    y1_log: bool = False,
    y2_log: bool = False,
) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=measurement.frequency_hz, y=y1, name=y1_name, mode="lines", yaxis="y"))
    fig.add_trace(go.Scatter(x=measurement.frequency_hz, y=y2, name=y2_name, mode="lines", yaxis="y2"))
    fig.update_layout(
        margin=dict(l=72, r=72, t=28, b=56),
        template="plotly_white",
        xaxis=dict(title="Frequency (Hz)", type="log", showgrid=True),
        yaxis=dict(title=y1_title, type="log" if y1_log else "linear", showgrid=True),
        yaxis2=dict(title=y2_title, type="log" if y2_log else "linear", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    return fig


def figure_for(measurement: Measurement, mode: str) -> go.Figure:
    if not measurement.has_data:
        return _dual_axis_figure(measurement, [], "|Z|", "Impedance (Ohm)", [], "Phase", "Phase (deg)", y1_log=True)
    if mode == "Permittivity":
        return _dual_axis_figure(measurement, measurement.er, "epsilon'", "Real Permittivity", measurement.ei, "epsilon''", "Imaginary Permittivity")
    if mode == "R + C":
        return _dual_axis_figure(measurement, measurement.resistance_ohm, "R", "Resistance (Ohm)", measurement.capacitance_f, "C", "Capacitance (F)", y1_log=True, y2_log=True)
    if mode == "Zr vs Zi":
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=measurement.zr_ohm, y=measurement.zi_ohm, name="Zr/Zi", mode="lines+markers"))
        fig.update_layout(
            margin=dict(l=72, r=32, t=28, b=56),
            template="plotly_white",
            xaxis=dict(title="Real Impedance (Ohm)", showgrid=True),
            yaxis=dict(title="Imaginary Impedance (Ohm)", showgrid=True),
        )
        return fig
    return _dual_axis_figure(
        measurement,
        measurement.impedance_ohm,
        "|Z|",
        "Impedance (Ohm)",
        measurement.phase_deg,
        "Phase",
        "Phase (deg)",
        y1_log=True,
    )


def table_rows(measurement: Measurement) -> list[dict[str, str]]:
    rows = []
    for index, values in enumerate(
        zip(
            measurement.frequency_hz,
            measurement.impedance_ohm,
            measurement.phase_deg,
            measurement.er,
            measurement.ei,
            strict=False,
        )
    ):
        freq, impedance, phase, er, ei = values
        rows.append(
            {
                "id": str(index),
                "frequency": f"{freq:.6g}",
                "impedance": f"{impedance:.6g}",
                "phase": f"{phase:.6g}",
                "er": f"{er:.6g}",
                "ei": f"{ei:.6g}",
            }
        )
    return rows


async def confirm_dialog(title: str, message: str, positive: str = "Continue") -> bool:
    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label(title).classes("text-lg font-semibold")
        ui.label(message).classes("text-sm text-slate-600")
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=lambda: dialog.submit(False)).props("flat")
            ui.button(positive, on_click=lambda: dialog.submit(True))
    return bool(await dialog)


@ui.page("/")
def main_page() -> None:
    state = {"connected": False, "calibrated": False, "plot_mode": "|Z| + Phase", "data_view": "Plot"}

    ui.add_head_html(
        """
        <style>
          body {
            background: #e5e7eb;
            color: #111827;
          }
          body.body--dark {
            background: #0f172a;
            color: #e5e7eb;
          }
          .control-rail {
            background: #f8fafc !important;
            border-color: #94a3b8 !important;
            box-shadow: 8px 0 24px rgba(15, 23, 42, 0.10);
          }
          body.body--dark .control-rail {
            background: #111827 !important;
            border-color: #334155 !important;
            box-shadow: 8px 0 28px rgba(0, 0, 0, 0.35);
          }
          .main-panel {
            background: radial-gradient(circle at top left, #ffffff 0, #e2e8f0 42%, #cbd5e1 100%) !important;
          }
          body.body--dark .main-panel {
            background: radial-gradient(circle at top left, #1e293b 0, #0f172a 48%, #020617 100%) !important;
          }
          .workflow-card {
            background: #ffffff !important;
            border: 1px solid #94a3b8 !important;
            color: #0f172a !important;
          }
          body.body--dark .workflow-card {
            background: #1e293b !important;
            border-color: #475569 !important;
            color: #f8fafc !important;
          }
          .workflow-card.status-connected {
            background: #dcfce7 !important;
            border-color: #16a34a !important;
          }
          body.body--dark .workflow-card.status-connected {
            background: #052e16 !important;
            border-color: #22c55e !important;
          }
          .workflow-card.status-skipped {
            background: #fef3c7 !important;
            border-color: #d97706 !important;
          }
          body.body--dark .workflow-card.status-skipped {
            background: #451a03 !important;
            border-color: #f59e0b !important;
          }
          .top-toolbar {
            background: rgba(248, 250, 252, 0.92) !important;
            border: 1px solid #94a3b8 !important;
            border-radius: 8px;
            padding: 8px;
          }
          body.body--dark .top-toolbar {
            background: rgba(15, 23, 42, 0.90) !important;
            border-color: #475569 !important;
          }
          .data-surface {
            background: rgba(255, 255, 255, 0.90) !important;
            border: 1px solid #94a3b8 !important;
            border-radius: 8px;
            padding: 10px;
          }
          body.body--dark .data-surface {
            background: rgba(15, 23, 42, 0.82) !important;
            border-color: #475569 !important;
          }
          .muted-text {
            color: #475569 !important;
          }
          body.body--dark .muted-text {
            color: #cbd5e1 !important;
          }
        </style>
        """
    )
    dark_mode = ui.dark_mode(False)
    ui.query("body").classes("bg-slate-200")

    def read_sample() -> SampleInfo:
        return SampleInfo(
            sample_id=str(sample_id_input.value or "sample").strip(),
            notes=str(notes_input.value or "").strip(),
            diameter_mm=float(diameter_input.value or 1.0),
            thickness_mm=float(thickness_input.value or 1.0),
        )

    def read_settings() -> SweepSettings:
        return SweepSettings(
            start_hz=float(start_input.value or 40.0),
            stop_hz=float(stop_input.value or 110_000_000.0),
            points=int(points_input.value or 201),
            sweep_type=str(sweep_type.value or "LOG").upper(),
            voltage=float(voltage_input.value or 0.5),
            bandwidth_factor=int(bandwidth_input.value or 4),
            point_average=bool(point_average.value),
            measurements_per_point=int(averages_input.value or 64),
        )

    def set_inputs(measurement: Measurement) -> None:
        sample_id_input.value = measurement.sample.sample_id
        notes_input.value = measurement.sample.notes
        diameter_input.value = measurement.sample.diameter_mm
        thickness_input.value = measurement.sample.thickness_mm
        start_input.value = measurement.settings.start_hz
        stop_input.value = measurement.settings.stop_hz
        points_input.value = measurement.settings.points
        sweep_type.value = measurement.settings.sweep_type
        voltage_input.value = measurement.settings.voltage
        bandwidth_input.value = measurement.settings.bandwidth_factor
        point_average.value = measurement.settings.point_average
        averages_input.value = measurement.settings.measurements_per_point

    def refresh() -> None:
        plot.update_figure(figure_for(current_measurement, state["plot_mode"]))
        data_table.update_rows(table_rows(current_measurement))
        data_table.update()
        if current_measurement.has_data:
            elapsed = "" if current_measurement.elapsed_s is None or math.isnan(current_measurement.elapsed_s) else f" in {current_measurement.elapsed_s:.1f}s"
            data_status.set_text(f"{len(current_measurement.frequency_hz)} points loaded{elapsed}")
        else:
            data_status.set_text("No data loaded")

    def set_connection_status(connected: bool) -> None:
        if connected:
            connection_card.set_text("Connection:   Connected")
            connection_card.classes(replace="workflow-card status-connected w-full rounded-md")
        else:
            connection_card.set_text("Connection:   Disconnected")
            connection_card.classes(replace="workflow-card w-full rounded-md")

    def set_calibration_status(status: str) -> None:
        if status == "calibrated":
            text, classes = "Calibration:   Calibrated", "workflow-card status-connected w-full rounded-md"
        elif status == "skipped":
            text, classes = "Calibration:   Skipped", "workflow-card status-skipped w-full rounded-md"
        else:
            text, classes = "Calibration:   Not calibrated", "workflow-card w-full rounded-md"
        calibration_card.set_text(text)
        calibration_card.classes(replace=classes)

    def show_workflow_step(step: str) -> None:
        connection_card.set_value(step == "connection")
        calibration_card.set_value(step == "calibration")
        acquisition_card.set_value(step == "acquisition")

    def select_data_view(view: str) -> None:
        state["data_view"] = view
        plot_container.set_visibility(view == "Plot")
        table_container.set_visibility(view == "Table")

    def set_theme(is_dark: bool | None) -> None:
        dark_mode.value = bool(is_dark)

    async def connect() -> None:
        try:
            connect_button.disable()
            disconnect_button.disable()
            identity = await run.io_bound(analyzer.connect, ip_input.value or "")
            state["connected"] = True
            status_label.set_text(f"Connected: {identity}")
            set_connection_status(True)
            show_workflow_step("calibration")
            ui.notify("Connected to analyzer", type="positive")
        except visa.VisaIOError as exc:
            state["connected"] = False
            status_label.set_text("Disconnected")
            set_connection_status(False)
            ui.notify(f"VISA error: {exc}", type="negative")
        except Exception as exc:
            state["connected"] = False
            status_label.set_text("Disconnected")
            set_connection_status(False)
            ui.notify(str(exc), type="negative")
        finally:
            connect_button.enable()
            disconnect_button.enable()

    async def disconnect() -> None:
        try:
            disconnect_button.disable()
            await run.io_bound(analyzer.disconnect)
            state["connected"] = False
            status_label.set_text("Disconnected")
            set_connection_status(False)
            show_workflow_step("connection")
            ui.notify("Disconnected", type="info")
        except visa.VisaIOError as exc:
            ui.notify(f"VISA error: {exc}", type="negative")
        finally:
            disconnect_button.enable()

    async def compensate(kind: str) -> None:
        if not state["connected"]:
            ui.notify("Connect to the analyzer first.", type="warning")
            return
        if not await confirm_dialog(f"{kind.title()} Compensation", f"Set the accessory to the {kind.upper()} configuration before continuing."):
            return
        try:
            open_button.disable()
            short_button.disable()
            await run.io_bound(analyzer.compensate, kind)
            state[f"{kind}_calibrated"] = True
            state["calibrated"] = bool(state.get("open_calibrated") and state.get("short_calibrated"))
            calibration_status.set_text(f"{kind.title()} compensation complete")
            if state["calibrated"]:
                set_calibration_status("calibrated")
                show_workflow_step("acquisition")
            ui.notify(f"{kind.title()} compensation complete", type="positive")
        except visa.VisaIOError as exc:
            ui.notify(f"VISA error: {exc}", type="negative")
        except Exception as exc:
            ui.notify(str(exc), type="negative")
        finally:
            open_button.enable()
            short_button.enable()

    async def skip_calibration() -> None:
        accepted = await confirm_dialog(
            "Skip Calibration",
            "Without open/short compensation, collected data can include accessory, cable, and fixture effects.",
            positive="Skip",
        )
        if accepted:
            state["calibrated"] = True
            calibration_status.set_text("Calibration skipped")
            set_calibration_status("skipped")
            show_workflow_step("acquisition")
            ui.notify("Calibration skipped", type="warning")

    async def sweep() -> None:
        global current_measurement
        if not state["connected"]:
            ui.notify("Connect to the analyzer first.", type="warning")
            return
        sample = read_sample()
        settings = read_settings()
        if not sample.sample_id:
            ui.notify("Enter a sample ID.", type="warning")
            return
        if sample.diameter_mm <= 0 or sample.thickness_mm <= 0:
            ui.notify("Diameter and thickness must be positive.", type="warning")
            return
        if settings.start_hz <= 0 or settings.stop_hz <= settings.start_hz or settings.points < 2:
            ui.notify("Use a positive start, a higher stop, and at least 2 points.", type="warning")
            return
        try:
            sweep_button.disable()
            sweep_button.props("loading")
            current_measurement = await run.io_bound(analyzer.sweep_and_read, sample, settings)
            refresh()
            ui.notify(f"Sweep complete: {len(current_measurement.frequency_hz)} points", type="positive")
        except visa.VisaIOError as exc:
            ui.notify(f"VISA error: {exc}", type="negative")
        except Exception as exc:
            ui.notify(str(exc), type="negative")
        finally:
            sweep_button.props(remove="loading")
            sweep_button.enable()

    def import_file(event: UploadEventArguments) -> None:
        global current_measurement
        try:
            content = read_uploaded_content(event.file)
            if not content:
                raise ValueError("Uploaded file is empty.")
            text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
            current_measurement = parse_impedspec_text(text)
            set_inputs(current_measurement)
            export_name_input.value = export_filename(current_measurement.sample.sample_id)
            refresh()
            ui.notify(f"Imported {event.file.name}", type="positive")
        except Exception as exc:
            ui.notify(f"Import failed: {exc}", type="negative")

    def export_file() -> None:
        if not current_measurement.has_data:
            ui.notify("No data to export.", type="warning")
            return
        filename = export_filename(current_measurement.sample.sample_id, export_name_input.value)
        ui.download(
            format_impedspec_text(current_measurement).encode("utf-8"),
            filename=filename,
            media_type="text/plain",
        )

    def select_plot_mode(value: str) -> None:
        state["plot_mode"] = value
        refresh()

    with ui.row().classes("w-full h-screen no-wrap gap-0"):
        with ui.column().classes("control-rail w-[420px] shrink-0 h-full border-r p-3 gap-2 overflow-auto"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("HP 4294A Interface").classes("text-2xl font-semibold")
                with ui.row().classes("items-center gap-1"):
                    ui.icon("light_mode").classes("text-sm muted-text")
                    ui.switch(value=False, on_change=lambda e: set_theme(e.value)).props("dense color=primary")
                    ui.icon("dark_mode").classes("text-sm muted-text")

            connection_card = ui.expansion("Connection    Disconnected", icon="settings_ethernet", value=True).classes("workflow-card w-full rounded-md")
            with connection_card:
                with ui.column().classes("w-full gap-2 p-2"):
                    ip_input = ui.input("IP address or VISA resource", value=DEFAULT_IP).classes("w-full")
                    status_label = ui.label("Disconnected").classes("text-sm muted-text")
                    connect_button = ui.button("Connect", on_click=connect).classes("w-full")
                    disconnect_button = ui.button("Disconnect", on_click=disconnect).props("outline").classes("w-full")

            calibration_card = ui.expansion("Calibration    Not calibrated", icon="settings", value=False).classes("workflow-card w-full rounded-md")
            with calibration_card:
                with ui.column().classes("w-full gap-2 p-2"):
                    calibration_status = ui.label("Open and short compensation not completed").classes("text-sm muted-text")
                    open_button = ui.button("Open Compensation", on_click=lambda: compensate("open")).classes("w-full")
                    short_button = ui.button("Short Compensation", on_click=lambda: compensate("short")).classes("w-full")
                    ui.button("Skip Calibration", on_click=skip_calibration).props("outline color=warning").classes("w-full")

            acquisition_card = ui.expansion("Acquisition", icon="show_chart", value=False).classes("workflow-card w-full rounded-md")
            with acquisition_card:
                with ui.column().classes("w-full gap-2 p-2"):
                    ui.label("Sample").classes("text-base font-medium")
                    sample_id_input = ui.input("Sample ID", value="sample").classes("w-full")
                    notes_input = ui.textarea("Notes").props("rows=2").classes("w-full")
                    with ui.row().classes("w-full gap-2"):
                        diameter_input = ui.number("Diameter (mm)", value=1.0, min=0.000001, format="%.6g").classes("grow")
                        thickness_input = ui.number("Thickness (mm)", value=1.0, min=0.000001, format="%.6g").classes("grow")

                    ui.separator()
                    ui.label("Sweep").classes("text-base font-medium")
                    with ui.row().classes("w-full gap-2"):
                        start_input = ui.number("Start (Hz)", value=40.0, min=1.0, format="%.6g").classes("grow")
                        stop_input = ui.number("Stop (Hz)", value=110_000_000.0, min=1.0, format="%.6g").classes("grow")
                    with ui.row().classes("w-full gap-2"):
                        points_input = ui.number("Points", value=201, min=2, max=501, step=1).classes("grow")
                        sweep_type = ui.select(["LOG", "LIN"], value="LOG", label="Sweep").classes("grow")
                    with ui.row().classes("w-full gap-2"):
                        voltage_input = ui.number("Voltage", value=0.5, min=0.0, format="%.6g").classes("grow")
                        bandwidth_input = ui.number("Bandwidth", value=2, min=1, max=5, step=1).classes("grow")
                    point_average = ui.checkbox("Point averaging", value=False)
                    averages_input = ui.number("Measurements per point", value=4, min=1, max=64, step=1).classes("w-full")
                    sweep_button = ui.button("Run Sweep", on_click=sweep).classes("w-full")

            files_card = ui.expansion("Files", icon="folder_open", value=False).classes("workflow-card w-full rounded-md")
            with files_card:
                with ui.column().classes("w-full gap-2 p-2"):
                    ui.upload(on_upload=import_file, label="Import impedspec .txt", auto_upload=True).props("accept=.txt").classes("w-full")
                    export_name_input = ui.input("Export filename", value="impedance_data.txt").classes("w-full")
                    ui.button("Export Current Data", on_click=export_file).classes("w-full")

        with ui.column().classes("main-panel grow h-full p-4 gap-3 overflow-hidden"):
            with ui.row().classes("top-toolbar w-full items-center justify-between gap-3"):
                with ui.row().classes("items-center gap-2"):
                    plot_mode = ui.toggle(["|Z| + Phase", "Permittivity", "R + C", "Zr vs Zi"], value="|Z| + Phase", on_change=lambda e: select_plot_mode(e.value))
                    data_view_toggle = ui.toggle(["Plot", "Table"], value="Plot", on_change=lambda e: select_data_view(e.value)).props("unelevated")
                data_status = ui.label("No data loaded").classes("text-sm muted-text")

            with ui.column().classes("data-surface w-full grow gap-3 overflow-hidden") as plot_container:
                plot = ui.plotly(figure_for(current_measurement, state["plot_mode"])).classes("w-full grow min-h-0")

            columns = [
                {"name": "frequency", "label": "Frequency (Hz)", "field": "frequency", "align": "right"},
                {"name": "impedance", "label": "Impedance (Ohm)", "field": "impedance", "align": "right"},
                {"name": "phase", "label": "Phase (deg)", "field": "phase", "align": "right"},
                {"name": "er", "label": "Real Permittivity", "field": "er", "align": "right"},
                {"name": "ei", "label": "Imaginary Permittivity", "field": "ei", "align": "right"},
            ]
            with ui.column().classes("data-surface w-full grow min-h-0 overflow-auto") as table_container:
                data_table = ui.table(columns=columns, rows=[], row_key="id", pagination=0).classes("w-full")
            table_container.set_visibility(False)


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    ui.run(title="HP 4294A Interface", host=host, port=port, reload=False, favicon=favicon_path, dark=False)


if __name__ in {"__main__", "__mp_main__"}:
    main()
