# Verticrane 기울기 기록 시스템

크레인의 수직도를 재는 무인 기록 장치입니다. **Raspberry Pi Zero 2 W**에
**HWT9037-485**(9축 IMU)를 RS-485로 붙여, 전원만 넣으면 스스로 25 Hz로 기록을
시작하고 운영자는 브라우저로 파일을 받아 갑니다. 한 크레인에
**BASE / MIDDLE / TOP** 세 대를 답니다.

**이 문서는 개발자용입니다.** 현장에서 쓰는 법은
[doc/운영자_안내서.md](doc/운영자_안내서.md)를 보세요.

| 문서 | 내용 |
|---|---|
| [TILT_기록시스템_구현요구사항.md](TILT_기록시스템_구현요구사항.md) | **요구사항.** 하드웨어·개발 환경·현장 조건·필수 동작 |
| [doc/운영자_안내서.md](doc/운영자_안내서.md) | 현장 사용법 |
| [doc/protocol.md](doc/protocol.md) | 센서 레지스터 맵과 환산 |
| [doc/raspberry_pi.md](doc/raspberry_pi.md) | 파이 설치 상세 |
| [bt/README.md](bt/README.md) | 블루투스 Wi-Fi 프로비저닝 |
| [agent.md](agent.md) | 작업 규칙과 이 코드베이스에서 틀리기 쉬운 것들 |

---

## 목차

