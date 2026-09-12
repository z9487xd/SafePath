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


# Free GPIOs used to encode the node number in binary. Avoids the LED, sensor
# and boot-strapping pins. N pins address 2^N - 1 nodes, so this list supports
# far more nodes than it has entries.
ADDR_PIN_POOL = [32, 33, 25, 26, 14, 13, 4]

# Corridors are the one thing that cannot be derived from the coordinates: walls
# are not in the positions. Everything downstream is, though - lengths, the LED
# direction matrix, the distance matrix - so move a node freely and the routing
# follows it. Nothing here is tuned to a particular layout.
#
# One property is worth watching while editing, and it is a property of the map,
# not of the algorithm: a node whose shortest way out is a direct edge to an exit
# has no decision to make. Weighting only ever raises a cost and an exit carries
# no sensor, so that edge keeps its plain length and always wins - which is the
# right answer, the node is standing at the door. But it means dynamic rerouting
# only ever shows up at nodes that reach an exit *through another sensor node*.
# reroute_report() prints who those are and by what margin on every run, so
# moving anything tells you immediately what it cost.
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

# Must match MESH_MAX_NODES in firmware/shared/mesh_protocol.h. Caught here so
# the message says what is wrong, instead of a static_assert deep in a C++ build.
MESH_MAX_NODES = 24


def build_adjacency():
    adjacency = {k: set() for k in nodes_data}
    problems = []

    for u, v in raw_edges:
        for end in (u, v):
            if end not in nodes_data:
                problems.append(f"corridor ({u}, {v}) names unknown node {end!r}")
        if u == v:
            problems.append(f"corridor ({u}, {v}) is a self loop")
        if u in adjacency and v in adjacency:
            if v in adjacency[u]:
                problems.append(f"corridor ({u}, {v}) is listed twice")
            adjacency[u].add(v)
            adjacency[v].add(u)

    return adjacency, problems


# Coordinates and corridors are typed by hand because walls are not derivable
# from positions, so the input cannot be generated - but it can be checked. A bad
# map is not cosmetic: this header is compiled into every board, and a node with
# no route to an exit blinks "trapped" forever with nothing anywhere saying why.
def validate_topology():
    keys = list(nodes_data.keys())
    exits = [k for k in keys if nodes_data[k]["type"] == "exit"]
    sensors = [k for k in keys if nodes_data[k]["type"] != "exit"]

    adjacency, problems = build_adjacency()

    if len(keys) > MESH_MAX_NODES:
        problems.append(f"{len(keys)} nodes exceeds MESH_MAX_NODES {MESH_MAX_NODES}")
    if not exits:
        problems.append("no exit nodes, so there is nothing to route towards")
    if not sensors:
        problems.append("no sensor nodes")

    # An unwired board reads 0 off its address pins and refuses to run only
    # because index 0 belongs to an exit, which resolveLocalNodeId() rejects.
    # Let a sensor land first and a forgotten jumper turns that board into a real
    # node instead - running with a wrong identity, every arrow confidently wrong.
    if keys and nodes_data[keys[0]]["type"] != "exit":
        problems.append(f"first entry {keys[0]!r} must be an exit: index 0 is what an "
                        f"unwired board reads, so it has to be a number no board can own")

    # A corridor list is written by hand and a forgotten line does not fail - it
    # silently becomes INF and quietly makes the routing worse.
    for k in sensors:
        seen, stack = {k}, [k]
        while stack:
            for n in adjacency[stack.pop()]:
                if n not in seen:
                    seen.add(n)
                    stack.append(n)
        if not any(e in seen for e in exits):
            problems.append(f"{k} cannot reach any exit")
        if not adjacency[k]:
            problems.append(f"{k} has no corridors")

    for k in exits:
        if not adjacency[k]:
            problems.append(f"exit {k} has no corridors, nobody can reach it")

    for a, b in ((a, b) for i, a in enumerate(keys) for b in keys[i + 1:]):
        if (nodes_data[a]["x"], nodes_data[a]["y"]) == (nodes_data[b]["x"], nodes_data[b]["y"]):
            problems.append(f"{a} and {b} share coordinates, any corridor between them is free")

    if problems:
        raise ValueError("topology rejected, nothing written:\n  - " + "\n  - ".join(problems))

    return adjacency, exits, sensors


