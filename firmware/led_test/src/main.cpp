// 8x8 WS2812B 矩陣測試：確認接線、顏色順序、走線方式（逐行 or 蛇形）。
// 序列埠 115200 會印出目前在跑哪一段。
#include <Arduino.h>
#include <FastLED.h>

#define LED_PIN 13
#define MATRIX_SIZE 8
#define NUM_LEDS (MATRIX_SIZE * MATRIX_SIZE)
// 64 顆全白滿亮約 3.8A，USB 供電撐不住，測試先壓低亮度
#define BRIGHTNESS 10

CRGB leds[NUM_LEDS];

void showFor(uint32_t ms) {
    FastLED.show();
    delay(ms);
}

// 1. 單色整片：確認每顆都會亮、紅綠藍沒有對調（對調就改 GRB）
void testColours() {
    const CRGB colours[] = { CRGB::Red, CRGB::Green, CRGB::Blue, CRGB::White };
    const char *names[] = { "RED", "GREEN", "BLUE", "WHITE" };
    for (int i = 0; i < 4; i++) {
        Serial.printf("[colour] %s\n", names[i]);
        fill_solid(leds, NUM_LEDS, colours[i]);
        showFor(1000);
    }
}

// 2. 依資料順序逐顆點亮：看第 8 顆（index 8）出現在第二列的左邊還是右邊
//    左邊 = 逐行（MATRIX_SERPENTINE 0），右邊 = 蛇形（MATRIX_SERPENTINE 1）
void testOrder() {
    Serial.println("[order] index 0 -> 63");
    FastLED.clear();
    for (int i = 0; i < NUM_LEDS; i++) {
        // 每列換一個顏色，比較好數
        leds[i] = CHSV((i / MATRIX_SIZE) * 32, 255, 255);
        showFor(60);
    }
    delay(1500);
}

// 3. 第一列紅、第一欄綠、index 0 白：確認矩陣上緣與原點位置
void testCorner() {
    Serial.println("[corner] row0=red col0=green index0=white");
    FastLED.clear();
    for (int i = 0; i < MATRIX_SIZE; i++) {
        leds[i] = CRGB::Red;                 // index 0..7
        leds[i * MATRIX_SIZE] = CRGB::Green; // 每列第一顆
    }
    leds[0] = CRGB::White;
    showFor(3000);
}

void testRainbow() {
    Serial.println("[rainbow]");
    for (int t = 0; t < 256; t += 2) {
        fill_rainbow(leds, NUM_LEDS, t, 4);
        showFor(15);
    }
}

void setup() {
    Serial.begin(115200);
    delay(200);
    Serial.printf("LED matrix test on GPIO%d, %d LEDs\n", LED_PIN, NUM_LEDS);
    FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUM_LEDS);
    FastLED.setBrightness(BRIGHTNESS);
    FastLED.clear(true);
}

void loop() {
    testColours();
    testOrder();
    testCorner();
    testRainbow();
    FastLED.clear(true);
    delay(500);
}
