#ifndef TOPOLOGY_H
#define TOPOLOGY_H

#include <stdint.h>

#define NUM_NODES 12
#define NUM_EXITS 2
#define INF 99999.0f

#define ID_EXA 0
#define ID_1 1
#define ID_2 2
#define ID_3 3
#define ID_4 4
#define ID_5 5
#define ID_6 6
#define ID_7 7
#define ID_8 8
#define ID_9 9
#define ID_10 10
#define ID_EXC 11

const uint8_t EXIT_NODES[NUM_EXITS] = { 0, 11 };

#define ADDR_PIN_COUNT 4
const uint8_t ADDR_PINS[ADDR_PIN_COUNT] = { 32, 33, 25, 26 };

// Node number in binary, 4 pins address up to 15 nodes.
// Tie these pins to GND on each board:
//   1 = 1  ->  GPIO32
//   2 = 2  ->  GPIO33
//   3 = 3  ->  GPIO32 + GPIO33
//   4 = 4  ->  GPIO25
//   5 = 5  ->  GPIO32 + GPIO25
//   6 = 6  ->  GPIO33 + GPIO25
//   7 = 7  ->  GPIO32 + GPIO33 + GPIO25
//   8 = 8  ->  GPIO26
//   9 = 9  ->  GPIO32 + GPIO26
//   10 = 10  ->  GPIO33 + GPIO26

#define NODE_ROLE_ARROW 1
#define NODE_ROLE_SPRINKLER 2
#define NODE_ROLE_SENSOR 3
#define SPRINKLER_DISTANCE 400.0f
#define SPRINKLER_LED_PIN 14
const uint8_t NODE_ROLES[NUM_NODES] = { 0, 1, 1, 2, 1, 2, 1, 2, 1, 2, 2, 0 };

// Corridor lengths in map units. INF means no corridor.
const float BASE_GRAPH[NUM_NODES][NUM_NODES] = {
    { 0.0f, INF, INF, INF, 350.01286f, INF, INF, INF, INF, INF, 338.42134f, INF },
    { INF, 0.0f, INF, 232.45860f, INF, 174.00000f, INF, INF, INF, INF, INF, INF },
    { INF, INF, 0.0f, INF, INF, 259.00000f, INF, 233.07080f, INF, INF, INF, INF },
    { INF, 232.45860f, INF, 0.0f, 327.00000f, INF, INF, INF, INF, INF, INF, INF },
    { 350.01286f, INF, INF, 327.00000f, 0.0f, INF, INF, INF, 449.58870f, INF, INF, INF },
    { INF, 174.00000f, 259.00000f, INF, INF, 0.0f, INF, INF, INF, INF, INF, INF },
    { INF, INF, INF, INF, INF, INF, 0.0f, INF, 291.00000f, INF, 162.63149f, INF },
    { INF, INF, 233.07080f, INF, INF, INF, INF, 0.0f, 258.26537f, INF, INF, INF },
    { INF, INF, INF, INF, 449.58870f, INF, 291.00000f, 258.26537f, 0.0f, 317.00000f, INF, INF },
    { INF, INF, INF, INF, INF, INF, INF, INF, 317.00000f, 0.0f, INF, 361.92955f },
    { 338.42134f, INF, INF, INF, INF, INF, 162.63149f, INF, INF, INF, 0.0f, INF },
    { INF, INF, INF, INF, INF, INF, INF, INF, INF, 361.92955f, INF, 0.0f },
};

// Arrow drawn on the matrix when walking row -> column. -1 means no corridor.
// 0 E, 1 NE, 2 N, 3 NW, 4 W, 5 SW, 6 S, 7 SE. N is the top of the map, so
// every matrix must be mounted flat with its top edge facing the map's top.
const int8_t ARROW_DIRS[NUM_NODES][NUM_NODES] = {
    { -1, -1, -1, -1,  4, -1, -1, -1, -1, -1,  6, -1 },
    { -1, -1, -1,  0, -1,  6, -1, -1, -1, -1, -1, -1 },
    { -1, -1, -1, -1, -1,  2, -1,  7, -1, -1, -1, -1 },
    { -1,  4, -1, -1,  0, -1, -1, -1, -1, -1, -1, -1 },
    {  0, -1, -1,  4, -1, -1, -1, -1,  6, -1, -1, -1 },
    { -1,  2,  6, -1, -1, -1, -1, -1, -1, -1, -1, -1 },
    { -1, -1, -1, -1, -1, -1, -1, -1,  4, -1,  1, -1 },
    { -1, -1,  3, -1, -1, -1, -1, -1,  1, -1, -1, -1 },
    { -1, -1, -1, -1,  2, -1,  0,  5, -1,  6, -1, -1 },
    { -1, -1, -1, -1, -1, -1, -1, -1,  2, -1, -1,  5 },
    {  2, -1, -1, -1, -1, -1,  5, -1, -1, -1, -1, -1 },
    { -1, -1, -1, -1, -1, -1, -1, -1, -1,  1, -1, -1 },
};

#endif
