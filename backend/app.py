"""Backend: reads the gateway, keeps the live snapshot, feeds the dashboard.

A worker thread reads JSON lines - from the gateway over USB serial, or from
the virtual mesh when USE_MOCK_SERIAL=true - and turns them into a per-node
snapshot. A single publisher task matches that snapshot against the OSHA rules
and pushes it to every connected dashboard, at most PUBLISH_HZ times a second
and at least once every 2s. The reader thread only marks the snapshot dirty; it
never writes to a socket itself.

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
    from . import opendata_client
except ImportError:
    from mock_mesh import VirtualMesh, HAZARD_NODE
    import opendata_client

USE_MOCK_SERIAL = os.getenv("USE_MOCK_SERIAL", "false").lower() == "true"
DEFAULT_BAUD = 115200
NODE_TIMEOUT_SECONDS = 6.0

# Frames per second pushed to the dashboards, independent of how fast the gateway
# talks. The mock emits one line per node per second; real hardware emits about
# five, so the arrival rate is not something the browser should inherit.
PUBLISH_HZ = 10

# Mirrors firmware/shared/mesh_protocol.h
NEXT_HOP_SAFE = -2
NEXT_HOP_TRAPPED = -1

# Mirrors the routing bands in firmware/node/src/main.cpp. Changing these here
# only changes what the dashboard shows; the nodes decide with their own copy.
SMOKE_HAZARD = 2000
SMOKE_WARNING = 800
TEMP_WARNING = 40.0
TEMP_HAZARD = 60.0
HAZARD_GAIN = 2.0

# Served to the dashboard so it never carries its own copy of these numbers.
LIMITS = {
    "smoke_warning": SMOKE_WARNING, "smoke_hazard": SMOKE_HAZARD,
    "temp_warning": TEMP_WARNING, "temp_hazard": TEMP_HAZARD,
}

base_dir = os.path.dirname(os.path.abspath(__file__))
osha_rules_path = os.path.abspath(os.path.join(base_dir, "../data/osha_rules.json"))
model_path = os.path.abspath(os.path.join(base_dir, "../data/factory_model.json"))
frontend_html_path = os.path.abspath(os.path.join(base_dir, "../frontend/dashboard.html"))

osha_rules = []
_osha_mtime = None

# Re-read when the file changes on disk. --reload restarts on .py edits but not on
# .json ones, so without this an edited rule file looks like a rule that does not
# work. A parse error keeps the last good set rather than silently dropping every
# rule mid-run.
def load_osha_rules():
    global osha_rules, _osha_mtime
    try:
        mtime = os.path.getmtime(osha_rules_path)
    except OSError:
        return
    if mtime == _osha_mtime:
        return
    _osha_mtime = mtime

    with open(osha_rules_path, "r", encoding="utf-8") as f:
        raw_rules = f.read().strip()
    if not raw_rules:
        osha_rules = []
        print("[Config] osha_rules.json is empty, no compliance rules loaded", flush=True)
        return
    try:
        parsed = json.loads(raw_rules)
    except Exception as e:
        print(f"[Config Error] osha_rules.json unchanged, parse failed: {e}", flush=True)
        return
    if not isinstance(parsed, list):
        print("[Config Error] osha_rules.json must be a list of rules", flush=True)
        return
    osha_rules = parsed
    print(f"[Config] Loaded {len(osha_rules)} OSHA rules", flush=True)

load_osha_rules()

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
    """Keeps the sockets single-writer.

    A websocket cannot be written by two tasks at once, and there were three
    writers racing: the serial thread on every incoming line, the periodic tick,
    and the first frame handed to a dashboard as it connects. The lock serialises
    them; publish() below stops the rate depending on the gateway.
    """

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.send_lock = asyncio.Lock()

    # A new dashboard is registered only after its first frame is away, so the
    # publisher cannot interleave a broadcast into the same socket mid-handshake.
    async def connect(self, websocket: WebSocket, initial: Optional[str] = None):
        await websocket.accept()
        async with self.send_lock:
            if initial is not None:
                try:
                    await websocket.send_text(initial)
                except Exception:
                    return
            self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        async with self.send_lock:
            for connection in list(self.active_connections):
                try:
                    await connection.send_text(message)
                except Exception:
                    self.disconnect(connection)

manager = ConnectionManager()
latest_telemetry: Dict[str, Any] = {}
telemetry_lock = threading.Lock()

# Set by the reader thread, cleared by publish(). The thread never touches the
# event loop now, which is what removed the overlapping sends.
telemetry_dirty = False

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

def band_factor(value: float, clean: float, block: float) -> Optional[float]:
    if value >= block:
        return None
    if value <= clean:
        return 1.0
    return 1.0 + HAZARD_GAIN * (value - clean) / (block - clean)

def hazard_factor(smoke: float, temp: Optional[float]) -> Optional[float]:
    """Same formula as hazardFactor() in the node firmware. None = impassable.

    Display only. It exists so the dashboard can show why a route moved before
    the node changed colour.
    """
    by_smoke = band_factor(smoke, SMOKE_WARNING, SMOKE_HAZARD)
    by_temp = 1.0 if temp is None else band_factor(temp, TEMP_WARNING, TEMP_HAZARD)
    if by_smoke is None or by_temp is None:
        return None
    return round(max(by_smoke, by_temp), 2)

# Compares the worst reading anywhere in the site against every OSHA rule.
# Not per node: a rule is either in force for the site or it is not.
def match_compliance_all(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    active_alerts = []

    max_smoke = 0.0
    max_temp = 0.0

    # OFFLINE nodes still count. A node that stops answering during a fire is the
    # expected outcome, not a reason to relax: the last thing it reported was the
    # corridor burning, and nothing since has said otherwise. Skipping it made the
    # alert vanish at the exact moment it mattered, leaving a calm-looking board.
    # Nodes that were never heard from hold smoke 0 and contribute nothing.
    for node_data in snapshot.values():
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

def ingest_pipeline(raw_json_str: str):
    global telemetry_dirty
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

        # None when the DHT22 did not answer. Never substituted with a guess:
        # an invented temperature would fire the thermal rule on its own.
        temp = safe_to_float(data.get("temp"))
        humidity = safe_to_float(data.get("humidity"))

        # Status has to agree with routing, so heat alone can mark a node too.
        status = "NORMAL"
        if smoke >= SMOKE_HAZARD or (temp is not None and temp >= TEMP_HAZARD):
            status = "HAZARD"
        # Strictly greater, to agree with band_factor()/bandFactor(), where a
        # reading equal to the clean edge still costs exactly 1.0. Using >= here
        # painted a node amber while the routing considered it untouched.
        elif smoke > SMOKE_WARNING or (temp is not None and temp > TEMP_WARNING):
            status = "WARNING"

        risk_factor = hazard_factor(smoke, temp)

        now = time.time()
        payload = {
            "smoke": int(smoke),
            "temp": temp,
            "humidity": int(humidity) if humidity is not None else None,
            "status": status,
            "next_hop": resolve_next_hop(data.get("next_hop")),
            "hops": data.get("hops"),
            "risk_factor": risk_factor,
            "last_seen": now
        }

        with telemetry_lock:
            latest_telemetry[node_str_id] = payload
            telemetry_dirty = True
    except Exception as e:
        print(f"[Ingest Error] {e} on raw payload: {raw_json_str}", flush=True)

# The virtual mesh replaces the serial port, not the parsing below it: the
# lines it produces are byte-for-byte what the real gateway prints.
def mock_serial_worker():
    with open(model_path, "r", encoding="utf-8") as f:
        mesh = VirtualMesh(json.load(f))

    print(f"[Mock] Virtual mesh online: {len(mesh.sensors)} nodes, scripted hazard at {HAZARD_NODE}", flush=True)

    tick = 0
    while True:
        for line in mesh.tick(tick):
            ingest_pipeline(line)
        tick += 1
        time.sleep(1.0)

def real_serial_worker():
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
                ingest_pipeline(line)
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

# The only writer to the dashboards. Publishing per incoming line meant a whole
# snapshot serialised and sent for every record - about five per node per second
# on real hardware, from a thread, with sends overlapping on the same socket.
# Coalescing here caps that at PUBLISH_HZ and keeps the 2s floor, so nodes that
# went quiet still turn OFFLINE on screen without anything arriving to trigger it.
async def publisher():
    global telemetry_dirty
    last_publish = 0.0
    while True:
        await asyncio.sleep(1.0 / PUBLISH_HZ)
        with telemetry_lock:
            due = telemetry_dirty
            telemetry_dirty = False
        now = time.monotonic()
        if due or now - last_publish >= 2.0:
            last_publish = now
            # This is the only task feeding the dashboards. An exception escaping
            # here would end it silently and freeze every board on its last frame
            # with nothing on screen or in the log to say why, so one bad frame
            # must cost one frame and no more.
            try:
                load_osha_rules()
                await manager.broadcast(build_frame(update_staleness_and_snapshot()))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[Publish Error] {e}", flush=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    target_worker = mock_serial_worker if USE_MOCK_SERIAL else real_serial_worker
    worker_thread = threading.Thread(target=target_worker, daemon=True)
    worker_thread.start()

    publish_task = asyncio.create_task(publisher())
    yield
    publish_task.cancel()

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

# The three Labour Ministry datasets, read from the on-disk cache. Separate from
# the WebSocket feed on purpose: this is reference material a person looks up, not
# live telemetry, so it does not belong in a frame pushed five times a second.
@app.get("/api/resources")
async def get_resources():
    try:
        return opendata_client.resources_payload()
    except Exception as e:
        # An empty payload leaves the resources tab blank but keeps the monitoring
        # view working, which is the half that matters during an incident.
        print(f"[OpenData Error] {e}", flush=True)
        return {"datasets": {}, "inspection_hotlines": {},
                "case_service_contacts": {}, "elearning_courses": []}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    snapshot = update_staleness_and_snapshot()
    await manager.connect(websocket, build_frame(snapshot) if snapshot else None)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
