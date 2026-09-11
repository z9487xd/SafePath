"""Backend: reads the gateway, keeps the live snapshot, feeds the dashboard.

A worker thread reads JSON lines - from the gateway over USB serial, or from
the virtual mesh when USE_MOCK_SERIAL=true - and turns them into a per-node
snapshot. Every update and every 2s tick, the snapshot is matched against the
OSHA rules and pushed to all connected dashboards over WebSocket.

Routing is not decided here. next_hop arrives already computed by the ESP32.

Run:
    USE_MOCK_SERIAL=true python3 -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
"""

import os
import json
import time
import asyncio
import threading
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
import serial
import serial.tools.list_ports
# Works both as `backend.app:app` from the repo root and `app:app` from backend/.
try:
    from .mock_mesh import VirtualMesh, HAZARD_NODE
except ImportError:
    from mock_mesh import VirtualMesh, HAZARD_NODE

USE_MOCK_SERIAL = os.getenv("USE_MOCK_SERIAL", "false").lower() == "true"
DEFAULT_BAUD = 115200
NODE_TIMEOUT_SECONDS = 6.0

# Mirrors firmware/shared/mesh_protocol.h
NEXT_HOP_SAFE = -2
NEXT_HOP_TRAPPED = -1

# Mirrors the routing bands in firmware/node/src/main.cpp. Changing these here
# only changes what the dashboard shows; the nodes decide with their own copy.
SMOKE_HAZARD = 2000
SMOKE_WARNING = 800
HAZARD_GAIN = 2.0

# Served to the dashboard so it never carries its own copy of these numbers.
LIMITS = {"smoke_warning": SMOKE_WARNING, "smoke_hazard": SMOKE_HAZARD}

base_dir = os.path.dirname(os.path.abspath(__file__))
osha_rules_path = os.path.abspath(os.path.join(base_dir, "../data/osha_rules.json"))
model_path = os.path.abspath(os.path.join(base_dir, "../data/factory_model.json"))
frontend_html_path = os.path.abspath(os.path.join(base_dir, "../frontend/dashboard.html"))

osha_rules = []
if os.path.exists(osha_rules_path):
    with open(osha_rules_path, "r", encoding="utf-8") as f:
        try:
            osha_rules = json.load(f)
        except Exception as e:
            print(f"[Config Error] Failed to parse osha_rules.json: {e}")

node_name_lookup = {}
if os.path.exists(model_path):
    with open(model_path, "r", encoding="utf-8") as f:
        try:
            factory_data = json.load(f)
            for idx, node in enumerate(factory_data.get("nodes", [])):
                node_name_lookup[node.get("index", idx)] = node.get("id", f"NODE_{idx}")
        except Exception as e:
            print(f"[Config Error] Failed to parse factory_model.json: {e}")
else:
    # No fallback map on purpose: inventing one would silently mislabel every
    # node. Unknown ids surface as NODE_<n> instead.
    print(f"[Config Error] {model_path} not found, run tools/place.py", flush=True)

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception:
                self.disconnect(connection)

manager = ConnectionManager()
latest_telemetry: Dict[str, Any] = {}
telemetry_lock = threading.Lock()

# SERIAL_PORT wins; otherwise pick the first port that looks like a USB bridge.
def auto_detect_serial_port() -> str:
    env_port = os.getenv("SERIAL_PORT")
    if env_port:
        return env_port

    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        desc = p.description.lower()
        if any(keyword in desc for keyword in ["ch340", "cp210", "uart", "usb", "serial"]):
            return p.device
    return ports[0].device if ports else "/dev/ttyUSB0"

def safe_to_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None

# Turns the firmware's integer next hop into a name the dashboard can use.
def resolve_next_hop(raw_value: Any) -> Optional[str]:
    parsed = safe_to_float(raw_value)
    if parsed is None:
        return None

    hop_index = int(parsed)
    if hop_index == NEXT_HOP_SAFE:
        return "SAFE"
    if hop_index == NEXT_HOP_TRAPPED:
        return "TRAPPED"
    return node_name_lookup.get(hop_index)

def hazard_factor(smoke: float) -> Optional[float]:
    """Same formula as hazardFactor() in the node firmware. None = impassable.

    Display only. It exists so the dashboard can show why a route moved before
    the node changed colour.
    """
    if smoke >= SMOKE_HAZARD:
        return None
    if smoke <= SMOKE_WARNING:
        return 1.0
    return round(1.0 + HAZARD_GAIN * (smoke - SMOKE_WARNING) / (SMOKE_HAZARD - SMOKE_WARNING), 2)

