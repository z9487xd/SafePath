// Sensor node firmware.
//
// Each node reads its own smoke sensor, broadcasts its whole known table over
// ESP-NOW, and runs Dijkstra locally to choose the next hop toward an exit.
// The LED strip shows that direction. No server is involved, so the mesh keeps
// working with mains power and internet down.
//pio run -t upload -e bench_n1n2
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

#define LED_PIN 13
// One 8x8 WS2812B matrix per board, laid flat with its top edge towards the top
// of the site map, so the arrows in topology.h point the right way.
#define MATRIX_SIZE 8
#define NUM_LEDS (MATRIX_SIZE * MATRIX_SIZE)
// How the data line runs through the panel. 0: every row left to right.
// 1: serpentine, odd rows run right to left. Check the module before trusting
// any arrow - the wrong value draws a scrambled picture that still lights up.
#define MATRIX_SERPENTINE 0
#define SMOKE_SENSOR_PIN 34
// Any override must remain distinct from every address, sensor and output pin.
// GPIO26 is an address pin in the 14-node map and cannot be used for DHT data.
#ifndef DHT_PIN
#define DHT_PIN 27
#endif


// DHT22 refuses to be read faster than every 2s.
#define DHT_INTERVAL_MS 2200

// Bench testing: build with -D FORCE_NODE_ID=2 to skip the address-pin lookup.
#define WIFI_CHANNEL 1
// Smoke and heat each map to three routing bands, matching the dashboard:
//   <= CLEAN      cost multiplier 1.0, no effect on routing
//   in between    multiplier ramps 1.0 -> 1.0 + HAZARD_GAIN
//   >= BLOCK      impassable
// A node's cost is whichever of the two is worse, so either signal alone can
// close a corridor. Both pairs need calibrating against the real site.
#define SMOKE_CLEAN 800
#define SMOKE_THRESHOLD 2000
#define TEMP_CLEAN_DECIC 400 // 40.0C
#define TEMP_BLOCK_DECIC 600
#define HAZARD_GAIN 2.0f
#define SENSOR_WARMUP_MS 5000
// Grace after warm-up for the first broadcasts to arrive, before an unheard-from
// neighbour is treated as impassable.
#define MESH_SETTLE_MS 1000
#define NODE_TIMEOUT_MS 6000
#define RX_QUEUE_DEPTH 8 //這是 queue.h裡面定義的QueueHandle_t的大小，這個queue是用來接收esp-now的資料包的

static_assert(NUM_NODES <= MESH_MAX_NODES, "NUM_NODES exceeds mesh record capacity");

// What this node currently believes about every node, its own slot included.
typedef struct {
    uint16_t smoke;
    int16_t  temp;
    uint8_t  humidity;
    uint8_t flags;
    uint16_t seq;
    int8_t   nextHop;
    uint8_t  hops;
    unsigned long lastSeen;
    bool     everSeen;
} NodeState;

CRGB leds[NUM_LEDS];
DHTesp dht;
uint8_t animFrame = 0;
unsigned long lastDhtRead = 0;
uint16_t dhtFailures = 0;

int localNodeId = -1;
bool meshReady = false;
static const char *startupError = nullptr;
uint8_t localMac[6] = {0};
unsigned long lastIdentityNotice = 0;

NodeState meshTable[NUM_NODES];
static QueueHandle_t rxQueue = NULL;
static QueueHandle_t controlQueue = NULL;
uint32_t seenCommands[32] = {};
uint8_t seenCommandCursor = 0;
unsigned long manualStarted = 0;
bool manualEnabled = false;

