#pragma once
#include "Arduino.h"
static int dhtSetups = 0;
struct TempAndHumidity { float temperature, humidity; };
struct DHTesp {
    enum { DHT22, ERROR_NONE };
    void setup(int, int) { ++dhtSetups; }
    TempAndHumidity getTempAndHumidity() { return {25, 50}; }
    int getStatus() { return ERROR_NONE; }
    const char *getStatusString() { return "OK"; }
};
