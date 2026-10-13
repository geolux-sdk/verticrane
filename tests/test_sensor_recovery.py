"""Fresh stability measurements after a sensor failure."""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_config
import recorder
import stability
import eink_panel


class SensorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sensor_recovery_")
        self.addCleanup(self.tmp.cleanup)
        self.rec = recorder.Recorder("/dev/null", app_config.RECORDER_DEFAULTS.copy(), self.tmp.name)
        self.rec._set_state(recorder.WAITING_STABLE)
        self.rec._next_time_save = self.rec._next_purge = self.rec._next_ip_check = float("inf")
        now = time.monotonic()
        for i in range(126):
            self.rec.monitor.add(stability.Sample(t=now - 5 + i * .04, roll=0, pitch=0, yaw=0,
                                                  acc=(0, 0, 1), gyro=(0, 0, 0)))
        self.rec._since_judged = 24
        self.rec.sensor = Mock()
        self.rec.sensor.config_warnings = []
        self.rec.sensor.serial = "test"
        self.rec.sensor.reconnected = False

    def test_disconnect_prevents_start(self):
        self.rec.sensor.connect.return_value = False
        with patch.object(recorder.time, "sleep"), patch.object(self.rec, "_start_recording") as start:
            self.rec._tick()
        start.assert_not_called()
        self.assertEqual(len(self.rec.monitor), 0)
        self.assertFalse(self.rec.status.sensor_ok)
        self.assertEqual(self.rec.status.sensor_issue, "센서 연결 끊김")
        self.assertFalse(self.rec.snapshot().sensor_ok)

    def test_invalid_reads_and_recovery(self):
        self.rec.sensor.connect.return_value = True
        for data in (None, {"AngX": 0}):
            self.rec.sensor.read_angles.return_value = data
            self.assertFalse(self.rec._poll_once(False))
            self.assertEqual(self.rec.status.sensor_issue, "측정값 읽기 실패")
        self.rec.sensor.read_angles.return_value = {"AngX": 0, "AngY": 0, "AngZ": 0}
        self.rec.sensor.read_temp.return_value = 20
        with patch.object(recorder.time, "sleep"):
            self.assertTrue(self.rec._poll_once(False))
        self.assertTrue(self.rec.status.sensor_ok)
        self.assertIsNone(self.rec.status.sensor_issue)
        self.assertEqual(len(self.rec.monitor), 1)
        self.assertFalse(self.rec.monitor.evaluate().stable)

    def test_disconnect_keeps_recording_file(self):
        self.rec._set_state(recorder.RECORDING)
        writer = Mock()
        self.rec.writer = writer
        self.rec.sensor.connect.return_value = False
        with patch.object(recorder.time, "sleep"):
            self.rec._tick()
        self.assertIs(self.rec.writer, writer)
        writer.close.assert_not_called()
        self.assertEqual(self.rec.state, recorder.RECORDING)

    def test_panel_priority(self):
        status = {"state": "recording", "sensor_issue": "센서 연결 끊김"}
        self.assertEqual(eink_panel.pick_screen(status), eink_panel.SCREEN_SENSOR)
        self.assertEqual(eink_panel.render(status).size, (200, 200))
        status["state"] = "error"
        self.assertEqual(eink_panel.pick_screen(status), eink_panel.SCREEN_ERROR)


if __name__ == "__main__":
    unittest.main()
