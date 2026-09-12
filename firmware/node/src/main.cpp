// Sensor node firmware.
//
// Each node reads its own smoke sensor, broadcasts its whole known table over
// ESP-NOW, and runs Dijkstra locally to choose the next hop toward an exit.
// The LED strip shows that direction. No server is involved, so the mesh keeps
// working with mains power and internet down.
//
// Every board runs this same binary. A board learns which node it is by reading
// its address pins, so nothing is configured per board in software.
// topology.h lists which pins each node needs tied to GND.

#include <Arduino.h>
#include <FastLED.h>
#include <DHTesp.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include "topology.h"
#include "mesh_protocol.h"

#define LED_PIN 18
#define NUM_LEDS 16
#define SMOKE_SENSOR_PIN 34
#define DHT_PIN 27


// DHT22 refuses to be read faster than every 2s.
#define DHT_INTERVAL_MS 2200

// Bench testing: build with -D FORCE_NODE_ID=2 to skip the MAC lookup.
#define WIFI_CHANNEL 1
// Smoke and heat each map to three routing bands, matching the dashboard:
//   <= CLEAN      cost multiplier 1.0, no effect on routing
//   in between    multiplier ramps 1.0 -> 1.0 + HAZARD_GAIN
//   >= BLOCK      impassable
// A node's cost is whichever of the two is worse, so either signal alone can
// close a corridor. Both pairs need calibrating against the real site.
#define SMOKE_CLEAN 800
#define SMOKE_THRESHOLD 2000
#define TEMP_CLEAN_DECIC 400
#define TEMP_BLOCK_DECIC 600
#define HAZARD_GAIN 2.0f
#define SENSOR_WARMUP_MS 5000
// Grace after warm-up for the first broadcasts to arrive, before an unheard-from
// neighbour is treated as impassable.
#define MESH_SETTLE_MS 1000
#define NODE_TIMEOUT_MS 6000
#define RX_QUEUE_DEPTH 8

static_assert(NUM_NODES <= MESH_MAX_NODES, "NUM_NODES exceeds mesh record capacity");

// What this node currently believes about every node, its own slot included.
typedef struct {
    uint16_t smoke;
    int16_t  temp;
    uint8_t  humidity;
    uint16_t seq;
    int8_t   nextHop;
    uint8_t  hops;
    unsigned long lastSeen;
    bool     everSeen;
} NodeState;

CRGB leds[NUM_LEDS];
DHTesp dht;
uint8_t chaseIndex = 0;
unsigned long lastDhtRead = 0;

int localNodeId = -1;
bool meshReady = false;
uint8_t localMac[6] = {0};
unsigned long lastIdentityNotice = 0;

NodeState meshTable[NUM_NODES];
static QueueHandle_t rxQueue = NULL;

unsigned long systemBootTime = 0;
unsigned long lastLedUpdate = 0;
unsigned long lastBroadcastTime = 0;
unsigned long lastPathCalcTime = 0;
uint16_t broadcastInterval = 200;
uint16_t localSeq = 0;
int cachedNextHop = NEXT_HOP_TRAPPED;
uint16_t sendFailures = 0;
unsigned long lastSendFault = 0;

