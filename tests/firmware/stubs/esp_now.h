#pragma once
#include "Arduino.h"
using esp_err_t = int;
#define ESP_OK 0
struct esp_now_peer_info_t { uint8_t peer_addr[6]; int channel; bool encrypt; };
static std::vector<std::vector<uint8_t>> transmissions;
static int radioResult = ESP_OK;
inline int esp_now_init() { return radioResult; }
inline int esp_now_register_recv_cb(void (*)(const uint8_t *, const uint8_t *, int)) { return ESP_OK; }
inline int esp_now_add_peer(const esp_now_peer_info_t *) { return ESP_OK; }
inline int esp_now_send(const uint8_t *, const uint8_t *data, size_t len) {
    transmissions.emplace_back(data, data + len); return radioResult;
}
inline uint32_t esp_random() { return 1; }
