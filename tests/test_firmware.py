"""Run the actual firmware sources with host substitutes for ESP32 I/O."""
from pathlib import Path
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
            subprocess.run([str(executable)], check=True, timeout=30)

    def test_node_packets_routes_and_control(self):
        self.run_firmware('node')

    def test_node_pin_conflict_stays_stopped(self):
        self.run_firmware('node', '-D DHT_PIN=26')

    def test_gateway_packets_and_serial(self):
        self.run_firmware('gateway')


if __name__ == '__main__':
    unittest.main()
