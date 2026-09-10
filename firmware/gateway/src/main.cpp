#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>

typedef struct struct_message {
    uint8_t nodeId;
    uint16_t smokeRaw;
    float temperature;
    uint8_t statusFlag;
} struct_message;

struct_message incomingPacket;

void onDataRecv(const uint8_t *mac, const uint8_t *data, int len) {
    if (len != sizeof(struct_message)) {
        return;
    }
    memcpy(&incomingPacket, data, sizeof(incomingPacket));

    const char* statusStr = "NORMAL";
    if (incomingPacket.statusFlag == 1) statusStr = "WARNING";
    else if (incomingPacket.statusFlag == 2) statusStr = "HAZARD";

    char jsonBuffer[128];
    snprintf(jsonBuffer, sizeof(jsonBuffer),
             "{\"node_id\": \"N%u\", \"smoke\": %u, \"temp\": %.1f, \"status\": \"%s\"}",
             incomingPacket.nodeId,
             incomingPacket.smokeRaw,
             incomingPacket.temperature,
             statusStr);

    Serial.println(jsonBuffer);
}

void setup() {
    Serial.begin(115200);
    WiFi.mode(WIFI_STA);

    if (esp_now_init() != ESP_OK) {
        Serial.println("{\"error\": \"ESP-NOW Init Failed\"}");
        return;
    }

    esp_now_register_recv_cb(onDataRecv);
}

void loop() {
    delay(100);
}