void drainControls() {
    ControlPacket command;
    while (xQueueReceive(controlQueue, &command, 0) == pdTRUE) {
        bool seen = false;
        for (uint32_t id : seenCommands) if (id == command.commandId) seen = true;
        if (seen) continue;
        seenCommands[seenCommandCursor++ % 32] = command.commandId;
        if (command.targetId == localNodeId && NODE_ROLES[localNodeId] == NODE_ROLE_SPRINKLER) {
            manualEnabled = command.enabled;
            manualStarted = millis();
        }
        if (command.hops < MESH_MAX_HOPS) {
            command.hops++;
            uint8_t broadcast[] = {255,255,255,255,255,255};
            esp_now_send(broadcast, (uint8_t *)&command, sizeof(command));
        }
    }
}


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
// 算自己的node id，這個id是用來辨識自己是哪一個node，這個id是透過讀取ADDR_PINS陣列裡面的腳位來決定的，這些腳位是用來接地的，接地代表1，沒接地代表0，這樣就可以用二進位的方式來表示node id。
int resolveLocalNodeId() {
    int id = 0;
#ifdef FORCE_NODE_ID
    id = FORCE_NODE_ID;
#else
    for (int bit = 0; bit < ADDR_PIN_COUNT; bit++) {
        pinMode(ADDR_PINS[bit], INPUT_PULLUP);
    }
    delay(5);

    for (int bit = 0; bit < ADDR_PIN_COUNT; bit++) {
        if (digitalRead(ADDR_PINS[bit]) == LOW) {
            id |= (1 << bit);
        }
    }
    // 開機印一次每支位址腳讀到的值（1 = 有接地），跳線接觸不良時看這行就知道是哪支
    Serial.print("{\"addr_pins\":{");
    for (int bit = 0; bit < ADDR_PIN_COUNT; bit++) {
        Serial.printf("%s\"GPIO%d\":%d", bit ? "," : "", ADDR_PINS[bit],
                      digitalRead(ADDR_PINS[bit]) == LOW);
    }
    Serial.printf("},\"id\":%d}\n", id);
#endif

    if (id < 0 || id >= NUM_NODES || isExitNode(id)) {
        return -1;
    }
    return id;
}

// Slow blink plus a repeated serial line, for the two states where this board
// cannot join the mesh. Distinguishable from a routing result: green means an
// exit, red means trapped, blue and magenta mean this board needs attention.
void showStandby(CRGB colour, const char *key, const char *error = nullptr) {
    if (millis() - lastIdentityNotice > 1000) {
        lastIdentityNotice = millis();
        char macText[18];
        formatMac(localMac, macText, sizeof(macText));
        char outMsg[128];
        snprintf(outMsg, sizeof(outMsg), "{\"%s\":\"%s\"}", key, error ? error : macText);
        Serial.println(outMsg);
    }

    if (millis() - lastLedUpdate > 40) {
        lastLedUpdate = millis();
        bool blinkState = ((millis() / 500) % 2) == 0;
        fill_solid(leds, NUM_LEDS, blinkState ? colour : CRGB::Black);
        FastLED.show();
    }
}

bool isNodeStale(int node) {//判斷node是否已經超過NODE_TIMEOUT_MS毫秒沒有收到資料包
    return !meshTable[node].everSeen || (millis() - meshTable[node].lastSeen > NODE_TIMEOUT_MS);
}

