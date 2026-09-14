"""A swarm of virtual ESP32 nodes, for running without hardware.

The simulation has to exercise the real algorithm, otherwise the dashboard
shows something the firmware would never do. So this module ports the node
firmware's cost model line for line:

    bandFactor(x)   = INF                       x >= block
                    = 1.0                       x <= clean
                    = 1.0 + 2.0 * (x-clean)/(block-clean)
    hazardFactor(v) = max(bandFactor(smoke), bandFactor(temp))
    edgeCost(u, v)  = BASE_GRAPH[u][v] * hazardFactor(v)

It replaces the serial port and nothing above it: the lines it emits are
identical to what firmware/gateway prints, so ingest_pipeline in app.py has no
branch for mock versus real hardware.
core is solve_next_hop(), which is the same Dijkstra and back-walk as in the firmware.
"""

import json
from collections import deque
from typing import Dict, List

INF = 99999.0
SMOKE_CLEAN = 800
SMOKE_THRESHOLD = 2000
TEMP_CLEAN = 40.0
TEMP_BLOCK = 60.0
HAZARD_GAIN = 2.0

NEXT_HOP_SAFE = -2 
NEXT_HOP_TRAPPED = -1

# Scripted scenario: smoke ramps up at HAZARD_NODE and clears again, so the
# route change and the colour change can be watched happening at different
# smoke levels.
HAZARD_NODE = "N1"
CYCLE_SECONDS = 40
BASELINE_SMOKE = 220
PEAK_SMOKE = 2600
BASELINE_TEMP = 24.0
PEAK_TEMP = 58.0
BASELINE_HUMIDITY = 55
PEAK_HUMIDITY = 20

# Where the gateway sits. Used only to fake a plausible relay depth for the
# `hops` field; delete this and _relay_depth() if you want a leaner mock.
GATEWAY_ANCHOR = "EX1"


class VirtualMesh:
    def __init__(self, topology: dict):
        nodes = topology.get("nodes", [])
        self.ids: List[str] = [n["id"] for n in nodes]
        self.index: Dict[str, int] = {n["id"]: n.get("index", i) for i, n in enumerate(nodes)}
        self.exits = {n["id"] for n in nodes if n.get("type") == "exit"}
        self.sensors = [n["id"] for n in nodes if n.get("type") != "exit"]

        size = len(self.ids)
        self.base = [[INF] * size for _ in range(size)]
        for i in range(size):
            self.base[i][i] = 0.0

        self.adjacency: Dict[str, List[str]] = {nid: [] for nid in self.ids}
        for edge in topology.get("edges", []):
            u, v = edge["source"], edge["target"]
            cost = float(edge["cost"])
            ui, vi = self.index[u], self.index[v]
            self.base[ui][vi] = cost
            self.base[vi][ui] = cost
            self.adjacency[u].append(v)
            self.adjacency[v].append(u)
        

        if HAZARD_NODE not in self.sensors:
            raise ValueError(f"HAZARD_NODE {HAZARD_NODE!r} is not a sensor node in this map")

        self.hops = self._relay_depth()
        self.smoke: Dict[str, int] = {nid: 0 for nid in self.ids}
        self.temp: Dict[str, float] = {nid: BASELINE_TEMP for nid in self.ids}
        self.humidity: Dict[str, int] = {nid: BASELINE_HUMIDITY for nid in self.ids}
        self.seq = 0

    # Hop count = graph distance from the gateway, minus the first direct hop.
    def _relay_depth(self) -> Dict[str, int]:
        depth = {GATEWAY_ANCHOR: 0} if GATEWAY_ANCHOR in self.adjacency else {}
        queue = deque(depth)
        while queue:
            current = queue.popleft()
            for neighbor in self.adjacency[current]:
                if neighbor not in depth:
                    depth[neighbor] = depth[current] + 1
                    queue.append(neighbor)
        return {nid: max(0, depth.get(nid, 1) - 1) for nid in self.ids}

    @staticmethod#獨立函式
    def _band(value: float, clean: float, block: float) -> float:
        if value >= block:
            return INF
        if value <= clean:
            return 1.0
        ## 處於危險緩衝區間：依比例加成 1.0 到 3.0 倍
        #1.0+2.0*(value-clean)/(block-clean)
        return 1.0 + HAZARD_GAIN * (value - clean) / (block - clean)

    # Cost multiplier for entering a node. INF means do not go there.
    def _hazard_factor(self, node_id: str) -> float:
        return max(self._band(self.smoke[node_id], SMOKE_CLEAN, SMOKE_THRESHOLD),
                   self._band(self.temp[node_id], TEMP_CLEAN, TEMP_BLOCK))

    def _edge_cost(self, ui: int, vi: int) -> float:#計算邊的成本
        base = self.base[ui][vi]
        if base >= INF:
            return INF
        factor = self._hazard_factor(self.ids[vi])
        if factor >= INF:
            return INF
        return base * factor

    # Same array-based Dijkstra and back-walk as solveNextHop() in the node
    # firmware, so both pick the same hop for the same readings.
    def solve_next_hop(self, node_id: str) -> int:
        start = self.index[node_id]
        size = len(self.ids)

        if node_id in self.exits and self.smoke[node_id] < SMOKE_THRESHOLD:
            return NEXT_HOP_SAFE

        dist = [INF] * size
        visited = [False] * size
        parent = [-1] * size
        dist[start] = 0.0
        #dijkstra
        for _ in range(size - 1):
            best = -1
            best_dist = INF
            for v in range(size):
                if not visited[v] and dist[v] < best_dist:
                    best_dist = dist[v]
                    best = v
            if best == -1:
                break
            visited[best] = True
            
            for v in range(size):
                if visited[v]:
                    continue
                cost = self._edge_cost(best, v)
                if cost < INF and dist[best] + cost < dist[v]:
                    dist[v] = dist[best] + cost
                    parent[v] = best
        #找到最短路徑的出口節點
        best_exit = -1
        min_exit_dist = INF
        for exit_id in self.exits:
            ei = self.index[exit_id]
            if ei == start:
                continue
            if dist[ei] < min_exit_dist:
                min_exit_dist = dist[ei]
                best_exit = ei

        if best_exit == -1 or min_exit_dist >= INF:
            return NEXT_HOP_TRAPPED
        #回溯找到從起點到出口的第一個節點
        current = best_exit
        for _ in range(size):
            if parent[current] == -1 or parent[current] == start:
                break
            current = parent[current]

        return current if parent[current] == start else NEXT_HOP_TRAPPED

    def _scripted_smoke(self, node_id: str, tick: int) -> int:
        phase = tick % CYCLE_SECONDS
        jitter = (tick + self.index[node_id] * 7) % 40

        if node_id != HAZARD_NODE:
            return BASELINE_SMOKE + jitter

        span = CYCLE_SECONDS // 4
        if phase < span:
            return BASELINE_SMOKE + jitter
        if phase < span * 2:
            progress = (phase - span) / span
            return int(BASELINE_SMOKE + (PEAK_SMOKE - BASELINE_SMOKE) * progress)
        if phase < span * 3:
            return PEAK_SMOKE
        progress = (phase - span * 3) / span
        return int(PEAK_SMOKE - (PEAK_SMOKE - BASELINE_SMOKE) * progress)

    def tick(self, tick_index: int) -> List[str]:
        """Advance one second and return the serial lines a gateway would print."""
        self.seq += 1
        for node_id in self.sensors:
            smoke = self._scripted_smoke(node_id, tick_index)
            self.smoke[node_id] = smoke
            # Heat and dryness track the smoke, the way a real fire would.
            ramp = 0.0 if node_id != HAZARD_NODE else min(
                1.0, max(0.0, (smoke - BASELINE_SMOKE) / (PEAK_SMOKE - BASELINE_SMOKE)))
            self.temp[node_id] = round(BASELINE_TEMP + (PEAK_TEMP - BASELINE_TEMP) * ramp, 1)
            self.humidity[node_id] = int(BASELINE_HUMIDITY + (PEAK_HUMIDITY - BASELINE_HUMIDITY) * ramp)
        #打包成json格式的字串，模擬網路傳輸
        lines = []
        for node_id in self.sensors:
            lines.append(json.dumps({
                "node_id": self.index[node_id],
                "smoke": self.smoke[node_id],
                "temp": self.temp[node_id],
                "humidity": self.humidity[node_id],
                "next_hop": self.solve_next_hop(node_id),
                "hops": self.hops[node_id],
            }, separators=(",", ":")))
        return lines