void formatMac(const uint8_t *mac, char *out, size_t outSize) {
    snprintf(out, outSize, "%02X:%02X:%02X:%02X:%02X:%02X",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

bool isExitNode(int node) {
    for (int i = 0; i < NUM_EXITS; i++) {
        if (EXIT_NODES[i] == node) {
            return true;
        }
    }
    return false;
}

// The node number written in binary across the address pins listed in
// topology.h, grounded pin = 1. N pins cover 2^N - 1 nodes, so growing the site
// costs one more wire rather than a redesign.
//
// An unwired board reads 0, and an out-of-range or exit value cannot be a board
// position either. All of those refuse to run rather than guess, because the
// wrong number makes every arrow point the wrong way while looking normal.
int resolveLocalNodeId() {
#ifdef FORCE_NODE_ID
    return FORCE_NODE_ID;
#else
    for (int bit = 0; bit < ADDR_PIN_COUNT; bit++) {
        pinMode(ADDR_PINS[bit], INPUT_PULLUP);
    }
    delay(5);

    int id = 0;
    for (int bit = 0; bit < ADDR_PIN_COUNT; bit++) {
        if (digitalRead(ADDR_PINS[bit]) == LOW) {
            id |= (1 << bit);
        }
    }

    if (id >= NUM_NODES || isExitNode(id)) {
        return -1;
    }
    return id;
#endif
}

// Slow blink plus a repeated serial line, for the two states where this board
// cannot join the mesh. Distinguishable from a routing result: green means an
// exit, red means trapped, blue and magenta mean this board needs attention.
void showStandby(CRGB colour, const char *key) {
    if (millis() - lastIdentityNotice > 1000) {
        lastIdentityNotice = millis();
        char macText[18];
        formatMac(localMac, macText, sizeof(macText));
        char outMsg[64];
        snprintf(outMsg, sizeof(outMsg), "{\"%s\":\"%s\"}", key, macText);
        Serial.println(outMsg);
    }

    if (millis() - lastLedUpdate > 40) {
        lastLedUpdate = millis();
        bool blinkState = ((millis() / 500) % 2) == 0;
        fill_solid(leds, NUM_LEDS, blinkState ? colour : CRGB::Black);
        FastLED.show();
    }
}

bool isNodeStale(int node) {
    return !meshTable[node].everSeen || (millis() - meshTable[node].lastSeen > NODE_TIMEOUT_MS);
}

// Runs on the WiFi task. Queue the packet and return; merging it here would
// race with the path calculation in loop().
#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
void onDataReceived(const esp_now_recv_info_t *info, const uint8_t *incomingData, int len) {
#else
void onDataReceived(const uint8_t *mac, const uint8_t *incomingData, int len) {
#endif
    if (len < (int)MESH_HEADER_SIZE || len > (int)sizeof(MeshPacket)) {
        return;
    }

    MeshPacket packet;
    memcpy(&packet, incomingData, len);

    if (packet.magic != MESH_MAGIC || packet.recordCount > MESH_MAX_NODES) {
        return;
    }
    if ((size_t)len != meshPacketSize(packet.recordCount)) {
        return;
    }

    xQueueSend(rxQueue, &packet, 0);
}

void mergeRecord(const MeshRecord &rec) {
    if (rec.originId >= NUM_NODES || rec.originId == localNodeId) {
        return;
    }
    if (rec.hops >= MESH_MAX_HOPS) {
        return;
    }

    NodeState &slot = meshTable[rec.originId];

    // A rebooted node restarts at sequence 0, which looks older than what we
    // hold. Once it has been silent past the timeout, accept any sequence.
    if (!isNodeStale(rec.originId) && !meshSeqNewer(rec.seq, slot.seq)) {
        return;
    }

    slot.smoke = rec.smokeLevel;
    slot.temp = rec.tempDeciC;
    slot.humidity = rec.humidityPct;
    slot.seq = rec.seq;
    slot.nextHop = rec.nextHop;
    slot.hops = rec.hops + 1;
    slot.lastSeen = millis();
    slot.everSeen = true;
}

void drainRxQueue() {
    MeshPacket packet;
    while (xQueueReceive(rxQueue, &packet, 0) == pdTRUE) {
        for (uint8_t i = 0; i < packet.recordCount; i++) {
            mergeRecord(packet.records[i]);
        }
    }
}

// Broadcast everything we know, not just our own reading. Each node relaying
// the full table is what carries data beyond a single radio hop.
void broadcastMeshTable() {
    MeshPacket packet;
    packet.magic = MESH_MAGIC;
    packet.senderId = localNodeId;
    packet.recordCount = 0;

    bool warmingUp = (millis() - systemBootTime <= SENSOR_WARMUP_MS);

    for (int i = 0; i < NUM_NODES; i++) {
        // Say nothing about ourselves until the MQ-2 has warmed. Until then this
        // node holds a reading of 0, and broadcasting that asserts clean air to
        // everyone else - so a board that resets inside a fire would spend five
        // seconds inviting its neighbours to route people into it. Staying quiet
        // leaves them on the last reading they had, which then ages out to INF on
        // its own. No data is allowed to mean safe anywhere else here either.
        if (i == localNodeId && warmingUp) {
            continue;
        }
        if (i != localNodeId && (isNodeStale(i) || meshTable[i].hops >= MESH_MAX_HOPS)) {
            continue;
        }

        MeshRecord &rec = packet.records[packet.recordCount++];
        rec.originId = (uint8_t)i;
        rec.seq = meshTable[i].seq;
        rec.smokeLevel = meshTable[i].smoke;
        rec.tempDeciC = meshTable[i].temp;
        rec.humidityPct = meshTable[i].humidity;
        rec.nextHop = meshTable[i].nextHop;
        rec.hops = (i == localNodeId) ? 0 : meshTable[i].hops;
    }

    uint8_t broadcastMac[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    esp_err_t sent = esp_now_send(broadcastMac, (uint8_t *)&packet,
                                  meshPacketSize(packet.recordCount));

    // A node that cannot transmit is not dangerous: its neighbours stop hearing
    // it, mark it stale and route around it, which is the fail-safe doing its
    // job. So this does not stop the node - it still routes itself correctly
    // from what it receives. But the strip would look perfectly normal while the
    // board is mute, so the serial line is the only place it can surface.
    if (sent != ESP_OK) {
        sendFailures++;
        if (millis() - lastSendFault > 2000) {
            lastSendFault = millis();
            char msg[72];
            snprintf(msg, sizeof(msg), "{\"send_failed\":%u,\"node_id\":%d}",
                     sendFailures, localNodeId);
            Serial.println(msg);
        }
    } else if (sendFailures > 0) {
        char msg[72];
        snprintf(msg, sizeof(msg), "{\"send_recovered_after\":%u,\"node_id\":%d}",
                 sendFailures, localNodeId);
        Serial.println(msg);
        sendFailures = 0;
    }
}

// Averaged to damp ADC noise; the raw pin jitters by tens of counts.
uint16_t readSensorSmoke() {
    uint32_t accumulator = 0;
    for (int i = 0; i < 16; i++) {
        accumulator += analogRead(SMOKE_SENSOR_PIN);
        delayMicroseconds(50);
    }
    return (uint16_t)(accumulator / 16);
}

// Maps one reading onto the three bands described above.
float bandFactor(float value, float clean, float block) {
    if (value >= block) {
        return INF;
    }
    if (value <= clean) {
        return 1.0f;
    }
    return 1.0f + HAZARD_GAIN * (value - clean) / (block - clean);
}

// Cost multiplier for entering a node. INF means do not go there.
float hazardFactor(int node) {
    if (node != localNodeId && !isExitNode(node)) {
        // Nobody transmits their own reading until warm, so at the instant the
        // warm-up ends every neighbour is still unheard-from. Without the extra
        // second that lands as one blink of "trapped" on every strip at power-on,
        // before the first packets land. The fail-safe is not weakened, only
        // started a second later: a genuinely absent node still goes to INF.
        if (millis() - systemBootTime > SENSOR_WARMUP_MS + MESH_SETTLE_MS
                && isNodeStale(node)) {
            return INF;
        }
    }

    // Clean air and normal temperature must both cost exactly 1.0. Without
    // that floor, sensor baseline drift alone flips routes with no fire.
    float bySmoke = bandFactor((float)meshTable[node].smoke, SMOKE_CLEAN, SMOKE_THRESHOLD);

    // A dead DHT22 means no information, not danger; smoke still governs.
    float byTemp = 1.0f;
    if (meshTable[node].temp != TEMP_UNKNOWN) {
        byTemp = bandFactor((float)meshTable[node].temp, TEMP_CLEAN_DECIC, TEMP_BLOCK_DECIC);
    }

    return (byTemp > bySmoke) ? byTemp : bySmoke;
}

// Physical distance scaled by how dangerous the destination is.
float edgeCost(int u, int v) {
    float base = BASE_GRAPH[u][v];
    if (base >= INF) {
        return INF;
    }

    float fv = hazardFactor(v);
    if (fv >= INF) {
        return INF;
    }

    return base * fv;
}

// Dijkstra to the cheapest reachable exit.
// Returns the adjacent node to walk to, or NEXT_HOP_SAFE / NEXT_HOP_TRAPPED.
int solveNextHop(int startNode) {
    if (isExitNode(startNode) && meshTable[startNode].smoke < SMOKE_THRESHOLD) {
        return NEXT_HOP_SAFE;
    }

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
        if (u == -1) {
            break;
        }
        visited[u] = true;

        for (int v = 0; v < NUM_NODES; v++) {
            if (visited[v]) {
                continue;
            }

            float cost = edgeCost(u, v);
            if (cost < INF && (dist[u] + cost < dist[v])) {
                dist[v] = dist[u] + cost;
                parent[v] = u;
            }
        }
    }

    int bestExit = -1;
    float minExitDist = INF;
    for (int i = 0; i < NUM_EXITS; i++) {
        int exitCandidate = EXIT_NODES[i];
        if (exitCandidate == startNode) {
            continue;
        }
        if (dist[exitCandidate] < minExitDist) {
            minExitDist = dist[exitCandidate];
            bestExit = exitCandidate;
        }
    }

    if (bestExit == -1 || minExitDist >= INF) {
        return NEXT_HOP_TRAPPED;
    }

    int curr = bestExit;
    int hops = 0;
    while (parent[curr] != -1 && parent[curr] != startNode && hops < NUM_NODES) {
        curr = parent[curr];
        hops++;
    }

    return (parent[curr] == startNode) ? curr : NEXT_HOP_TRAPPED;
}

// How bad the air is where this board itself stands, from its own readings only.
//
// Routing deliberately ignores this: edgeCost() weights the *destination*, so a
// node's own smoke never inflates its own way out - which is right, the people
// standing in the smoke are the ones who most need to be sent somewhere. But it
// left the strip saying it with a calm green chase, identical to a clean
// corridor. Direction is still the message; urgency is how loudly it is said.
typedef enum {
    URGENCY_CLEAR = 0,
    URGENCY_RISING = 1,
    URGENCY_CRITICAL = 2
} Urgency;

// Same band edges as the routing cost and the dashboard colours, so a strip that
// has turned red cannot disagree with a node drawn red on screen.
Urgency localUrgency() {
    uint16_t smoke = meshTable[localNodeId].smoke;
    int16_t temp = meshTable[localNodeId].temp;
    bool hasTemp = (temp != TEMP_UNKNOWN);

    if (smoke >= SMOKE_THRESHOLD || (hasTemp && temp >= TEMP_BLOCK_DECIC)) {
        return URGENCY_CRITICAL;
    }
    if (smoke > SMOKE_CLEAN || (hasTemp && temp > TEMP_CLEAN_DECIC)) {
        return URGENCY_RISING;
    }
    return URGENCY_CLEAR;
}

// Frame interval per level. The dot crosses 16 LEDs in 640ms / 400ms / 240ms, so
// the corridor visibly hurries as it fills. The fade stays put at 60: the tail
// length is set by the fade alone, not by the frame rate, so the chase keeps the
// same shape and only gets faster.
const uint16_t LED_FRAME_MS[3] = { 40, 25, 15 };

// Not CRGB::Orange: at this brightness a WS2812B renders it green-ish, which is
// the one thing this colour must never be mistaken for. Tune on the real strip.
const CRGB LED_CHASE_COLOUR[3] = { CRGB::Green, CRGB(255, 100, 0), CRGB::Red };

// Blinking red across the whole strip when trapped, otherwise a chase running
// towards nextHop, coloured and paced by how bad it is here.
void renderLedAnimation(int nextHop, Urgency urgency) {
    if (nextHop == NEXT_HOP_SAFE) {
        fill_solid(leds, NUM_LEDS, CRGB::Green);
        return;
    }

    if (nextHop == NEXT_HOP_TRAPPED) {
        bool blinkState = ((millis() / 250) % 2) == 0;
        fill_solid(leds, NUM_LEDS, blinkState ? CRGB::Red : CRGB::Black);
        return;
    }

    fadeToBlackBy(leds, NUM_LEDS, 60);

    int8_t dir = 1;
    if (nextHop >= 0 && nextHop < NUM_NODES) {
        dir = LED_DIRECTIONS[localNodeId][nextHop];
    }

    CRGB colour = LED_CHASE_COLOUR[urgency];
    if (dir >= 0) {
        leds[chaseIndex] = colour;
    } else {
        leds[(NUM_LEDS - 1) - chaseIndex] = colour;
    }

    chaseIndex = (chaseIndex + 1) % NUM_LEDS;
}

void setup() {
    Serial.begin(115200);
    systemBootTime = millis();
    pinMode(SMOKE_SENSOR_PIN, INPUT);

    FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUM_LEDS);
    FastLED.setBrightness(120);
    dht.setup(DHT_PIN, DHTesp::DHT22);

    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    WiFi.macAddress(localMac);

    localNodeId = resolveLocalNodeId();
    if (localNodeId < 0) {
        return;
    }

    char macText[18];
    formatMac(localMac, macText, sizeof(macText));
    char bootMsg[64];
    snprintf(bootMsg, sizeof(bootMsg), "{\"boot_node_id\":%d,\"mac\":\"%s\"}", localNodeId, macText);
    Serial.println(bootMsg);

    for (int i = 0; i < NUM_NODES; i++) {
        meshTable[i].smoke = 0;
        meshTable[i].temp = TEMP_UNKNOWN;
        meshTable[i].humidity = HUMIDITY_UNKNOWN;
        meshTable[i].seq = 0;
        meshTable[i].nextHop = NEXT_HOP_TRAPPED;
        meshTable[i].hops = 0;
        meshTable[i].lastSeen = millis();
        meshTable[i].everSeen = false;
    }
    meshTable[localNodeId].everSeen = true;

    rxQueue = xQueueCreate(RX_QUEUE_DEPTH, sizeof(MeshPacket));
    if (rxQueue == NULL) {
        Serial.println("RX queue creation failed");
        return;
    }

    esp_wifi_set_promiscuous(true);
    esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
    esp_wifi_set_promiscuous(false);

    if (esp_now_init() != ESP_OK) {
        Serial.println("ESP-NOW init failed");
        return;
    }

    if (esp_now_register_recv_cb(onDataReceived) != ESP_OK) {
        Serial.println("ESP-NOW receive callback registration failed");
        return;
    }

    // Without the broadcast peer every esp_now_send() below fails. Unchecked,
    // the board would chase a confident green arrow while telling nobody
    // anything - the one failure mode the address-pin check exists to prevent.
    esp_now_peer_info_t peerInfo = {};
    memset(peerInfo.peer_addr, 0xFF, 6);
    peerInfo.channel = WIFI_CHANNEL;
    peerInfo.encrypt = false;
    if (esp_now_add_peer(&peerInfo) != ESP_OK) {
        Serial.println("ESP-NOW broadcast peer registration failed");
        return;
    }

    cachedNextHop = solveNextHop(localNodeId);
    meshTable[localNodeId].nextHop = (int8_t)cachedNextHop;
    meshReady = true;
}

void runNode() {
    drainRxQueue();

    uint16_t rawSmoke = readSensorSmoke();
    uint16_t validatedSmoke = 0;
    if (millis() - systemBootTime > SENSOR_WARMUP_MS) {
        validatedSmoke = rawSmoke;
    }

    meshTable[localNodeId].smoke = validatedSmoke;
    meshTable[localNodeId].lastSeen = millis();

    if (millis() - lastDhtRead > DHT_INTERVAL_MS) {
        lastDhtRead = millis();
        TempAndHumidity reading = dht.getTempAndHumidity();
        if (dht.getStatus() == DHTesp::ERROR_NONE) {
            meshTable[localNodeId].temp = (int16_t)lroundf(reading.temperature * 10.0f);
            meshTable[localNodeId].humidity = (uint8_t)lroundf(reading.humidity);
        } else {
            meshTable[localNodeId].temp = TEMP_UNKNOWN;
            meshTable[localNodeId].humidity = HUMIDITY_UNKNOWN;
        }
    }

    if (millis() - lastPathCalcTime > 200) {
        lastPathCalcTime = millis();
        cachedNextHop = solveNextHop(localNodeId);
        meshTable[localNodeId].nextHop = (int8_t)cachedNextHop;
    }

    if (millis() - lastBroadcastTime > broadcastInterval) {
        lastBroadcastTime = millis();
        broadcastInterval = 180 + (uint16_t)(esp_random() % 41);
        meshTable[localNodeId].seq = ++localSeq;
        broadcastMeshTable();
    }

    Urgency urgency = localUrgency();
    if (millis() - lastLedUpdate > LED_FRAME_MS[urgency]) {
        lastLedUpdate = millis();
        renderLedAnimation(cachedNextHop, urgency);
        FastLED.show();
    }
}

void loop() {
    if (localNodeId < 0) {
        // Address pins unset or set to a value no board can have. topology.h
        // lists which pins this board should have tied to GND.
        showStandby(CRGB::Blue, "bad_address");
    } else if (!meshReady) {
        // Radio or queue failed to start. Routing would be meaningless here.
        showStandby(CRGB::Magenta, "mesh_init_failed");
    } else {
        runNode();
    }

    // The loop task never blocks on its own: delayMicroseconds() busy-waits and
    // FastLED does not yield. Without this the idle task never gets scheduled
    // and the task watchdog reboots the board.
    delay(1);
}