// Runs on the WiFi task. Queue the packet and return; merging it here would
// race with the path calculation in loop().
#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
void onDataReceived(const esp_now_recv_info_t *info, const uint8_t *incomingData, int len) {
#else
void onDataReceived(const uint8_t *mac, const uint8_t *incomingData, int len) {
#endif
    if (len == sizeof(ControlPacket) && incomingData[0] == CONTROL_MAGIC) {
        ControlPacket command;
        memcpy(&command, incomingData, sizeof(command));
        if (command.commandId && command.targetId < NUM_NODES &&
            NODE_ROLES[command.targetId] == NODE_ROLE_SPRINKLER &&
            command.enabled <= 1 && command.hops <= MESH_MAX_HOPS)
            xQueueSend(controlQueue, &command, 0);
        return;
    }
    //篩選資料包，檢查資料包的長度是否符合MeshPacket的大小，如果不符合就直接return
    if (len < (int)MESH_HEADER_SIZE || len > (int)sizeof(MeshPacket)) {
        return;
    }

    MeshPacket packet = {};
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
    // 篩選資料，檢查資料是否有效
    if (rec.originId >= NUM_NODES || rec.originId == localNodeId || isExitNode(rec.originId)) {
        return;
    }
    if (rec.hops >= MESH_MAX_HOPS) {
        return;
    }

    NodeState &slot = meshTable[rec.originId];

    // A reboot may restart the counter after timeout, but an exact duplicate
    // must never refresh lastSeen: another relay may still hold that old record.
    if (slot.everSeen && (rec.seq == slot.seq ||
        (!isNodeStale(rec.originId) && !meshSeqNewer(rec.seq, slot.seq)))) {
        return;
    }

    slot.smoke = rec.smokeLevel;
    slot.temp = rec.tempDeciC;
    slot.humidity = rec.humidityPct;
    slot.flags = rec.flags;
    slot.seq = rec.seq;
    slot.nextHop = rec.nextHop;
    slot.hops = rec.hops + 1;
    slot.lastSeen = millis();
    slot.everSeen = true;
}

void drainRxQueue() {//從queue裡面取出資料包，並且把資料包裡面的每一個record都merge到meshTable裡面
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
        if (isExitNode(i)) {
            continue;
        }
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
        rec.flags = meshTable[i].flags;
        rec.nextHop = meshTable[i].nextHop;
        rec.hops = (i == localNodeId) ? 0 : meshTable[i].hops;
    }

    uint8_t broadcastMac[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    esp_err_t sent = esp_now_send(broadcastMac, (uint8_t *)&packet,
                                  meshPacketSize(packet.recordCount));//發射

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
    if (isExitNode(node)) {
        return 1.0f;
    }
#ifdef BENCH_LIVE_MASK
    // 桌上測試：只有 mask 裡的節點是實體板子，其餘當成空氣乾淨的走廊，
    // 否則沒接的節點會被故障保護判成 INF，兩片板子永遠只有一條路、看不到轉向。
    // 實體板子仍保留失聯即 INF 的保護。正式燒錄不要開。
    if (!((BENCH_LIVE_MASK >> node) & 1)) {
        return 1.0f;
    }
#endif
    if (node != localNodeId) {
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

    return (byTemp > bySmoke) ? byTemp : bySmoke;//取max
}

// Physical distance scaled by how dangerous the destination is.
float edgeCost(int u, int v) {//鄰近節點的成本計算，u是起點，v是終點，成本是由兩個因素決定的，一個是u到v的距離，另一個是v的危險程度，危險程度越高，成本越高
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
    float dist[NUM_NODES];
    bool visited[NUM_NODES];
    int parent[NUM_NODES];

    for (int i = 0; i < NUM_NODES; i++) {
        dist[i] = INF;
        visited[i] = false;
        parent[i] = -1;
    }
    dist[startNode] = 0.0f;

    for (int count = 0; count < NUM_NODES - 1; count++) {//Dijkstra演算法的主要迴圈，會跑NUM_NODES-1次，每次找出最短距離的節點，並更新其鄰居的距離
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

    //倒著寫
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

// Frame interval per level. The arrow steps forward every ARROW_STEP_FRAMES
// frames through ARROW_STEPS positions, so one sweep takes 640ms / 400ms / 240ms
// and the arrow visibly hurries as the air gets worse. The fade stays put at 60:
// the tail length is set by the fade alone, so only the speed changes.
const uint16_t LED_FRAME_MS[3] = { 40, 25, 15 };
#define ARROW_STEP_FRAMES 4
#define ARROW_STEPS 4

// Rows top to bottom, bit 7 = leftmost column. Only N and NE are drawn by hand;
// the other six directions are these two turned in 90 degree steps.
const uint8_t GLYPH_N[MATRIX_SIZE]  = { 0x00, 0x18, 0x3C, 0x7E, 0xDB, 0x18, 0x18, 0x18 };
const uint8_t GLYPH_NE[MATRIX_SIZE] = { 0x00, 0x1E, 0x06, 0x0A, 0x12, 0x20, 0x40, 0x00 };
const uint8_t GLYPH_X[MATRIX_SIZE]  = { 0x81, 0x42, 0x24, 0x18, 0x18, 0x24, 0x42, 0x81 };

// Screen step for each ARROW_DIRS value (0 E, 1 NE ... 7 SE), row grows downward.
const int8_t DIR_COL_STEP[8] = { 1, 1, 0, -1, -1, -1, 0, 1 };
const int8_t DIR_ROW_STEP[8] = { 0, -1, -1, -1, 0, 1, 1, 1 };
//二維轉一
uint16_t matrixIndex(int row, int col) {
    if (MATRIX_SERPENTINE && (row & 1)) {
        col = (MATRIX_SIZE - 1) - col;
    }
    return (uint16_t)(row * MATRIX_SIZE + col);
}

bool glyphPixel(const uint8_t *glyph, int row, int col) {
    return (glyph[row] >> ((MATRIX_SIZE - 1) - col)) & 1;
}

// Pixel (row, col) of the arrow for dir. Each quarter turn counter-clockwise
// takes the picture at (row, col) from (col, SIZE-1-row) of the one before.
bool arrowPixel(int dir, int row, int col) {
    const uint8_t *glyph = (dir & 1) ? GLYPH_NE : GLYPH_N;
    int turns = ((dir & 1) ? (dir - 1) : (dir - 2)) / 2;
    turns = ((turns % 4) + 4) % 4;
    for (int t = 0; t < turns; t++) {
        int srcRow = col;
        int srcCol = (MATRIX_SIZE - 1) - row;
        row = srcRow;
        col = srcCol;
    }
    return glyphPixel(glyph, row, col);
}

void drawArrow(int dir, int offset, CRGB colour) {
    for (int row = 0; row < MATRIX_SIZE; row++) {
        for (int col = 0; col < MATRIX_SIZE; col++) {
            if (!arrowPixel(dir, row, col)) {
                continue;
            }
            int r = row + DIR_ROW_STEP[dir] * offset;
            int c = col + DIR_COL_STEP[dir] * offset;
            if (r >= 0 && r < MATRIX_SIZE && c >= 0 && c < MATRIX_SIZE) {
                leds[matrixIndex(r, c)] = colour;
            }
        }
    }
}

void drawGlyph(const uint8_t *glyph, CRGB colour) {
    fill_solid(leds, NUM_LEDS, CRGB::Black);
    for (int row = 0; row < MATRIX_SIZE; row++) {
        for (int col = 0; col < MATRIX_SIZE; col++) {
            if (glyphPixel(glyph, row, col)) {
                leds[matrixIndex(row, col)] = colour;
            }
        }
    }
}

// Not CRGB::Orange: at this brightness a WS2812B renders it green-ish, which is
// the one thing this colour must never be mistaken for. Tune on the real strip.
const CRGB LED_CHASE_COLOUR[3] = { CRGB::Green, CRGB(255, 100, 0), CRGB::Red };

// Blinking red X when trapped, otherwise an arrow sliding towards nextHop,
// coloured and paced by how bad it is here.
void renderLedAnimation(int nextHop, Urgency urgency) {
    // if (nextHop == NEXT_HOP_SAFE) {
    //     fill_solid(leds, NUM_LEDS, CRGB::Green);
    //     return;
    // }

    // No corridor to nextHop cannot come out of solveNextHop, but an arrow drawn
    // from a missing entry would point somewhere at random. Say trapped instead.
    int dir = -1;
    if (nextHop >= 0 && nextHop < NUM_NODES) {
        dir = ARROW_DIRS[localNodeId][nextHop];
    }

    if (nextHop == NEXT_HOP_TRAPPED || dir < 0 || dir > 7) {
        bool blinkState = ((millis() / 250) % 2) == 0;
        drawGlyph(GLYPH_X, blinkState ? CRGB::Red : CRGB::Black);
        return;
    }

    fadeToBlackBy(leds, NUM_LEDS, 60);

    // Starts two steps behind centre and ends one step past it, so the arrow
    // travels the way it points.
    int step = (animFrame / ARROW_STEP_FRAMES) % ARROW_STEPS;
    drawArrow(dir, step - 2, LED_CHASE_COLOUR[urgency]);

    animFrame = (animFrame + 1) % (ARROW_STEP_FRAMES * ARROW_STEPS);
}

// Unweighted shortest corridor distances: danger blocks evacuation, but must
// never block propagation of a sprinkler trigger. Exits are terminal doors.
bool nearbyDanger() {
    float dist[NUM_NODES];
    bool visited[NUM_NODES] = {};
    for (int i=0; i<NUM_NODES; i++) dist[i] = INF;
    dist[localNodeId] = 0;
    for (int step=0; step<NUM_NODES; step++) {
        int u=-1;
        for (int i=0; i<NUM_NODES; i++)
            if (!visited[i] && (u<0 || dist[i]<dist[u])) u=i;
        if (u<0 || dist[u]>=SPRINKLER_DISTANCE) break;
        visited[u]=true;
        if (!isExitNode(u) && !isNodeStale(u) &&
            (meshTable[u].smoke >= SMOKE_THRESHOLD ||
             (meshTable[u].temp != TEMP_UNKNOWN && meshTable[u].temp >= TEMP_BLOCK_DECIC))) return true;
        if (isExitNode(u)) continue;
        for (int v=0; v<NUM_NODES; v++)
            if (BASE_GRAPH[u][v]<INF && dist[u]+BASE_GRAPH[u][v]<dist[v])
                dist[v]=dist[u]+BASE_GRAPH[u][v];
    }
    return false;
}

void updateSprinkler() {
    if (NODE_ROLES[localNodeId] != NODE_ROLE_SPRINKLER) return;
    if (manualEnabled && millis()-manualStarted >= MANUAL_DURATION_MS) manualEnabled=false;
    bool automatic = nearbyDanger();
    bool active = automatic || manualEnabled;
    meshTable[localNodeId].flags = (active ? 1 : 0) | (automatic ? 2 : 0) | (manualEnabled ? 4 : 0);
    digitalWrite(SPRINKLER_LED_PIN, active ? HIGH : LOW);
}

void setup() {
    Serial.begin(115200);
    // Validate before DHT.setup() or pinMode() can drive an address jumper.
    const uint8_t ioPins[] = {DHT_PIN, SPRINKLER_LED_PIN, LED_PIN, SMOKE_SENSOR_PIN};
    for (size_t i = 0; i < sizeof(ioPins) / sizeof(ioPins[0]); ++i) {
        for (size_t j = 0; j < i; ++j) {
            if (ioPins[i] == ioPins[j]) startupError = "GPIO conflict: sensor/output pins overlap";
        }
        for (int bit = 0; bit < ADDR_PIN_COUNT; ++bit) {
            if (ioPins[i] == ADDR_PINS[bit]) startupError = "GPIO conflict: sensor/output overlaps address pins";
        }
    }
    for (int bit = 0; bit < ADDR_PIN_COUNT; ++bit)
        for (int other = 0; other < bit; ++other)
            if (ADDR_PINS[bit] == ADDR_PINS[other]) startupError = "GPIO conflict: duplicate address pins";
    if (startupError) {
        Serial.println(startupError);
        return;
    }
    pinMode(SPRINKLER_LED_PIN, OUTPUT);
    digitalWrite(SPRINKLER_LED_PIN, LOW);
    systemBootTime = millis();
    pinMode(SMOKE_SENSOR_PIN, INPUT);


    dht.setup(DHT_PIN, DHTesp::DHT22);

    if (!WiFi.mode(WIFI_STA)) {
        startupError = "WiFi station init failed";
        return;
    }
    WiFi.disconnect();
    WiFi.macAddress(localMac);

    localNodeId = resolveLocalNodeId();
    if (localNodeId < 0) {
        return;
    }

    if (NODE_ROLES[localNodeId] == NODE_ROLE_ARROW) {
        FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUM_LEDS);
        FastLED.setBrightness(10);
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
        meshTable[i].flags = 0;
        meshTable[i].seq = 0;
        meshTable[i].nextHop = NEXT_HOP_TRAPPED;
        meshTable[i].hops = 0;
        meshTable[i].lastSeen = millis();
        meshTable[i].everSeen = false;
    }
    meshTable[localNodeId].everSeen = true;
    //建立一個queue，這個queue是用來接收esp-now的資料包的，這個queue的大小是RX_QUEUE_DEPTH，這個queue的元素大小是MeshPacket
    rxQueue = xQueueCreate(RX_QUEUE_DEPTH, sizeof(MeshPacket));
    controlQueue = xQueueCreate(8, sizeof(ControlPacket));
    if (rxQueue == NULL || controlQueue == NULL) {
        startupError = "RX queue creation failed";
        Serial.println(startupError);
        return;
    }
    // 設定WiFi為promiscuous模式，並設定頻道為WIFI_CHANNEL，然後再關閉promiscuous模式
    if (esp_wifi_set_promiscuous(true) != ESP_OK ||
        esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE) != ESP_OK ||
        esp_wifi_set_promiscuous(false) != ESP_OK) {
        startupError = "WiFi channel setup failed";
        return;
    }

    if (esp_now_init() != ESP_OK) {
        startupError = "ESP-NOW init failed";
        Serial.println(startupError);
        return;
    }

    if (esp_now_register_recv_cb(onDataReceived) != ESP_OK) {
        startupError = "ESP-NOW receive callback registration failed";
        Serial.println(startupError);
        return;
    }

    // Without the broadcast peer every esp_now_send() below fails. Unchecked,
    // the board would chase a confident green arrow while telling nobody
    // anything - the one failure mode the address-pin check exists to prevent.
    //註冊一個ESP-NOW的peer，這個peer是用來接收廣播的資料包的，這個peer的MAC地址是FF:FF:FF:FF:FF:FF，這個peer的頻道是WIFI_CHANNEL，這個peer不加密
    esp_now_peer_info_t peerInfo = {};
    memset(peerInfo.peer_addr, 0xFF, 6);
    peerInfo.channel = WIFI_CHANNEL;
    peerInfo.encrypt = false;
    if (esp_now_add_peer(&peerInfo) != ESP_OK) {
        startupError = "ESP-NOW broadcast peer registration failed";
        Serial.println(startupError);
        return;
    }
    //計算出下一個跳點，並且把下一個跳點存到meshTable裡面
    cachedNextHop = solveNextHop(localNodeId);
    meshTable[localNodeId].nextHop = (int8_t)cachedNextHop;
    meshReady = true;
}

void runNode() {
    drainRxQueue();
    drainControls();

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
            if (dhtFailures > 0) {
                char msg[72];
                snprintf(msg, sizeof(msg), "{\"dht_recovered_after\":%u,\"node_id\":%d}",
                         dhtFailures, localNodeId);
                Serial.println(msg);
                dhtFailures = 0;
            }
        } else {
            meshTable[localNodeId].temp = TEMP_UNKNOWN;
            meshTable[localNodeId].humidity = HUMIDITY_UNKNOWN;
            // The gateway only ever shows null, which cannot tell a missing wire
            // (TIMEOUT) from a noisy one (CHECKSUM). Once per read, every ~2s.
            dhtFailures++;
            char msg[80];
            snprintf(msg, sizeof(msg), "{\"dht_failed\":\"%s\",\"count\":%u,\"node_id\":%d}",
                     dht.getStatusString(), dhtFailures, localNodeId);
            Serial.println(msg);
        }
    }

    if (millis() - lastPathCalcTime > 200) {
        lastPathCalcTime = millis();
        cachedNextHop = solveNextHop(localNodeId);
        meshTable[localNodeId].nextHop = (int8_t)cachedNextHop;
    }

#ifndef BENCH_LIVE_MASK
    // 每秒印自己的讀值與燈號等級，校正 MQ-2 底線用（0 綠 / 1 琥珀 / 2 紅）
    static uint32_t lastSelfLog = 0;
    if (millis() - lastSelfLog > 1000) {
        lastSelfLog = millis();
        Serial.printf("{\"self\":%d,\"raw_smoke\":%u,\"temp\":%d,\"urgency\":%d,\"next\":%d}\n",
                      localNodeId, rawSmoke, meshTable[localNodeId].temp,
                      (int)localUrgency(), cachedNextHop);
    }
#endif

#ifdef BENCH_LIVE_MASK
    // 桌上測試用：每秒印自己的讀值、下一跳、以及收到的其他實體板子的讀值與成本倍率
    static uint32_t lastBenchLog = 0;
    if (millis() - lastBenchLog > 1000) {
        lastBenchLog = millis();
        Serial.printf("{\"bench\":%d,\"smoke\":%u,\"temp\":%d,\"next\":%d",
                      localNodeId, meshTable[localNodeId].smoke,
                      meshTable[localNodeId].temp, cachedNextHop);
        for (int i = 0; i < NUM_NODES; i++) {
            if (i != localNodeId && ((BENCH_LIVE_MASK >> i) & 1)) {
                Serial.printf(",\"N%d\":{\"smoke\":%u,\"temp\":%d,\"stale\":%d,\"factor\":%.2f}",
                              i, meshTable[i].smoke, meshTable[i].temp,
                              isNodeStale(i), hazardFactor(i));
            }
        }
        Serial.println("}");
    }
#endif

    updateSprinkler();

    if (millis() - lastBroadcastTime > broadcastInterval) {
        lastBroadcastTime = millis();
        broadcastInterval = 180 + (uint16_t)(esp_random() % 41);
        meshTable[localNodeId].seq = ++localSeq;
        broadcastMeshTable();
    }

    Urgency urgency = localUrgency();
    if (NODE_ROLES[localNodeId] == NODE_ROLE_ARROW && millis() - lastLedUpdate > LED_FRAME_MS[urgency]) {
        lastLedUpdate = millis();
        renderLedAnimation(cachedNextHop, urgency);
        FastLED.show();
    }
}

void loop() {
    if (startupError != nullptr) {
        showStandby(CRGB::Magenta, "startup_error", startupError);
    } else if (localNodeId < 0) {
        // Address pins unset or set to a value no board can have. topology.h
        // lists which pins this board should have tied to GND.
        showStandby(CRGB::Blue, "bad_address");
        // 跳線接觸不良時開機那一次可能讀錯，每秒重讀；讀到合法 ID 就重開，
        // 讓 setup() 從頭用正確身分初始化，不用拔電
        static uint32_t lastAddrRetry = 0;
        if (millis() - lastAddrRetry > 1000) {
            lastAddrRetry = millis();
            if (resolveLocalNodeId() >= 0) {
                ESP.restart();
            }
        }
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
