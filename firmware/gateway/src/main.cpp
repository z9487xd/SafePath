// Gateway firmware.
//
// Listens to the ESP-NOW mesh and prints one JSON line per new reading to USB
// serial for the backend. It also forwards bounded sprinkler commands; routing remains on nodes.

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <ctype.h>
#include <errno.h>
#include <stdlib.h>
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

// NULL once the queue and the radio are both up. Held as the message itself so
// loop() can keep repeating it: the backend normally opens the port well after
// the gateway has booted, and a line printed once in setup() is long gone by
// then - the operator would see a silent port and no reason for it.
static const char *startupError = NULL;

// Runs on the WiFi task. Validate and queue only; printing happens in loop().
void onDataReceived(RecvInfoPtr info, const uint8_t *incomingData, int len) {
    //篩選資料包，檢查資料包的長度是否符合MeshPacket的大小，如果不符合就直接return
    if (len < (int)MESH_HEADER_SIZE || len > (int)sizeof(MeshPacket)) {
        return;
    }

    MeshPacket packet = {};
    memcpy(&packet, incomingData, len);
    // 篩選資料包，檢查資料包的magic是否符合MESH_MAGIC，如果不符合就直接return
    if (packet.magic != MESH_MAGIC || packet.recordCount > MESH_MAX_NODES) {
        return;
    }
    // 篩選資料包，檢查資料包的長度是否符合MeshPacket的大小，如果不符合就直接return
    if ((size_t)len != meshPacketSize(packet.recordCount)) {
        return;
    }

    xQueueSend(rxQueue, &packet, 0);
}

void setup() {
    //115200是USB serial的baud rate，這個baud rate是ESP32的預設值
    Serial.begin(115200);

    for (int i = 0; i < MESH_MAX_NODES; i++) {
        originTable[i].seq = 0;
        originTable[i].lastSeen = 0;
        originTable[i].everSeen = false;
    }

    rxQueue = xQueueCreate(RX_QUEUE_DEPTH, sizeof(MeshPacket));
    if (rxQueue == NULL) {
        startupError = "queue creation failed";
        return;
    }
    if (!WiFi.mode(WIFI_STA)) {
        startupError = "WiFi station init failed";
        return;
    }
    WiFi.disconnect();

    //這是 ESP32 在沒有連線到 WiFi AP 時，仍然可以接收 ESP-NOW 的資料包的方式。這裡先把 WiFi 設定成 promiscuous 模式，然後設定頻道為 WIFI_CHANNEL，最後再關閉 promiscuous 模式。這樣就可以在指定的頻道上接收 ESP-NOW 的資料包。
    if (esp_wifi_set_promiscuous(true) != ESP_OK ||
        esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE) != ESP_OK ||
        esp_wifi_set_promiscuous(false) != ESP_OK) {
        startupError = "WiFi channel setup failed";
        return;
    }

    if (esp_now_init() != ESP_OK) {
        startupError = "ESP-NOW init failed";
        return;
    }

    esp_now_peer_info_t peer = {};
    memset(peer.peer_addr, 255, 6);
    peer.channel = WIFI_CHANNEL;
    if (esp_now_add_peer(&peer) != ESP_OK) {
        startupError = "broadcast peer registration failed";
        return;
    }

    // Unchecked, this fails silently and the gateway then blocks forever on a
    // queue nothing will ever fill, which reads exactly like a quiet mesh.
    if (esp_now_register_recv_cb(onDataReceived) != ESP_OK) {
        startupError = "ESP-NOW receive callback registration failed";
        return;
    }
}

static ControlPacket pendingCommand = {};
static uint8_t commandRetries = 0;
static unsigned long commandLastSent = 0;

// sscanf's unsigned conversions accept negative numbers and overflow. Reject
// malformed fields before narrowing them into the binary control packet.
bool parseControlCommand(const char *line, ControlPacket &command) {
    const char prefix[] = "SPRINKLER ";
    if (strncmp(line, prefix, sizeof(prefix) - 1) != 0) return false;
    const char *cursor = line + sizeof(prefix) - 1;
    const unsigned long limits[] = {UINT32_MAX, MESH_MAX_NODES - 1, 1};
    unsigned long fields[3];
    for (int i = 0; i < 3; ++i) {
        while (isspace(static_cast<unsigned char>(*cursor))) ++cursor;
        if (*cursor < '0' || *cursor > '9') return false;
        char *end;
        errno = 0;
        fields[i] = strtoul(cursor, &end, 10);
        if (errno == ERANGE || fields[i] > limits[i]) return false;
        if (*end && !isspace(static_cast<unsigned char>(*end))) return false;
        cursor = end;
    }
    while (isspace(static_cast<unsigned char>(*cursor))) ++cursor;
    if (*cursor || fields[0] == 0) return false;
    command = {CONTROL_MAGIC, static_cast<uint32_t>(fields[0]),
               static_cast<uint8_t>(fields[1]), static_cast<uint8_t>(fields[2]), 0};
    return true;
}

