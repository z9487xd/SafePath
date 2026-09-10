import os
import json
import time
import asyncio
import threading
from typing import List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import serial

USE_MOCK_SERIAL = True
SERIAL_PORT = "/dev/ttyUSB0"
BAUD_RATE = 115200

app = FastAPI()

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()
latest_telemetry = {}

base_dir = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(base_dir, "../data/osha_rules.json"), "r", encoding="utf-8") as f:
    osha_rules = json.load(f)

def match_compliance(smoke: int, temp: float):
    alerts = []
    for rule in osha_rules:
        th = rule["trigger_threshold"]
        if smoke >= th["smoke"] and temp >= th["temp"]:
            alerts.append(rule)
    return alerts

def ingest_pipeline(raw_json_str: str, loop: asyncio.AbstractEventLoop):
    try:
        data = json.loads(raw_json_str)
        node_id = data.get("node_id")
        smoke = data.get("smoke", 0)
        temp = data.get("temp", 25.0)

        latest_telemetry[node_id] = data
        matched_rules = match_compliance(smoke, temp)

        frame = {
            "timestamp": time.time(),
            "nodes": latest_telemetry,
            "compliance_alerts": matched_rules
        }
        asyncio.run_coroutine_threadsafe(
            manager.broadcast(json.dumps(frame)), loop
        )
    except Exception:
        pass

def mock_serial_worker(loop: asyncio.AbstractEventLoop):
    tick = 0
    while True:
        tick += 1
        hazard_active = (tick % 20) > 10
        mock_payload = {
            "node_id": "N3",
            "smoke": 2800 if hazard_active else 300,
            "temp": 62.5 if hazard_active else 26.0,
            "status": "HAZARD" if hazard_active else "NORMAL"
        }
        ingest_pipeline(json.dumps(mock_payload), loop)
        time.sleep(1.0)

def real_serial_worker(loop: asyncio.AbstractEventLoop):
    ser = None
    while True:
        try:
            if ser is None:
                ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1.0)
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if line.startswith("{") and line.endswith("}"):
                ingest_pipeline(line, loop)
        except Exception:
            ser = None
            time.sleep(2.0)

@app.on_event("startup")
def startup_event():
    loop = asyncio.get_event_loop()
    target_worker = mock_serial_worker if USE_MOCK_SERIAL else real_serial_worker
    t = threading.Thread(target=target_worker, args=(loop,), daemon=True)
    t.start()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)