1. [구조](#1-구조) · 2. [동작 개요](#2-동작-개요) · 3. [설치](#3-설치) ·
4. [실행](#4-실행) · 5. [HTTP API](#5-http-api) · 6. [파일 포맷](#6-파일-포맷) ·
7. [설정](#7-설정) · 8. [개발자 도구](#8-개발자-도구) · 9. [테스트](#9-테스트) ·
10. [문제 해결](#10-문제-해결)

---

## 1. 구조

### 운영 (저장소 루트) — systemd가 띄우는 것

| 파일 | 역할 |
|---|---|
| `recorder.py` | **진입점.** 기록 루프와 상태 기계, 25 Hz 폴링 |
| `ahrs_file.py` | `.dat` 포맷 — 헤더·블록·슬롯 이름·복구·병합 |
| `stability.py` | 안정화 판정 (자이로 RMS·가속도 표준편차·자세각 표준편차) |
| `filestore.py` | 파일 목록·연속 그룹·휴지통 |
| `web/` | 운영자 웹 (Flask, 8080). `routes.py` + `templates/` |
| `eink_panel.py` | e-paper 화면 구성과 갱신 스레드 |
| `gdey0154d67.py` | e-paper 드라이버 (SSD1681, SPI0) |
| `network_utils.py` | 기록기·패널 공통 IP 조회 |
| `read_status.py` | 센서 연결·상태 읽기 **(recorder가 사용)** |
| `hwt9037_485.py` | 장치 모델 (Modbus RTU) |
| `port_config.py` | 시리얼 포트 결정 |
| `app_config.py` | 설정·PIN·로깅 (`config.json`) |
| `verticrane-recorder.service` | systemd 유닛 |
| `install.sh` `install_requirements.sh` `update.sh` `test.sh` | 설치·배포·점검 |

### PC용 도구 (`pc/`)

| 파일 | 역할 |
|---|---|
| `collect.py` | 운영자의 PC·노트북에서 Pi의 기록 파일 회수. Python 표준 라이브러리만 사용 |

PC에서 저장소 루트 기준으로 `python pc/collect.py <장비IP>`를 실행합니다.
`pc/collect.py` 파일만 PC에 복사했다면 해당 폴더에서 `python collect.py <장비IP>`로
실행할 수 있습니다. 내려받는 파일의 상대 경로는 명령을 실행한 폴더를 기준으로 합니다.

### 데이터 (`data/`)

| 이름 | 내용 |
|---|---|
| `FLAG_NNN.dat` | 기록 파일 |
| `FLAG_NNN.dat.partial` | 기록 중 (목록에 뜨지 않음) |
| `.slot` | 슬롯 카운터 |
| `.lastknown` | 마지막으로 알던 시각 |
| `.bootcount` | 부팅 횟수 (로그용) |
| `trash/` | 받아간 파일. 7일 뒤 삭제 |
| `corrupt/` | 헤더가 깨져 격리된 파일 |

### 의존 관계

```
recorder.py --> ahrs_file.py      포맷
            --> stability.py      판정
            --> filestore.py      목록·휴지통
            --> read_status.py --> hwt9037_485.py --> pymodbus
            --> web/           --> filestore.py
            --> eink_panel.py  --> gdey0154d67.py --> spidev, lgpio
```

`web/`과 `eink_panel.py`는 **각각 별도 스레드**에서 돌고, recorder가 준비해 둔
스냅샷만 읽습니다. 패널 갱신 1회가 SPI에서 1.4초를 잡아먹기 때문에 폴링 루프에
둘 수 없습니다.

### 센서 운용 조건

HWT9037-485는 Modbus 요청에 응답하는 방식이며 기록기가 주기적으로 값을 읽습니다.
연결 시 아래 설정을 확인합니다. 설정이 다르거나 확인할 수 없으면 로그와
`config_warnings`에 남기며, 설정 경고 자체로 기록을 중단하지 않습니다.

| 항목 | 운용값 | 레지스터 |
|---|---|---|
| 설치 방향 | 수평 (`0`) | `0x23` |
| 자세각 알고리즘 | 6축 (`1`) | `0x24` |
| 센서 대역폭 | 10 Hz (`5`) | `0x1F` |
| 통신 속도 | 115200 bps 우선, 9600 bps 재시도 | `0x04` |

가속도·각속도·자기·자세각은 `0x34`부터 15개 레지스터를 한 번에 읽고,
온도는 `0x43`에서 기본 1초마다 읽습니다. 레지스터 환산은
[doc/protocol.md](doc/protocol.md)를 참고하세요.

---

## 2. 동작 개요

**장비는 전원을 켜면 자동으로 측정·기록하고, 운영자는 나중에 브라우저로 파일을
회수합니다.** 현장 운용에는 웹의 기록 시작 버튼이 없습니다. 아래는 기본 설정으로
실행할 때의 흐름이며, 개발용 `--force` 옵션은 [4. 실행](#4-실행)에서 다룹니다.

### 전원을 켠 뒤 기록이 시작되기까지

```text
전원 켜짐 → OS 부팅 → 기록 서비스 시작 → 이전 .partial 복구·센서 연결
                                          ↓
                                  설치 유예 (기본 60초)
                                          ↓
                    ┌────────── 네트워크 연결 여부 ──────────┐
                    │ 연결 없음                            │ 연결 있음
                    │                                      ↓
                    │                         IP 할당 대기 (최대 90초)
                    │                                      ↓
                    │                            접속 대기 (기본 60초)
                    └─────────────────┬────────────────────┘
                                      ↓
                                 센서 안정화 확인
                                      ↓
                              25 Hz 자동 기록 시작

설치 유예·IP 할당·접속 대기 중 운영자가 접속 → maintenance (자동 기록을 시작하지 않음)
기록 중 운영자가 접속 → 현재 기록을 휴지통으로 이동 → maintenance
```

1. **시작 준비:** systemd가 `recorder.py`를 실행합니다. 이전 실행에서 남은
   `.dat.partial`을 복구한 뒤 웹 파일 목록을 제공하고, 센서 연결을 시도합니다.
   OS 부팅·파일 복구·센서 연결에 걸리는 시간은 장치 상태에 따라 달라집니다.
2. **설치 유예:** 기본 60초 동안 기록하지 않습니다. 전원을 켠 뒤 크레인에 올라가
   장비를 설치하는 시간을 확보합니다. 네트워크가 없어도 이 대기는 적용됩니다.
3. **접속 대기:** 설치 유예가 끝났을 때 네트워크 링크가 있으면 IP 할당을 최대
   90초 기다린 뒤, 운영자가 브라우저로 접속할 수 있도록 기본 60초를 더 기다립니다.
   이미 IP가 있으면 IP 대기는 바로 끝납니다. 네트워크 링크가 없으면 이 두 단계를
   건너뜁니다. IP 대기 시간이 초과되어도 접속 대기는 진행합니다.
4. **안정화 확인:** 센서의 자이로·가속도·자세각 변동이 설정된 기준을 만족하면
   기록을 시작합니다. 대기 중에도 센서 데이터를 읽으므로 이미 안정화되어 있으면
   이 단계가 짧게 끝날 수 있습니다. 센서가 응답하지 않으면 재연결을 시도하며
   정상 데이터 없이 기록을 시작하지 않습니다.

따라서 기록 시작까지의 시간은 고정되어 있지 않습니다. 서비스 시작 이후의 기본
대기는 네트워크가 없으면 설치 유예 60초, IP가 이미 있으면 설치 유예 60초와 접속
대기 60초이며, 여기에 시작 준비와 필요한 안정화 시간이 더해집니다. 각 시간은
`config.json`의 설정으로 조정합니다.

### 기록 중의 동작과 화면

- 센서를 **초당 25회** 읽어 `data/BASE_045.dat.partial` 같은 파일에 저장합니다.
  `BASE / MIDDLE / TOP`은 설치 위치, 숫자는 순환하는 슬롯 번호입니다.
- 기본 설정에서는 전원이 꺼지거나 기록을 중지할 때까지 한 파일에 기록합니다.
  `segment_minutes`를 설정하면 지정한 간격으로 파일을 나눕니다.
- 기록 중 흔들림이 발생하면 로그와 데이터 블록에 표시하며 **기록은 계속합니다.**
  `stop_on_unstable=true`로 변경한 경우에는 기록을 끝내고 안정화를 다시 기다립니다.
- e-paper는 기록 시작 후 약 15초간 **“기록 중”**을 보여준 뒤, 합성 기울기(%),
  X축 Roll·Y축 Pitch(°), 센서 온도, 기록 경과·샘플 수를 표시합니다.
  측정 화면은 기본 60초마다 갱신되며, 데이터 기록 주기와 화면 갱신 주기는 다릅니다.
  하단에는 장치 IP 또는 `NO NETWORK`가 표시됩니다.

### 브라우저 접속과 파일 회수

e-paper에 표시된 주소로 `http://<장비IP>:8080`에 접속합니다. **운영자가 접속하면
자동 기록을 시작하지 않으며, 기록 중이면 중단합니다.** 설치 유예·접속 대기 중 접속하면 기록을
시작하지 않고, 기록 중 접속하면 그때 진행 중이던 파일을 종료하여 `trash/`로
옮깁니다. 이전에 끝난 측정 파일은 그대로 회수할 수 있습니다.

일반 페이지·API 요청을 접속으로 취급합니다. `/healthz`, 정적 파일·favicon 요청,
`?auto=1`을 붙인 자동 조회는 예외입니다. 기록을 유지하며 상태를 확인하려면
`/api/status?auto=1`을 사용합니다.

파일 목록에는 완료된 `.dat`만 표시됩니다. 회수 확인 요청
(`/collected`)이 오면 기본 설정에서 해당 파일을 `trash/`로 옮기며, 기본 보관 기간은
7일입니다. 브라우저 UI는 다운로드 클릭 시 이 요청도 보냅니다. 휴지통 이동은
즉시 삭제가 아니며, 실제 다운로드 완료를 장치가 판정하는 것도 아닙니다.

`maintenance`에서는 브라우저를 닫아도 자동 기록이 재개되지 않습니다.
다음 측정을 시작하려면 전원을 다시 켜거나 기록 서비스를 재시작하여 시작 절차를
다시 진행합니다.

### 전원 차단과 다음 부팅

기록 중 전원이 끊기면 `.dat.partial`이 남습니다. 다음 서비스 시작 시 CRC를 검사해
완전한 블록을 보존하고 불완전한 끝부분을 제거하여 `.dat`로 복구합니다. 복구가
완료되면 웹 목록에서 회수할 수 있으며, 이후에는 새로운 슬롯 파일로 기록합니다.
서비스를 정상 중지하면 종료 처리로 파일을 확정합니다.

### API에 표시되는 상태

| 상태 | 의미 |
|---|---|
| `waiting_http` | 기록 시작 전 대기. 설치 유예·IP 할당·접속 대기를 포함 |
| `waiting_stable` | 센서 안정화 대기 |
| `recording` | 측정 데이터를 파일에 기록 중 |
| `maintenance` | 접속 또는 기록 중지 후 대기. 자동 기록하지 않음 |
| `error` | 저장 실패 또는 저장 공간 부족으로 기록 중지. 원인을 확인한 후 전원을 껐다 켭니다. |

센서 연결 또는 측정값 읽기가 실패하면 이전 측정값을 화면에서 지우고 재연결을 기다립니다.
기록 시작 전에는 복구 후 새 데이터로 안정화를 다시 확인합니다. 기록 도중 통신이 끊기면
파일을 유지하며, 복구 후 실제 수신한 데이터부터 저장을 이어갑니다. 통신 실패는 저장 오류와 구분합니다.

설치 유예는 별도의 `waiting_mount` 상태가 아니며, 대기 단계는 로그에서
“설치 유예”, “접속 대기” 등으로 구분합니다.

---

## 3. 설치

### 새 장비 — 한 번에

```bash
git clone https://github.com/geolux-sdk/verticrane.git ~/verticrane
cd ~/verticrane && chmod +x *.sh dev/*.sh
./install.sh              # 진단 후 필요한 것만 적용. --dry 로 미리 보기
```

`install.sh`가 하는 일: UART/SPI 활성화, `dtoverlay=miniuart-bt` 전환, 그룹 가입
(`dialout`/`spi`/`gpio`), 파이썬 의존성, cloud-init 해제, systemd 유닛 등록.

### 수동으로 할 때

<details>
<summary><b>0. 시리얼 포트 열기 (최초 1회)</b></summary>

파이의 온보드 UART는 기본적으로 꺼져 있고 시리얼 콘솔이 포트를 점유합니다.
게다가 Zero 2 W는 성능이 좋은 PL011을 블루투스가 가져가고 GPIO에는 mini UART를
배정하는데, mini UART는 보레이트가 코어 클럭에 묶여 있어 115200 Modbus에서
프레임이 깨질 수 있습니다.

```bash
sudo cp /boot/firmware/config.txt /boot/firmware/config.txt.bak

sudo tee -a /boot/firmware/config.txt >/dev/null <<'CFG'

enable_uart=1
dtoverlay=miniuart-bt      # 센서는 PL011, 블루투스는 mini UART
dtparam=spi=on             # e-paper
CFG

sudo sed -i 's/console=serial0,115200 //' /boot/firmware/cmdline.txt
sudo reboot
```

재부팅 후 `ls -l /dev/serial0`에서 `-> ttyAMA0`이 보이면 정상입니다.

`dtoverlay=disable-bt`를 쓰면 블루투스가 죽어 **BLE Wi-Fi 프로비저닝을 못 씁니다.**
`miniuart-bt`는 센서에 PL011을 유지하면서 블루투스를 살려 둡니다.

</details>

<details>
<summary><b>1~5. 이름 · 의존성 · 센서 · 점검 · 서비스</b></summary>

```bash
# hostname 이 그대로 SENSOR_ID 가 되고 접속 주소가 됩니다
sudo hostnamectl set-hostname pi-tilt001

./install_requirements.sh                  # sudo 없이!
sudo usermod -aG dialout,spi,gpio $USER    # 재로그인 필요
sudo apt install -y fonts-nanum            # 없으면 패널 한글이 영문으로 대체

.venv/bin/python dev/configure_sensor.py --baud 115200   # 센서 최초 설정
./test.sh                                                # 자가 점검

sudo cp verticrane-recorder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now verticrane-recorder
sudo systemctl disable --now verticrane-dashboard 2>/dev/null || true
```

</details>

### 업데이트

```bash
./update.sh && sudo systemctl restart verticrane-recorder
```

---

## 4. 실행

아래 명령은 Pi에 SSH로 로그인한 뒤 실행합니다.

### 서비스 상태·시작·중지

```bash
# 현재 서비스 상태 확인
systemctl status verticrane-recorder

# 서비스 시작
sudo systemctl start verticrane-recorder

# 서비스 중지 — 진행 중인 기록 파일을 종료 처리
sudo systemctl stop verticrane-recorder

# 서비스 재시작 — 설치 유예·접속 대기부터 다시 진행
sudo systemctl restart verticrane-recorder
```

`stop`은 현재 서비스를 중지합니다. 부팅 시 자동 실행 설정은 유지되므로 다음
부팅에는 다시 시작됩니다. 부팅 시 자동 실행도 끄거나 다시 켜려면 아래를 사용합니다.

```bash
sudo systemctl disable --now verticrane-recorder  # 지금 중지 + 자동 실행 해제
sudo systemctl enable --now verticrane-recorder   # 자동 실행 등록 + 지금 시작
```

### 로그 확인

```bash
# 최근 로그 80줄
journalctl -u verticrane-recorder -n 80 --no-pager

# 이번 부팅의 로그
journalctl -b -u verticrane-recorder --no-pager

# 최근 10분의 로그
journalctl -u verticrane-recorder --since "10 minutes ago" --no-pager

# 실시간 로그 — Ctrl+C로 조회 종료 (서비스는 계속 실행)
journalctl -u verticrane-recorder -f
```

SSH 접속과 로그 조회는 브라우저 접속으로 취급되지 않으므로 자동 기록 시작이나
진행 중인 기록에 영향을 주지 않습니다.

### 직접 실행

```bash
# 직접 실행 — 서비스를 먼저 멈춰야 합니다 (시리얼 포트가 하나뿐)
./dev/devmode.sh .venv/bin/python recorder.py
```

`devmode.sh`는 기존 서비스를 중지하고 명령을 실행한 뒤, 종료 시 기존에 실행 중이던
서비스를 다시 시작합니다. 직접 실행한 기록기를 종료하려면 `Ctrl+C`를 누릅니다.

### `recorder.py` 옵션

| 옵션 | 뜻 |
|---|---|
| `--port /dev/ttyUSB0` | 시리얼 포트 고정 (기본은 자동 결정) |
| `--data-dir PATH` | 기록 디렉터리 |
| `--force recording` | 대기를 건너뛰고 바로 안정화 → 기록 |
| `--force maintenance` | 기록하지 않고 웹만 |
| `--seconds N` | N초 뒤 정상 종료 |
| `--http-wait N` | `http_wait_seconds` 덮어쓰기 |
| `--port-http N` | 웹 포트 |
| `--no-web` / `--no-panel` | 각각 끄기 |
| `--require-sensor` | 센서가 없으면 기동 실패 (기본은 계속 진행) |

---

## 5. HTTP API

포트 8080. 다운로드를 제외하고 응답은 JSON입니다.

> ### 접속에는 부작용이 있습니다
>
> `/healthz`와 `?auto=1`이 붙은 요청을 뺀 **모든 요청이 "운영자가 왔다"로
> 세어집니다.** 자동 기록을 시작하지 않으며, 기록 중이었다면 중단하고 그 기록을 휴지통으로 옮깁니다.
> 스크립트로 폴링할 때는 반드시 `?auto=1`을 붙이세요.

### 상태

| | |
|---|---|
| `GET /api/status` | 현재 상태 |
| `GET /healthz` | 살아 있는지만. 접속으로 세지 않음 |

```json
{
  "sensor_id": "pi-tilt001", "position": "TOP", "device_serial": "WT4200068151",
  "state": "recording", "file": "TOP_003.dat.partial",
  "started_at": 1772179369.29, "elapsed_s": 812.4,
  "samples": 20310, "blocks": 812,
  "tilt_pct": 0.0623, "temp_c": 26.3,
  "sensor_ok": true, "device_time": "2026-08-28 12:02:25",
  "stability": { "stable": true, "reason": "STABLE", "metrics": [] },
  "config_warnings": [], "free_mb": 23057.0
}
```

### 파일

| | |
|---|---|
| `GET /api/files` | 확정 파일 목록 + 통계 |
| `GET /api/files/{name}` | 단일 파일 다운로드 |
| `GET /api/groups/{id}` | 연속 그룹을 하나로 병합해 다운로드 |
| `POST /api/files/{name}/collected` | 받았음을 확인 → 휴지통으로 이동 |
| `POST /api/groups/{id}/collected` | 그룹 전체를 받았음을 확인 |
| `DELETE /api/files/{name}` | 삭제 (PIN 필요) |

```json
{
  "files": [{
    "name": "TOP_003.dat", "size": 1889952, "slot": 3,
    "start_epoch": 1772179369.29, "start": "2026-08-28 12:02:25",
    "duration_s": 1534.0, "samples": 38350, "blocks": 1534,
    "recovered": false, "position": "TOP", "group": 0
  }],
  "groups": [{ "group": 0, "files": 1, "bytes": 1889952, "duration_s": 1534.0 }],
  "stats": { "files": 1, "groups": 1, "bytes": 1889952, "trashed": 0, "free_mb": 23057.0 }
}
```

**전송이 끝났는지 서버는 알 수 없습니다.** 응답이 완료됐다는 것은 바이트가 소켓에
나갔다는 뜻일 뿐입니다. 그래서 파일을 휴지통으로 옮기는 기준은 다운로드가 아니라 브라우저가
보내는 `/collected`입니다. 브라우저 UI는 받기 클릭 시 이 요청을 다운로드와 함께
보내므로, 전송이 중간에 끊겨도 파일이 휴지통으로 이동했을 수 있습니다.
휴지통의 파일은 보관 기간 동안 다시 다운로드할 수 있습니다.
아래의 `collect.py`는 파일 저장과 크기 확인을 마친 뒤에만 회수 확인을 보냅니다.

### 제어

| | |
|---|---|
| `POST /api/record/stop` | 기록 중지 및 파일 확정 |
| `GET /settings` `POST /settings` | 설정 화면 (저장에 PIN 필요) |

**시작하는 API는 없습니다** ([2. 동작 개요](#2-동작-개요) 참조).

### PC에서 한 번에 받아오기 — `pc/collect.py`

사람이 트리거하면 목록 조회부터 회수 확인까지 알아서 끝냅니다. 표준 라이브러리만
쓰므로 현장에 들고 갈 노트북에 설치할 것이 없습니다.

```bash
python pc/collect.py 192.168.0.19                       # 한 대
python pc/collect.py 10.0.0.11 10.0.0.12 10.0.0.13      # 크레인 한 대 분(3기)
python pc/collect.py 192.168.0.19 --out ./2026-08-28    # 받을 폴더 지정
python pc/collect.py 192.168.0.19 --keep                # 받은 뒤에도 장비의 파일 목록에 유지
```

```
192.168.0.19
  pi-tilt001 [BASE]  상태 recording
     기록 중이던 파일은 휴지통으로 들어갑니다 (README.md). 이 목록에는 없습니다.
     + BASE_000.dat         1.8 MB   3.2 MB/s → 휴지통
     + BASE_001.dat       445.2 KB   1.9 MB/s → 휴지통

5개 · 2.9 MB → /home/op/verticrane-data
```

받는 순서는 이렇습니다.

1. `GET /api/status?auto=1` — 어느 장비인지 확인 (이 요청은 접속으로 세지 않음)
2. `GET /api/files` — 목록. **이 요청부터 접속으로 세어져 진행 중인 기록이 끝납니다**
3. 파일마다 `.part`로 받아 `fsync`하고, `Content-Length`와 맞는지 확인한 뒤 rename
4. 맞을 때만 `POST /api/files/{name}/collected`

**3번과 4번의 순서가 요점입니다.** 장비는 전송이 끝났는지 알 수 없으므로(응답 완료는
바이트가 소켓에 나갔다는 뜻일 뿐), 파일을 휴지통으로 옮기는 기준은 **받은 쪽이 디스크에
있음을 확인하고 보내는 4번뿐**입니다. 중간에 끊기면 4번이 안 가고 파일은 장비에
그대로 남습니다.

장비별 하위 폴더(`verticrane-data/pi-tilt001/`)로 나눠 받으므로 한 크레인의 세 대를
한 폴더에 모아도 섞이지 않습니다.

### 파일명 검증

경로 구분자와 `..`를 허용하지 않고 `FLAG_NNN.dat` 정규식에 정확히 맞는 것만
받습니다. 통과한 뒤에도 열기 전에 `realpath`로 `data/` 하위인지 다시 확인합니다.

---

## 6. 파일 포맷

`.dat`는 64바이트 헤더 뒤에 1232바이트 고정 블록이 반복되는 형식입니다.
정수·실수는 little-endian으로 저장하며, 포맷 정의는 `ahrs_file.py`에서 관리합니다.

### 파일명과 저장 상태

파일명은 **설치 위치와 슬롯 번호**로 구성합니다.

| 형식 | 예시 | 의미 |
|---|---|---|
| `FLAG_NNN.dat` | `TOP_003.dat` | 기록이 종료되었거나 복구가 완료된 파일 |
| `FLAG_NNN.dat.partial` | `TOP_003.dat.partial` | 기록 중이거나 종료 처리가 완료되지 않은 파일 |

- `FLAG`: 설치 위치를 나타냅니다. `BASE`, `MIDDLE`, `TOP`이며, 위치를 설정하지
  않았으면 `UNSET`입니다.
- `NNN`: 파일을 생성할 때 부여하는 세 자리 슬롯 번호입니다. `000`부터 `999`까지
  순환하며, `999` 다음에는 `000`으로 돌아갑니다. 측정 날짜나 누적 기록 횟수를
  뜻하지 않습니다. 동일한 파일명을 다시 사용하면 기존 기록이 덮어써질 수 있으므로
  필요한 기록은 PC로 회수해 보관해야 합니다.
- `.partial`: 기록 중인 파일에 붙습니다. 정상 종료 또는 다음 부팅의 복구 처리가
  완료되면 이 확장자를 제거하여 `.dat` 파일로 확정합니다.

**기록 시작 시각과 복구 여부는 파일명에 넣지 않고 파일 내부의 헤더에 저장합니다.**
예를 들어 `TOP_003.dat`의 기록 날짜와 전원 차단 후 복구 여부는 헤더를 읽어
확인합니다. 파일명이 같더라도 슬롯이 재사용되었다면 서로 다른 측정 기록입니다.

이 규칙을 사용하면 기록 시각이나 복구 여부에 따라 파일명을 바꿀 필요가 없고,
기록기·웹 목록·PC 회수 도구가 같은 파일명 규칙으로 파일을 찾을 수 있습니다.

### 헤더 (64 B)

| 오프셋 | 내용 | 크기 |
|---|---|---|
| 0 | magic `AHRSBIN` + NUL | 8 |
| 8 | 포맷 버전 (현재 **2**) | 2 |
| 10 | 블록 크기 (1232) | 2 |
| 12 | 블록당 샘플 수 (25) | 2 |
| 14 | 샘플레이트 Hz (25) | 2 |
| 16 | **기록 시작 시각** (double) | 8 |
| 24 | SENSOR_ID (hostname) | 16 |
| 40 | SENSOR_FLAG (0 UNSET / 1 BASE / 2 MIDDLE / 3 TOP) | 2 |
| 42 | 장치 시리얼 | 12 |
| 54 | **파일 플래그** (bit 0 = 전원 차단 복구) | 1 |
| 55 | 예약 | 5 |
| 60 | 헤더 CRC32 (앞 60바이트) | 4 |

#### 기록 시작 시각은 어디에 저장하는가

파일 앞부분의 64바이트 헤더에는 기록 시작 시각, 장치 식별자, 설치 위치 등의 정보가
들어 있습니다. 위 표의 **오프셋은 파일 시작점에서 몇 바이트 떨어져 있는지**를
뜻합니다. 기록 시작 시각은 오프셋 16부터 8바이트에 저장합니다.

시각은 파일을 만들 때 장치의 시스템 시계에서 읽어 저장합니다. 내부적으로는
1970년 1월 1일 UTC부터 지난 초를 나타내는 숫자이며, 웹 화면과 CSV 변환 도구가
이를 사람이 읽을 수 있는 날짜·시간으로 표시합니다. 예를 들어 실제로 오전 10시에
기록을 시작했더라도 장치 시계가 오전 9시라면 파일에도 오전 9시로 저장됩니다.
따라서 **저장된 날짜·시간의 정확도는 기록 당시 장치 시계에 달려 있습니다.**

저장된 시작 시각은 이후 장치 시계가 바뀌거나 파일을 복구하더라도 수정하지
않습니다. 프로그램은 이 시각의 정확도를 별도로 평가하거나, 과거 파일의 시각을
나중에 보정하지 않습니다. 보정 정보를 담는 별도 파일도 만들지 않습니다.

#### 파일 목록과 연속 기록 병합에 어떻게 사용하는가

파일 목록은 슬롯 번호가 아니라 **헤더의 기록 시작 시각을 기준으로 정렬**합니다.
슬롯 번호는 `999` 다음에 `000`으로 돌아가므로 번호만으로는 기록 순서를 알 수
없기 때문입니다.

여러 파일을 연속 기록으로 묶을 때는 앞 파일의 시작 시각에 기록 길이를 더해
종료 시각을 계산하고, 다음 파일의 시작 시각과 비교합니다. 기본적으로 두 시각의
차이가 2초 이내이면 같은 연속 그룹으로 분류합니다. 이 기준은
`merge_gap_tolerance_seconds`로 조정합니다.

예를 들어 다음과 같이 기록되었다면 A와 B는 연속 그룹이 되고, C는 별도 그룹입니다.

| 파일 | 헤더의 시작 시각 | 기록 길이 | 계산한 종료 시각 |
|---|---|---|---|
| A | 10:00:00 | 10분 | 10:10:00 |
| B | 10:10:00 | 5분 | 10:15:00 |
| C | 10:20:00 | 5분 | 10:25:00 |

A와 B를 한 파일로 병합하면 A의 헤더를 사용하고, B의 데이터는 A가 시작한 시점에서
10분 뒤에 이어지는 것으로 배치합니다. 병합 파일을 새로 만들며 원본 파일의
시작 시각을 수정하지 않습니다.

**장치 시계가 일정하게 1시간 느린 경우**에는 위 시각이 모두 1시간씩 앞당겨져
저장되어도 A와 B 사이의 시간 간격은 같습니다. 이 조건에서는 상대적인 기록 순서와
연속 여부를 판단할 수 있습니다. 다만 기록 사이에 시계를 수동으로 변경하거나
시각 동기화로 시계가 크게 바뀌면 시간 간격도 달라질 수 있습니다. 따라서
장치 시각이 틀려도 정렬과 병합이 항상 정확하다는 뜻은 아닙니다.

#### 전원 차단 후 복구할 때 헤더에서 바뀌는 정보

기록 중 전원이 끊기면 파일이 `.dat.partial` 상태로 남습니다. 다음 부팅에서는
데이터 블록의 CRC를 검사하고, 끝부분에 불완전한 블록이 있으면 그 부분을 제거합니다.
정상 블록을 보존한 뒤 헤더의 **파일 플래그(오프셋 54)에 복구 표시**를 추가하고,
변경된 헤더에 맞게 **헤더 CRC32(오프셋 60)를 다시 계산**합니다.

이때 **기록 시작 시각과 장치 식별 정보는 그대로 유지**합니다. 즉, “헤더를 다시
쓴다”는 것은 시작 시각을 보정한다는 뜻이 아니라 복구 여부와 검사값을 갱신한다는
뜻입니다. 이 처리는 이전 기록이 끝난 `.partial` 파일을 대상으로 하며, 새 측정
데이터를 덧붙이는 작업과 동시에 수행하지 않습니다. 처리가 완료되면 `.partial`을
제거하여 `.dat`로 확정하고 웹 파일 목록에 표시합니다.

### 블록 (1232 B)

25샘플 묶음. 샘플마다 12개 float — Roll/Pitch/Yaw, 가속도 3축, 자이로 3축, 자기 3축.
블록마다 CRC32와 상태 플래그가 붙습니다.

| 오프셋 | 내용 | 크기·형식 |
|---|---|---|
| 0 | 샘플 수 (25) | 2바이트, uint16 |
| 2 | 상태 플래그 | 2바이트, uint16 |
| 4 | 첫 샘플부터 마지막 샘플까지의 시간 | 2바이트, uint16, ms |
| 6 | 예약 | 2바이트 |
| 8 | 블록 첫 샘플의 기록 시작 이후 경과 시간 | 8바이트, uint64, ms |
| 16 | 센서 온도 | 4바이트, float32, °C |
| 20 | 예약 | 4바이트 |
| 24 | 샘플 25개 (각 48바이트) | 1200바이트 |
| 1224 | 예약 | 4바이트 |
| 1228 | 앞 1228바이트의 CRC32 | 4바이트, uint32 |

각 샘플의 순서는 가속도 X/Y/Z, 각속도 X/Y/Z, 자기 X/Y/Z, Roll/Pitch/Yaw입니다.
기울기 %는 Roll/Pitch로 계산하며 별도로 저장하지 않습니다. 한 블록을 채우지 못한
마지막 샘플은 종료 시 저장하지 않습니다. CRC가 맞지 않거나 샘플 수가 25가 아닌
블록은 정상 블록으로 인정하지 않습니다.

| 플래그 | 뜻 |
|---|---|
| bit 0 | 이 블록을 채우는 동안 시리얼 읽기 실패 |
| bit 1 | 시리얼 링크 재접속 |
| bit 2 | 안정화 기준을 벗어난 구간 |

**블록의 경과 시간은 날짜·시각 변경의 영향을 받지 않는 경과 시간 기준으로
계산합니다.** 기록 시작 시점을 0으로 두고 각 블록까지 지난 시간을 저장하므로,
기록 중 장치의 날짜나 시각이 바뀌어도 이 값은 영향을 받지 않습니다.
예를 들어 “기록 시작 후 3분 12초”라는 정보와 “몇 월 며칠 몇 시”라는 정보는
서로 다른 기준으로 관리합니다.

### CSV 변환

```bash
.venv/bin/python dev/ahrsbin_to_csv.py data/TOP_003.dat
.venv/bin/python dev/ahrsbin_to_csv.py data/*.dat --report
```

---

## 7. 설정

설정은 `config.json`에 저장합니다. 파일에 없는 항목은 `app_config.py`의 기본값을
사용합니다. `config.json`은 장치마다 다르며 Git에 포함하지 않습니다.

**아래 현재값은 2026-10-13에 Pi `pi-tilt001`(`192.168.0.19`)에 접속하여 확인한
설정입니다.** Pi의 `config.json`을 읽고, 파일에 없는 항목은 해당 Pi 코드의
기본값으로 보완한 값입니다. 서비스와 실제 실행 프로세스 모두 설정을 덮어쓰는
명령행 옵션 없이 `recorder.py`를 실행하고 있었습니다.

확인 당시 Pi 코드 버전은 `ee201c3`입니다. 이 표는 확인 시점의 설정 기록이며,
자동으로 갱신되지 않습니다. Pi 설정을 변경하면 표도 다시 확인해 갱신해야 합니다.
PC의 `config.json`은 별도이며 이 표의 기준으로 사용하지 않습니다.

### 기록기 설정 — `config.json`의 `recorder` 항목

변경 위치의 **웹**은 `http://<장비IP>:8080/settings`에서 수정할 수 있다는 뜻입니다.
**파일**은 SSH로 `config.json`을 직접 수정해야 하는 항목입니다.

| 파라미터 | 의미 | 단위 | 현재값 (Pi) | 기본값 | 변경 위치 |
|---|---|---|---|---|---|
| `sensor_flag` | 크레인 설치 위치. `base` / `middle` / `top` / `unset`; 파일명 앞부분에 반영 | — | `base` | `unset` | 웹 |
| `contact_face` | 설치 안내 그림에서 구조물에 닿는 면. `bottom` / `top` / `left` / `right`; 측정 축을 변환하지 않음 | — | `bottom` | `bottom` | 파일 |
| `mount_delay_seconds` | 서비스 시작 후 장비 설치를 위해 기록하지 않고 기다리는 시간 | 초 | 60 | 60 | 파일 |
| `network_wait_seconds` | 네트워크 링크가 있을 때 IP 할당을 기다리는 최대 시간 | 초 | 90 | 90 | 파일 |
| `http_wait_seconds` | 설치 유예·IP 대기 이후 운영자의 브라우저 접속을 기다리는 시간. 네트워크 링크가 없으면 건너뜀 | 초 | 60 | 60 | 웹 |
| `stability_window_seconds` | 안정화 판단에 사용할 최근 센서 데이터의 시간 범위 | 초 | 5.0 | 5.0 | 파일 |
| `stability_min_samples` | 안정화 판단에 필요한 최소 샘플 수 | 개 | 100 | 100 | 파일 |
| `gyro_rms_max_dps` | 안정 상태로 판단할 자이로 회전 속도 RMS의 상한 | °/s | 0.5 | 0.5 | 파일 |
| `accel_std_max_g` | 안정 상태로 판단할 가속도 변동(표준편차)의 상한 | g | 0.01 | 0.01 | 파일 |
| `attitude_std_max_deg` | 안정 상태로 판단할 자세각 변동(표준편차)의 상한 | ° | 0.3 | 0.3 | 파일 |
| `stop_on_unstable` | `true`: 흔들림 발생 시 기록 종료 후 안정화 대기. `false`: 기록을 계속하며 흔들림 표시 | — | `false` | `false` | 파일 |
| `record_fsync_interval_seconds` | 기록 데이터를 디스크에 반영하는 `fsync` 호출 간격 | 초 | 1.0 | 1.0 | 파일 |
| `segment_minutes` | 기록 파일 분할 간격. `0`이면 시간에 따른 분할 없이 한 파일에 기록 | 분 | 0 | 0 | 웹 |
| `merge_gap_tolerance_seconds` | 앞 파일의 종료와 다음 파일의 시작 사이에 허용하는 차이. 이 범위 내이면 연속 그룹으로 분류 | 초 | 2.0 | 2.0 | 파일 |
| `time_save_interval_seconds` | 장치가 마지막으로 확인한 시스템 시각을 `.lastknown`에 저장하는 간격. 다음 시작 시 시각 복원에 사용 | 초 | 10 | 10 | 파일 |
| `panel_refresh_seconds` | e-paper 측정 화면 갱신 간격. 정상 동작 시 다른 화면에는 적용하지 않음 | 초 | 60 | 60 | 파일 |
| `ip_check_interval_seconds` | IP 변경을 확인하여 패널 갱신을 요청하는 간격 | 초 | 10 | 10 | 파일 |
| `panel_rotation` | e-paper 화면의 시계 방향 회전각. `0` / `90` / `180` / `270` | ° | 90 | 90 | 파일 |
| `delete_after_download` | `true`: 회수 확인 요청 시 파일을 휴지통으로 이동. `false`: 원래 파일 목록에 유지; 즉시 삭제하는 설정은 아님 | — | `true` | `true` | 웹 |
| `trash_retention_days` | 휴지통 파일 보관 기간. 기간이 지나면 정리 작업에서 삭제; 저장 공간 부족 시에는 더 일찍 정리될 수 있음 | 일 | 7 | 7 | 웹 |
| `min_free_mb` | 기록에 필요한 최소 여유 공간. 부족하면 휴지통을 정리하고, 그래도 확보되지 않으면 기록 중지 | MB | 500 | 500 | 웹 |
| `http_port` | 운영자 웹·HTTP API가 사용하는 포트 | — | 8080 | 8080 | 파일 |

안정화 판단은 최소 샘플 수를 확보하고 자이로·가속도·자세각 기준을 모두 만족해야
통과합니다. 각 기준을 지나치게 낮추면 기록이 시작되지 않을 수 있고, 높이면
움직이는 중에도 기록이 시작될 수 있습니다.

측정 화면은 한 번 갱신하는 데 약 1.4초가 걸립니다. `panel_refresh_seconds`를
불필요하게 줄이면 패널 갱신 횟수가 늘어납니다. 코드에서는 최소 10초로 제한하며,
기본값은 60초입니다. 패널 출력 실패 시에는 별도로 30초 뒤 재시도합니다.

### 공통·개발 분석 설정 — `config.json`의 최상위 항목

다음 항목은 `recorder` 안에 넣지 않고 `config.json`의 최상위에 저장합니다.

| 파라미터 | 의미 | 단위 | 현재값 (Pi) | 기본값 | 변경 위치 |
|---|---|---|---|---|---|
| `LOG_LEVEL` | 로그 출력 수준. 기본 `INFO`; 상세 진단은 `DEBUG`, 경고 위주 확인은 `WARNING` | — | `INFO` | `INFO` | 파일 |
| `slope_threshold_pct` | 개발용 분석·대시보드에서 이동평균 기울기를 평가하는 기준. 운영 기록기의 자동 시작·중지 조건에는 사용하지 않음 | % | 0.1 | 0.1 | 파일 / 개발 대시보드 |
| `ma_seconds` | 개발용 분석·대시보드의 기울기 이동평균 계산 시간 범위 | 초 | 1.0 | 1.0 | 파일 / 개발 대시보드 |

PIN 인증용 저장 항목은 이 표에서 제외했습니다. 파일을 직접 수정한 설정은
서비스를 재시작하여 반영합니다. 재시작하면 진행 중인 기록을 종료하고 설치 유예·
접속 대기부터 다시 진행합니다. 현재 운영값을 확인하려면 해당 Pi의 설정 파일을
읽어야 하며, 실행 인자의 설정 덮어쓰기도 함께 확인해야 합니다.

---

## 8. 개발자 도구

`dev/`의 도구들은 **운영자에게 노출되지 않습니다.** 웹에서 갈 수 있는 숨은 링크도
없고, SSH로 직접 실행하는 것이 유일한 방법입니다.

### 시리얼 포트는 하나뿐입니다

기록기가 포트를 점유하고 있어 `dev/` 도구 대부분이 그냥은 동작하지 않습니다.
**`devmode.sh`를 쓰세요** — 서비스를 멈추고, 명령을 실행하고, **끝나면 반드시
다시 켭니다.** Ctrl-C를 눌러도, 명령이 실패해도 되살립니다.

```bash
./dev/devmode.sh                                    # 개발자 셸 (나가면 복구)
./dev/devmode.sh .venv/bin/python read_status.py    # 명령 하나만
./dev/devmode.sh ./dev/run_dashboard.sh             # Streamlit 대시보드
```

> 손으로 `systemctl stop`을 하는 것은 쉽지만 다시 켜는 것을 잊기는 더 쉽습니다.
> 조용히 기록을 멈춘 현장 장비가 이 프로젝트에서 가장 나쁜 결과입니다.

| 파일 | 역할 |
|---|---|
| `devmode.sh` | 서비스를 멈추고 도구를 실행한 뒤 되살림 |
| `ahrsbin_to_csv.py` | `.dat` → CSV 변환, `--report`로 분석까지 |
| `configure_sensor.py` | 센서 설정 (6축 알고리즘, baud) |
| `dashboard.py` `pages/setup.py` | Streamlit 분석 대시보드 |
| `analyze_tilt.py` | CSV 분석 (통계·FFT) |
| `log_tilt.py` | CSV 직접 기록 (구형) |
| `install_requirements.bat` | Windows 개발 환경 |

시험 코드는 `tests/`에 모았습니다. `tests/hardware/`는 센서·패널·GPIO 직접 시험,
`tests/tools/fake_card.py`는 합성 기록으로 웹 화면을 확인하는 도구,
`tests/ble_web/`는 BLE 웹 UI 자동 검사입니다.

운영 의존성은 `requirements.txt`, 분석·대시보드 의존성은 `requirements-dev.txt`입니다.
Pi 운영 설치는 `./install_requirements.sh`, 개발 도구 설치는
`./install_requirements.sh --dev` 또는 Windows의 `dev\install_requirements.bat`를 사용합니다.
개발 의존성까지 갱신하려면 `./update.sh --dev`를 사용합니다.
SPI/GPIO 의존성과 한글 폰트(`fonts-nanum`)는 `install.sh`에서 OS 패키지로 설치합니다.
`install_requirements.sh`는 Python 가상환경과 Python 패키지만 설치합니다.

SCL3300 지원과 전용 비교·시험 코드는 제거했습니다. `SENSOR_COMPARISON.md`와
동명의 PPTX는 과거 실험 기록으로만 보존하며, 현재 운용 센서는 HWT9037-485입니다.

### e-paper 미리보기 — 하드웨어 없이

측정 화면은 합성 기울기(%)와 센서 X축 Roll / Y축 Pitch(부호 포함 소수점 세 자리, °)를
함께 표시합니다. 각도 누락 시 `--.---°`로 표시하며 기본 갱신 주기는 60초입니다.

```bash
python eink_panel.py --out panel.png --scale 3 --screen boot
python eink_panel.py --out panel.png --screen install --position BASE
```

`--screen`은 `boot` / `install` / `record` / `measure` / `brand`.

### 안정화 기준 재검증 — 하드웨어 없이

```bash
python stability.py data/*.csv
python stability.py data/*.csv --gyro-rms 0.1     # 기준을 바꿔 시험
```

---

## 9. 테스트

### 하드웨어 없이 — 언제나 통과해야 합니다

```bash
python tests/run.py              # 운영 코드 소프트웨어 검사 (센서 접근 없음)
python tests/run.py --all        # 개발 분석 + BLE 웹 검사 포함 (개발 의존성, Node.js 필요)
python tests/test_ahrs_file.py     # 포맷: 쓰기 → 자르기 → 복구 → 복구 표식 → 병합 → 슬롯 이름
python tests/test_stability.py     # 판정: 0/360 경계, 윈도우 경계, 움직임 거부
python tests/test_eink_panel.py    # 패널: 상태→화면, 갱신 예약, 다섯 화면 렌더링
```

### 장비에서

```bash
./test.sh                    # 운영 소프트웨어 검사만; 서비스 중지 불필요
./dev/devmode.sh ./test.sh --hardware --seconds 10  # 센서 검사 후 서비스 복구
```

센서 검사는 개발 의존성이 필요합니다. `--hardware`는 기록 서비스가 실행 중이면
거부합니다. 세부 실행 방법은 [tests/README.md](tests/README.md)를 참고하세요.

### 손으로 확인해야 하는 것

| 확인할 것 | 방법 |
|---|---|
| 접속 없으면 자동 기록 | 인자 없이 띄우고 2분 건드리지 않는다 → `recording` |
| 접속하면 대기 | 60초 안에 `/api/status`를 부른다 → `maintenance` |
| 안정화 판정 | 기록 중 흔든다 → 로그에 사유, 블록에 플래그 |
| **전원 차단 복구** | 기록 중 **전원을 뽑는다** → 다음 부팅에서 `.partial`이 떨어지고 헤더에 복구 표식 |
| 경로 검증 | `/api/files/../../etc/passwd` → 404 |

전원 차단은 **정상 종료로는 그 경로를 타지 않습니다.** 반드시 케이블을 뽑으세요.

---

## 10. 문제 해결

### `ModuleNotFoundError: pymodbus`

시스템 `python`으로 실행한 경우입니다. `.venv/bin/python`을 쓰세요.

### `/dev/serial0`이 없음

[3. 설치](#3-설치)의 시리얼 포트 열기를 실행하고 재부팅하세요.

### 센서 연결 실패

- 기록기가 포트를 쓰고 있지 않은지 (`devmode.sh` 사용)
- `dialout` 그룹에 들어 있는지 (`usermod` 후 재로그인 필요)
- 8·10번 핀(GPIO14/15) 결선과 RS-485 A/B 극성

### e-paper가 안 그려짐

- `dtparam=spi=on`이 `/boot/firmware/config.txt`에 있는지
- `spi` / `gpio` 그룹에 들어 있는지
- 한글이 네모로 나오면 `sudo apt install -y fonts-nanum`

**패널이 없어도 기록은 계속됩니다.** 로그에 경고만 남습니다.

### `.local` 주소로 접속이 안 됨

통신사 DNS가 없는 도메인에 엉뚱한 주소를 돌려주면서 mDNS 응답이 묻힙니다
(실측에서 `218.38.137.28`로 해석됐습니다). 장비 쪽 avahi는 정상입니다.
**IP를 쓰세요.**

### WiFi가 바뀌어 아예 접속할 수 없음

화면도 키보드도 없어 SSH도 못 들어갑니다. **블루투스로 Wi-Fi를 다시 붙일 수
있습니다** — [bt/README.md](bt/README.md). 그래서 `disable-bt`가 아니라
`miniuart-bt`를 씁니다.

### 웹이 응답하는지 빠르게 보기

```bash
curl localhost:8080/healthz
```

이 요청은 운영자 접속으로 세지 않으므로 자동 기록을 막지 않습니다.

> **무응답 감시(워치독)는 두지 않았습니다.** HTTP 응답 확인은 이 시스템에서
> 잘못된 지표입니다 — 웹과 기록이 별도 스레드라 기록이 멈춰도 웹은 200을
> 돌려주고, 반대로 웹만 막히면 멀쩡한 기록을 재시작해 버립니다. systemd의
> `Restart=always`가 프로세스 사망만 처리합니다.

### 부팅이 느림

첫 화면까지 걸리는 시간은 OS 부팅과 서비스 시작에 따라 달라집니다.
평소보다 부팅이 느리면 아래 명령으로 지연 구간을 확인합니다.

```bash
systemd-analyze
systemd-analyze critical-chain verticrane-recorder.service
```

`install.sh`가 cloud-init을 끄고(`/etc/cloud/cloud-init.disabled`), 유닛에서
기록 서비스는 네트워크 준비 완료를 기다리지 않습니다. 네트워크 대기는
서비스 내부에서 설치 유예 이후에 처리합니다.
