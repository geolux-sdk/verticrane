"""Run software checks by default; hardware access requires --hardware."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--all', action='store_true', help='Include developer and BLE web checks (Node required)')
    parser.add_argument('--hardware', action='store_true', help='Read the sensor; stop the recorder first')
    parser.add_argument('--port')
    parser.add_argument('--seconds', type=float, default=1.0)
    args = parser.parse_args()
    if args.all and not shutil.which('node'):
        parser.error('--all requires Node.js for BLE web checks')
    commands = [[sys.executable, str(path)] for path in sorted((ROOT / 'tests').glob('test_*.py'))]
    sensor = [sys.executable, str(ROOT / 'tests/hardware/sensor_check.py')]
    if args.all:
        commands += [sensor + ['--no-hardware'], ['node', str(ROOT / 'tests/ble_web/run.js')]]
    if args.hardware:
        # On deployed Linux, prevent simultaneous access to the recorder's port.
        if shutil.which('systemctl') and subprocess.run(
                ['systemctl', 'is-active', '--quiet', 'verticrane-recorder']).returncode == 0:
            parser.error('Recorder is active. Run through ./dev/devmode.sh ./test.sh --hardware')
        commands.append(sensor + ['--seconds', str(args.seconds)] + (['--port', args.port] if args.port else []))
    failed = []
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    for command in commands:
        print('\nRUN:', ' '.join(command), flush=True)
        if subprocess.run(command, cwd=ROOT, env=env).returncode:
            failed.append(' '.join(command))
    print('\nSuites:', len(commands), 'Failed:', len(failed), flush=True)
    for command in failed:
        print('FAILED:', command)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
