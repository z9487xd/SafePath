#include "../../firmware/gateway/src/main.cpp"

void deliver(MeshRecord record) {
    MeshPacket packet = {};
    packet.magic = MESH_MAGIC; packet.senderId = 3; packet.recordCount = 1;
    packet.records[0] = record;
    onDataReceived(nullptr, reinterpret_cast<uint8_t *>(&packet), meshPacketSize(1));
    loop();
}

int main() {
    setup(); assert(startupError == nullptr);
    MeshRecord record = {3, 100, 700, -1, HUMIDITY_UNKNOWN, 4, 0, 0};
    deliver(record);
    assert(Serial.output.find("\"temp\":-0.1") != std::string::npos);
    assert(Serial.output.find("\"humidity\":null") != std::string::npos);
    Serial.output.clear();
    testClock += NODE_TIMEOUT_MS + 1;
    deliver(record);
    assert(Serial.output.empty());  // An old relay must not refresh dashboard connectivity.
    record.seq = 1; record.tempDeciC = TEMP_UNKNOWN; record.flags = 5;
    deliver(record);
    assert(Serial.output.find("\"temp\":null") != std::string::npos);
    assert(Serial.output.find("\"sprinkler_flags\":5") != std::string::npos);

    Serial.input = "SPRINKLER 4294967295 5 1\r\n";
    readCommands();
    assert(transmissions.size() == 1);
    ControlPacket command;
    memcpy(&command, transmissions.back().data(), sizeof(command));
    assert(command.commandId == UINT32_MAX && command.targetId == 5 && command.enabled == 1);
    testClock += 150; loop();
    testClock += 150; loop();
    testClock += 150; loop();
    assert(transmissions.size() == 3 && commandRetries == 0);
    assert(transmissions[0] == transmissions[1] && transmissions[1] == transmissions[2]);

    const char *invalid[] = {
        "SPRINKLER -1 5 1\n", "SPRINKLER 4294967296 5 1\n",
        "SPRINKLER 1 4294967301 1\n", "SPRINKLER 1 5 4294967297\n",
        "SPRINKLER 0 5 1\n", "SPRINKLER 1 255 1\n", "SPRINKLER 1 5 2\n",
        "SPRINKLER 1 5 1 trailing\n", "SPRINKLER 1 5\n", "SPRINKLER +1 5 1\n",
    };
    for (const char *line : invalid) {
        Serial.input = line; readCommands(); assert(transmissions.size() == 3);
    }
    Serial.input = std::string(100, 'X') + "\nSPRINKLER 10 5 0\n";
    readCommands(); assert(transmissions.size() == 4);
    commandRetries = 0;
    Serial.input = "SPRINKLER 11 5";
    readCommands(); assert(transmissions.size() == 4);
    Serial.input = " 1\n";
    readCommands(); assert(transmissions.size() == 5);
    commandRetries = 0;
    const char embeddedNull[] = "SPRINKLER 12 5 1\0junk\n";
    Serial.input.assign(embeddedNull, sizeof(embeddedNull) - 1);
    readCommands(); assert(transmissions.size() == 5);

    MeshPacket packet = {};
    packet.magic = MESH_MAGIC; packet.recordCount = 1;
    const auto *bytes = reinterpret_cast<const uint8_t *>(&packet);
    for (int len = 0; len <= int(sizeof(packet)) + 1; ++len) {
        onDataReceived(nullptr, bytes, len);
        assert(rxQueue->items.size() == (len == int(meshPacketSize(1)) ? 1U : 0U));
        rxQueue->items.clear();
    }
    packet.recordCount = 255;
    onDataReceived(nullptr, bytes, sizeof(packet));
    assert(rxQueue->items.empty());
    wifiOK = false;
    setup(); assert(startupError != nullptr);
    loop();
    wifiOK = true; startupError = nullptr; channelResult = -1;
    setup(); assert(startupError != nullptr);
    loop();
}
