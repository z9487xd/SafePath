"""Generates the site map for everything else from one place.

Edit the coordinates and corridors here, run this file, and it rewrites both:

    data/factory_model.json          read by the backend and the dashboard
    firmware/node/include/topology.h compiled into every node

Never edit those two by hand. Keeping one source is what stops the firmware and
the dashboard from disagreeing about the building.
"""

import os
import json
import math

# Dictionary order defines the node numbering used by the firmware (ID_EX1 = 0,
# ID_N1 = 1, ...), so reordering these lines renumbers every board and changes
# which address jumpers each board needs.
nodes_data = {
    "EX1": {"x": 300, "y": 300, "type": "exit"},
    "N1":  {"x": 260, "y": 500, "type": "node"},
    "N2":  {"x": 200, "y": 300, "type": "node"},
    "N3":  {"x": 130, "y": 160, "type": "node"},
    "N4":  {"x": 260, "y": 200, "type": "node"},
    "EX2": {"x": 100, "y": 100, "type": "exit"}
}


raw_edges = [
    ("EX1", "N4"),
    ("EX1", "N3"),
    ("N3",  "N4"),
    ("N2",  "N3"),
    ("N1",  "N2"),
    ("N2",  "EX2"),
    ("N1",  "EX2"),
    ("N4",  "N2"),
    ("N4",  "N1")
]

def calculate_edge_weight(p1, p2, positions):
    x1, y1 = positions[p1]["x"], positions[p1]["y"]
    x2, y2 = positions[p2]["x"], positions[p2]["y"]
    return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)

def generate_topology():
    edges_data = [
        (u, v, calculate_edge_weight(u, v, nodes_data))
        for u, v in raw_edges
    ]

    base_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.abspath(os.path.join(base_dir, "../data/factory_model.json"))
    header_path = os.path.abspath(os.path.join(base_dir, "../firmware/node/include/topology.h"))

    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    os.makedirs(os.path.dirname(header_path), exist_ok=True)

    node_keys = list(nodes_data.keys())
    num_nodes = len(node_keys)
    node_to_idx = {k: idx for idx, k in enumerate(node_keys)}

    # For the backend and the dashboard
    json_payload = {
        "nodes": [
            {"id": k, "index": node_to_idx[k], "x": v["x"], "y": v["y"], "type": v["type"]}
            for k, v in nodes_data.items()
        ],
        "edges": [{"source": u, "target": v, "cost": round(w, 2)} for u, v, w in edges_data]
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_payload, f, indent=2)

    # For the firmware: a distance matrix and a per-corridor LED flow direction
    exit_indices = [idx for k, idx in node_to_idx.items() if nodes_data[k]["type"] == "exit"]

    dist_matrix = [["INF" for _ in range(num_nodes)] for _ in range(num_nodes)]
    dir_matrix = [[" 0" for _ in range(num_nodes)] for _ in range(num_nodes)]

    for i in range(num_nodes):
        dist_matrix[i][i] = "0.0f"

    for u, v, w in edges_data:
        ui, vi = node_to_idx[u], node_to_idx[v]

        # Corridors are two-way, so the distance matrix is symmetric
        cost_str = f"{w:.1f}f"
        dist_matrix[ui][vi] = cost_str
        dist_matrix[vi][ui] = cost_str

        # Direction is not: walking u->v lights the strip the opposite way to v->u
        dir_matrix[ui][vi] = " 1"
        dir_matrix[vi][ui] = "-1"

    # For the firmware
    header_lines = [
        "#ifndef TOPOLOGY_H",
        "#define TOPOLOGY_H",
        "",
        "#include <stdint.h>",
        "",
        f"#define NUM_NODES {num_nodes}",
        f"#define NUM_EXITS {len(exit_indices)}",
        "#define INF 99999.0f",
        ""
    ]

    for k, idx in node_to_idx.items():
        header_lines.append(f"#define ID_{k} {idx}")

    header_lines.append("")
    header_lines.append(f"const uint8_t EXIT_NODES[NUM_EXITS] = {{ {', '.join(map(str, exit_indices))} }};")
    header_lines.append("")

    # Each board reads its own index off three address pins, so the wiring
    # table belongs next to the numbering it comes from.
    header_lines.append("// Address jumpers. Tie these pins to GND on each board:")
    for k, idx in node_to_idx.items():
        if nodes_data[k]["type"] == "exit":
            continue
        bits = [f"ADDR{b}" for b in range(3) if idx >> b & 1]
        header_lines.append(f"//   {k} = {idx}  ->  " + (" + ".join(bits) if bits else "none"))
    header_lines.append("")

    header_lines.append("// Corridor lengths in map units. INF means no corridor.")
    header_lines.append("const float BASE_GRAPH[NUM_NODES][NUM_NODES] = {")
    for row in dist_matrix:
        header_lines.append("    { " + ", ".join(row) + " },")
    header_lines.append("};")
    header_lines.append("")

    header_lines.append("// Which way the LED strip should chase. +1 forward, -1 backward, 0 unused.")
    header_lines.append("const int8_t LED_DIRECTIONS[NUM_NODES][NUM_NODES] = {")
    for row in dir_matrix:
        header_lines.append("    { " + ", ".join(row) + " },")
    header_lines.append("};")
    header_lines.append("")

    header_lines.append("#endif")

    with open(header_path, "w", encoding="utf-8") as f:
        f.write("\n".join(header_lines) + "\n")

if __name__ == "__main__":
    generate_topology()