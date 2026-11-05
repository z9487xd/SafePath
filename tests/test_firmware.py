"""Run the actual firmware sources with host substitutes for ESP32 I/O."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUBS = ROOT / 'tests/firmware/stubs'


class FirmwareTests(unittest.TestCase):
    def run_firmware(self, name, *flags):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / name
            subprocess.run([
                'g++', '-std=c++11', '-Wall', '-Wextra', '-Wno-unused-parameter',
                '-fsanitize=undefined', '-fsanitize-undefined-trap-on-error',
                '-I', str(STUBS), '-I', str(ROOT / 'firmware/shared'),
                '-I', str(ROOT / 'firmware/node/include'), *flags,
                str(ROOT / f'tests/firmware/{name}.cpp'), '-o', str(executable),
            ], check=True)
            return subprocess.run([str(executable)], check=True, timeout=30,
                                  stdout=subprocess.PIPE, text=True).stdout

    def read_pin_snapshots(self, *flags):
        snapshots = {}
        for line in self.run_firmware('pin_debug', *flags).splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if 'case' in row:
                current = snapshots[row['case']] = {}
            else:
                current[row['pin_debug']] = row
        return snapshots

    def check_pin_snapshots(self, snapshots, forced=False):
        expected = {'node', 'gpio', 'address', 'mq2', 'dht22', 'sprinkler', 'matrix'}
        for snapshot in snapshots.values():
            self.assertEqual(set(snapshot), expected)
        running = snapshots['running']
        self.assertEqual(running['node']['self'], 1 if forced else 3)
        self.assertEqual(running['node']['state'], 'running')
        self.assertEqual(running['address']['levels'],
                         {'GPIO32': 0, 'GPIO33': 0, 'GPIO25': 1, 'GPIO26': 1})
        self.assertEqual(running['address']['forced_id'], forced)
        self.assertEqual(running['address']['matches_node'], not forced)
        self.assertEqual(running['mq2']['gpio'], 34)
        self.assertEqual(running['mq2']['raw_adc'], 1234)
        self.assertEqual(running['mq2']['used_adc'], 1200)
        self.assertEqual(running['dht22']['gpio'], 27)
        self.assertEqual(running['dht22']['temp_c'], 24.5)
        self.assertEqual(running['dht22']['humidity_pct'], 56)
        self.assertEqual(running['sprinkler']['gpio'], 14)
        self.assertEqual(running['sprinkler']['level'], 0 if forced else 1)
        self.assertEqual(running['sprinkler']['command'], 0 if forced else 1)
        self.assertEqual(running['sprinkler']['manual'], not forced)
        self.assertEqual(running['matrix']['gpio'], 13)
        self.assertEqual(running['matrix']['enabled'], forced)
        self.assertEqual(running['matrix']['buffer_lit_pixels'], 1 if forced else 0)
        self.assertIn(running['matrix']['idle_level'], (0, 1))
        levels = running['gpio']['levels']
        self.assertEqual(set(levels), {'GPIO13', 'GPIO14', 'GPIO27', 'GPIO32',
                                      'GPIO33', 'GPIO25', 'GPIO26'})
        self.assertTrue(all(level in (0, 1) for level in levels.values()))
        self.assertEqual(levels['GPIO14'], running['sprinkler']['level'])
        self.assertEqual(levels['GPIO27'], running['dht22']['level'])
        self.assertEqual(running['gpio']['adc'], {'GPIO34': 1234})
        changed = snapshots['jumper_changed']['address']
        self.assertEqual(changed['pin_id'], 10)
        self.assertFalse(changed['matches_node'])
        failed = snapshots['dht_timeout']['dht22']
        self.assertEqual(failed['status'], 'TIMEOUT')
        self.assertEqual(failed['failures'], 2)
        self.assertIsNone(failed['temp_c'])
        self.assertIsNone(failed['humidity_pct'])
        self.assertTrue(snapshots['warmup']['mq2']['warming_up'])
        self.assertEqual(snapshots['warmup']['mq2']['used_adc'], 0)
        self.assertEqual(snapshots['warmup']['mq2']['raw_adc'], 1234)
        self.assertEqual(snapshots['output_off']['sprinkler']['level'], 0)
        self.assertEqual(snapshots['output_off']['sprinkler']['command'], 0)
        unplugged = snapshots['no_peripheral_response']
        self.assertIsNone(unplugged['dht22']['temp_c'])
        self.assertEqual(unplugged['dht22']['status'], 'TIMEOUT')
        self.assertEqual(unplugged['gpio']['levels']['GPIO27'], 0)
        self.assertEqual(unplugged['gpio']['levels']['GPIO14'], 0)
        self.assertEqual(unplugged['gpio']['levels']['GPIO13'], 0)
        high = snapshots['raw_inputs_high']
        self.assertEqual(high['gpio']['levels']['GPIO27'], 1)
        self.assertEqual(high['gpio']['levels']['GPIO13'], 1)
        self.assertEqual(high['dht22']['level'], 1)
        self.assertEqual(high['matrix']['idle_level'], 1)
        self.assertIsNone(high['dht22']['temp_c'])
        bad = snapshots['bad_address']
        self.assertEqual(bad['node']['state'], 'bad_address')
        self.assertEqual(bad['address']['pin_id'], 0)
        self.assertIsNone(bad['address']['matches_node'])
        self.assertEqual(bad['mq2']['raw_adc'], 321)
        self.assertIsNone(bad['mq2']['used_adc'])

    def test_pin_debug_readings_and_no_io_side_effects(self):
        self.check_pin_snapshots(self.read_pin_snapshots())

    def test_bench_pin_debug(self):
        self.check_pin_snapshots(self.read_pin_snapshots('-D', 'BENCH_LIVE_MASK=0x06'))

    def test_forced_id_pin_debug(self):
        self.check_pin_snapshots(self.read_pin_snapshots('-D', 'FORCE_NODE_ID=1'), forced=True)

    def test_pin_debug_conflicting_pins_are_not_read(self):
        snapshot = self.read_pin_snapshots('-D', 'DHT_PIN=26')['startup_conflict']
        self.assertEqual(snapshot['node']['state'], 'startup_error')
        self.assertTrue(all(x is None for x in snapshot['address']['levels'].values()))
        self.assertIsNone(snapshot['mq2']['raw_adc'])
        self.assertIsNone(snapshot['sprinkler']['level'])
        self.assertIsNone(snapshot['dht22']['level'])
        self.assertEqual(snapshot['dht22']['status'], 'NOT_INITIALIZED')
        self.assertTrue(all(x is None for x in snapshot['gpio']['levels'].values()))
        self.assertIsNone(snapshot['gpio']['adc']['GPIO34'])

    def test_node_packets_routes_and_control(self):
        self.run_firmware('node')

    def test_node_pin_conflict_stays_stopped(self):
        self.run_firmware('node', '-D DHT_PIN=26')

    def test_gateway_packets_and_serial(self):
        self.run_firmware('gateway')


if __name__ == '__main__':
    unittest.main()
