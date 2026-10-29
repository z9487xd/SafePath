#pragma once
#include "Arduino.h"
enum { WIFI_STA };
static bool wifiOK = true;
struct TestWiFi {
    bool mode(int) { return wifiOK; }
    bool disconnect() { return true; }
    void macAddress(uint8_t *mac) { memset(mac, 0, 6); }
} static WiFi;
