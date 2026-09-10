import json
import heapq

def load_topology(filepath: str) -> dict:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def build_adjacency_list(topology: dict) -> tuple[dict, list, list]:
    adj = {node["id"]: [] for node in topology["nodes"]}
    exits = [node["id"] for node in topology["nodes"] if node["type"] == "exit"]
    walkable_nodes = [node["id"] for node in topology["nodes"] if node["type"] != "exit"]

    for edge in topology["edges"]:
        u = edge["source"]
        v = edge["target"]
        w = edge["base_distance"]
        adj[u].append((v, w))
        adj[v].append((u, w))

    return adj, exits, walkable_nodes

def dijkstra_single_node(start_node: str, adj: dict, exits: list, blocked_node: str = None) -> dict:
    dist = {node: float("inf") for node in adj}
    parent = {node: None for node in adj}
    dist[start_node] = 0.0

    pq = [(0.0, start_node)]
    visited = set()

    while pq:
        curr_dist, u = heapq.heappop(pq)

        if u in visited:
            continue
        visited.add(u)

        if u in exits:
            continue

        for neighbor, weight in adj[u]:
            if neighbor == blocked_node or u == blocked_node:
                continue

            cost = weight
            if curr_dist + cost < dist[neighbor]:
                dist[neighbor] = curr_dist + cost
                parent[neighbor] = u
                heapq.heappush(pq, (dist[neighbor], neighbor))

    best_exit = None
    min_exit_dist = float("inf")
    for ex in exits:
        if dist[ex] < min_exit_dist:
            min_exit_dist = dist[ex]
            best_exit = ex

    if best_exit is None or min_exit_dist == float("inf"):
        return {"path": [], "next_hop": None, "target_exit": None, "total_cost": float("inf")}

    path = []
    curr = best_exit
    while curr is not None:
        path.append(curr)
        curr = parent[curr]
    path.reverse()

    next_hop = path[1] if len(path) >= 2 else None

    return {
        "path": path,
        "next_hop": next_hop,
        "target_exit": best_exit,
        "total_cost": min_exit_dist
    }

def evaluate_all_nodes(topology: dict, blocked_node: str = None) -> dict:
    adj, exits, walkable_nodes = build_adjacency_list(topology)
    results = {}

    for node_id in walkable_nodes:
        if node_id == blocked_node:
            results[node_id] = {
                "status": "BLOCKED_HAZARD",
                "next_hop": None,
                "target_exit": None,
                "path": []
            }
        else:
            res = dijkstra_single_node(node_id, adj, exits, blocked_node)
            results[node_id] = {
                "status": "ACTIVE",
                "next_hop": res["next_hop"],
                "target_exit": res["target_exit"],
                "total_dist": res["total_cost"],
                "path": res["path"]
            }
    return results

if __name__ == "__main__":
    topo = load_topology("factory_model.json")

    print("=== SCENARIO 1: NORMAL WORKPLACE STATE ===")
    normal_results = evaluate_all_nodes(topo, blocked_node=None)
    for node, data in normal_results.items():
        print(f"[{node}] -> Directs to: {data['next_hop']} | Target Exit: {data['target_exit']} | Route: {data['path']}")

    print("\n=== SCENARIO 2: FIRE AT N3 (CHEMICAL STORAGE) ===")
    fire_results = evaluate_all_nodes(topo, blocked_node="N5")
    for node, data in fire_results.items():
        if data["status"] == "BLOCKED_HAZARD":
            print(f"[{node}] -> CRITICAL: HAZARD SOURCE (EVACUATE IMMEDIATE)")
        else:
            print(f"[{node}] -> Directs to: {data['next_hop']} | Target Exit: {data['target_exit']} | Route: {data['path']}")