"""Turns a floor-plan picture into the simplified colour blocks the dashboard draws.

    python -X utf8 tools/build_floor_plan.py <plan image>

The picture is read only for which colour is where. Walls, doors, text and the symbols
inside the rooms are thrown away: the output is a handful of plain rectangles in
data/floor_plan.json, in the same coordinate system as data/factory_model.json.

Doors are deliberately not carried over. The real doors have moved since the picture
was drawn, and the exits that matter are the exit nodes of the topology.

Placing the picture on the node map is a pure translation plus one uniform scale, so
the picture keeps its own proportions and the nodes keep their positions relative to
each other. Nothing in the repository records where the nodes physically stand, so
the three numbers below were fitted to where the user marked each node on a screenshot
of the map with the floor plan behind it, under the constraints that every node lies in
the room it was marked in, EXA is on the right wall and EXC on the bottom wall. The marks
were hand-drawn, so the fit is within about 60 picture pixels, not exact.
If the nodes really stand somewhere else, change PLACEMENT and run this again.
"""

import json
import os
import sys
from collections import Counter

from PIL import Image

OUT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../data/floor_plan.json"))

# Sampled from the picture: one pixel well inside each coloured area.
SAMPLES = {"cook": (150, 600), "serve": (500, 600), "prep": (450, 850), "storage": (400, 260), "rinse": (320, 800)}
CELL = 8          # picture pixels per grid cell; coarse on purpose, the output is a sketch
TOLERANCE = 45    # RGB distance within which a pixel counts as that colour

# picture pixel -> node map coordinate: node 1 stands at picture pixel NODE1_PX, and one
# picture pixel is SCALE node-map units in both directions.
PLACEMENT = {"scale": 1.66, "node1_px": (190, 527), "node1_world": (276, 663)}

# Nodes drawn higher or lower than the topology says while the base map is on, as
# (dx, dy) in node-map units, e.g. {"1": (0, -70)}. For drawing only: routing, costs and
# the firmware keep the coordinates in factory_model.json, so no edge length changes, but
# the lines to the moved nodes no longer have the length of their cost. Empty on purpose:
# the single shared placement above keeps every distance proportional.
NODE_OFFSETS = {}


def classify(image):
    reference = {k: image.getpixel(p) for k, p in SAMPLES.items()}

    def nearest(pixel):
        kind = min(reference, key=lambda k: sum((a - b) ** 2 for a, b in zip(pixel, reference[k])))
        distance = sum((a - b) ** 2 for a, b in zip(pixel, reference[kind]))
        return kind if distance < TOLERANCE ** 2 else None

    width, height = image.size
    grid = []
    for gy in range(height // CELL):
        row = []
        for gx in range(width // CELL):
            count = Counter()
            for y in range(gy * CELL, gy * CELL + CELL):
                for x in range(gx * CELL, gx * CELL + CELL):
                    kind = nearest(image.getpixel((x, y)))
                    if kind:
                        count[kind] += 1
            row.append(count.most_common(1)[0][0] if sum(count.values()) >= CELL * CELL * 0.3 else None)
        grid.append(row)
    return grid


# The orange strip down the right-hand side is drawn wider than in the picture (from
# column 72, not 84) so that nodes 6 and 10 both stand in it: with the picture's width
# the corridor between them would cross a room wall.
# The rooms as plain rectangles, read off the grid above. Fitting rectangles by machine
# gave fifty ragged pieces because the walls between rooms are not straight in the
# picture; this is the same layout with the walls straightened. LAYOUT cells are grid
# columns and rows of the whole picture, and check() reports how well each one matches.
LAYOUT = [
    ("storage", 12, 91, 22, 44),
    ("cook", 12, 38, 44, 86), ("cook", 12, 32, 86, 107), ("cook", 12, 38, 107, 136),
    ("rinse", 32, 50, 86, 107),
    ("serve", 38, 91, 44, 86), ("serve", 72, 91, 86, 136),
    ("prep", 50, 72, 86, 107), ("prep", 38, 72, 107, 136),
]


def check(grid):
    for kind, x0, x1, y0, y1 in LAYOUT:
        cells = [grid[y][x] for y in range(y0, y1) for x in range(x0, x1)]
        print(f"{kind:8s} {100.0 * cells.count(kind) / len(cells):5.1f}% of cells match")


def to_node_space(px, py):
    k, (u, v), (x, y) = PLACEMENT["scale"], PLACEMENT["node1_px"], PLACEMENT["node1_world"]
    return round(x + k * (px - u)), round(y + k * (py - v))


def main():
    image = Image.open(sys.argv[1]).convert("RGB")
    grid = classify(image)
    check(grid)
    zones = []
    for kind, x0, x1, y0, y1 in LAYOUT:
        wx1, wy1 = to_node_space(x0 * CELL, y0 * CELL)
        wx2, wy2 = to_node_space(x1 * CELL, y1 * CELL)
        zones.append({"kind": kind, "x": wx1, "y": wy1, "w": wx2 - wx1, "h": wy2 - wy1})
    json.dump({"note": "由 tools/build_floor_plan.py 從平面圖簡化而成，座標與 factory_model.json 相同；不含門與文字",
               "placement": PLACEMENT, "node_offsets": NODE_OFFSETS, "zones": zones}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(len(zones), "zones ->", OUT)


if __name__ == "__main__":
    main()
