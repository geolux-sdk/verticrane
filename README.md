# Verticrane 기울기 기록 시스템

크레인의 수직도를 재는 무인 기록 장치입니다. **Raspberry Pi Zero 2 W**에
**HWT9037-485**(9축 IMU)를 RS-485로 붙여, 전원만 넣으면 스스로 25 Hz로 기록을
시작하고 운영자는 브라우저로 파일을 받아 갑니다. 한 크레인에
**BASE / MIDDLE / TOP** 세 대를 답니다.

**이 문서는 개발자용입니다.** 현장에서 쓰는 법은
[doc/운영자_안내서.md](doc/운영자_안내서.md)를 보세요.

| 문서 | 내용 |
|---|---|
| [TILT_기록시스템_구현요구사항.md](TILT_기록시스템_구현요구사항.md) | **설계 근거.** 무엇을 왜 그렇게 정했는지. 코드를 고치기 전에 읽으세요 |
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
| `read_status.py` | 센서 연결·상태 읽기 **(recorder가 사용)** |
| `hwt9037_485.py` | 장치 모델 (Modbus RTU) |
| `port_config.py` | 시리얼 포트 결정 |
| `app_config.py` | 설정·PIN·로깅 (`config.json`) |
| `verticrane-recorder.service` | systemd 유닛 |
| `install.sh` `install_requirements.sh` `update.sh` `test.sh` | 설치·배포·점검 |

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

---

## 2. 동작 개요

```
전원 --11초--> recorder --60초--> 설치 유예 --60초--> 접속 대기 --> 안정화 --> 기록
                                                        |
                                                   접속하면 --> maintenance
```

상태는 다섯입니다.

| 상태 | 의미 |
|---|---|
| `waiting_mount` | 설치 유예. 크레인에 오르는 시간 |
| `waiting_http` | 접속 대기. 네트워크가 있을 때만 |
| `waiting_stable` | 센서 안정화 대기 |
| `recording` | 기록 중 |
| `maintenance` | 접속이 있었다. 자동 기록하지 않는다 |

**기록을 시작하는 길은 자동 하나뿐입니다.** 웹에 시작 버튼이 없습니다 — 페이지에
닿았다는 것 자체가 "사람이 옆에 있다"는 뜻이고 그건 자동 기록을 억제하는 조건이라,
시작을 요청하는 것은 자기 존재가 부정하는 일을 해 달라는 뜻이 됩니다 (요구사항 §3).

**기록 중에 유효한 HTTP 요청이 오면 그 기록은 휴지통으로 갑니다.** 상태 페이지의
자기 갱신은 `?auto=1`을 달아 예외로 둡니다.

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

```bash
# systemd (상시 가동)
systemctl status verticrane-recorder
journalctl -u verticrane-recorder -f

# 직접 실행 — 서비스를 먼저 멈춰야 합니다 (시리얼 포트가 하나뿐)
./dev/devmode.sh .venv/bin/python recorder.py
```

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
> 세어집니다.** 자동 기록이 억제되고, 기록 중이었다면 그 기록이 휴지통으로 갑니다.
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
나갔다는 뜻일 뿐입니다. 그래서 파일을 은퇴시키는 것은 다운로드가 아니라 브라우저가
보내는 `/collected`입니다. 중간에 끊기면 이 요청이 오지 않아 파일이 목록에 남습니다.

### 제어

| | |
|---|---|
| `POST /api/record/stop` | 기록 중지 및 파일 확정 |
| `GET /settings` `POST /settings` | 설정 화면 (저장에 PIN 필요) |

