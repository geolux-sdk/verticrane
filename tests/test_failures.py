"""Regression checks for unavailable displays and sensor configuration reads."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest
from unittest.mock import Mock, patch

import eink_panel as ep
import recorder
from test_eink_panel import _Recorder


class FailureTests(unittest.TestCase):
    def test_panel_failure_retries_same_frame_after_backoff(self):
        panel = ep.PanelThread(_Recorder(), {})
        panel._started = 0
        with patch.object(ep, 'local_ip', return_value=None), \
                patch.object(ep.time, 'monotonic', return_value=100) as clock, \
                patch.object(ep, 'show', side_effect=[False, True]) as show:
            status = panel._draw()
            self.assertIsNone(panel._last_frame)
            self.assertEqual(panel._next_wait(status), 30)
            clock.return_value = 110
            panel._draw()
            self.assertEqual(show.call_count, 1)
            clock.return_value = 130
            panel._draw()
            panel._draw()
            self.assertEqual(show.call_count, 2)
            self.assertIsNotNone(panel._last_frame)
            self.assertIsNone(panel._next_wait(status))

    def test_failed_changed_frame_preserves_last_success(self):
        panel = ep.PanelThread(_Recorder(), {})
        panel._started = 0
        with patch.object(ep.time, 'monotonic', return_value=100) as clock, \
                patch.object(ep, 'local_ip', return_value=None) as ip, \
                patch.object(ep, 'show', side_effect=[True, False, True]) as show:
            panel._draw()
            previous = panel._last_frame
            ip.return_value = '192.168.0.19'
            panel._draw()
            self.assertEqual(panel._last_frame, previous)
            clock.return_value = 130
            panel._draw()
            self.assertNotEqual(panel._last_frame, previous)
            self.assertEqual(show.call_count, 3)

    def test_sensor_config_unknown_is_not_match(self):
        link = recorder.SensorLink('unused')
        link.device = Mock()
        # Even stale cached values must not make a failed read look successful.
        link.device.registerData = {r: v[1] for r, v in recorder.EXPECTED_CONFIG.items()}
        for result in (None, []):
            link.device.readReg.return_value = result
            with patch.object(recorder.logger, 'info') as info:
                warnings = link._verify_config()
                self.assertEqual(len(warnings), len(recorder.EXPECTED_CONFIG))
                self.assertTrue(all('확인 불가' in w for w in warnings))
                info.assert_not_called()
        link.device.readReg.side_effect = OSError('offline')
        self.assertEqual(len(link._verify_config()), len(recorder.EXPECTED_CONFIG))

    def test_sensor_config_match_and_mismatch(self):
        link = recorder.SensorLink('unused')
        link.device = Mock()
        link.device.readReg.side_effect = lambda reg, count: [recorder.EXPECTED_CONFIG[reg][1]]
        self.assertEqual(link._verify_config(), [])
        first = next(iter(recorder.EXPECTED_CONFIG))
        link.device.readReg.side_effect = lambda reg, count: [recorder.EXPECTED_CONFIG[reg][1] + (reg == first)]
        self.assertEqual(len(link._verify_config()), 1)
        link.device = None
        self.assertIn('확인 불가', link._verify_config()[0])


if __name__ == '__main__':
    unittest.main()
