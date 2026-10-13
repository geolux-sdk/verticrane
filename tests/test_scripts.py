"""Exercise devmode's service guards with fake commands, never real systemd."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which('bash')
if not BASH and Path('C:/Program Files/Git/bin/bash.exe').exists():
    BASH = 'C:/Program Files/Git/bin/bash.exe'


def shell_path(path):
    text = Path(path).as_posix()
    return '/' + text[0].lower() + text[2:] if len(text) > 1 and text[1] == ':' else text


@unittest.skipUnless(BASH, 'Bash is required for shell regression tests')
class ScriptTests(unittest.TestCase):
    def test_shell_syntax(self):
        for name in ('dev/devmode.sh', 'dev/run_dashboard.sh', 'test.sh', 'install.sh',
                     'install_requirements.sh', 'update.sh'):
            with self.subTest(name=name):
                result = subprocess.run([BASH, '-n', str(ROOT / name)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def run_case(self, mode, tool_code):
        with tempfile.TemporaryDirectory(prefix='verticrane_shell_') as tmp:
            folder = Path(tmp)
            scripts = {
                'systemctl': '''#!/usr/bin/env bash
echo "systemctl $*" >> "$TEST_LOG"
case "$1" in
  is-active) test ! -f "$TEST_STOPPED" ;;
  stop)
    if [ "$TEST_MODE" = fail ]; then exit 1; fi
    if [ "$TEST_MODE" != still ]; then touch "$TEST_STOPPED"; fi ;;
  start) exit 0 ;;
esac
''',
                'sudo': '#!/usr/bin/env bash\nexec "$@"\n',
                'sleep': '#!/usr/bin/env bash\nexit 0\n',
            }
            for name, source in scripts.items():
                path = folder / name
                path.write_text(source, encoding='utf-8', newline='\n')
                path.chmod(0o755)
            log = folder / 'calls.log'
            env = dict(os.environ, TEST_BIN=shell_path(folder), TEST_LOG=shell_path(log),
                       TEST_STOPPED=shell_path(folder / 'stopped'), TEST_MODE=mode,
                       TEST_SCRIPT=shell_path(ROOT / 'dev/devmode.sh'), TEST_TOOL_CODE=str(tool_code))
            result = subprocess.run([BASH, '-c',
                '''export PATH="$TEST_BIN:$PATH"; exec bash "$TEST_SCRIPT" bash -c 'echo tool >> "$TEST_LOG"; exit "$TEST_TOOL_CODE"' '''
                ], env=env, capture_output=True, text=True)
            return result.returncode, log.read_text(encoding='utf-8'), result.stderr

    def test_stop_failure_blocks_tool(self):
        code, log, _ = self.run_case('fail', 0)
        self.assertNotEqual(code, 0)
        self.assertNotIn('\ntool\n', '\n' + log)

    def test_still_active_blocks_tool(self):
        code, log, _ = self.run_case('still', 0)
        self.assertNotEqual(code, 0)
        self.assertNotIn('\ntool\n', '\n' + log)

    def test_tool_failure_restores_once_and_preserves_exit(self):
        code, log, err = self.run_case('ok', 7)
        self.assertEqual(code, 7, err)
        self.assertIn('\ntool\n', '\n' + log)
        self.assertEqual(log.count('systemctl start verticrane-recorder'), 1)


if __name__ == '__main__':
    unittest.main()
