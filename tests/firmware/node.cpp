#include "../../firmware/node/src/main.cpp"

void resetReadings() {
    for (int i = 0; i < NUM_NODES; ++i) {
        meshTable[i] = {};
        meshTable[i].everSeen = true;
        meshTable[i].lastSeen = testClock;
        meshTable[i].temp = TEMP_UNKNOWN;
    }
}

void testRecords() {
    localNodeId = 1;
    resetReadings();
    MeshRecord record = {3, 100, 2500, 650, 50, 4, 0, 0};
    mergeRecord(record);
    const auto received = meshTable[3].lastSeen;
    testClock += NODE_TIMEOUT_MS + 1;
    mergeRecord(record);
    assert(isNodeStale(3));  // A repeated old packet cannot revive a dead node.
    assert(meshTable[3].lastSeen == received);
    record.seq = 1;  // A rebooted origin can restart its counter after timeout.
    mergeRecord(record);
    assert(!isNodeStale(3) && meshTable[3].seq == 1);
    record.seq = 0;
    mergeRecord(record);
    assert(meshTable[3].seq == 1);
    meshTable[3].seq = 65535;
    mergeRecord(record);
    assert(meshTable[3].seq == 0);  // Normal sequence wrap.
    record.seq = 1; record.hops = MESH_MAX_HOPS;
    mergeRecord(record);
    assert(meshTable[3].seq == 0);
    record.originId = 255;
    mergeRecord(record);  // Must never index past the table.
}

void testPacketsAndCommands() {
    MeshPacket packet = {};
    packet.magic = MESH_MAGIC; packet.senderId = 3; packet.recordCount = 1;
    packet.records[0] = {3, 10, 2000, TEMP_UNKNOWN, HUMIDITY_UNKNOWN, 4, 0, 0};
    const auto *bytes = reinterpret_cast<const uint8_t *>(&packet);
    for (int len = 0; len <= int(sizeof(packet)) + 1; ++len) {
        onDataReceived(nullptr, bytes, len);
        assert(rxQueue->items.size() == (len == int(meshPacketSize(1)) ? 1U : 0U));
        rxQueue->items.clear();
    }
    packet.recordCount = 255;
    onDataReceived(nullptr, bytes, sizeof(packet));
    assert(rxQueue->items.empty());

    localNodeId = 5; resetReadings();
    ControlPacket command = {CONTROL_MAGIC, 123, 5, 1, 0};
    auto deliver = [&]() {
        onDataReceived(nullptr, reinterpret_cast<uint8_t *>(&command), sizeof(command));
        drainControls();
        updateSprinkler();
    };
    deliver();
    assert(manualEnabled && pinWrites[SPRINKLER_LED_PIN] == HIGH);
    const auto start = manualStarted;
    const auto sent = transmissions.size();
    testClock += 10000;
    deliver();
    assert(manualStarted == start && transmissions.size() == sent);
    testClock = start + MANUAL_DURATION_MS - 1;
    updateSprinkler(); assert(manualEnabled);
    testClock += 1;
    updateSprinkler(); assert(!manualEnabled && meshTable[5].flags == 0);
    deliver();  // Gateway retries after expiry must not restart the lease.
    assert(!manualEnabled);

    ++command.commandId; deliver(); assert(manualEnabled);
    meshTable[5].smoke = 2000; meshTable[5].lastSeen = testClock;
    ++command.commandId; command.enabled = 0; deliver();
    assert(!manualEnabled && meshTable[5].flags == 3);  // Automatic danger stays active.
    meshTable[5].smoke = 0;
    ++command.commandId; command.targetId = 1; command.enabled = 1; deliver();
    assert(!manualEnabled);  // Relay another sprinkler's command without applying it.
    ++command.commandId;
    command.targetId = 2; deliver(); assert(controlQueue->items.empty() && !manualEnabled);
    command.targetId = 5; command.enabled = 2; deliver(); assert(!manualEnabled);
    command.enabled = 1; command.hops = MESH_MAX_HOPS + 1; deliver(); assert(!manualEnabled);
    command.hops = MESH_MAX_HOPS;
    const auto before = transmissions.size();
    deliver(); assert(manualEnabled && transmissions.size() == before);
    manualEnabled = false;
}

