#pragma once
#include "esp_now.h"
#define WIFI_SECOND_CHAN_NONE 0
static int channelResult = ESP_OK;
inline int esp_wifi_set_promiscuous(bool) { return ESP_OK; }
inline int esp_wifi_set_channel(int, int) { return channelResult; }
