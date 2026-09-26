# SafePath

SafePath is an ESP32-based field safety monitoring and evacuation-route visualization system. Sensor nodes read smoke and temperature/humidity data, exchange status over ESP-NOW, and calculate their next hop to a safe exit locally. A gateway forwards mesh status to the backend over USB serial, and the browser dashboard displays live node status and routes, safety resources, and a fire hydrant map.

> This project is intended for prototyping and demonstration. It is not a certified fire-protection, life-safety, or statutory reporting system.

## Features

- Live display of node connectivity, smoke, temperature, alerts, and next-hop routes.
- Sensor nodes adjust route costs based on smoke and temperature. Hazardous nodes are treated as impassable. ESP32 nodes calculate routes; the backend receives and displays their results.
- Virtual mesh mode for exploring the dashboard and route changes without hardware.
- Pages for workplace incident reporting thresholds, worker support contacts, occupational safety courses, and fire hydrant locations, backed by the following open datasets:
  - [Labor Inspection Agency Reporting Hotline](https://data.gov.tw/dataset/41461) — Ministry of Labor, Occupational Safety and Health Administration
  - [Worker Case Management Service Contacts by County/City](https://data.gov.tw/dataset/44744) — Ministry of Labor, Occupational Safety and Health Administration
  - [Occupational Injury Type Classification Table](https://data.gov.tw/dataset/41471) — Ministry of Labor, Occupational Safety and Health Administration
  - [Labor eLearning Courses](https://labor-elearning.mol.gov.tw/) — Ministry of Labor, Occupational Safety and Health Administration
  - [Taipei Metro Area Fire Hydrant Locations](https://data.gov.tw/dataset/128639) — Taipei Water Department
- Local JSON caches in `data/opendata/` keep reference resources available offline.

TBD

## Project Structure

```text
backend/                 FastAPI backend, virtual mesh, and open-data reader
frontend/                Live dashboard and map views
firmware/node/           ESP32 sensor-node and route-calculation firmware
firmware/gateway/        ESP32 mesh receiver and USB serial gateway firmware
firmware/shared/         Shared firmware mesh protocol
firmware/Test/           Sensor and LED test firmware
shared/                  Shared protocol header
data/                    Site topology, safety rules, and open-data caches
tools/place.py           Topology source; generates JSON and firmware headers
```

## Quick Start: Simulation

Create a Python virtual environment and install the backend dependencies from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
$env:USE_MOCK_SERIAL = "true"
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
```

Open <http://127.0.0.1:8000/>. The simulator builds a virtual mesh from `data/factory_model.json` and periodically raises smoke levels at node `N1` to demonstrate route changes.

If PowerShell blocks virtual-environment activation, run the commands using `.venv\Scripts\python.exe` directly, for example:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
```

## Connect Physical Hardware

1. Flash the sensor-node and gateway firmware to their respective ESP32 boards. Make sure the nodes and gateway use the same ESP-NOW channel.
2. Connect the gateway to the computer running the backend over USB. The backend uses 115200 baud and attempts to detect the serial port automatically.
3. Start the backend without setting `USE_MOCK_SERIAL=true`. If automatic detection selects the wrong port, set `SERIAL_PORT` in PowerShell:

	```powershell
	$env:SERIAL_PORT = "COM3"
	python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
	```

	Replace `COM3` with the gateway's actual port. Common Linux device names include `/dev/ttyUSB0` and `/dev/ttyACM0`.

The gateway prints mesh readings as JSON Lines. The backend reads them from the serial port and streams live status to the dashboard over a WebSocket. Physical mode requires an accessible USB serial port.

## Build and Flash Firmware

Firmware projects use PlatformIO with the Arduino framework and an ESP32 development-board configuration. Use the PlatformIO extension for VS Code or install PlatformIO Core CLI.

Build and flash the sensor-node firmware:

```powershell
Set-Location firmware/node
pio run
pio run -t upload
```

Build and flash the gateway firmware in a separate terminal:

```powershell
Set-Location firmware/gateway
pio run
pio run -t upload
```

If multiple serial devices are connected, configure the correct `upload_port` in the PlatformIO project settings. All sensor nodes use the same firmware binary and identify themselves from the address pins listed in `topology.h`; check each board's jumpers against the site topology before deployment.

## Change the Site Topology

Edit the node coordinates and corridor definitions in `tools/place.py`, then run this command from the repository root:

```powershell
python tools/place.py
```

The script generates `data/factory_model.json` and `firmware/node/include/topology.h`. Do not edit only one generated file by hand, or the dashboard and firmware may disagree about node numbering or routes. Rebuild and reflash the sensor-node firmware after changing the topology.

## API

- `GET /`: serves the monitoring dashboard.
- `GET /api/topology`: returns nodes, corridors, and sensor thresholds.
- `GET /api/resources`: returns cached safety and response resources with source-status metadata.
- `WS /ws`: streams live node telemetry.

## Configuration

- `USE_MOCK_SERIAL=true`: use the virtual mesh. If unset or `false`, the backend reads from a physical serial port.
- `SERIAL_PORT`: select the gateway serial port. If unset, the backend attempts automatic detection.
- Backend serial baud rate: `115200`.
- Keep routing thresholds consistent when changing them. Check both `backend/app.py` and `firmware/node/src/main.cpp`.

## Data Notes

The backend and frontend read `data/osha_rules.json`, `data/factory_model.json`, and JSON files under `data/opendata/`. Open-data pages use local caches. If a cache entry has no verified source URL or retrieval timestamp, the dashboard reports it as unverified. Confirm the source, update date, and applicability of these datasets before operational use.