void testRoutes() {
    // Independent all-pairs oracle, including every pair of simultaneous fires.
    testClock += 10000;
    systemBootTime = 0;
    for (int fireA = 0; fireA <= 14; ++fireA) {
        for (int fireB = fireA; fireB <= 14; ++fireB) {
            resetReadings();
            if (fireA) meshTable[fireA].smoke = SMOKE_THRESHOLD;
            if (fireB) meshTable[fireB].temp = TEMP_BLOCK_DECIC;
            float costs[NUM_NODES][NUM_NODES];
            for (int u = 0; u < NUM_NODES; ++u)
                for (int v = 0; v < NUM_NODES; ++v)
                    costs[u][v] = u == v ? 0 : ((v == fireA || v == fireB) && v != 0 ? INF : BASE_GRAPH[u][v]);
            for (int k = 0; k < NUM_NODES; ++k)
                for (int u = 0; u < NUM_NODES; ++u)
                    for (int v = 0; v < NUM_NODES; ++v)
                        costs[u][v] = std::min(costs[u][v], costs[u][k] + costs[k][v]);
            for (localNodeId = 1; localNodeId <= 14; ++localNodeId) {
                float best = INF;
                for (int exit : EXIT_NODES) best = std::min(best, costs[localNodeId][exit]);
                int hop = solveNextHop(localNodeId);
                if (best >= INF) { assert(hop == NEXT_HOP_TRAPPED); continue; }
                assert(hop >= 0 && hop < NUM_NODES);
                assert(BASE_GRAPH[localNodeId][hop] < INF);
                assert(isExitNode(hop) || (hop != fireA && hop != fireB));
                float tail = INF;
                for (int exit : EXIT_NODES) tail = std::min(tail, costs[hop][exit]);
                assert(fabsf(BASE_GRAPH[localNodeId][hop] + tail - best) < 0.01f);
                renderLedAnimation(hop, URGENCY_CLEAR);
            }
        }
    }
    localNodeId = 3; resetReadings();
    for (int i = 1; i <= 14; ++i) if (i != 3) meshTable[i].everSeen = false;
    assert(solveNextHop(3) == NEXT_HOP_TRAPPED);
    assert(bandFactor(800, SMOKE_CLEAN, SMOKE_THRESHOLD) == 1);
    assert(bandFactor(1400, SMOKE_CLEAN, SMOKE_THRESHOLD) == 2);
    assert(bandFactor(2000, SMOKE_CLEAN, SMOKE_THRESHOLD) == INF);
    for (int dir = 0; dir < 8; ++dir)
        for (int offset = -2; offset <= 1; ++offset) drawArrow(dir, offset, CRGB::Green);
}

int main() {
    setup();
#if DHT_PIN == 26
    assert(!meshReady && dhtSetups == 0);  // Reject the conflict before driving address pins.
    for (int i = 0; i < 10; ++i) { testClock += 1100; loop(); }
    assert(ESP.restarts == 0);
    assert(Serial.output.find("GPIO conflict") != std::string::npos);
#else
    assert(meshReady);
    for (address = 0; address <= 15; ++address)
        assert(resolveLocalNodeId() == (address > 0 && address < 15 ? int(address) : -1));
    testRecords();
    testPacketsAndCommands();
    testRoutes();
    meshReady = false; wifiOK = false; address = 1;
    setup();
    assert(!meshReady && startupError != nullptr);
    testClock += 1100; loop(); assert(ESP.restarts == 0);
    wifiOK = true; startupError = nullptr; channelResult = -1;
    setup();
    assert(!meshReady && startupError != nullptr);
    testClock += 1100; loop(); assert(ESP.restarts == 0);
#endif
}
