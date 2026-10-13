"""Storage error classification and panel selection without hardware."""
import errno
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eink_panel
import recorder


class StorageErrorTests(unittest.TestCase):
    def test_causes(self):
        cases = {
            errno.ENOSPC: "저장 공간 부족",
            errno.EROFS: "저장 장치 쓰기 금지",
            errno.EIO: "저장 장치 입출력 오류",
            errno.EACCES: "파일 쓰기 권한 없음",
            errno.EPERM: "파일 쓰기 권한 없음",
            None: "파일 저장 실패",
        }
        for code, expected in cases.items():
            with self.subTest(code=code):
                self.assertEqual(recorder.storage_error_reason(OSError(code, "failure")), expected)

    def test_error_overrides_boot_and_renders(self):
        status = {"state": recorder.ERROR, "booting": True,
                  "error_reason": "저장 장치 입출력 오류"}
        self.assertEqual(eink_panel.pick_screen(status), eink_panel.SCREEN_ERROR)
        self.assertEqual(eink_panel.render(status, rotate=0).size, (200, 200))


if __name__ == "__main__":
    unittest.main()
