# Verticrane 기울기 기록 시스템

크레인의 수직도를 재는 무인 기록 장치입니다. **Raspberry Pi Zero 2 W**에
**HWT9037-485**(9축 IMU)를 붙여, 전원만 넣으면 스스로 기록을 시작하고
운영자는 브라우저로 파일을 받아 갑니다.

한 크레인에 **BASE / MIDDLE / TOP** 세 대를 설치해 높이별 기울기를 봅니다.

| | |
|---|---|
| 요구사항 | [TILT_기록시스템_구현요구사항.md](TILT_기록시스템_구현요구사항.md) |
| 센서 프로토콜 | [doc/protocol.md](doc/protocol.md) |
| 라즈베리파이 안내 | [doc/raspberry_pi.md](doc/raspberry_pi.md) |

---

# 운영자용

## 쓰는 법

1. **전원을 켭니다.**
2. **차량 근처라면** 1분 안에 브라우저로 접속하세요. 장비가 대기 상태로 있어
   파일을 받거나 설정을 바꿀 수 있습니다.
   ```
   http://pi-tilt001.local:8080
   ```
   접속 주소는 장비 이름과 같습니다. e-paper 화면 아래쪽에 IP 주소도 나옵니다.
3. **측정 지점에서 켜면** 접속하는 사람이 없으므로, 센서가 안정되는 대로
   **자동으로 기록을 시작합니다.** 따로 할 일이 없습니다.
4. 회수한 뒤 다시 켜서 접속하면 목록에서 파일을 받습니다.

## 화면 보는 법

e-paper 화면은 **전원이 꺼져도 그대로 남습니다.** 장비에 붙은 명패이기도 합니다.

```
┌────────────────────────────────┐
│ pi-tilt001              TOP    │  장비 이름 · 설치 위치
├────────────────────────────────┤
│    ↑Z                          │
│  ┌──────┐      0.062           │  기울기 (%)
│  │ ⊙Y →X│      % tilt          │
│  ├──────┤   REC 2:15:23        │  기록 경과 시간
│  ▨▨▨▨▨▨                       │
│   접촉면                        │  빗금 친 면을 구조물에 붙입니다
├────────────────────────────────┤
│ R -1.09   P -0.06              │  Roll · Pitch
│ REC  12475 smp  26.3C          │
├────────────────────────────────┤
│ 192.168.0.19        14:43:53   │  접속 주소 · 갱신 시각
└────────────────────────────────┘
```

> **화면은 값을 보여줄 뿐 판정하지 않습니다.** 기울기가 괜찮은지는 파일을
> 가져가는 서버가 정합니다.

- **화면은 1분에 한 번 갱신됩니다.** 실시간 값은 브라우저에서 보세요.
- 시각 옆에 `?`가 붙어 있으면 아직 시계가 맞지 않은 것입니다. 네트워크가 닿으면
  저절로 맞춰지고, 기록 파일 이름도 그때 바로잡힙니다.
- `NO NETWORK`는 WiFi가 끊긴 상태입니다. 기록에는 지장이 없습니다.

## 설치할 때

**설치 위치(BASE / MIDDLE / TOP)를 반드시 지정하세요.** 설정 화면에서 고릅니다.
지정하지 않으면 파일 이름이 `UNSET_`으로 시작해서, 나중에 어느 높이의 데이터인지
알 수 없게 됩니다.

- 빗금 친 **아랫면**을 구조물에 붙입니다.
- **Z축이 위**를 향하게 놓습니다.
- **한번 설치한 뒤에는 방향을 바꾸지 마세요.** 방향이 바뀌면 그 전후 데이터를
  같이 볼 수 없습니다.

## 파일 받기

목록에서 **받기**를 누르면 됩니다.

- 받은 파일은 목록에서 사라지고 휴지통에 7일간 남습니다.
- **전송이 중간에 끊기면 파일은 그대로 있습니다.** 다시 받으세요.
- 시간이 이어지는 파일은 **하나로 합쳐서** 받을 수 있습니다.
- 파일 이름이 곧 어느 장비의 언제 기록인지입니다:
  `TOP_20260827_143629.ahrsbin`

| 이름에 붙는 표시 | 뜻 |
|---|---|
| `UNSET_` | 설치 위치를 지정하지 않았습니다 |
| `.unsynced` | 기록할 때 시계가 맞지 않았습니다. 데이터는 온전합니다 |
| `.recovered` | 전원이 갑자기 끊겨 마지막 1초쯤이 잘렸습니다 |

