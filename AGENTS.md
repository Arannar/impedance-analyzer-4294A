# AI Agent Guide

This repository contains a NiceGUI and PyVISA application for controlling an HP 4294A Precision Impedance Analyzer. The app is intentionally practical and instrument-facing: preserve known working HP 4294A command behavior unless the user explicitly asks to change it.

## Project Purpose

The goal is to provide a browser-based replacement for a Qt-style impedance spectroscopy workflow while keeping the HP 4294A backend reliable. Users should be able to:

- Connect to the analyzer over LAN.
- Perform open and short compensation.
- Configure sample metadata and sweep parameters.
- Run synchronized single sweeps.
- Plot impedance, phase, resistance, capacitance, and Nyquist-style data.
- Import and export impedspec-compatible `.txt` files from the browser.

The user wants explicit credit to the upstream inspiration project, `giovanirech/impedspec`. Keep the README acknowledgement intact unless the user asks to rewrite it. This project should remain a clean reimplementation; do not copy GPL upstream source code, Qt UI files, or sample data into this repository.

## Repository Layout

```text
.
├── pyproject.toml
├── pixi.lock
├── src/
│   └── impedance_analyzer/
│       ├── __init__.py
│       ├── app.py
│       └── favicon.ico
└── tests/
    └── test_measurement_model.py
```

The app is currently implemented mostly in `src/impedance_analyzer/app.py`. Keep changes focused unless the user asks for a larger refactor.

## Pixi Tasks

Run the web app:

```powershell
pixi run app
```

Run the unit tests:

```powershell
pixi run test
```

Compile-check source and tests:

```powershell
pixi run python -m compileall src tests
```

If you start the NiceGUI server during verification, stop it before finishing so the user can run `pixi run app` cleanly.

## Container Deployment

The repository includes:

```text
Dockerfile
.dockerignore
docker-compose.yml
```

The container uses `python:3.12-slim`, installs the project in editable mode, exposes port `8080`, and starts:

```text
python -m impedance_analyzer.app
```

`main()` reads `HOST` and `PORT` from the environment. Defaults are:

```text
HOST=0.0.0.0
PORT=8080
```

Keep those defaults container-friendly. If changing app startup behavior, verify both `pixi run app` on Windows and `docker compose up -d --build` on Linux remain straightforward.

## Architecture

`app.py` has four main responsibility areas:

1. Low-level parsing helpers
   - `_strip_ieee_block`
   - `_parse_binary_floats`
   - `_parse_floats`
   - `_one_value_per_point`
   - `_resource_candidates`

2. Measurement model
   - `SampleInfo`
   - `SweepSettings`
   - `Measurement`
   - `build_measurement`

3. File import/export
   - `parse_impedspec_text`
   - `format_impedspec_text`
   - `read_uploaded_content`
   - `export_filename`

4. Instrument and UI
   - `HP4294A`
   - `_InstrumentContext`
   - Plot and table helpers
   - `main_page`
   - `main`

The current structure is compact but deliberate. Extracting modules is reasonable for future work, but do not mix a broad refactor with instrument behavior changes.

## Instrument Backend Contract

All PyVISA operations must happen off NiceGUI's main thread. Existing UI callbacks use:

```python
await run.io_bound(...)
```

Keep that pattern for connection, compensation, sweep setup, sweep execution, and data reads.

The `HP4294A` class owns a `threading.Lock`. Keep instrument access serialized. The HP 4294A should not receive overlapping commands from multiple UI actions.

### Connection

The default IP address shown in the UI is configured with:

```text
DEFAULT_INSTRUMENT_IP
```

The fallback value in source should remain a generic documentation address, not a private lab address.

When the user enters a plain IP address, `_resource_candidates` tries:

```text
TCPIP0::{ip}::inst0::INSTR
TCPIP::{ip}::inst0::INSTR
TCPIP0::{ip}::INSTR
TCPIP::{ip}::INSTR
TCPIP0::{ip}::5025::SOCKET
TCPIP::{ip}::5025::SOCKET
```

The backend tries the default VISA backend first and then `@py`.

### Calibration Commands

Open compensation uses `COMA`. Short compensation uses `COMB`.

The command flow is:

```text
HOLD
E4TP OFF
TRGS INT
ESNB 1
*SRE 4
*CLS
COMA or COMB
*WAI
*OPC?
```

Do not remove the completion wait. The UI prompts the user before each compensation step.

### Sweep Setup

Sweep settings are applied with:

```text
STAR {start_hz}
STOP {stop_hz}
POIN {points}
POWMOD VOLT
POWE {voltage}
SWPT {LOG or LIN}
BWFACT {bandwidth_factor}
PAVERFACT {measurements_per_point}
PAVER {ON or OFF}
MEASTAT {ON or OFF}
MEAS IMPH
```