// Fixed-size line buffer: no blocking read or unbounded String allocation.
void readCommands() {
    static char line[80];
    static size_t used = 0;
    static bool overflow = false;
    while (Serial.available()) {
        char ch = Serial.read();
        if (ch == '\n') {
            line[used] = 0;
            ControlPacket command;
            if (!overflow && parseControlCommand(line, command)) {
                uint8_t broadcast[] = {255,255,255,255,255,255};
                esp_err_t result = esp_now_send(broadcast, (uint8_t *)&command, sizeof(command));
                pendingCommand = command;
                commandRetries = 2;
                commandLastSent = millis();
                Serial.printf("{\"command_sent\":%lu,\"ok\":%s}\n",
                              static_cast<unsigned long>(command.commandId), result == ESP_OK ? "true" : "false");
            }
            used=0; overflow=false;
        } else if (ch == '\0') overflow = true;
        else if (used < sizeof(line)-1) line[used++]=ch;
        else overflow=true;
    }
}

void loop() {
    // setup() can return before the queue exists. Reaching xQueueReceive() with
    // a NULL handle takes the board down instead of reporting anything, so the
    // fault has to be answered here rather than assumed away.
    //故障排除：setup()可能在queue建立之前就返回了。如果在xQueueReceive()中使用NULL的queue handle，會導致板子崩潰，而不是報告任何錯誤，所以這個錯誤必須在這裡處理，而不是假設它不存在。
    if (startupError != NULL) {
        char errMsg[96];
        snprintf(errMsg, sizeof(errMsg), "{\"error\":\"%s\"}", startupError);
        Serial.println(errMsg);
        delay(2000);
        return;
    }

    readCommands();
    // Same command ID makes retries idempotent on every field node.
    if (commandRetries && millis() - commandLastSent >= 150) {
        uint8_t broadcast[] = {255,255,255,255,255,255};
        esp_now_send(broadcast, (uint8_t *)&pendingCommand, sizeof(pendingCommand));
        commandLastSent = millis();
        commandRetries--;
    }
    MeshPacket packet;
    // Bound the wait so serial commands and retries run even with no telemetry.
    if (xQueueReceive(rxQueue, &packet, pdMS_TO_TICKS(10)) != pdTRUE) {
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
        bool stale = !slot.everSeen || (millis() - slot.lastSeen > NODE_TIMEOUT_MS);//stale表示斷線
        if (slot.everSeen && (rec.seq == slot.seq || (!stale && !meshSeqNewer(rec.seq, slot.seq)))) {
            continue;
        }

        slot.seq = rec.seq;
        slot.lastSeen = millis();
        slot.everSeen = true;

        // Built with integer maths so the line does not depend on float
        // printf support, and null rather than 0 when a sensor did not answer.
        //轉換溫度和濕度的格式
        char tempText[10] = "null";
        if (rec.tempDeciC != TEMP_UNKNOWN) {
            int16_t whole = rec.tempDeciC < 0 ? -rec.tempDeciC : rec.tempDeciC;
            snprintf(tempText, sizeof(tempText), "%s%d.%d",
                     rec.tempDeciC < 0 ? "-" : "", whole / 10, whole % 10);
        }

        char humidityText[6] = "null";
        if (rec.humidityPct != HUMIDITY_UNKNOWN) {
            snprintf(humidityText, sizeof(humidityText), "%u", rec.humidityPct);
        }
        //組裝成JSON格式的字串，並且把這個字串印出來
        char jsonBuffer[180];
        snprintf(jsonBuffer, sizeof(jsonBuffer),
                 "{\"node_id\":%u,\"smoke\":%u,\"temp\":%s,\"humidity\":%s,"
                 "\"next_hop\":%d,\"hops\":%u,\"sprinkler_flags\":%u}",
                 rec.originId,
                 rec.smokeLevel,
                 tempText,
                 humidityText,
                 rec.nextHop,
                 rec.hops, rec.flags);

        Serial.println(jsonBuffer);
    }
}