---

# 설치 (라즈베리파이)

## 0. 시리얼 포트 열기 (최초 1회)

파이의 온보드 UART는 기본적으로 꺼져 있고 시리얼 콘솔이 포트를 점유합니다.
게다가 Zero 2 W는 성능이 좋은 PL011을 블루투스가 가져가고 GPIO에는 mini UART를
배정하는데, mini UART는 보레이트가 코어 클럭에 묶여 있어 115200 Modbus에서
프레임이 깨질 수 있습니다.

```bash
sudo cp /boot/firmware/config.txt  /boot/firmware/config.txt.bak
sudo cp /boot/firmware/cmdline.txt /boot/firmware/cmdline.txt.bak

sudo tee -a /boot/firmware/config.txt >/dev/null <<'EOF'

enable_uart=1
dtoverlay=disable-bt
EOF

sudo sed -i 's/console=serial0,115200 //' /boot/firmware/cmdline.txt
sudo reboot
```

재부팅 후 `ls -l /dev/serial0`에서 `-> ttyAMA0`이 보이면 정상입니다.

> 블루투스도 써야 한다면 `disable-bt` 대신 **`miniuart-bt`** 를 쓰세요. 포트 경로가
> 그대로라 코드 수정이 필요 없습니다. 대신 코어 클럭이 250MHz로 고정됩니다.

## 1. 장비 이름 정하기

**hostname이 그대로 SENSOR_ID가 되고 접속 주소가 됩니다.** 의미 있게 지으세요.

```bash
sudo hostnamectl set-hostname pi-tilt001
```

## 2. 설치

```bash
cd ~
git clone https://github.com/geolux-sdk/verticrane.git
cd verticrane
chmod +x *.sh dev/*.sh
./install_requirements.sh          # sudo 없이!
sudo usermod -aG dialout $USER     # 재로그인 필요
```

`fonts-nanum`이 없으면 e-paper의 한글이 영문으로 대체됩니다:
```bash
sudo apt install -y fonts-nanum
```

## 3. 센서 설정 (최초 1회)

```bash
.venv/bin/python dev/configure_sensor.py --baud 115200
```

## 4. 자가 점검

```bash
./test.sh --no-hardware    # 소프트웨어만
./test.sh                  # 센서 포함
```

## 5. 상시 가동

```bash
sudo cp verticrane-recorder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now verticrane-recorder

# 예전 대시보드 서비스가 등록돼 있다면 반드시 끄세요 (같은 시리얼 포트를 다툽니다)
sudo systemctl disable --now verticrane-dashboard 2>/dev/null || true
```

관리:
```bash
systemctl status verticrane-recorder
journalctl -u verticrane-recorder -f
```

## 6. 업데이트

```bash
./update.sh
sudo systemctl restart verticrane-recorder
```

---

# 개발자용

`dev/`에 있는 도구들은 **운영자에게 노출되지 않습니다.** 웹에서 접근할 수 있는
숨겨진 링크도 없습니다. SSH로 접속해서 직접 실행하는 것이 유일한 방법입니다.

## 중요: 시리얼 포트는 하나뿐입니다

기록기가 포트를 점유하고 있어 `dev/` 도구 대부분이 그냥은 동작하지 않습니다.
**`devmode.sh`를 쓰세요** — 서비스를 멈추고, 명령을 실행하고, **끝나면 반드시
다시 켭니다.** Ctrl-C를 눌러도, 명령이 실패해도 되살립니다.

```bash
./dev/devmode.sh                                    # 개발자 셸 (나가면 복구)
./dev/devmode.sh .venv/bin/python read_status.py    # 명령 하나만
./dev/devmode.sh ./dev/run_dashboard.sh             # 대시보드
```

> 손으로 `systemctl stop`을 하는 것은 쉽지만 다시 켜는 것을 잊기는 더 쉽습니다.
> 조용히 기록을 멈춘 현장 장비가 이 프로젝트에서 가장 나쁜 결과입니다.

## 기록 파일 다루기

```bash
# .ahrsbin -> CSV (기존 분석 도구가 읽는 형식)
.venv/bin/python dev/ahrsbin_to_csv.py data/TOP_20260827_143629.ahrsbin

# 변환하고 분석 리포트까지
.venv/bin/python dev/ahrsbin_to_csv.py data/*.ahrsbin --report
```

## 기준값 맞추기