# Compares the worst reading anywhere in the site against every OSHA rule.
# Not per node: a rule is either in force for the site or it is not.
def match_compliance_all(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    active_alerts = []

    max_smoke = 0.0
    max_temp = 0.0

    for node_data in snapshot.values():
        if node_data.get("status") == "OFFLINE":
            continue
        s = safe_to_float(node_data.get("smoke")) or 0.0
        t = safe_to_float(node_data.get("temp")) or 0.0
        if s > max_smoke:
            max_smoke = s
        if t > max_temp:
            max_temp = t

    for rule in osha_rules:
        th = rule.get("trigger_threshold", {})
        smoke_threshold = safe_to_float(th.get("smoke"))
        temp_threshold = safe_to_float(th.get("temp"))

        smoke_ok = (max_smoke >= smoke_threshold) if smoke_threshold is not None else True
        temp_ok = (max_temp >= temp_threshold) if temp_threshold is not None else True

        if smoke_ok and temp_ok and (smoke_threshold is not None or temp_threshold is not None):
            active_alerts.append(rule)

    return active_alerts

# Marks nodes we have not heard from as OFFLINE and returns a copy to publish.
def update_staleness_and_snapshot() -> Dict[str, Any]:
    now = time.time()
    with telemetry_lock:
        for node_id, data in latest_telemetry.items():
            last_seen = data.get("last_seen", 0)
            if now - last_seen > NODE_TIMEOUT_SECONDS:
                data["status"] = "OFFLINE"
        return {k: v.copy() for k, v in latest_telemetry.items()}

def build_frame(snapshot: Dict[str, Any]) -> str:
    return json.dumps({
        "timestamp": time.time(),
        "source": "mock" if USE_MOCK_SERIAL else "serial",
        "nodes": snapshot,
        "compliance_alerts": match_compliance_all(snapshot)
    })

def ingest_pipeline(raw_json_str: str, loop: asyncio.AbstractEventLoop):
    try:
        data = json.loads(raw_json_str)
        raw_node_id = data.get("node_id")
        if raw_node_id is None:
            if "error" in data:
                print(f"[Gateway] {data['error']}", flush=True)
            return

        if isinstance(raw_node_id, str) and raw_node_id.isdigit():
            raw_node_id = int(raw_node_id)

        if isinstance(raw_node_id, int):
            node_str_id = node_name_lookup.get(raw_node_id, f"NODE_{raw_node_id}")
        else:
            node_str_id = str(raw_node_id)

        parsed_smoke = safe_to_float(data.get("smoke", 0))
        smoke = parsed_smoke if parsed_smoke is not None else 0.0

        # No node reports temperature yet, so it is derived from smoke. The
        # thermal OSHA rule is therefore triggered by a synthesised value.
        if "temp" in data:
            parsed_temp = safe_to_float(data["temp"])
            temp = parsed_temp if parsed_temp is not None else round(24.0 + (smoke / 75.0), 1)
        else:
            temp = round(24.0 + (smoke / 75.0), 1)

        status = "NORMAL"
        if smoke >= SMOKE_HAZARD:
            status = "HAZARD"
        elif smoke >= SMOKE_WARNING:
            status = "WARNING"

        risk_factor = hazard_factor(smoke)

        now = time.time()
        payload = {
            "smoke": int(smoke),
            "temp": temp,
            "status": status,
            "next_hop": resolve_next_hop(data.get("next_hop")),
            "hops": data.get("hops"),
            "risk_factor": risk_factor,
            "last_seen": now
        }

        with telemetry_lock:
            latest_telemetry[node_str_id] = payload

        asyncio.run_coroutine_threadsafe(
            manager.broadcast(build_frame(update_staleness_and_snapshot())), loop
        )
    except Exception as e:
        print(f"[Ingest Error] {e} on raw payload: {raw_json_str}", flush=True)

# The virtual mesh replaces the serial port, not the parsing below it: the
# lines it produces are byte-for-byte what the real gateway prints.
def mock_serial_worker(loop: asyncio.AbstractEventLoop):
    with open(model_path, "r", encoding="utf-8") as f:
        mesh = VirtualMesh(json.load(f))

    print(f"[Mock] Virtual mesh online: {len(mesh.sensors)} nodes, scripted hazard at {HAZARD_NODE}", flush=True)

    tick = 0
    while True:
        for line in mesh.tick(tick):
            ingest_pipeline(line, loop)
        tick += 1
        time.sleep(1.0)

def real_serial_worker(loop: asyncio.AbstractEventLoop):
    ser = None
    while True:
        try:
            if ser is None:
                port = auto_detect_serial_port()
                ser = serial.Serial(port, DEFAULT_BAUD, timeout=1.0)
                print(f"[Serial] Successfully connected to {port}", flush=True)

            line = ser.readline().decode("utf-8", errors="ignore").strip()

            if not line:
                time.sleep(0.01)
                continue

            if line.startswith("{") and line.endswith("}"):
                ingest_pipeline(line, loop)
            else:
                time.sleep(0.01)

        except Exception as e:
            print(f"[Serial Error] {e}", flush=True)
            if ser:
                try:
                    ser.close()
                except Exception:
                    pass
            ser = None
            time.sleep(2.0)

# Nodes that go quiet produce no events, so publish on a timer as well.
async def periodic_staleness_checker():
    while True:
        await asyncio.sleep(2.0)
        await manager.broadcast(build_frame(update_staleness_and_snapshot()))

@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    target_worker = mock_serial_worker if USE_MOCK_SERIAL else real_serial_worker
    worker_thread = threading.Thread(target=target_worker, args=(loop,), daemon=True)
    worker_thread.start()

    staleness_task = asyncio.create_task(periodic_staleness_checker())
    yield
    staleness_task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def get_index():
    if os.path.exists(frontend_html_path):
        return FileResponse(
            frontend_html_path,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return {"status": "SafePath API Online"}

@app.get("/api/topology")
async def get_topology():
    if os.path.exists(model_path):
        with open(model_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            nodes_dict = {
                str(n["id"]): {"x": n["x"], "y": n["y"], "type": n["type"]}
                for n in data.get("nodes", [])
            }
            return {"nodes": nodes_dict, "edges": data.get("edges", []), "limits": LIMITS}
    return {"nodes": {}, "edges": [], "limits": LIMITS}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)

    snapshot = update_staleness_and_snapshot()
    if snapshot:
        await websocket.send_text(build_frame(snapshot))

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
