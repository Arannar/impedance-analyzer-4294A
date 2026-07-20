# HP 4294A Impedance Analyzer

A NiceGUI web application for controlling an HP 4294A Precision Impedance Analyzer over LAN with PyVISA. The app provides a modern browser UI for connection, open/short compensation, sweep setup, live plotting, data table review, and impedspec-compatible text import/export.

## Features

- Connect to the analyzer by IP address or full VISA resource string.
- Default instrument IP address: `10.59.133.242`.
- Thread-safe PyVISA backend using NiceGUI background workers so instrument I/O does not block the browser UI.
- Workflow cards for:
  - Connection
  - Calibration
  - Acquisition
  - File import/export
- HP 4294A sweep setup:
  - Start and stop frequency
  - Number of points
  - LIN or LOG sweep
  - Test voltage
  - Bandwidth factor
  - Point averaging and measurements per point
- Open and short compensation flow with an explicit skip option.
- Plotly views:
  - `|Z| + Phase` versus frequency
  - `Permittivity` versus frequency
  - `R + C` versus frequency
  - `Zr vs Zi` Nyquist-style plot
- Table view with frequency, impedance, phase, real permittivity, and imaginary permittivity.
- Browser-based `.txt` import and export compatible with the impedspec-style sample format.
- Light and dark mode.

## Acknowledgements