# Everything below is a diagnostic, not part of the running system.
# Run `python3 backend/mock_mesh.py` after editing tools/place.py.
#不用看，檢查路徑變化的測試程式碼 跟place.py差不多
def route_report(mesh: "VirtualMesh") -> List[str]:
    """How much smoke it takes to move each node's route, for this topology.

    These are properties of the current map, not constants: moving one node in
    place.py changes every one of them. Compute them, never write them down.
    """
    def clean():
        for nid in mesh.ids:
            mesh.smoke[nid] = 0
            mesh.temp[nid] = BASELINE_TEMP

    def hop_name(nid):
        h = mesh.solve_next_hop(nid)
        return {NEXT_HOP_SAFE: "SAFE", NEXT_HOP_TRAPPED: "TRAPPED"}.get(h, mesh.ids[h])

    lines = []
    for node_id in mesh.sensors:
        clean()
        first = hop_name(node_id)

        if first in mesh.exits or first in ("SAFE", "TRAPPED"):
            lines.append(f"  {node_id} -> {first:<8} straight to an exit, no reading moves it")
            continue

        switch_at, switch_to = None, None
        for level in range(0, SMOKE_THRESHOLD + 1, 5):
            clean()
            mesh.smoke[first] = level
            now = hop_name(node_id)
            if now != first:
                switch_at, switch_to = level, now
                break

        if switch_at is None:
            lines.append(f"  {node_id} -> {first:<8} stays until {first} is blocked outright")
            continue

        band = "CLEAN - would flap on sensor noise" if switch_at <= SMOKE_CLEAN else "weighted band"
        lines.append(
            f"  {node_id} -> {first:<8} switches to {switch_to:<5} once {first} reads {switch_at:>4}  ({band})")
    return lines


if __name__ == "__main__":
    import os

    model = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../data/factory_model.json")
    with open(model, "r", encoding="utf-8") as fh:
        mesh = VirtualMesh(json.load(fh))

    print(f"Smoke bands: clean <= {SMOKE_CLEAN} < weighted < {SMOKE_THRESHOLD} <= blocked")
    print(f"Temp bands : clean <= {TEMP_CLEAN} < weighted < {TEMP_BLOCK} <= blocked")
    print("Scan below varies smoke only.\n")
    for line in route_report(mesh):
        print(line)
