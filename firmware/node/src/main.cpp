#include <Arduino.h>
#include <FastLED.h>
#include <WiFi.h>
#include "topology.h"

#define LED_PIN 18
#define NUM_LEDS 16
#define LOCAL_NODE_ID ID_N2

CRGB leds[NUM_LEDS];
float dynamicGraph[NUM_NODES][NUM_NODES];

uint16_t readSensorSmoke() {
    return 350;
}

int solveNextHop(int startNode, int hazardNode) {
    float dist[NUM_NODES];
    bool visited[NUM_NODES];
    int parent[NUM_NODES];

    for (int i = 0; i < NUM_NODES; i++) {
        dist[i] = INF;
        visited[i] = false;
        parent[i] = -1;
    }
    dist[startNode] = 0.0f;

    for (int count = 0; count < NUM_NODES - 1; count++) {
        float minDist = INF;
        int u = -1;
        for (int v = 0; v < NUM_NODES; v++) {
            if (!visited[v] && dist[v] < minDist) {
                minDist = dist[v];
                u = v;
            }
        }
        if (u == -1) break;
        visited[u] = true;

        for (int v = 0; v < NUM_NODES; v++) {
            float cost = dynamicGraph[u][v];
            if (u == hazardNode || v == hazardNode) cost = INF;
            if (!visited[v] && cost < INF && dist[u] + cost < dist[v]) {
                dist[v] = dist[u] + cost;
                parent[v] = u;
            }
        }
    }

    int bestExit = (dist[ID_EX1] < dist[ID_EX2]) ? ID_EX1 : ID_EX2;
    if (dist[bestExit] >= INF) return -1;

    int path[NUM_NODES];
    int len = 0;
    int curr = bestExit;
    while (curr != -1 && len < NUM_NODES) {
        path[len++] = curr;
        curr = parent[curr];
    }
    if (len >= 2 && path[len - 1] == startNode) {
        return path[len - 2];
    }
    return -1;
}

void setup() {
    Serial.begin(115200);
    FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUM_LEDS);

    for (int i = 0; i < NUM_NODES; i++) {
        for (int j = 0; j < NUM_NODES; j++) {
            dynamicGraph[i][j] = BASE_GRAPH[i][j];
        }
    }
}

void loop() {
    uint16_t smoke = readSensorSmoke();
    int hazardNode = (smoke > 2000) ? LOCAL_NODE_ID : -1;
    int nextHop = solveNextHop(LOCAL_NODE_ID, hazardNode);

    fill_solid(leds, NUM_LEDS, CRGB::Black);
    if (nextHop == ID_N1) {
        fill_solid(leds, NUM_LEDS, CRGB::Green);
    } else if (nextHop == ID_N3) {
        fill_solid(leds, NUM_LEDS, CRGB::Blue);
    } else {
        fill_solid(leds, NUM_LEDS, CRGB::Red);
    }
    FastLED.show();
    delay(50);
}