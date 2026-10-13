# 테스트

모든 명령은 저장소 루트에서 실행합니다. 자동 검사는 임시 합성 데이터를 사용하며
운영 `data/`를 변경하거나 센서·e-paper에 접근하지 않습니다.

| 위치 | 용도 |
|---|---|
| `test_*.py` | 운영 코드·오류 처리·HTTP API·개발 모드 회귀 검사 |
| `ble_web/` | Node.js에서 가짜 BLE 장치로 웹 UI 검사 |
| `hardware/` | 실제 센서·패널·GPIO에 접근하는 수동 시험 |
| `tools/fake_card.py` | 합성 기록 파일을 생성하고 웹 UI를 확인하는 도구 |

```bash
python tests/run.py               # 운영 소프트웨어 검사
python tests/run.py --all         # 개발 도구와 BLE 웹 검사까지
node tests/ble_web/run.js         # BLE 웹 검사만
python tests/tools/fake_card.py --serve
```

기본 검사는 `requirements.txt`가 필요합니다. `--all`은 `requirements-dev.txt`와
Node.js가 필요합니다. 셸 회귀 검사는 Bash가 없으면 명시적으로 건너뜁니다.
Windows에서는 Git Bash를 자동으로 찾습니다.

Pi의 하드웨어 검사는 기록기와 포트/SPI를 공유하므로 `devmode.sh`를 사용합니다.
서비스 정지에 실패하면 도구를 실행하지 않으며, 도구 종료 후 기존 서비스를 복구합니다.
센서 검사에는 개발 의존성도 필요합니다.

```bash
./install_requirements.sh --dev
./dev/devmode.sh ./test.sh --hardware --seconds 10
./dev/devmode.sh .venv/bin/python tests/hardware/eink_test.py --pattern
./dev/devmode.sh .venv/bin/python tests/hardware/eink_status.py --seconds 30
```

`test.sh`는 기본적으로 소프트웨어 검사만 수행합니다. 센서 검사는 `--hardware`로
명시하며, 실행 중인 기록 서비스가 발견되면 거부합니다.
개별 하드웨어 스크립트는 `devmode.sh`를 통해 실행하세요.