기록된 CSV로 안정화 판정 기준을 검증할 수 있습니다. 하드웨어가 필요 없습니다.

```bash
python stability.py data/*.csv
python stability.py data/*.csv --gyro-rms 0.1     # 기준을 바꿔 시험
```

## e-paper 화면 미리보기

```bash
python eink_panel.py --out panel.png --scale 3    # 하드웨어 없이 PNG로
python eink_panel.py --position BASE --alarm      # 실제 패널에 그리기
```

## 구성

**운영 (루트)**

| 파일 | 역할 |
|---|---|
| `recorder.py` | 기록 루프와 상태 기계. systemd가 띄우는 것 |
| `ahrs_file.py` | `.ahrsbin` 포맷 — 읽기·쓰기·복구·병합 |
| `stability.py` | 안정화 판정 |
| `filestore.py` | 파일 목록·연속 그룹·휴지통 |
| `web/` | 운영자 웹 (Flask, 8080) |
| `eink_panel.py` | e-paper 화면 |
| `read_status.py` | 센서 연결·상태 읽기 **(recorder가 사용)** |
| `hwt9037_485.py` | 장치 모델 (Modbus) |
| `port_config.py` | 시리얼 포트 결정 |
| `app_config.py` | 설정·PIN·로그 (`config.json`) |
| `gdey0154d67.py` | e-paper 드라이버 |

**개발자 (`dev/`)**

| 파일 | 역할 |
|---|---|
| `devmode.sh` | 서비스를 멈추고 도구를 실행한 뒤 되살림 |
| `ahrsbin_to_csv.py` | `.ahrsbin` → CSV 변환 |
| `dashboard.py`, `pages/setup.py` | Streamlit 분석 대시보드 |
| `analyze_tilt.py` | CSV 분석 (통계·FFT) |
| `log_tilt.py` | CSV 직접 기록 (구형) |
| `configure_sensor.py` | 센서 설정 (6축 알고리즘, baud) |
| `eink_status.py`, `eink_test.py` | 패널 시험 도구 |
| `test.py` | 대시보드 자가 점검 |
| `verticrane-dashboard.service` | 옛 운영 서비스. 참고용으로만 남겨 둠 |
| `install_requirements.bat` | Windows 개발 환경 설치 |

## 자가 점검

```bash
python test_ahrs_file.py     # 포맷: 쓰기 → 자르기 → 복구 → 병합 (하드웨어 불필요)
python test_stability.py     # 판정: 0/360 경계, 윈도우, 움직임 거부
./test.sh                    # 위 둘 + 센서 점검
```

---

# 문제 해결

- **기록이 시작되지 않음** — 웹 상태 화면의 안정화 판정 표를 보세요. 어느 지표가
  기준을 넘었는지 나옵니다. 장비가 흔들리거나 제대로 안착되지 않은 경우가 대부분입니다.
- **파일 이름이 `UNSET_`으로 시작** — 설정 화면에서 설치 위치를 지정하세요.
- **파일 이름에 `.unsynced`** — 기록할 때 네트워크가 없어 시계를 못 맞춘 것입니다.
  데이터는 온전하고 상대 시간(기록 시작 후 몇 초)은 정확합니다.
- **`ModuleNotFoundError: pymodbus`** — 시스템 `python`으로 실행한 경우입니다.
  `.venv/bin/python`을 쓰세요.
- **`/dev/serial0`이 없음** — 위 **0. 시리얼 포트 열기**를 실행하고 재부팅하세요.
- **센서 연결 실패** — 기록기가 포트를 쓰고 있지 않은지(`devmode.sh` 사용),
  `dialout` 그룹에 들어 있는지, 8·10번 핀 결선과 RS-485 A/B 극성을 확인하세요.
- **e-paper 한글이 네모로 나옴** — `sudo apt install -y fonts-nanum`.
- **웹이 응답하는지 빠르게 보기** — `curl localhost:8080/healthz`. 이 요청은
  운영자 접속으로 세지 않으므로 자동 기록을 막지 않습니다.

> **무응답 감시(워치독)는 두지 않았습니다.** HTTP 응답 확인은 이 시스템에서
> 잘못된 지표입니다 — 웹과 기록이 별도 스레드라, 기록이 멈춰도 웹은 200을
> 돌려주고 반대로 웹만 막히면 멀쩡한 기록을 재시작해 버립니다. systemd의
> `Restart=always`가 프로세스 사망만 처리합니다.
