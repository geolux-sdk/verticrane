# Verticrane 기울기 모니터링

WitMotion **HWT9037-485** 9축 IMU(기울기 센서)를 Modbus RTU / RS-485로 읽어
기울기를 측정·기록·분석하고, 웹 대시보드로 보여주는 도구 모음입니다.
**Windows**(개발)와 **라즈베리파이**(현장 배포)에서 동일하게 동작합니다.

## 하드웨어

- 센서: **HWT9037-485** (9축 IMU, Modbus RTU over RS-485)
- 연결: **USB-RS485 어댑터**(예: CH340) → Windows `COMx`, 라즈베리파이 `/dev/ttyUSB0`
- 통신 속도: **115200 bps**(운용 기준). 공장 초기값은 9600이며 `configure_sensor.py`로 변경·저장
- 프로토콜 상세: [doc/protocol.md](doc/protocol.md)

## 구성

| 파일 | 역할 |
|------|------|
| `hwt9037_485.py` | 장치 모델 (레지스터 읽기/쓰기, save/reboot) |
| `port_config.py` | 시리얼 포트 결정 (`--port` / `VERTICRANE_PORT` / 자동감지 / 기본값) |
| `app_config.py` | 설정·관리자 PIN 저장 (`config.json`) |
| `read_status.py` | 센서 상태 읽기·디코드 (CLI) + 대시보드용 함수 |
| `configure_sensor.py` | 6축 알고리즘 설정 + 선택적 baud 변경 |
| `log_tilt.py` | 기울기를 CSV로 기록 + 분석 리포트 생성 |
| `analyze_tilt.py` | CSV 분석 (통계·FFT·평가 리포트) |
| `dashboard.py` | Streamlit 웹 대시보드 |
| `pages/setup.py` | 숨겨진 `/setup` 관리자 페이지 |
| `test.py` | 대시보드 기능 자가 점검 |

> CLI 도구 중 **수동 실행**은 `configure_sensor.py`(설정)와 `test.py`(점검)이고,
> 나머지는 대시보드가 사용/실행합니다.

---

# 소스 받기 (git)

저장소: `https://github.com/geolux-sdk/verticrane.git`

### 라즈베리파이 / Linux
```bash
sudo apt update && sudo apt install -y git    # git 없으면
cd ~
git clone https://github.com/geolux-sdk/verticrane.git
cd verticrane
```
최신 코드 받기(이미 클론한 경우): `./update.sh` (git pull + 필요 시 의존성 재설치)