This project is loosely inspired by [giovanirech/impedspec](https://github.com/giovanirech/impedspec), a GPL-3.0 Python/Qt application for impedance spectroscopy with the Keysight/HP 4294A.

This repository is not a fork of impedspec and does not include copied source code, UI files, or sample data from that project. It reimplements a similar lab workflow with NiceGUI, Plotly, PyVISA, and a container-friendly deployment path.

If this app is used in work that also relies on the original ImpedSpec methodology, sample format, or workflow, cite and credit the upstream ImpedSpec project and its authors as appropriate.

## Requirements

This project is managed with [pixi](https://pixi.sh/). The Python dependencies are declared in `pyproject.toml`:

- `nicegui`
- `numpy`
- `plotly`
- `pyvisa`
- `pyvisa-py`

The app can use either the system VISA backend, such as NI-VISA, or the pure Python `pyvisa-py` backend. The backend tries both when connecting.

## Quick Start

Install the environment:

```powershell
pixi install
```

Run the app:

```powershell
pixi run app
```

Open the NiceGUI URL printed in the terminal, usually:

```text
http://localhost:8080
```

If port `8080` is already in use, stop the existing app process before starting a new one.

## Typical Workflow

1. Start the app with `pixi run app`.
2. Confirm the IP address in the Connection card.
   - The default is `10.59.133.242`.
   - You may also enter a full VISA resource string.
3. Click `Connect`.
4. Run open compensation.
5. Run short compensation.
6. Enter sample metadata:
   - Sample ID
   - Notes
   - Diameter in mm
   - Thickness in mm
7. Enter sweep settings.
8. Click `Run Sweep`.
9. Review the data in Plot or Table view.
10. Export the measurement as a `.txt` file if needed.

If you intentionally skip calibration, the app marks the calibration step as skipped and allows acquisition to continue.

## Instrument Connection

The backend accepts either a plain IP address or a full VISA resource. For a plain IP address, it tries several common HP 4294A LAN resource forms:

```text
TCPIP0::{ip}::inst0::INSTR
TCPIP::{ip}::inst0::INSTR
TCPIP0::{ip}::INSTR
TCPIP::{ip}::INSTR
TCPIP0::{ip}::5025::SOCKET
TCPIP::{ip}::5025::SOCKET
```

The socket form is useful for instruments or gateways that expose the analyzer on port `5025`.

## Sweep Commands

The app configures the HP 4294A with commands including:

```text
STAR
STOP
POIN
POWMOD VOLT
POWE
SWPT LIN/LOG
BWFACT
PAVERFACT
PAVER
MEASTAT
MEAS IMPH
SING
*OPC?
```

Data is read using:

```text
FORM5
OUTPSWPRM?
TRAC A
OUTPDTRC?
TRAC B
OUTPDTRC?
```

Trace A is treated as impedance magnitude. Trace B is treated as phase.

## Calculated Columns

Given impedance magnitude `Z`, phase angle `theta` in degrees, frequency `freq`, sample thickness `d`, and electrode area `A`, the app computes:

```text
Zr = Z * cos(theta)
Zi = -Z * sin(theta)
R = Z / cos(theta)
C = Zi / (Zr * freq * R * 2*pi)
er = C * d / (e0 * A)
ei = er * tan((90 + theta) * pi / 180)
A = pi * D^2 / 4
```

Diameter and thickness are entered in mm and converted to meters for the calculations.

Invalid or singular calculations, such as division by zero, are represented as blank or `NaN` values instead of crashing the app.

## File Format

Exported files are tab-delimited text files with metadata comments followed by numeric rows. Example:

```text
#sample	 sample
#d(mm)	 1.0
#D(mm)	 1.0
#freq_start	 40.0
#freq_stop	 110000000.0
#sweep	 LOG
#num_points	 201
#bandwidth	 2
#voltage	 0.5
#point_average	 OFF
#measurements_per_point	 4
#notes
#Freq(Hz)	 Z(ohms)	 Phase(degrees)	 er_Re	 er_Im
4.000000e+01	1.000000e+03	-4.500000e+01	1.000000e+00	2.000000e+00
```

The importer ignores non-numeric table header lines and reads the first three numeric columns as frequency, impedance magnitude, and phase.

## Troubleshooting

### Port 8080 Is Already In Use

Only one NiceGUI process can bind to port `8080`. Stop the previous app process, then run:

```powershell
pixi run app
```

### VISA Resource Not Found

Check that:

- The analyzer is powered on.
- The PC can reach the instrument IP address.
- The IP address in the app is correct.
- The VISA backend is installed and visible to PyVISA.
- The instrument or gateway accepts one of the resource strings listed above.

### Sweep Completes But No Data Appears

The app waits for `*OPC?` before reading trace data. If no data appears, check:

- The analyzer is not held in an unexpected front-panel state.
- Trace A and Trace B are available for `MEAS IMPH`.
- The selected point count is supported by the instrument.
- The connection is stable during long sweeps.

### Imported File Has No Numeric Rows

The importer expects whitespace-delimited numeric rows. At minimum, each data row should contain:

```text
frequency impedance phase
```

Additional numeric columns are allowed.

## Development Commands

Run tests:

```powershell
pixi run test
```

Compile-check source and tests:

```powershell
pixi run python -m compileall src tests
```

Run the app:

```powershell
pixi run app
```

## Ubuntu Server Deployment With Docker

The app is ready to run as a container on a lab Ubuntu Server. Because the HP 4294A is controlled over LAN, the container only needs normal network access to the instrument IP address.

On the Ubuntu server, install Docker:

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Log out and back in after adding your user to the `docker` group.

Copy this project folder to the server, for example with `scp`, `rsync`, Git, or a shared lab drive. From the project folder on the server, build and start the app:

```bash
docker compose up -d --build
```

Open the app from another lab computer:

```text
http://<ubuntu-server-ip>:8080
```

View logs:

```bash
docker compose logs -f
```

Stop the app:

```bash
docker compose down
```

Update after copying newer code to the server:

```bash
docker compose up -d --build
```

### Network Check From The Server

Before blaming Docker, verify the Ubuntu server can reach the analyzer:

```bash
ping 10.59.133.242
```

If ICMP ping is blocked in the lab, test the common SCPI socket port instead:

```bash
nc -vz 10.59.133.242 5025
```

Install `nc` if needed:

```bash
sudo apt install -y netcat-openbsd
```

If the server cannot reach the analyzer, fix the lab network route, VLAN, firewall, or instrument IP settings first. The app cannot connect from inside Docker until the host can connect.

### Optional systemd Autostart

Docker Compose with `restart: unless-stopped` will restart the container after reboots if the Docker service starts. If you want a dedicated systemd unit for the Compose project, create `/etc/systemd/system/impedance-analyzer.service`:

```ini
[Unit]
Description=HP 4294A Impedance Analyzer
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/impedance-analyzer
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

Then enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now impedance-analyzer
```
