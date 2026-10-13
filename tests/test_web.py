"""HTTP regression checks using synthetic files; no sensor or live server."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tempfile
import unittest
from unittest.mock import Mock, patch

import ahrs_file as af
import app_config
from recorder import Status
from test_ahrs_file import make_blocks
from web import create_app


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='verticrane_web_test_')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.rec = Mock()
        self.rec.snapshot.return_value = Status(state='recording', sensor_ok=True)
        self.cfg = app_config.RECORDER_DEFAULTS.copy()
        self.app = create_app(self.rec, str(self.root), self.cfg)
        self.app.testing = True
        self.client = self.app.test_client()
        self.name = 'BASE_001.dat'
        with af.Writer(str(self.root / self.name), af.Header(position=af.POS_BASE)) as writer:
            for block in make_blocks(2):
                writer.write_block(block)
        self.original = (self.root / self.name).read_bytes()

    def test_polling_does_not_signal_operator(self):
        for url in ('/healthz', '/api/status?auto=1'):
            self.assertEqual(self.client.get(url).status_code, 200)
        self.rec.note_http_request.assert_not_called()
        self.assertEqual(self.client.get('/api/status').status_code, 200)
        self.rec.note_http_request.assert_called_once()

    def test_download_range_and_retirement(self):
        url = '/api/files/' + self.name
        with self.client.get(url) as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, self.original)
        self.assertTrue((self.root / self.name).exists())
        with self.client.get(url, headers={'Range': 'bytes=10-29'}) as response:
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.data, self.original[10:30])
            self.assertEqual(response.headers['Content-Length'], '20')
        self.assertEqual(self.client.post(url + '/collected').status_code, 200)
        self.assertFalse((self.root / self.name).exists())
        self.assertEqual((self.root / 'trash' / self.name).read_bytes(), self.original)
        with self.client.get(url) as response:
            self.assertEqual(response.data, self.original)

    def test_disabled_retirement_and_pin_rejection(self):
        self.cfg['delete_after_download'] = False
        self.assertEqual(self.client.post('/api/files/' + self.name + '/collected').json['retired'], [])
        with patch('web.routes._pin_ok', return_value=False):
            self.assertEqual(self.client.delete('/api/files/' + self.name).status_code, 403)
        self.assertTrue((self.root / self.name).exists())

    def test_paths_cannot_escape_data_directory(self):
        for url in ('/api/raw/../outside.txt', '/api/files/../outside.dat', '/api/browse?path=../'):
            self.assertEqual(self.client.get(url).status_code, 404)


if __name__ == '__main__':
    unittest.main()
