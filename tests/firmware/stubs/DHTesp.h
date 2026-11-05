#pragma once
#include "Arduino.h"
static int dhtSetups = 0;
static int dhtReadCalls = 0;
static int dhtStatus = 0;
struct TempAndHumidity { float temperature, humidity; };
struct DHTesp {
    enum { DHT22 = 0, ERROR_NONE = 0, ERROR_TIMEOUT = 1, ERROR_CHECKSUM = 2 };
    void setup(int, int) { ++dhtSetups; }
    TempAndHumidity getTempAndHumidity() { ++dhtReadCalls; return {25, 50}; }
    int getStatus() { return dhtStatus; }
    const char *getStatusString() { return dhtStatus == ERROR_TIMEOUT ? "TIMEOUT" : dhtStatus == ERROR_CHECKSUM ? "CHECKSUM" : "OK"; }
};