### Windows
[Git for Windows](https://git-scm.com/download/win) 설치 후, 터미널(또는 Git Bash)에서:
```bat
git clone https://github.com/geolux-sdk/verticrane.git
cd verticrane
```
최신 코드 받기: `git pull`

> 비공개 저장소라면 클론 시 GitHub **사용자명 + Personal Access Token**(비밀번호 자리에 토큰)을
> 입력해야 합니다. 토큰은 GitHub → Settings → Developer settings → Personal access tokens에서 발급.

---

# Windows (개발용)

시스템 파이썬을 그대로 사용합니다.

### 1. 설치
```bat
install_requirements.bat
```

### 2. 센서 설정 (최초 1회)
6축 알고리즘 + 통신 속도 115200으로 설정·저장 (공장 9600 센서도 자동으로 찾음):
```bat
python configure_sensor.py --baud 115200
```

### 3. 자가 점검
```bat
python test.py                 :: 전체 (센서 필요), 라이브 1초
python test.py --seconds 10    :: 라이브 측정 10초
python test.py --no-hardware   :: 소프트웨어만 (센서 없이)
```

### 4. 대시보드 실행
```bat
run_dashboard.bat
```
브라우저: `http://localhost:8501`

### 기울기 로그 (선택)
```bat
python log_tilt.py 10          :: 10분간 기록 → data\*.csv + *.txt
```

> Windows 기본 포트는 `COM11`입니다. 다르면 `--port COM3`처럼 지정하세요.

---

# 라즈베리파이 (현장 배포)

PEP 668 때문에 시스템 파이썬에 직접 설치할 수 없으므로 **가상환경 `.venv`** 를 사용합니다.
설치/업데이트 스크립트가 `.venv`를 자동으로 만들고 사용합니다. **`sudo` 없이** 실행하세요.

### 1. 설치 (최초 1회)
```bash
cd ~/verticrane
chmod +x *.sh                    # 실행 권한
./install_requirements.sh        # .venv 생성 + 의존성 설치 (sudo 없이!)

sudo usermod -aG dialout $USER   # 시리얼 권한 → 재로그인/재부팅 필요
```

### 2. 센서 설정 (최초 1회)
```bash
.venv/bin/python configure_sensor.py --baud 115200
```

### 3. 자가 점검
`test.sh`는 자동으로 `.venv` 파이썬을 사용합니다.
```bash
./test.sh                    # 전체 (센서 필요), 라이브 1초
./test.sh --seconds 10       # 라이브 측정 10초
./test.sh --no-hardware      # 소프트웨어만 (센서 없이)
```
`N/N 통과`가 나오면 정상입니다.

### 4. 대시보드 실행
```bash
./run_dashboard.sh           # .venv를 자동으로 사용
```
브라우저: `http://<파이IP>:8501` (같은 네트워크의 PC/폰에서 접속 가능)

### 5. 상시 가동 (systemd)
전원만 켜면 자동 시작되고, 죽으면 자동 재시작됩니다.
```bash
sudo cp ~/verticrane/verticrane-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now verticrane-dashboard
```
관리:
```bash
systemctl status verticrane-dashboard         # 상태
journalctl -u verticrane-dashboard -f         # 로그
sudo systemctl restart verticrane-dashboard   # 재시작 (코드 업데이트 후)
sudo systemctl stop verticrane-dashboard      # 중지
```
> 서비스로 띄운 뒤엔 `./run_dashboard.sh`를 따로 실행하지 마세요(포트 8501 충돌).

### 6. 업데이트
```bash
./update.sh                                   # git pull + 필요 시 의존성 재설치
sudo systemctl restart verticrane-dashboard   # systemd 가동 중이면 재시작
```

### CLI 도구 실행 규칙 (중요)
라즈베리파이에서 CLI 도구는 **반드시 venv 파이썬**으로 실행합니다.
시스템 `python`으로 실행하면 `ModuleNotFoundError: pymodbus`가 납니다.
```bash
.venv/bin/python <스크립트>          # 예: .venv/bin/python read_status.py
# 또는 활성화 후
source .venv/bin/activate
python <스크립트>
```
대시보드(`run_dashboard.sh`)와 systemd 서비스는 **자동으로 venv**를 쓰므로 활성화가 필요 없습니다.

자세한 라즈베리파이 안내: [doc/raspberry_pi.md](doc/raspberry_pi.md)

---

# 공통

## 관리자 설정 (`/setup`)

기울기 경보 **임계값**과 **이동평균 윈도우**(판단 기준)는 숨겨진 관리자 페이지에서
바꿉니다. 사이드바에 링크가 없고 URL로만 접근합니다.

1. `http://<기기IP>:8501/setup` 접속
2. **PINCODE** 입력 (초기값 `01023538099`)
3. 임계값·이동평균 수정 후 저장, **PINCODE 변경**

- 설정은 `config.json`에 저장되며 대시보드와 분석 리포트에 즉시 반영됩니다.
- `config.json`은 git에 포함되지 않습니다(기기별). 새 기기는 기본값으로 시작하니
  **첫 사용 시 PIN을 반드시 변경**하세요.

> 보안 수준: URL 은닉 + PIN의 가벼운 보호입니다. 외부 노출이 필요하면
> VPN 또는 리버스 프록시(HTTPS+인증)를 거치세요.

## 포트 지정

모든 도구는 다음 순서로 시리얼 포트를 결정합니다:

1. `--port` 인자 (예: `--port /dev/ttyUSB0`)
2. `VERTICRANE_PORT` 환경변수
3. USB 시리얼 어댑터 자동 감지
4. 플랫폼 기본값 (Linux `/dev/ttyUSB0`, Windows `COM11`)

어댑터가 여러 개면 `/dev/serial/by-id/...` 경로를 `--port`에 지정하는 것이 안정적입니다.

## 문제 해결

- **`ModuleNotFoundError: No module named 'pymodbus'`** (라즈베리파이) — 시스템 `python`으로
  실행한 경우입니다. `.venv/bin/python <스크립트>` 또는 `source .venv/bin/activate` 후 실행하세요.
- **`No response at 9600 bps` 메시지** — 정상입니다. 115200을 먼저 시도하므로 보통 바로 연결됩니다.
- **센서 연결 실패** — (Pi) `dialout` 그룹 추가 후 재로그인했는지, USB 어댑터가 꽂혀 있는지,
  다른 프로그램이 포트를 점유하고 있지 않은지 확인하세요.
- **종합 점검** — Windows `python test.py`, 라즈베리파이 `./test.sh`로 어느 단계에서 막히는지 확인하세요.
