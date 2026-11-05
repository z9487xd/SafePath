#pragma once
#include "Arduino.h"
struct CRGB {
    enum { Black = 0, Red = 0xff0000, Green = 0x00ff00, Blue = 0x0000ff, Magenta = 0xff00ff };
    int value;
    uint8_t r, g, b;
    CRGB(int colour = 0) : value(colour), r((colour >> 16) & 255), g((colour >> 8) & 255), b(colour & 255) {}
    CRGB(int red, int green, int blue) : value((red << 16) | (green << 8) | blue), r(red), g(green), b(blue) {}
};
enum { WS2812B, GRB };
struct TestLED {
    template<int, int, int> void addLeds(CRGB *, int) {}
    void setBrightness(int) {}
    void show() {}
} static FastLED;
inline void fill_solid(CRGB *leds, int size, CRGB colour) {
    for (int i = 0; i < size; ++i) leds[i] = colour;
}
inline void fadeToBlackBy(CRGB *, int, int) {}
