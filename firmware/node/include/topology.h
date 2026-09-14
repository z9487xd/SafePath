#ifndef TOPOLOGY_H
#define TOPOLOGY_H

#include <stdint.h>

#define NUM_NODES 6
#define NUM_EXITS 2
#define INF 99999.0f

#define ID_EX1 0
#define ID_N1 1
#define ID_N2 2
#define ID_N3 3
#define ID_N4 4
#define ID_EX2 5

const uint8_t EXIT_NODES[NUM_EXITS] = { 0, 5 };

#define ADDR_PIN_COUNT 3
const uint8_t ADDR_PINS[ADDR_PIN_COUNT] = { 32, 33, 25 };

// Node number in binary, 3 pins address up to 7 nodes.
// Tie these pins to GND on each board:
//   N1 = 1  ->  GPIO32
//   N2 = 2  ->  GPIO33
//   N3 = 3  ->  GPIO32 + GPIO33
//   N4 = 4  ->  GPIO25

// Corridor lengths in map units. INF means no corridor.
const float BASE_GRAPH[NUM_NODES][NUM_NODES] = {
    { 0.0f, 100.0f, INF, INF, INF, INF },
    { 100.0f, 0.0f, 100.0f, INF, 150.0f, INF },
    { INF, 100.0f, 0.0f, 150.0f, 180.3f, INF },
    { INF, INF, 150.0f, 0.0f, 100.0f, 100.0f },
    { INF, 150.0f, 180.3f, 100.0f, 0.0f, INF },
    { INF, INF, INF, 100.0f, INF, 0.0f },
};

// Arrow drawn on the matrix when walking row -> column. -1 means no corridor.
// 0 E, 1 NE, 2 N, 3 NW, 4 W, 5 SW, 6 S, 7 SE. N is the top of the map, so
// every matrix must be mounted flat with its top edge facing the map's top.
const int8_t ARROW_DIRS[NUM_NODES][NUM_NODES] = {
    { -1,  0, -1, -1, -1, -1 },
    {  4, -1,  0, -1,  6, -1 },
    { -1,  4, -1,  6,  5, -1 },
    { -1, -1,  2, -1,  4,  0 },
    { -1,  2,  1,  0, -1, -1 },
    { -1, -1, -1,  4, -1, -1 },
};

#endif
