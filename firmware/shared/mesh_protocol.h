// Wire format shared by the sensor nodes and the gateway.
//
// Node and gateway are separate PlatformIO projects. Both include this file so
// the packet layout exists in exactly one place: a mismatch between two copies
// would not fail the build, it would silently drop packets over the air.
//
// A node broadcasts every reading it knows about, not just its own, so a
// reading reaches listeners that are out of the originator's radio range.

#ifndef MESH_PROTOCOL_H
#define MESH_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#define MESH_MAGIC 0x5A// The first byte of every packet, to detect misaligned or corrupted data.
#define MESH_MAX_NODES 24
#define MESH_MAX_HOPS 8

// Sentinels for a sensor that did not answer this round. Missing data must be
// distinguishable from a real reading of zero.-32768C are outside the range of the sensors, and 255% humidity is impossible.
#define TEMP_UNKNOWN INT16_MIN
#define HUMIDITY_UNKNOWN 0xFF

#define NEXT_HOP_SAFE (-2)
#define NEXT_HOP_TRAPPED (-1)

// One node's latest reading. originId is who measured it, not who sent it.
typedef struct __attribute__((packed)) {
    uint8_t  originId;
    uint16_t seq;
    uint16_t smokeLevel;
    int16_t  tempDeciC;     // temperature * 10
    uint8_t  humidityPct;
    int8_t   nextHop;
    uint8_t  hops;
} MeshRecord;

// Only recordCount records are actually transmitted, so packet size varies.
typedef struct __attribute__((packed)) {
    uint8_t    magic;
    uint8_t    senderId;
    uint8_t    recordCount;
    MeshRecord records[MESH_MAX_NODES];
} MeshPacket;

#define MESH_HEADER_SIZE offsetof(MeshPacket, records)

static inline size_t meshPacketSize(uint8_t recordCount) {
    return MESH_HEADER_SIZE + (size_t)recordCount * sizeof(MeshRecord);
}

// Sequence numbers wrap at 65535. A plain `a > b` would read a wrapped value
// as old and reject fresh data forever.
static inline bool meshSeqNewer(uint16_t a, uint16_t b) {
    return (int16_t)(a - b) > 0;
}

#endif
