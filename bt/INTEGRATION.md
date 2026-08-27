# BLE Wi-Fi 설정 — verticrane 적용 메모

원본: `pi-bt-wifi-setup` 프로젝트. 이 폴더는 그 코드를 verticrane에 맞춰 들여온 것이다.
프로토콜과 사용법은 [README.md](README.md)를 그대로 보면 된다. 여기에는 **이 프로젝트에
붙이면서 달라진 것과, 붙일 때 걸렸던 것**만 적는다.

## 왜 필요한가

차량 WiFi의 SSID나 비밀번호가 바뀌면 장비가 네트워크에 못 붙고, 그러면 **웹으로 접속할
수단이 사라진다.** 화면도 키보드도 없는 장비라 SSH도 못 들어간다. BLE는 그 상황에서
Wi-Fi를 다시 붙여주기 위한 **설정 채널 전용**이다. 측정 데이터는 여전히 Wi-Fi로 간다.

## 부팅 설정을 바꿔야 했다 (중요)

verticrane는 센서를 위해 블루투스를 **꺼놓고 있었다.**

```
dtoverlay=disable-bt      # 이전
dtoverlay=miniuart-bt     # 지금
```

Zero 2 W는 성능이 좋은 PL011을 블루투스가 가져가고 GPIO에는 mini UART를 준다. mini UART는
보레이트가 코어 클럭에 묶여 있어 **115200 Modbus에서 프레임이 깨진다.** 그래서 원래는
블루투스를 떼어내고 PL011을 GPIO로 돌렸다.

`miniuart-bt`는 **반대로 한다** — 센서는 PL011을 그대로 쓰고, 블루투스만 mini UART로
옮긴다. BLE는 대역폭이 작아 mini UART로 충분하다.

실측으로 확인한 것:

| | 결과 |
| --- | --- |
| `/dev/serial0` | `ttyAMA0` (그대로, 코드 수정 없음) |
| 센서 200회 폴링 | **성공 200 / 실패 0** |
| Modbus 트랜잭션 | 6.2 ms (25 Hz 폴링 주기 40 ms에 여유) |
| 블루투스 어댑터 | `hci0 UP RUNNING` |
| 코어 클럭 | 400 MHz |

되돌리려면 `/boot/firmware/config.txt.bak-bt`가 있다.

## 원본에서 바꾼 것

| 바꾼 것 | 이유 |
| --- | --- |
| 광고 이름을 **hostname**으로 | MAC 뒷자리(`Pi-BLE-15EE`)로는 한 크레인의 세 대를 구분할 수 없다. hostname은 파일명의 SENSOR_ID·웹 제목·e-paper 상단과 같은 문자열이다 |
| Pretendard 폰트(2 MB) 제외 | 저장소에 폰트 바이너리를 넣지 않는다. 이름으로만 부르므로 설치돼 있으면 쓰이고, 없으면 시스템 글꼴로 떨어진다. 한글이 안 나오는 경우는 없다 |
| systemd 경로를 `/home/pi/verticrane/bt` 로 | |
| 프로젝트 `.venv` 사용 | `--system-site-packages`로 만들어져 있어 apt의 `python3-dbus`/`python3-gi`가 그대로 보인다 |

## 기록기와의 관계

**충돌하지 않는다.** BLE는 시리얼 포트를 쓰지 않고, 별개 프로세스이며, 이 서비스가
죽어도 측정은 계속된다. 반대로 기록기가 죽어도 BLE는 살아 있어 Wi-Fi를 고칠 수 있다.

다만 **root로 돈다.** 광고 등록(mgmt 소켓)과 `nmcli` 설정 변경 둘 다 권한이 필요하다.
기록기는 `pi`로 돈다.

## 설치

```bash
sudo apt install -y bluez python3-dbus python3-gi
cd ~/verticrane && .venv/bin/pip install "bluezero==0.9.1"

sudo cp bt/systemd/pi-bt-wifi-setup.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pi-bt-wifi-setup
```

`python3-dbus`와 `python3-gi`는 **apt로만 설치된다.** pip으로 넣으려 하면 PyGObject
빌드에서 실패한다.

## 쓰는 법

PC/폰 Chrome에서 `bt/web/index.html`을 열고(또는 `python bt/web/serve.py`로 띄우고),
**장비 연결 → 검색 → SSID 선택 + 비밀번호 → 연결 → IP 확인**.

Web Bluetooth가 필요하므로 **Chrome 계열**에서만 동작한다(Safari 불가).
