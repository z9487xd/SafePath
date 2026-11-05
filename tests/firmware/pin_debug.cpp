#include <iostream>
#include "../../firmware/node/src/main.cpp"

void snapshot(const char *name, bool expectAdcRead = false) {
    const std::vector<int> modes(pinModes, pinModes + 40);
    const std::vector<int> writes(pinWrites, pinWrites + 40);
    const auto sent = transmissions.size();
    const int dhtReads = dhtReadCalls;
    const int adcReads = analogReadCalls;
    const int restarts = ESP.restarts;
    Serial.output.clear();
    testClock += PIN_DEBUG_INTERVAL_MS;
    logPinDebug();
    assert(!Serial.output.empty());
    const auto log = Serial.output;
    logPinDebug();
    assert(Serial.output == log); // Throttled even if loop runs again immediately.
    assert(std::vector<int>(pinModes, pinModes + 40) == modes);
    assert(std::vector<int>(pinWrites, pinWrites + 40) == writes);
    assert(transmissions.size() == sent && ESP.restarts == restarts);
    assert(dhtReadCalls == dhtReads);
    assert(analogReadCalls - adcReads == (expectAdcRead ? 16 : 0));
    std::cout << "{\"case\":\"" << name << "\"}\n" << log;
}

int main() {
    address = 3;
    setup();
#if DHT_PIN == 26
    assert(startupError && !ioPinsConfigured && !addressPinsConfigured);
    snapshot("startup_conflict");
#else
    assert(meshReady && ioPinsConfigured && addressPinsConfigured);
    smokeInput = 1234;
    testClock += SENSOR_WARMUP_MS + PIN_DEBUG_INTERVAL_MS + 1;
    Serial.output.clear();
    loop();
    assert(Serial.output.find("\"pin_debug\":\"mq2\"") != std::string::npos);
    assert(Serial.output.find("\"raw_adc\":1234") != std::string::npos);
    auto &self = meshTable[localNodeId];
    self.smoke = 1200;
    self.temp = 245;
    self.humidity = 56;
    self.flags = NODE_ROLES[localNodeId] == NODE_ROLE_SPRINKLER ? 5 : 0;
    latestRawSmoke = 1234;
    hasSmokeReading = true;
    lastDhtRead = testClock;
    manualEnabled = (self.flags & 4) != 0;
    manualStarted = testClock;
    digitalWrite(SPRINKLER_LED_PIN, (self.flags & 1) ? HIGH : LOW);
    fill_solid(leds, NUM_LEDS, CRGB::Black);
    if (matrixInitialized) leds[0] = CRGB::Green;
    testClock += SENSOR_WARMUP_MS + 1;
    snapshot("running");

    address = 10;
    snapshot("jumper_changed"); // Report live jumpers without adopting a new ID.

    self.temp = TEMP_UNKNOWN;
    self.humidity = HUMIDITY_UNKNOWN;
    dhtStatus = DHTesp::ERROR_TIMEOUT;
    dhtFailures = 2;
    snapshot("dht_timeout");

    systemBootTime = testClock;
    self.smoke = 0;
    snapshot("warmup"); // Raw ADC is visible while routing still uses zero.

    self.flags = 0;
    manualEnabled = false;
    digitalWrite(SPRINKLER_LED_PIN, LOW);
    snapshot("output_off");

    // No DHT response and no LED feedback: raw GPIO values remain available.
    self.temp = TEMP_UNKNOWN;
    self.humidity = HUMIDITY_UNKNOWN;
    dhtStatus = DHTesp::ERROR_TIMEOUT;
    externallyDrivenPins = (1ULL << DHT_PIN) | (1ULL << LED_PIN);
    inputLevels[DHT_PIN] = LOW;
    inputLevels[LED_PIN] = LOW;
    snapshot("no_peripheral_response");
    inputLevels[DHT_PIN] = HIGH;
    inputLevels[LED_PIN] = HIGH;
    snapshot("raw_inputs_high");
    externallyDrivenPins = 0;

    localNodeId = -1;
    meshReady = false;
    address = 0;
    smokeInput = 321;
    snapshot("bad_address", true);
#endif
}
