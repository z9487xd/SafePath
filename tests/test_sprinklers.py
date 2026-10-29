import asyncio
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from backend import app as api
from backend.mock_mesh import VirtualMesh, SCENARIOS, SCENARIO_SECONDS, CYCLE_SECONDS
from tools import place

ROOT = Path(__file__).resolve().parents[1]


class SprinklerTests(unittest.TestCase):
    def setUp(self):
        self.mesh = VirtualMesh(json.loads((ROOT / 'data/factory_model.json').read_text()))

    def test_image_topology_and_pins(self):
        self.assertEqual(len(self.mesh.ids), 17)
        self.assertEqual(self.mesh.sensors, [str(i) for i in range(1, 15)])
        for i in range(1, 15):
            self.assertEqual(self.mesh.index[str(i)], i)
        self.assertEqual(list(self.mesh.roles.values()).count('sprinkler'), 8)
        self.assertEqual(list(self.mesh.roles.values()).count('arrow'), 5)
        self.assertNotIn('EXC', self.mesh.adjacency['5'])
        self.assertEqual(place.ADDR_PIN_POOL[:4], [32, 33, 25, 26])
        self.assertNotIn(place.SPRINKLER_LED_PIN, place.ADDR_PIN_POOL[:4])

    def test_corridor_distance_strict_boundary_and_self(self):
        self.mesh.smoke['3'] = 2000
        self.assertEqual(self.mesh.sprinkler_flags('1'), 3)  # 395
        self.assertEqual(self.mesh.sprinkler_flags('6'), 3)
        self.assertEqual(self.mesh.sprinkler_flags('11'), 0)
        self.mesh.sprinkler_distance = 395
        self.assertEqual(self.mesh.sprinkler_flags('1'), 0)
        self.mesh.smoke['1'] = 2000
        self.assertEqual(self.mesh.sprinkler_flags('1'), 3)

    def test_heat_and_manual_auto(self):
        self.mesh.temp['2'] = 60
        self.assertEqual(self.mesh.sprinkler_flags('1'), 3)
        self.mesh.manual_until['1'] = time.monotonic() + 30
        self.assertEqual(self.mesh.sprinkler_flags('1'), 7)
        self.mesh.manual_until['1'] = 0
        self.assertEqual(self.mesh.sprinkler_flags('1'), 3)
        self.mesh.temp['2'] = 24
        self.assertEqual(self.mesh.sprinkler_flags('1'), 0)

    def test_manual_lease_expires_at_30_seconds(self):
        self.mesh.manual_until['5'] = 130.0
        with patch('time.monotonic', return_value=129.999):
            self.assertEqual(self.mesh.sprinkler_flags('5'), 5)
        with patch('time.monotonic', return_value=130.0):
            self.assertEqual(self.mesh.sprinkler_flags('5'), 0)
        self.mesh.smoke['5'] = 2600
        with patch('time.monotonic', return_value=130.0):
            self.assertEqual(self.mesh.sprinkler_flags('5'), 3)

    def test_api_serial_and_ingest(self):
        class Serial:
            def write(self, value):
                self.value = value
                return len(value)
        serial = Serial()
        with patch.object(api, 'USE_MOCK_SERIAL', False), patch.object(api, 'serial_connection', serial):
            result = api.control_sprinkler('8', 'on')
            self.assertEqual(result['status'], 'sent')
            self.assertEqual(serial.value.decode().split()[2:], ['8', '1'])
            with self.assertRaises(api.HTTPException):
                api.control_sprinkler('2', 'on')
        with patch.object(api, 'USE_MOCK_SERIAL', False), patch.object(api, 'serial_connection', None):
            with self.assertRaises(api.HTTPException) as error:
                api.control_sprinkler('1', 'on')
            self.assertEqual(error.exception.status_code, 503)
        api.ingest_pipeline(json.dumps({'node_id': 1, 'smoke': 220, 'sprinkler_flags': 5}))
        self.assertTrue(api.latest_telemetry['1']['sprinkler_active'])
        self.assertTrue(api.latest_telemetry['1']['sprinkler_manual'])
        self.assertFalse(api.latest_telemetry['1']['sprinkler_auto'])

    def test_multi_fire_scenarios_change_routes_and_recover(self):
        expected_changes = {0: {'3', '12'}, 1: {'4', '7'}, 2: {'3'}, 3: {'3', '6', '12'}}
        for index, scenario in enumerate(SCENARIOS):
            start = index * SCENARIO_SECONDS
            self.mesh.tick(start)
            baseline = {node: self.mesh.solve_next_hop(node) for node in self.mesh.sensors}
            records = [json.loads(line) for line in self.mesh.tick(start + 20)]
            danger = {str(row['node_id']) for row in records if row['smoke'] >= 2000 or row['temp'] >= 60}
            self.assertEqual(danger, set(scenario))
            changed = {node for node in self.mesh.sensors if self.mesh.solve_next_hop(node) != baseline[node]}
            self.assertEqual(changed, expected_changes[index])
            self.assertTrue(any(row['sprinkler_flags'] & 2 for row in records))
            self.mesh.tick(start + 44)
            self.assertEqual({node: self.mesh.solve_next_hop(node) for node in self.mesh.sensors}, baseline)
        thermal = SCENARIOS[3]['7']
        self.assertLess(thermal[0], 2000)
        self.assertGreaterEqual(thermal[1], 60)

    def test_complete_simulation_cycle_has_valid_routes(self):
        for tick in range(CYCLE_SECONDS + 1):
            records = [json.loads(line) for line in self.mesh.tick(tick)]
            self.assertEqual(len(records), 14)
            for row in records:
                node = self.mesh.ids[row['node_id']]
                hop = row['next_hop']
                if hop >= 0:
                    self.assertIn(self.mesh.ids[hop], self.mesh.adjacency[node])
                    self.assertLess(self.mesh._hazard_factor(self.mesh.ids[hop]), 99999)
                else:
                    self.assertEqual(hop, -1)

    def test_firmware_trigger_and_lease(self):
        # Compile the actual firmware functions with host GPIO/time substitutes.
        source = (ROOT / 'firmware/node/src/main.cpp').read_text()
        functions = source[source.index('bool nearbyDanger()'):source.index('void setup()')]
        harness = r'''
#include <cassert>
#include "topology.h"
#include "mesh_protocol.h"
#define SMOKE_THRESHOLD 2000
#define TEMP_BLOCK_DECIC 600
#define HIGH 1
#define LOW 0
struct State { uint16_t smoke=0; int16_t temp=TEMP_UNKNOWN; uint8_t flags=0; };
State meshTable[NUM_NODES];
bool stale[NUM_NODES] = {};
int localNodeId=ID_1;
unsigned long clockMs=0, manualStarted=0;
bool manualEnabled=false;
int output=0;
unsigned long millis() { return clockMs; }
void digitalWrite(int, int value) { output=value; }
bool isNodeStale(int i) { return stale[i]; }
bool isExitNode(int i) { return NODE_ROLES[i]==0; }
''' + functions + r'''
int main() {
    meshTable[ID_3].smoke=2000;
    assert(nearbyDanger());
    stale[ID_3]=true;
    assert(!nearbyDanger());
    meshTable[ID_1].temp=600;
    updateSprinkler(); assert(output==HIGH && meshTable[ID_1].flags==3);
    meshTable[ID_1].temp=TEMP_UNKNOWN;
    manualEnabled=true; manualStarted=0; clockMs=29999;
    updateSprinkler(); assert(output==HIGH && meshTable[ID_1].flags==5);
    clockMs=30000;
    updateSprinkler(); assert(output==LOW && meshTable[ID_1].flags==0);
    stale[ID_3]=false;
    localNodeId=ID_11;
    assert(!nearbyDanger());
}
'''
        with tempfile.TemporaryDirectory() as folder:
            cpp = Path(folder) / 'trigger.cpp'
            cpp.write_text(harness)
            exe = Path(folder) / 'trigger'
            subprocess.run(['g++', '-std=c++11', '-I', str(ROOT/'firmware/node/include'),
                            '-I', str(ROOT/'firmware/shared'), str(cpp), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
