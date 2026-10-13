"""PC collector slot reuse and interrupted transfers using synthetic files."""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ahrs_file as af
from pc import collect as c


class Response(io.BytesIO):
    def __init__(self, body, status=200, content_range=""):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Range": content_range}


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="collect_test_")
        self.addCleanup(self.tmp.cleanup)
        self.room = Path(self.tmp.name)
        self.old = af.Header(start_epoch=1, sensor_id="device").pack() + b"OLD_DATA"
        self.new = af.Header(start_epoch=2, sensor_id="device").pack() + b"NEW_DATA"
        self.dest = self.room / "BASE_000.dat"

    def test_reused_slot_preserves_old_file(self):
        self.dest.write_bytes(self.old)
        path, exists = c._destination(str(self.room), self.dest.name, self.new[:64], len(self.new))
        self.assertFalse(exists)
        self.assertNotEqual(path, str(self.dest))
        Path(path).write_bytes(self.new)
        self.assertEqual(self.dest.read_bytes(), self.old)
        self.assertEqual(c._destination(str(self.room), self.dest.name, self.new[:64], len(self.new)),
                         (path, True))

    def test_stale_part_is_restarted_even_when_full_size(self):
        Path(str(self.dest) + ".part").write_bytes(self.old)
        with patch.object(c, "_remote_header", return_value=self.new[:64]), \
             patch.object(c.urllib.request, "urlopen", return_value=Response(self.new)) as request:
            c.download("http://test", self.dest.name, str(self.dest), len(self.new))
        self.assertIsNone(request.call_args.args[0].get_header("Range"))
        self.assertEqual(self.dest.read_bytes(), self.new)

    def test_matching_part_resumes(self):
        Path(str(self.dest) + ".part").write_bytes(self.new[:68])
        response = Response(self.new[68:], 206, "bytes 68-71/72")
        with patch.object(c, "_remote_header", return_value=self.new[:64]), \
             patch.object(c.urllib.request, "urlopen", return_value=response) as request:
            c.download("http://test", self.dest.name, str(self.dest), len(self.new))
        self.assertEqual(request.call_args.args[0].get_header("Range"), "bytes=68-")
        self.assertEqual(self.dest.read_bytes(), self.new)

    def test_changed_remote_is_not_finalised(self):
        with patch.object(c, "_remote_header", side_effect=[self.new[:64], self.old[:64]]), \
             patch.object(c.urllib.request, "urlopen", return_value=Response(self.new)):
            with self.assertRaises(OSError):
                c.download("http://test", self.dest.name, str(self.dest), len(self.new))
        self.assertFalse(self.dest.exists())

    def test_ignored_range_restarts(self):
        Path(str(self.dest) + ".part").write_bytes(self.new[:68])
        with patch.object(c, "_remote_header", return_value=self.new[:64]), \
             patch.object(c.urllib.request, "urlopen", return_value=Response(self.new)):
            c.download("http://test", self.dest.name, str(self.dest), len(self.new))
        self.assertEqual(self.dest.read_bytes(), self.new)

    def test_invalid_header_is_rejected(self):
        with patch.object(c.urllib.request, "urlopen", return_value=Response(b"X" * 64)):
            with self.assertRaises(OSError):
                c._remote_header("http://test", self.dest.name)

    def test_collect_downloads_new_same_size_recording(self):
        self.room.joinpath("device").mkdir()
        original = self.room / "device" / self.dest.name
        original.write_bytes(self.old)
        with patch.object(c, "_get", side_effect=[{"sensor_id": "device"},
                {"files": [{"name": self.dest.name, "size": len(self.new)}]}]), \
             patch.object(c, "_remote_header", return_value=self.new[:64]), \
             patch.object(c.urllib.request, "urlopen", return_value=Response(self.new)):
            self.assertEqual(c.collect("test", str(self.room), True), (1, len(self.new), 0))
        self.assertEqual(original.read_bytes(), self.old)
        self.assertEqual(len(list(original.parent.glob("*.dat"))), 2)


if __name__ == "__main__":
    unittest.main()
