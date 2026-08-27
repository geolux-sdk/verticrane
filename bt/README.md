# Pi Wi-Fi 프로비저닝 — BLE 로 Wi-Fi 붙이기

라즈베리 파이 Zero 2 W 를 **BLE Peripheral(GATT Server)** 로 띄워, 브라우저(PWA)에서
**Wi-Fi SSID 를 고르고 비밀번호를 넣고 할당된 IP 를 확인**하는 장치 설정용 프로젝트.

실사용 데이터 통신은 **Wi-Fi** 로 한다. BLE 는 그 Wi-Fi 를 붙여주기 위한
**설정 채널 전용**이다(화면·키보드 없는 장비의 초기 설정 문제 해결).

- **Pi 역할**: Peripheral / GATT Server (Pi 가 광고 → 브라우저가 연결)
- **클라이언트**: 설치형 PWA (Web Bluetooth) — Windows/데스크톱·안드로이드 Chrome
- **전송 규격**: Nordic UART Service(NUS) 위에 **줄 단위 JSON 프로토콜**
- **Wi-Fi 제어**: NetworkManager(`nmcli`) — Debian 12/13 기본
- **라이브러리**: [bluezero](https://github.com/ukBaz/python-bluezero) (BlueZ D-Bus 래퍼)

## 구성

| 파일/폴더 | 내용 |
|---|---|
| `ble_peripheral.py` | BLE GATT 서버 + 프레이밍 + 명령 디스패치 — **Pi 에서 실행** |
| `wifi_manager.py` | `nmcli` 래퍼(스캔·연결·상태·삭제). BLE 와 무관하게 단독 실행 가능 |
| [`web/`](web/) | Web Bluetooth 클라이언트(PWA) — **PC/폰 Chrome 에서 실행** ([web/README.md](web/README.md)) |

흐름: Pi 에서 `ble_peripheral.py` 실행 → PC Chrome 에서 `web/` 앱 열기 →
**장비 연결 → 검색 → SSID 선택 + 비밀번호 → 연결 → IP 확인**.

## 서비스 / 캐릭터리스틱

| 이름 | UUID | 속성 | 방향 |
|---|---|---|---|
| NUS Service | `6E400001-…` | — | — |
| RX | `6E400002-…` | Write | 브라우저 → Pi |
| TX | `6E400003-…` | Notify | Pi → 브라우저 |

## 프로토콜 (PROTO_VERSION = 4)

한 메시지 = **JSON 한 줄 + `\n`**. 양쪽 모두 **20바이트씩** 쪼개 보내고, 받는 쪽에서
`\n` 이 나올 때까지 이어 붙인다. ATT MTU 협상 결과와 무관하게 안전한 크기가 20바이트라
고정했다(스캔 결과처럼 1KB 넘는 메시지도 그대로 오간다).

**호스트 → Pi**

| 명령 | 설명 |
|---|---|
| `{"cmd":"scan"}` | 주변 AP 검색(`nmcli dev wifi list --rescan yes`) |
| `{"cmd":"status"}` | 현재 상태/IP 조회 |
| `{"cmd":"connect","ssid":"..","psk":"..","hidden":false}` | 접속 시도 |
| `{"cmd":"saved","ssid":".."}` | 저장된 비밀번호의 **길이만** 조회(화면에 ● 를 그 수만큼 표시) |
| `{"cmd":"secret","ssid":".."}` | 저장된 비밀번호 **평문** 조회 — 사용자가 [표시] 를 누를 때만 |
| `{"cmd":"forget","ssid":".."}` | 저장된 프로필 삭제(비번 변경된 AP 재설정용) |
| `{"cmd":"disconnect"}` | Wi-Fi 연결만 해제 |
| `{"cmd":"ping"}` | **keepalive** — 웹앱이 1초마다 자동 전송. 응답 없음(단방향) |

**Pi → 호스트**

| 이벤트 | 설명 |
|---|---|
| `{"ev":"hello","name","fw","proto"}` | 구독 직후 신원 |
| `{"ev":"hb","n":N}` | 2초 주기 하트비트(웹앱 워치독용) |
| `{"ev":"status", …}` | `iface,state,connected,ssid,ip,gateway,dns,mac,hostname` |
| `{"ev":"scan","nets":[{"s","q","k","c"}]}` | SSID·신호(0~100)·잠금·현재연결 |
| `{"ev":"progress","msg"}` | 진행 상황(연결 중 등) |
| `{"ev":"result","cmd","ok","msg"}` | 명령 종료(성공/실패) |

JSON 이 아닌 원문을 보내면 `{"ev":"echo","msg":…}` 로 되돌려준다(디버그용).

> `nmcli` 는 수 초~수십 초 블로킹이라 **워커 스레드**에서 돌린다. GLib 메인루프가
> 멈추지 않아야 하트비트가 계속 나가고, 웹앱이 좀비 연결로 오판하지 않는다.

### 연결 유지 — 양방향 워치독

BLE 링크는 양쪽 다 모르게 죽을 수 있다. 그래서 **양방향 모두 주기적으로 데이터를
흘리고**, 상대의 침묵을 링크 사망으로 판정한다.

| 방향 | 주기 | 감시하는 쪽 | 침묵 판정 | 판정 시 동작 |
|---|---|---|---|---|
| Pi → 웹 (`ev:hb`) | 2초 | 웹앱 | 8초 | 스스로 `gatt.disconnect()` → 자동 재연결 루프 |
| 웹 → Pi (`cmd:ping`) | 1초 | Pi | 5초 | 호스트를 강제 해제 → **광고 재등록**(워치독이 처리) |

Pi 쪽 감시가 필요한 이유: 호스트가 사라진 걸 `bluetoothd` 가 모르면 연결이 살아 있는
것으로 남고, **connectable 광고는 연결 중엔 꺼져 있으므로 아무도 그 장비를 찾지 못한다.**

`_rx_watchdog()` 은 1초마다 **"지금 상태가 맞는지" 를 맞춰 놓는 역할**을 한다.
사건이 일어난 자리에서 광고를 올리지 않고 이 한 곳에서만 처리하므로, 등록이 한 번
실패해도 다음 초에 저절로 복구된다.

- 호스트가 붙어 있는데 5초간 조용하다 → 강제로 끊는다(뒷정리는 `_reset_link()` 한 곳)
- 붙어 있는 호스트가 없는데 광고가 안 떠 있다 → 다시 올린다

`ping` 에는 **답하지 않는다.** 받았다는 사실 자체가 목적이고, 답장하면 20바이트 청크
3개가 매초 되돌아가 링크만 좀먹는다. 웹앱은 반대로 **쓰기 성공** 을 링크 생존의
증거로 삼고, 모든 전송이 지나는 `writeLine()` 에서 3초를 넘기면 곧바로 끊는다
(keepalive 뿐 아니라 `scan`·`connect` 같은 명령도 같은 보호를 받는다).

## 라즈베리 파이 설정 (Pi 에서 실행)

```bash
# 1) 시스템 패키지 (BlueZ + Python D-Bus/GObject 바인딩 + NetworkManager)
sudo apt update
sudo apt install -y bluez python3-dbus python3-gi python3-pip network-manager

# 2) 블루투스 서비스 확인
sudo systemctl status bluetooth      # active (running) 여야 함
sudo btmgmt info                     # 어댑터(hci0)와 current settings 확인

# 3) Wi-Fi 가 NetworkManager 관리인지 확인
nmcli device                         # wlan0 이 목록에 보여야 함

# 4) bluezero 설치
#    시스템 dbus/gi 패키지를 함께 쓰려면 venv 는 --system-site-packages 로:
python3 -m venv --system-site-packages venv
pip install -r requirements.txt      # (또는 ./venv/bin/pip)
```

## 실행

```bash
# 광고(mgmt 소켓)와 Wi-Fi 설정 변경 둘 다 권한이 필요하므로 sudo(root) 로 실행
sudo ./venv/bin/python ble_peripheral.py
```

`[ADV] 광고 등록 성공` → `BLE 광고 시작…` 이 뜨면 준비 완료. 종료는 `Ctrl+C`.

### 자동 시작 (systemd)

실사용 장비는 부팅과 동시에 광고가 떠 있어야 한다. 유닛 파일은
[`systemd/pi-bt-wifi-setup.service`](systemd/pi-bt-wifi-setup.service) 에 있다.

```bash
sudo install -m 644 systemd/pi-bt-wifi-setup.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pi-bt-wifi-setup
```

| 명령 | 용도 |
|---|---|
| `systemctl status pi-bt-wifi-setup` | 상태 확인 |
| `journalctl -u pi-bt-wifi-setup -f` | 실시간 로그(옛 `/tmp/ble.log` 대체) |
| `sudo systemctl restart pi-bt-wifi-setup` | 코드 갱신 후 재시작 |
| `sudo systemctl disable --now pi-bt-wifi-setup` | 자동 시작 해제(수동 실행으로 되돌릴 때) |

- `Restart=always` — 프로세스가 죽으면 3초 뒤 되살린다. 죽은 채로 두면 광고가
  사라져 **아무도 장비를 찾지 못하기** 때문이다.
- `ExecStartPre` 로 `rfkill unblock bluetooth` 를 먼저 실행한다(부팅 직후 soft
  block 이면 광고 등록이 실패). 어댑터 전원은 코드가 D-Bus 로 직접 켠다 —
  `btmgmt`(CLI)는 tty 가 없으면 멈추므로 유닛에서 쓰지 않는다.
- 손으로 실행해 디버깅할 때는 서비스를 먼저 멈춰야 한다(어댑터를 두 프로세스가
  동시에 쓰면 광고 인스턴스가 충돌한다): `sudo systemctl stop pi-bt-wifi-setup`

Wi-Fi 부분만 BLE 없이 확인하려면:

```bash
sudo ./venv/bin/python wifi_manager.py status
sudo ./venv/bin/python wifi_manager.py scan
sudo ./venv/bin/python wifi_manager.py connect <SSID> [비밀번호]
sudo ./venv/bin/python wifi_manager.py forget <SSID>
```

## Wi-Fi 연결 동작

1. `nmcli -w 45 device wifi connect <SSID> [password <PSK>] [hidden yes] ifname wlan0`
2. **인증 실패이고 그 SSID 의 저장 프로필이 남아 있으면** — 옛 비밀번호가 원인인
   전형적인 경우다. 프로필을 지우고 **한 번만** 재시도한다.
3. 성공하면 그 프로필에 `autoconnect yes`, `autoconnect-priority 10` 을 걸어
   **재부팅 후에도 이 네트워크로 먼저 붙게** 한다.
4. IP 를 못 받으면(DHCP 실패) 연결은 됐어도 실패로 보고한다.

## 동작 원리 — 광고 우회 (중요)

이 컨트롤러(BCM43430, BT 4.x)는 `bluetoothd` 가 사용하는 **확장 광고(Extended
Advertising)** mgmt 명령을 거부한다(`Add Ext Adv Data` → Invalid Parameters).
그래서 bluezero 의 `publish()` 로 광고를 등록하면 실패한다. **GATT 서버 등록은 정상.**

`ble_peripheral.py` 는 이를 우회한다:

1. bluezero 로 **GATT 앱만** 등록 (`register_application`)
2. 광고는 **커널 mgmt control 소켓에 직접** `ADD_ADVERTISING(0x003e)` 전송
   (구형 `LE Set Advertising Data` 경로 → 이 컨트롤러에서 동작)
3. 광고 간격은 debugfs(`adv_min/max_interval`)로 **100ms** 로 단축(빠른 발견)
4. 로컬 이름을 **주 광고 데이터**에 실음 — 일부 Windows/Chrome 스택이 스캔응답의
   이름을 못 잡는 문제 회피

> 참고: `btmgmt` CLI 는 tty 없이(nohup/백그라운드) 실행하면 멈추므로 subprocess 로
> 쓰지 않고, Python 에서 mgmt 소켓을 직접 연다. (이 Python 빌드의 socket 모듈이
> HCI channel bind 를 지원하지 않아 `ctypes` 로 `libc.bind()` 를 직접 호출한다.)

## 보안 주의

**Wi-Fi 비밀번호가 BLE 링크로 평문 전송된다.** 현재 구성은 페어링/본딩 없이
암호화되지 않은 LE 링크를 쓰므로, 근처에서 스니핑하면 PSK 가 노출될 수 있다.
설정 시점에만 쓰는 채널이라 위험 노출 시간은 짧지만, 운영 환경에서 문제가 되면
다음 중 하나가 필요하다:

- 캐릭터리스틱에 `encrypt-write` 플래그 + LE Secure Connections 본딩 요구
- 또는 앱 레벨 키 교환(ECDH)으로 PSK 만 별도 암호화

## 문제 해결

- **어댑터를 못 찾음 / 전원 꺼짐**: `sudo btmgmt power on`
  (이 이미지엔 `rfkill` 명령이 없을 수 있음 — soft block 도 대개 `btmgmt power on` 으로 해제됨)
- **광고 등록 실패(Invalid Parameters)**: 위 "광고 우회" 참고 — 이미 코드에 반영됨
- **연결됐다 바로 끊김**: peripheral 이 실제로 떠 있는지 확인.
  광고 인스턴스는 프로세스가 죽어도 커널에 남을 수 있어(유령 광고), GATT 서버 없이
  연결되면 즉시 끊긴다. 재시작 전 `sudo btmgmt rm-adv 1` 로 정리.
- **`nmcli 가 없습니다`**: NetworkManager 미설치/미사용. `network-manager` 설치 후
  `nmcli device` 에 wlan0 이 보이는지 확인(dhcpcd/netplan 단독 구성이면 이관 필요).
- **연결은 됐는데 IP 가 없음**: 공유기 DHCP 문제이거나 802.1X(기업용) 네트워크.
  `WPA2 802.1X` 로 표시되는 AP 는 ID/인증서가 더 필요해 이 앱으로는 붙지 않는다.
- **권한 오류**: `sudo`(root)로 실행했는지 확인 (mgmt 소켓/광고/`nmcli` 설정 변경에 필요)

## 참고

- 개발은 Windows(`d:\python\pi-bt-wifi-setup`)에서 하고 코드는 scp 로 Pi 에 복사해
  실행한다. Pi 쪽 경로는 `~/pi-bt-wifi-setup`.
  (Windows 에서는 BLE Peripheral 실행 불가 — Pi 에서만 동작)
- 프로토콜을 바꾸면 `ble_peripheral.py` 의 `PROTO_VERSION` 과
  `web/index.html` 의 `PROTO_VERSION` 을 함께 올린다. 다르면 웹앱이 경고를 띄운다.
