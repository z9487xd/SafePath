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

Open <http://127.0.0.1:8000/>. The simulator builds a virtual mesh from `data/factory_model.json` and cycles through simultaneous fires at nodes 6+13, 8+11, 6+14, and 7+13 to demonstrate route changes; each scenario lasts 48 seconds and includes rise, peak, and recovery phases. Node 7 also exercises heat-only danger.

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


## image.png map and sprinkler LEDs (2026-10-29)

The map has 14 physical nodes: arrows 3/4/7/9/12, sprinklers
1/5/6/8/10/11/13/14, and sensor-only 2. EXA/EXB/EXC are virtual exits.
5 has no direct edge to EXC, as drawn in the image.

All **field nodes** use the same `firmware/node` default `esp32dev` binary.
The USB gateway still uses `firmware/gateway`; rebuild and flash both projects
because the mesh record format changed. Do not use the legacy N1/N2 bench or
DHT26 configurations with this map: GPIO26 is now an address pin.

Address pins are GPIO32/33/25/26 (grounded = 1). See the generated wiring table
in `firmware/node/include/topology.h` for each board's jumpers. No jumper reads
zero and is rejected. Arrow matrix DATA stays GPIO13, MQ-2 stays GPIO34, DHT22
DATA stays GPIO27. Sprinkler LED: **GPIO14 → 220–330 ohm resistor → LED anode;
LED cathode → GND**. This is a small ordinary LED driven at 3.3 V, not a mains
lamp. GPIO14 is on the same header side as the existing pins on the usual
ESP32 DevKit layout; check the actual board labels before wiring.

`tools/place.py` owns `SPRINKLER_DISTANCE` (default **400 image/map units**,
not metres) and `SPRINKLER_LED_PIN`. Run `python tools/place.py`, then rebuild.
Only sprinkler nodes drive GPIO14. An LED turns on when the node itself or a
fresh sensor record at **shortest corridor distance strictly below the limit**
reports smoke ≥2000 or temperature ≥60 °C. Distances sum corridor lengths,
ignore evacuation hazard weights, and do not traverse exits as shortcuts.
No line-of-sight proximity or transitive sprinkler-to-sprinkler activation is
used. Expired readings (6 seconds) are excluded from automatic triggers;
this prototype does not latch stale fire readings or survive a power loss.

Dashboard buttons call `POST /api/sprinklers/{id}/on` (manual 30-second lease)
or `/auto` (cancel manual lease). Backend writes a bounded serial command,
gateway broadcasts it, and nodes relay each command once with a hop limit.
Automatic danger still keeps the LED on after returning to auto. The UI says
"sent" only after serial write and shows output from node telemetry, so a lost
radio command is not presented as successful actuation. There is currently no
automatic retry or delivery guarantee; inspect the node's reported output.
Gateway disconnection returns HTTP 503. Mock mode supports the same buttons.

Verification: `python -m unittest discover -s tests -v`,
`node --check frontend/mesh-view.js`, `pio run -d firmware/node`, and
`pio run -d firmware/gateway`. Actual RF delivery and wiring require hardware.


### 節點編號與撒水操作

節點名稱直接使用 1～14，編號與 ESP32 位址跳線讀到的數值一致。
原圖位置依序對應：A1→1、A2→2、A3→3、A4→4、A5→5、B1→6、B2→7、
B3→8、C1→9、C2→10、D1→11、D2→12、D3→13、D4→14。

- 箭頭：3、4、7、9、12（感測＋箭頭矩陣）。
- 撒水：1、5、6、8、10、11、13、14（感測＋GPIO14 普通 LED）。
- 僅感測：2。

普通 LED 接法：GPIO14 → 220～330Ω 電阻 → LED 長腳（正極），短腳接 GND。
所有現場節點燒錄 node 的 esp32dev；主控 ESP32 燒錄 gateway，USB 接後端電腦。
依 topology.h 接好位址跳線，使用新產生的地圖啟動／重新啟動後端。
開 dashboard，點選地圖上的撒水節點，按「撒水 30 秒」，觀察回報「手動撒水」。
按「恢復自動」可取消手動撒水。若自動危險條件仍成立，LED 仍會亮。
自動條件：自己或走道最短距離 <400 地圖單位內的在線節點，煙霧 ≥2000 ADC
或溫度 ≥60°C；例如 3 號危險，會觸發相距 395 的 1 號及相距約 232 的 6 號。
無硬體時可用 USE_MOCK_SERIAL=true 操作同樣按鈕，僅模擬燈號。

地圖預設使用寬版顯示，壓縮原圖的垂直空白以提高節點可讀性；按「原圖比例」
可還原 image.png 的長寬比例。此操作只影響瀏覽器座標，走道距離與韌體觸發條件保持不變。
地圖支援縮放、捲動、選取節點直接操作撒水 LED。


### 控制台更新（2026-10-29）

撒水控制使用右側固定選單與「啟動撒水」按鈕，持續 30 秒；畫面會等待
節點的新回報確認手動模式，10 秒內未確認會顯示失敗並可重試。
地圖使用 SVG viewBox 縮放，可使用 ＋／−、滾輪與拖曳；「重設」還原視野。
原圖比例按鈕僅影響畫面。請重新啟動後端並重新整理頁面。
新版 gateway 加入同一指令重送兩次，採相同 ID 去重，需重新燒錄 gateway。
這不等於送達保證，成功狀態仍依節點回報；實際硬體與 RF 需上板驗證。
