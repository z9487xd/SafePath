// DHT22 讀不到的診斷。序列埠 115200，每輪檢查 GPIO27 和 GPIO26：
//   float_hi / float_lo  關掉內部上拉、只看外部電路時的電位
//       一直 HIGH  = 有外部上拉且 DHT 有電（正常）
//       亂跳或 LOW = 沒上拉電阻，或 DHT 沒電 / 沒共地
//   dht22 / dht11        用兩種型號各讀一次
#include <Arduino.h>
#include "DHTesp.h"

const uint8_t PINS[] = { 27, 26 };

void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println("dht_test start");
}

void probe(uint8_t pin) {
    pinMode(pin, INPUT);
    delay(5);
    int hi = 0;
    for (int i = 0; i < 50; i++) {
        hi += digitalRead(pin);
        delayMicroseconds(200);
    }

    DHTesp dht;
    dht.setup(pin, DHTesp::DHT22);
    delay(2100);
    TempAndHumidity a = dht.getTempAndHumidity();
    const char *s22 = dht.getStatusString();
    char r22[40];
    snprintf(r22, sizeof(r22), "%s %.1fC %.0f%%", s22, a.temperature, a.humidity);

    dht.setup(pin, DHTesp::DHT11);
    delay(2100);
    TempAndHumidity b = dht.getTempAndHumidity();
    char r11[40];
    snprintf(r11, sizeof(r11), "%s %.1fC %.0f%%", dht.getStatusString(), b.temperature, b.humidity);

    Serial.printf("GPIO%d float_hi=%d/50  dht22=%s  dht11=%s\n", pin, hi, r22, r11);
}

void loop() {
    for (uint8_t p : PINS) {
        probe(p);
    }
}
