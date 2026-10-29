// 找出哪些腳「自己接地」。序列埠 115200。
// 所有腳都開內部上拉，沒接東西應該讀到 HIGH；讀到 LOW 就是被拉到 GND。
//   low=[...]   現在接地的腳，每 0.5 秒印一次
//   變化時另印一行 ">> 27 接地" / ">> 27 放開"，方便拿杜邦線一支一支碰 GND 對照
// 測 27 是不是預設接地：先把 27 上的 DHT22 等東西全拔掉，開機看 low 裡有沒有 27。
#include <Arduino.h>

// 可以安全上拉讀取的腳（避開 flash 6~11、開機 strapping 0/2/5/12/15）
const uint8_t SCAN_PINS[] = { 4, 13, 14, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33 };
const int NUM_SCAN = sizeof(SCAN_PINS) / sizeof(SCAN_PINS[0]);

bool wasLow[NUM_SCAN];

void setup() {
    Serial.begin(115200);
    delay(500);
    for (int i = 0; i < NUM_SCAN; i++) {
        pinMode(SCAN_PINS[i], INPUT_PULLUP);
    }
    delay(5);

    Serial.println("pin_test start");
    Serial.print("開機時已接地: [");
    bool first = true;
    for (int i = 0; i < NUM_SCAN; i++) {
        wasLow[i] = (digitalRead(SCAN_PINS[i]) == LOW);
        if (wasLow[i]) {
            Serial.printf("%s%d", first ? "" : ",", SCAN_PINS[i]);
            first = false;
        }
    }
    Serial.println("]");
}

void loop() {
    for (int i = 0; i < NUM_SCAN; i++) {
        bool low = (digitalRead(SCAN_PINS[i]) == LOW);
        if (low != wasLow[i]) {
            Serial.printf(">> %d %s\n", SCAN_PINS[i], low ? "接地" : "放開");
            wasLow[i] = low;
        }
    }

    static uint32_t lastPrint = 0;
    if (millis() - lastPrint > 500) {
        lastPrint = millis();
        Serial.print("low=[");
        bool first = true;
        for (int i = 0; i < NUM_SCAN; i++) {
            if (wasLow[i]) {
                Serial.printf("%s%d", first ? "" : ",", SCAN_PINS[i]);
                first = false;
            }
        }
        Serial.println("]");
    }
    delay(20);
}
