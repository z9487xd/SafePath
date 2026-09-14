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

// Which way the LED strip should chase. +1 forward, -1 backward, 0 unused.
const int8_t LED_DIRECTIONS[NUM_NODES][NUM_NODES] = {
    {  0,  1,  0,  0,  0,  0 },
    { -1,  0,  1,  0, -1,  0 },
    {  0, -1,  0,  1,  1,  0 },
    {  0,  0, -1,  0,  1, -1 },
    {  0,  1, -1, -1,  0,  0 },
    {  0,  0,  0,  1,  0,  0 },
};

#endif