# Which nodes actually have a decision to make. A node whose cheapest way out is
# a direct edge to an exit is frozen for good: weighting only raises costs and an
# exit carries no sensor, so no reading anywhere can move its arrow. Dynamic
# rerouting is the whole point of the system, and a map that lost it still
# generates, still compiles and still animates - so it has to be said out loud.
def reroute_report(edges_data, exits, sensors):
    cost = {}
    for u, v, w in edges_data:
        cost.setdefault(u, {})[v] = w
        cost.setdefault(v, {})[u] = w

    # Plain shortest path on corridor length, no hazard weighting: this asks what
    # the map allows, not what today's readings happen to do.
    def to_nearest_exit(src, banned):
        dist, todo = {src: 0.0}, {src}
        while todo:
            u = min(todo, key=lambda n: dist[n])
            todo.remove(u)
            for v, w in cost.get(u, {}).items():
                if v != banned and dist.get(v, math.inf) > dist[u] + w:
                    dist[v] = dist[u] + w
                    todo.add(v)
        return min((dist[e] for e in exits if e in dist), default=math.inf)

    lines, frozen = [], []
    for k in sensors:
        routes = sorted(
            (w if first in exits else w + to_nearest_exit(first, k), first)
            for first, w in cost.get(k, {}).items()
        )
        best = routes[0]
        if best[1] in exits:
            frozen.append(k)
            lines.append(f"  {k:<4} -> {best[1]:<4} direct exit, no reading can move it")
        elif len(routes) < 2 or routes[1][0] == math.inf:
            frozen.append(k)
            lines.append(f"  {k:<4} -> {best[1]:<4} only way out, no alternative to switch to")
        else:
            gap = (routes[1][0] - best[0]) / best[0] * 100
            lines.append(f"  {k:<4} -> {best[1]:<4} falls back to {routes[1][1]:<4} "
                         f"({gap:.0f}% longer), so smoke on {best[1]} turns it")
    return lines, frozen


def generate_topology():
    adjacency, exits, sensors = validate_topology()

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

    # Each board reads its own index as a binary number off the address pins,
    # so the wiring table belongs next to the numbering it comes from.
    addr_bits = max(1, (num_nodes - 1).bit_length())
    if addr_bits > len(ADDR_PIN_POOL):
        raise ValueError(f"{num_nodes} nodes need {addr_bits} address pins, "
                         f"pool only has {len(ADDR_PIN_POOL)}")
    addr_pins = ADDR_PIN_POOL[:addr_bits]

    header_lines.append(f"#define ADDR_PIN_COUNT {addr_bits}")
    header_lines.append(f"const uint8_t ADDR_PINS[ADDR_PIN_COUNT] = {{ "
                        + ", ".join(str(p) for p in addr_pins) + " };")
    header_lines.append("")
    header_lines.append(f"// Node number in binary, {addr_bits} pins address up to "
                        f"{2 ** addr_bits - 1} nodes.")
    header_lines.append("// Tie these pins to GND on each board:")
    for k, idx in node_to_idx.items():
        if nodes_data[k]["type"] == "exit":
            continue
        wires = [f"GPIO{addr_pins[b]}" for b in range(addr_bits) if idx >> b & 1]
        header_lines.append(f"//   {k} = {idx}  ->  " + " + ".join(wires))
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

    lines, frozen = reroute_report(edges_data, exits, sensors)
    print(f"{num_nodes} nodes ({len(sensors)} with sensors, {len(exits)} exits), "
          f"{len(edges_data)} corridors, {addr_bits} address pins")
    print("\n".join(lines))
    if len(frozen) == len(sensors):
        print("\nWARNING: every sensor node is frozen. Smoke anywhere will change the "
              "colours\n         and change no arrow: the site cannot demonstrate dynamic "
              "rerouting.\n         Remove a direct node-to-exit corridor to give a node "
              "a choice.")

if __name__ == "__main__":
    generate_topology()