**시작하는 API는 없습니다** ([2. 동작 개요](#2-동작-개요) 참조).

### 한 번에 받아오기 — `collect.py`

사람이 트리거하면 목록 조회부터 회수 확인까지 알아서 끝냅니다. 표준 라이브러리만
쓰므로 현장에 들고 갈 노트북에 설치할 것이 없습니다.

```bash
python collect.py 192.168.0.19                       # 한 대
python collect.py 10.0.0.11 10.0.0.12 10.0.0.13      # 크레인 한 대 분(3기)
python collect.py 192.168.0.19 --out ./2026-08-28    # 받을 폴더 지정
python collect.py 192.168.0.19 --keep                # 받되 장비에서 은퇴시키지 않음
```

```
192.168.0.19
  pi-tilt001 [BASE]  상태 recording
     기록 중이던 파일은 휴지통으로 들어갑니다 (§3). 이 목록에는 없습니다.
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
바이트가 소켓에 나갔다는 뜻일 뿐), 파일을 은퇴시키는 것은 **받은 쪽이 디스크에
있음을 확인하고 보내는 4번뿐**입니다. 중간에 끊기면 4번이 안 가고 파일은 장비에
그대로 남습니다.

장비별 하위 폴더(`verticrane-data/pi-tilt001/`)로 나눠 받으므로 한 크레인의 세 대를
한 폴더에 모아도 섞이지 않습니다.

### 파일명 검증

경로 구분자와 `..`를 허용하지 않고 `FLAG_NNN.dat` 정규식에 정확히 맞는 것만
받습니다. 통과한 뒤에도 열기 전에 `realpath`로 `data/` 하위인지 다시 확인합니다.

---

## 6. 파일 포맷

`.dat` — 64바이트 헤더 + 1232바이트 고정 블록의 반복. 자세한 것은 요구사항 §5.

### 이름

```
FLAG_NNN.dat            TOP_003.dat
FLAG_NNN.dat.partial    기록 중
```

**이름은 카드 위의 자리이지 내용 설명이 아닙니다.** 시각도 복구 여부도 들어 있지
않고 전부 헤더 안에 있습니다. 슬롯은 `000`~`999`를 돌며 **덮어씁니다** — 장비는
노트북으로 옮기기 전의 버퍼이지 저장소가 아닙니다.

> 이름에 사실을 적으면 사실이 바뀔 때 이름을 바꿔야 하고, 그 이름을 읽는 코드
> 전부가 철자에 합의해야 합니다. 접미사 순서에 대한 불일치 하나가 멀쩡한 기록을
> 카드에 남겨 둔 채 운영자 목록에서 지운 적이 있습니다.

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

**오프셋 16이 시각의 전부이고 아무것도 이것을 고치지 않습니다.** 장비는 자기
시계가 맞는지 확인할 방법이 없어 등급을 매기지 않습니다 — 시각 품질도, 소급
보정도, 사이드카도 없습니다 (요구사항 §3).

목록 정렬과 연속 그룹 병합이 이 값을 쓰는데 **차이만** 쓰므로 절대값이 틀려도
성립합니다. 한 카드의 모든 기록이 같은 시계에서 나왔기 때문입니다.

헤더를 다시 쓰는 곳은 **복구 단계 하나뿐**입니다. 그때 파일은 아직 확정되지 않았고
읽거나 덧붙이는 것이 없습니다.

### 블록 (1232 B)

25샘플 묶음. 샘플마다 12개 float — Roll/Pitch/Yaw, 가속도 3축, 자이로 3축, 자기 3축.
블록마다 CRC32와 상태 플래그가 붙습니다.

| 플래그 | 뜻 |
|---|---|
| bit 0 | 이 블록을 채우는 동안 시리얼 읽기 실패 |
| bit 1 | 시리얼 링크 재접속 |
| bit 2 | 안정화 기준을 벗어난 구간 |

**블록의 경과 시간은 단조 시계 기준이라 언제나 정확합니다.** 시계를 못 믿어도
"기록 시작 후 3분 12초"는 맞습니다. 못 믿는 것은 몇 월 며칠인지뿐입니다.

### CSV 변환

```bash
.venv/bin/python dev/ahrsbin_to_csv.py data/TOP_003.dat
.venv/bin/python dev/ahrsbin_to_csv.py data/*.dat --report
```

---

## 7. 설정

`config.json`의 `recorder` 섹션. 전체 목록과 근거는 요구사항 §10.

**웹 화면에서 바꾸는 것** — 운영자가 현장에서 정해야 하는 값

`sensor_flag` · `mount_delay_seconds` · `http_wait_seconds` ·
`network_wait_seconds` · `segment_minutes` · `delete_after_download` ·
`trash_retention_days` · `min_free_mb`

**파일에서만 바꾸는 것** — 공학적으로 정해진 값

`stability_window_seconds` · `stability_min_samples` · `gyro_rms_max_dps` ·
`accel_std_max_g` · `attitude_std_max_deg` · `stop_on_unstable` ·
`record_fsync_interval_seconds` · `merge_gap_tolerance_seconds` ·
`time_save_interval_seconds` · `ip_check_interval_seconds` · `contact_face` ·
`panel_refresh_seconds` · `panel_rotation` · `http_port`

> **안정화 기준을 화면에 두지 않은 이유:** 손으로 만지면 기록이 아예 시작되지
> 않거나 흔들리는 중에 시작됩니다. 화면에 있는 설정은 언젠가 누군가 바꿉니다.

`panel_refresh_seconds`는 **측정 화면에만** 적용됩니다. 나머지 화면은 이벤트로만
그립니다. **초 단위로 낮추지 마세요** — 갱신 1회가 1.4초이고 패널 수명이 갱신
횟수로 정해집니다 (1초 주기면 약 2주에 소진).

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
| `eink_status.py` `eink_test.py` | 패널 시험 |
| `test.py` | 대시보드 자가 점검 |
| `install_requirements.bat` | Windows 개발 환경 |

### e-paper 미리보기 — 하드웨어 없이

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
python test_ahrs_file.py     # 포맷: 쓰기 → 자르기 → 복구 → 복구 표식 → 병합 → 슬롯 이름
python test_stability.py     # 판정: 0/360 경계, 윈도우 경계, 움직임 거부
python test_eink_panel.py    # 패널: 상태→화면, 갱신 예약, 다섯 화면 렌더링
```

### 장비에서

```bash
./test.sh                    # 위 셋 + 센서 통신 점검
```

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

전원에서 첫 화면까지 약 11초입니다. 그보다 오래 걸리면:

```bash
systemd-analyze
systemd-analyze critical-chain verticrane-recorder.service
```

`install.sh`가 cloud-init을 끄고(`/etc/cloud/cloud-init.disabled`), 유닛에서
`After=network.target`을 뺍니다. 둘 다 되돌리면 10초가 더 붙습니다.
