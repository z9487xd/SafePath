import os
import json
import networkx as nx

def generate_topology():
    nodes_data = {
        "EX1": {"x": 300,  "y": 300, "type": "exit",     "name": "Main Gate A"},
        "N1":  {"x": 260, "y": 0, "type": "corridor", "name": "West Corridor"},
        "N2":  {"x": 100, "y": 200, "type": "junction", "name": "Central Hub"},
        "N3":  {"x": 100, "y": 300, "type": "hazard",   "name": "Chemical Storage"},
        "N4":  {"x": 260, "y": 200, "type": "corridor", "name": "North Hall"},
        "EX2": {"x": 100, "y": 0,  "type": "exit",     "name": "Emergency Exit B"}
    }

    #edges算法: (u, v, w) = (起點, 終點, 權重)
    edges_data = [
        ("EX1", "N4", 3.0),
        ("EX1", "N3", 3.0),
        ("N3",  "N4", 4.0),
        ("N2",  "N3", 5.0),
        ("N1",  "N2", 2.0),
        ("N2",  "EX2", 3.0)
    ]

    base_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.abspath(os.path.join(base_dir, "../data/factory_model.json"))
    header_path = os.path.abspath(os.path.join(base_dir, "../firmware/node/include/topology.h"))

    # 1. Export JSON Interface
    json_payload = {
        "nodes": [{"id": k, **v} for k, v in nodes_data.items()],
        "edges": [{"source": u, "target": v, "cost": w} for u, v, w in edges_data]
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_payload, f, indent=2)

    # 2. Export C++ Header Interface
    node_keys = list(nodes_data.keys())
    num_nodes = len(node_keys)
    node_to_idx = {k: idx for idx, k in enumerate(node_keys)}

    header_lines = [
        "#ifndef TOPOLOGY_H",
        "#define TOPOLOGY_H\n",
        f"#define NUM_NODES {num_nodes}",
        "#define INF 99999.0f\n"
    ]

    for k, idx in node_to_idx.items():
        header_lines.append(f"#define ID_{k} {idx}")

    header_lines.append("\nconst float BASE_GRAPH[NUM_NODES][NUM_NODES] = {")
    for i in range(num_nodes):
        row = []
        for j in range(num_nodes):
            row.append("0.0f" if i == j else "INF")
        for u, v, w in edges_data:
            ui, vi = node_to_idx[u], node_to_idx[v]
            if i == ui and j == vi: row[j] = f"{w:.1f}f"
            elif i == vi and j == ui: row[j] = f"{w:.1f}f"
        header_lines.append("    { " + ", ".join(row) + " },")
    header_lines.append("};\n")
    header_lines.append("#endif")

    with open(header_path, "w", encoding="utf-8") as f:
        f.write("\n".join(header_lines))

if __name__ == "__main__":
    generate_topology()