Single sweeps use:

```text
*CLS
SING
*OPC?
TRAC A
AUTO
TRAC B
AUTO
```

The `*OPC?` wait is important. Reading trace data too early can corrupt or jumble displayed data.

### Data Reads

The app uses `FORM5` and reads HP 4294A data as IEEE binary blocks:

```text
FORM5
OUTPSWPRM?
TRAC A
OUTPDTRC?
TRAC B
OUTPDTRC?
```

Known working assumptions:

- `FORM5` returns 32-bit floating point values.
- Values are little-endian for the current setup.
- PyVISA reads should use `read_binary_values(datatype="f", is_big_endian=False, expect_termination=False)`.
- After a binary read, the code drains one possible trailing byte with a short timeout.
- Do not replace this with a naive `read_raw()` parser without testing against the live analyzer.
- `OUTPDTRC?` can return interleaved value/status pairs. `_one_value_per_point` keeps one measurement value per point.
- Trace A is impedance magnitude.
- Trace B is phase.

If trace alignment issues return, first verify sweep completion, trace selection, point count, and the value/status stride before changing calculations.

## Measurement Model

`build_measurement` truncates frequency, impedance, and phase arrays to their common minimum length, then computes derived values with NumPy.

The formulas are:

```text
Zr = Z * cos(theta)
Zi = -Z * sin(theta)
R = Z / cos(theta)
C = Zi / (Zr * freq * R * 2*pi)
```

`theta` is phase in degrees.

Division-by-zero and invalid numeric cases should not crash the app. Preserve the current behavior of returning finite values or `math.nan`.

## File Import And Export

Exported text files use metadata comments plus tabular numeric rows. The current key names intentionally match the impedspec-style format:

```text
#sample
#freq_start
#freq_stop
#sweep
#num_points
#bandwidth
#voltage
#point_average
#measurements_per_point
#notes
#Freq(Hz)  Z(ohms)  Phase(degrees)  Zr(ohms)  Zi(ohms)  R(ohms)  C(F)
```

The importer:

- Reads metadata from comment lines starting with `#`.
- Ignores text header rows.
- Accepts whitespace-delimited numeric data.
- Requires at least three numeric columns: frequency, impedance, phase.
- Recomputes derived quantities from the imported raw columns and metadata.

`read_uploaded_content` must support NiceGUI upload objects that expose content directly and file-like objects. This has regressed before; keep the unit test coverage.

## NiceGUI UI Notes

The UI is in `main_page`.

The left column uses collapsible workflow cards:

- Connection starts expanded.
- Calibration starts collapsed.
- Acquisition starts collapsed and should stay open after sweeps.
- Files is manually controlled.

On successful connection, the UI marks Connection as green, collapses it, and expands Calibration. On full calibration, Calibration turns green and Acquisition opens. If calibration is skipped, Calibration turns yellow and Acquisition opens.

The right side has a top toolbar with plot mode selection and Plot/Table view selection. Both plot and table data should refresh regardless of which view is currently visible.

Keep the UI responsive during sweeps:

- Disable the sweep button.
- Show loading state.
- Run instrument I/O through `run.io_bound`.
- Catch `visa.VisaIOError` and display `ui.notify` instead of crashing.

## Tests

Current tests are in `tests/test_measurement_model.py` and cover:

- Derived calculation shape and finite output.
- Import/export round trip.
- Division-by-zero resilience.
- Export filename sanitization.
- Small NiceGUI upload object content handling.

For code changes, run:

```powershell
pixi run python -m compileall src tests
pixi run test
```

For UI-only documentation or style changes, tests are optional but still useful.

For live instrument changes, also smoke-test manually:

1. Connect to the analyzer.
2. Run open compensation.
3. Run short compensation.
4. Run LOG and LIN sweeps.
5. Confirm nonzero frequency, magnitude, and phase data.
6. Confirm Trace A and Trace B stay aligned.
7. Switch Plot/Table views.
8. Export and import the resulting file.

## Coding Guidelines For Agents

- Prefer small, behavior-preserving patches.
- Do not copy GPL upstream implementation code into this project. Reimplement behavior from the observed feature surface.
- Preserve the current LAN defaults unless the user requests a change.
- Keep PyVISA calls serialized and off the UI thread.
- Do not remove `*OPC?` waits from sweep or compensation flows.
- Be careful with HP 4294A binary data reads; they are easy to break.
- Do not change trace meaning casually: Trace A is magnitude, Trace B is phase.
- Avoid broad visual rewrites unless the user explicitly asks for them.
- Use `apply_patch` for manual edits.
- Do not leave a NiceGUI server running after verification.
