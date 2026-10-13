"""Startup must report a missing sensor before any recording begins."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_config
import eink_panel
import recorder


class StartupSensorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="startup_sensor_")
        self.addCleanup(self.tmp.cleanup)
        self.rec = recorder.Recorder("/dev/serial0", app_config.RECORDER_DEFAULTS.copy(), self.tmp.name)
        self.rec.sensor = Mock()
        self.rec.sensor.port = "/dev/serial0"
        self.rec.sensor.config_warnings = []
        self.rec.sensor.serial = ""

    def test_missing_sensor_immediately_enters_error(self):
        self.rec.sensor.connect.return_value = False
        panel = Mock()
        self.rec.panel = panel
        with patch.object(self.rec, "wait_for_http") as wait, \
             patch.object(self.rec, "_start_recording") as start:
            self.assertEqual(self.rec.run(run_seconds=0.001), 0)
        wait.assert_not_called()
        start.assert_not_called()
        self.assertEqual(self.rec.state, recorder.ERROR)
        self.assertEqual(self.rec.status.error_reason, "센서 통신 실패")
        self.assertIn("/dev/serial0", self.rec.status.error)
        self.assertEqual(eink_panel.pick_screen({"state": self.rec.state, "booting": True}),
                         eink_panel.SCREEN_ERROR)
        panel.refresh_now.assert_called()
        self.assertFalse(list(Path(self.tmp.name).glob("*.dat*")))

    def test_error_does_not_retry_or_start_recording(self):
        self.rec.sensor.connect.return_value = False
        self.rec.run(run_seconds=0.001)
        calls = self.rec.sensor.connect.call_count
        self.rec._next_time_save = self.rec._next_purge = float("inf")
        with patch.object(self.rec, "_poll_once") as poll, \
             patch.object(self.rec, "_start_recording") as start, \
             patch.object(recorder.time, "sleep"):
            self.rec._tick()
        self.assertEqual(self.rec.sensor.connect.call_count, calls)
        poll.assert_not_called()
        start.assert_not_called()

    def test_connected_sensor_can_enter_normal_idle(self):
        self.rec.sensor.connect.return_value = True
        self.assertEqual(self.rec.run(force=recorder.MAINTENANCE, run_seconds=0.001), 0)
        self.assertEqual(self.rec.state, recorder.MAINTENANCE)
        self.assertIsNone(self.rec.status.error_reason)


if __name__ == "__main__":
    unittest.main()
