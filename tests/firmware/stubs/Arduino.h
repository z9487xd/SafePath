#pragma once
#include <cassert>
#include <cmath>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define pdTRUE 1
#define pdFALSE 0
#define pdMS_TO_TICKS(x) (x)
#define ESP_ARDUINO_VERSION_MAJOR 2

static unsigned long testClock = 10000;
static int pinWrites[40] = {};
static int pinModes[40] = {};
static unsigned address = 1;
static int smokeInput = 0;
inline unsigned long millis() { return testClock; }
inline void delay(unsigned long ms) { testClock += ms; }
inline void delayMicroseconds(unsigned) {}
inline void pinMode(int pin, int mode) { pinModes[pin] = mode; }
inline void digitalWrite(int pin, int value) { pinWrites[pin] = value; }
inline int analogRead(int) { return smokeInput; }
inline int digitalRead(int pin) {
    const int pins[] = {32, 33, 25, 26};
    for (int bit = 0; bit < 4; ++bit)
        if (pin == pins[bit]) return (address & (1U << bit)) ? LOW : HIGH;
    return HIGH;
}
struct TestSerial {
    std::string input, output;
    void begin(int) {}
    int available() { return input.size(); }
    char read() { char c = input.front(); input.erase(0, 1); return c; }
    template<typename T> void print(T value) {
        std::ostringstream text; text << value; output += text.str();
    }
    template<typename T> void println(T value) { print(value); output += '\n'; }
    void printf(const char *fmt, ...) {
        char buffer[512]; va_list args;
        va_start(args, fmt); vsnprintf(buffer, sizeof(buffer), fmt, args); va_end(args);
        output += buffer;
    }
} static Serial;
struct TestESP { int restarts = 0; void restart() { ++restarts; } } static ESP;

struct TestQueue {
    size_t capacity, itemSize;
    std::deque<std::vector<uint8_t>> items;
    TestQueue(size_t count, size_t size) : capacity(count), itemSize(size) {}
};
using QueueHandle_t = TestQueue *;
static std::vector<std::unique_ptr<TestQueue>> queueStorage;
inline QueueHandle_t xQueueCreate(size_t count, size_t size) {
    queueStorage.emplace_back(new TestQueue(count, size));
    return queueStorage.back().get();
}
inline int xQueueSend(QueueHandle_t queue, const void *data, unsigned) {
    assert(queue);
    if (queue->items.size() == queue->capacity) return pdFALSE;
    const auto *bytes = static_cast<const uint8_t *>(data);
    queue->items.emplace_back(bytes, bytes + queue->itemSize);
    return pdTRUE;
}
inline int xQueueReceive(QueueHandle_t queue, void *data, unsigned) {
    assert(queue);
    if (queue->items.empty()) return pdFALSE;
    memcpy(data, queue->items.front().data(), queue->itemSize);
    queue->items.pop_front();
    return pdTRUE;
}
