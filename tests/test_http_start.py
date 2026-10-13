"""Operator connection and recording start ordering without hardware."""
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_config
import recorder


class HttpStartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="http_start_")
        self.addCleanup(self.tmp.cleanup)
        self.rec = recorder.Recorder("/dev/null", app_config.RECORDER_DEFAULTS.copy(), self.tmp.name)
        self.rec._set_state(recorder.WAITING_STABLE)
        self.rec._next_time_save = self.rec._next_purge = float("inf")

    def test_connection_while_settling(self):
        self.rec.note_http_request()
        with patch.object(self.rec, "_poll_once") as poll, patch.object(recorder.time, "sleep"):
            self.rec._tick()
        self.assertEqual(self.rec.state, recorder.MAINTENANCE)
        self.assertFalse(poll.called)
        self.assertIsNone(self.rec.writer)
        self.assertFalse(list(Path(self.tmp.name).glob("*.dat*")))

    def test_connection_before_start(self):
        self.rec.note_http_request()
        with patch.object(self.rec, "_open_recording") as open_file:
            self.rec._start_recording()
        open_file.assert_not_called()
        self.assertEqual(self.rec.state, recorder.MAINTENANCE)

    def test_connection_during_file_creation(self):
        opening = threading.Event()
        release = threading.Event()
        requested = threading.Event()
        errors = []

        def open_file():
            opening.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release file creation")
            self.rec._set_state(recorder.RECORDING)

        def start():
            try:
                self.rec._start_recording()
            except Exception as exc:
                errors.append(exc)

        def connect():
            requested.set()
            self.rec.note_http_request()

        with patch.object(self.rec, "_open_recording", side_effect=open_file):
            worker = threading.Thread(target=start)
            visitor = threading.Thread(target=connect)
            worker.start()
            try:
                self.assertTrue(opening.wait(5))
                visitor.start()
                self.assertTrue(requested.wait(5))
            finally:
                release.set()
                worker.join(5)
                if visitor.ident is not None:
                    visitor.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(visitor.is_alive())
        self.assertFalse(errors)
        self.assertTrue(self.rec._http_seen.is_set())
        self.assertTrue(self.rec._want_discard.is_set())
        with patch.object(self.rec, "_stop_recording") as stop, patch.object(recorder.time, "sleep"):
            self.rec._tick()
        stop.assert_called_once_with(discard=True)
        self.assertEqual(self.rec.state, recorder.MAINTENANCE)

    def test_error_is_preserved(self):
        self.rec._set_state(recorder.ERROR)
        self.rec.note_http_request()
        with patch.object(self.rec, "_open_recording") as open_file:
            self.rec._start_recording()
        open_file.assert_not_called()
        self.assertEqual(self.rec.state, recorder.ERROR)


if __name__ == "__main__":
    unittest.main()
