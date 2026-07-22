# HP 4294A Impedance Analyzer

A NiceGUI web application for controlling an HP 4294A Precision Impedance Analyzer over LAN with PyVISA. The app provides a modern browser UI for connection, open/short compensation, sweep setup, live plotting, data table review, and impedspec-compatible text import/export.

## Features

- Connect to the analyzer by IP address or full VISA resource string.
- Default instrument IP address can be set with `DEFAULT_INSTRUMENT_IP`.
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
  - `R + C` versus frequency
  - `Zr vs Zi` Nyquist-style plot
- Table view with frequency, impedance, phase, real/imaginary impedance, resistance, and capacitance.
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
   - The default can be set with `DEFAULT_INSTRUMENT_IP`.
   - You may also enter a full VISA resource string.
3. Click `Connect`.
4. Run open compensation.
5. Run short compensation.
6. Enter sample metadata:
   - Sample ID
   - Notes
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

Given impedance magnitude `Z`, phase angle `theta` in degrees, and frequency `freq`, the app computes:

```text
Zr = Z * cos(theta)
Zi = -Z * sin(theta)
R = Z / cos(theta)
C = Zi / (Zr * freq * R * 2*pi)
```

Invalid or singular calculations, such as division by zero, are represented as blank or `NaN` values instead of crashing the app.

## File Format

Exported files are tab-delimited text files with metadata comments followed by numeric rows. Example:

```text
#sample	 sample
#freq_start	 40.0
#freq_stop	 110000000.0
#sweep	 LOG
#num_points	 201
#bandwidth	 2
#voltage	 0.5
#point_average	 OFF
#measurements_per_point	 4
#notes
#Freq(Hz)	 Z(ohms)	 Phase(degrees)	 Zr(ohms)	 Zi(ohms)	 R(ohms)	 C(F)
4.000000e+01	1.000000e+03	-4.500000e+01	7.071068e+02	7.071068e+02	1.414214e+03	1.989437e-06
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

The app is ready to run as a container on an Ubuntu Server. Because the HP 4294A is controlled over LAN, the container only needs normal network access to the instrument address.

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
http://<server-ip>:18080
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

Before blaming Docker, verify the Ubuntu server can reach the analyzer. Replace `<instrument-ip>` with the analyzer address used in your environment:

```bash
ping <instrument-ip>
```

If ICMP ping is blocked in the lab, test the common SCPI socket port instead:

```bash
nc -vz <instrument-ip> 5025
```

Install `nc` if needed:

```bash
sudo apt install -y netcat-openbsd
```

If the server cannot reach the analyzer, fix the network route, firewall, or instrument address settings first. The app cannot connect from inside Docker until the host can connect.

To set the startup IP shown in the web UI, edit `docker-compose.yml` or pass an environment variable:

```bash
DEFAULT_INSTRUMENT_IP=<instrument-ip> docker compose up -d --build
```

### Caddy Reverse Proxy Under A Subpath

The Compose deployment sets `ROOT_PATH=/impedance-analyzer` by default, so the supported browser URL behind Caddy is:

```text
http://labdesktop.clients.net.dtu.dk/impedance-analyzer/
```

Add the following site block to `/etc/caddy/Caddyfile`. The explicit `http://` scheme keeps this deployment on HTTP and prevents Caddy from attempting automatic HTTPS certificate provisioning:

```caddyfile
http://labdesktop.clients.net.dtu.dk {
    redir /impedance-analyzer /impedance-analyzer/ 308

    handle_path /impedance-analyzer/* {
        reverse_proxy 127.0.0.1:18080
    }

    # Keep handlers for other applications here.
}
```

Use `handle_path` so Caddy removes `/impedance-analyzer` from the upstream request path. Uvicorn receives the same prefix through its `root_path` setting, which NiceGUI uses when generating browser-facing asset and WebSocket URLs. Caddy's reverse proxy handles WebSocket upgrades automatically, so no separate WebSocket route is required.

The app exposes `GET /healthz` for service availability checks. Behind Caddy this is available at `/impedance-analyzer/healthz`; it reports only that the web process is responding and does not indicate whether the physical analyzer is connected.

Build the container, validate the Caddy configuration, and reload Caddy:

```bash
docker compose up -d --build
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

The published port `18080` remains useful for diagnostics, but the Caddy subpath is the supported browser entry point for this deployment. To use a different Compose root path, set `ROOT_PATH` before starting the container. Running `pixi run app` without `ROOT_PATH` remains backward-compatible and serves the app at `http://localhost:8080/`.

This lab deployment uses unencrypted HTTP. Requests, uploaded measurement files, and browser traffic are not protected by TLS while travelling over the network.

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
