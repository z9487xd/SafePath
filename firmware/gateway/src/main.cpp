// Gateway firmware.
//
// Listens to the ESP-NOW mesh and prints one JSON line per new reading to USB
// serial for the backend. It only receives, so it can never influence routing.

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include "mesh_protocol.h"

#define WIFI_CHANNEL 1
#define RX_QUEUE_DEPTH 16
#define NODE_TIMEOUT_MS 6000

#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
typedef const esp_now_recv_info_t *RecvInfoPtr;
#else
typedef const uint8_t *RecvInfoPtr;
#endif

// Last sequence number seen per originating node, used for de-duplication.
typedef struct {
    uint16_t seq;
    unsigned long lastSeen;
    bool everSeen;
} OriginState;

static QueueHandle_t rxQueue = NULL;
static OriginState originTable[MESH_MAX_NODES];

// Runs on the WiFi task. Validate and queue only; printing happens in loop().
void onDataReceived(RecvInfoPtr info, const uint8_t *incomingData, int len) {
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

void setup() {
    Serial.begin(115200);

    for (int i = 0; i < MESH_MAX_NODES; i++) {
        originTable[i].seq = 0;
        originTable[i].lastSeen = 0;
        originTable[i].everSeen = false;
    }

    rxQueue = xQueueCreate(RX_QUEUE_DEPTH, sizeof(MeshPacket));
    if (rxQueue == NULL) {
        Serial.println("{\"error\": \"Queue creation failed\"}");
        return;
    }

    WiFi.mode(WIFI_STA);
    WiFi.disconnect();

    esp_wifi_set_promiscuous(true);
    esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
    esp_wifi_set_promiscuous(false);

    if (esp_now_init() != ESP_OK) {
        Serial.println("{\"error\": \"ESP-NOW init failed\"}");
        return;
    }

    esp_now_register_recv_cb(onDataReceived);
}

void loop() {
    MeshPacket packet;

    if (xQueueReceive(rxQueue, &packet, portMAX_DELAY) != pdTRUE) {
        return;
    }

    // Every node relays every record, so the same reading arrives many times.
    // Forward it only when its sequence number is newer than what we last saw.
    for (uint8_t i = 0; i < packet.recordCount; i++) {
        const MeshRecord &rec = packet.records[i];
        if (rec.originId >= MESH_MAX_NODES) {
            continue;
        }

        OriginState &slot = originTable[rec.originId];
        bool stale = !slot.everSeen || (millis() - slot.lastSeen > NODE_TIMEOUT_MS);
        if (!stale && !meshSeqNewer(rec.seq, slot.seq)) {
            continue;
        }

        slot.seq = rec.seq;
        slot.lastSeen = millis();
        slot.everSeen = true;

        char jsonBuffer[96];
        snprintf(jsonBuffer, sizeof(jsonBuffer),
                 "{\"node_id\":%u,\"smoke\":%u,\"next_hop\":%d,\"hops\":%u}",
                 rec.originId,
                 rec.smokeLevel,
                 rec.nextHop,
                 rec.hops);

        Serial.println(jsonBuffer);
    }
}
