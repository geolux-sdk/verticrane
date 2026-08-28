#!/usr/bin/env python3
"""
라즈베리 파이 - BLE Wi-Fi 프로비저닝 Peripheral (GATT Server)

역할:
  실사용 통신은 Wi-Fi 로 한다. BLE 는 그 Wi-Fi 를 붙여주기 위한
  "설정 채널" 전용이다:
    1) 주변 SSID 목록을 호스트(웹앱)에 보여주고
    2) 호스트가 고른 SSID + 비밀번호를 받아 접속하고
    3) 받은 IP 주소를 호스트에게 알려준다.

전송 규격:
  Nordic UART Service(NUS) 위에 줄 단위 JSON 프로토콜을 얹었다.
  한 메시지 = JSON 한 줄 + '\\n'. 양쪽 모두 20바이트씩 쪼개 보내고
  받는 쪽에서 '\\n' 이 나올 때까지 이어 붙인다. (ATT MTU 협상 결과와
  무관하게 안전한 크기가 20바이트라서 고정.)

  연결 유지:
    양방향 모두 주기적으로 데이터를 흘려 링크 사망을 빨리 알아챈다.
    Pi -> 호스트 는 하트비트(hb, 2초), 호스트 -> Pi 는 keepalive(ping, 1초).
    어느 쪽이든 정해진 시간 동안 수신이 없으면 상대가 사라진 것으로 보고
    링크를 스스로 끊는다. Pi 는 끊은 뒤 광고를 다시 올려 재연결을 받는다.
    ping 에는 답장하지 않는다 — 받았다는 사실 자체가 목적이다.

  호스트 -> Pi : {"cmd":"scan"} {"cmd":"status"} {"cmd":"ping"}
                 {"cmd":"connect","ssid":"..","psk":"..","hidden":false}
                 {"cmd":"forget","ssid":".."} {"cmd":"disconnect"}
  Pi -> 호스트 : {"ev":"hello",..} {"ev":"hb","n":N} {"ev":"scan","nets":[..]}
                 {"ev":"status",..} {"ev":"progress","msg":".."}
                 {"ev":"result","cmd":"..","ok":true|false,"msg":".."}

실행:
  sudo python3 ble_peripheral.py
  (BLE 광고와 nmcli 설정 변경 모두 권한이 필요하므로 sudo 로 실행)
"""

import ctypes
import json
import os
import queue
import socket
import struct
import threading
import time
from collections import deque

# 아래 셋은 Pi(리눅스)에만 있는 모듈이라, 개발용 Windows 에서는 설치되지 않는다.
# 편집기(Pylance)가 "import 를 찾을 수 없음" 으로 표시하는 것이 정상이므로 무시한다.
# 실행은 Pi 에서만 하며, 거기서는 venv --system-site-packages 로 모두 잡힌다.
from bluezero import adapter        # type: ignore
from bluezero import peripheral     # type: ignore
from gi.repository import GLib      # type: ignore

import wifi_manager

# ---------------------------------------------------------------------------
# Nordic UART Service (NUS) UUID
# ---------------------------------------------------------------------------
NUS_SERVICE = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
NUS_RX_CHAR = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # 호스트 -> Pi (Write)
NUS_TX_CHAR = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # Pi -> 호스트 (Notify)

# 광고 이름. 장비가 여러 대여도 구분되도록 main() 에서 어댑터 MAC 뒷자리를
# 붙여 "Pi-BLE-3A7F" 처럼 개체별 고유 이름으로 바꾼다. (호스트 웹앱은
# "Pi-BLE" prefix 로 필터하므로 prefix 는 유지할 것.)
LOCAL_NAME = "Pi-BLE-Test"  # 기본값 — main() 에서 교체됨

PROTO_VERSION = 4           # 웹앱과 맞춰야 하는 프로토콜 세대
FW_VERSION = "2026-08-11.4"

# 프레이밍
CHUNK = 20                  # 한 번의 Notify/Write 로 안전하게 실어보낼 바이트 수
TX_INTERVAL_MS = 10         # 청크 사이 간격 (약 2KB/s)
RX_BUF_LIMIT = 8192         # '\n' 없이 이만큼 쌓이면 쓰레기로 보고 버림

# 하트비트 — 웹앱 워치독이 이걸 못 받으면 좀비 연결로 판단해 재연결한다.
HEARTBEAT_MS = 2000
# 화면의 Wi-Fi 정보를 알아서 최신으로 유지하는 주기(사용자 조작 없이).
STATUS_REFRESH_S = 15

# 호스트 생존 감시(RX 워치독). 웹앱이 1초마다 keepalive({"cmd":"ping"})를
# 보내므로, 연결 상태인데 이만큼 아무것도 안 들어오면 링크가 죽은 것이다.
# 링크가 어정쩡하게 죽으면 bluetoothd 가 끊김을 알려주지 않아 광고가 꺼진
# 채로 남는다 — 그러면 아무도 이 장비를 찾지 못한다. 그래서 직접 끊고
# 광고를 다시 올린다.
RX_TIMEOUT_S = 5.0

# 광고를 여는 시간. 이 창이 닫히면 아무도 장비를 찾을 수 없고, 다시 열려면
# 전원을 껐다 켜야 한다.
#
# 상시로 두지 않는 이유: 이 채널에는 페어링도 암호화도 없다. 근처의 누구나 Wi-Fi
# 설정을 바꿔 **장비를 존재하지 않는 망으로 옮겨버릴 수 있고**, 그러면 화면도
# 키보드도 없는 크레인 위 장비에서 데이터를 가져올 방법이 사라진다. 되돌릴 유일한
# 경로가 방금 공격에 쓰인 그 BLE다.
#
# README 는 원래 "설정 시점에만 쓰는 채널이라 위험 노출 시간은 짧다"고 적었는데,
# 서비스가 systemd 로 상시 기동되어 사실이 아니었다. 이 창이 그 문장을 사실로
# 만든다.
ADV_WINDOW_S = float(os.environ.get("VERTICRANE_BLE_WINDOW_S", "180"))
RX_WATCHDOG_S = 1
# 연결 직후에는 호스트가 아직 아무것도 못 보낸다 — 서비스 검색과 알림 구독을
# 끝내야 첫 keepalive 를 보낼 수 있고, 그 준비가 5초를 넘길 때가 있다(특히 GATT
# 캐시가 없는 첫 연결). 그동안 워치독이 물면 정상 연결이 끊기고, 호스트는 다시
# 붙었다 또 끊기는 루프에 빠진다. 그래서 구독 전까지는 더 길게 기다린다.
CONNECT_GRACE_S = 20.0

_state = {
    "tx_char": None,     # 알림을 보낼 TX Characteristic 객체
    "notifying": False,  # 호스트가 TX 구독(subscribe) 중인지 여부
    "window_ends": 0.0,  # 이 시각이 지나면 광고를 내린다 (monotonic)
    "window_shut": False,  # 이미 내렸다 — 로그를 한 번만 남기려고
    "beat": 0,           # 하트비트 카운터
    "device": None,      # 현재 연결된 호스트(bluezero Device) — 없으면 None
    "last_rx": 0.0,      # 마지막 수신 시각(monotonic). 0 이면 호스트 없음
    "adv_dirty": False,  # 광고를 다시 올려야 하는 상태인지(워치독이 해소)
}

_tx_queue = deque()      # 보낼 청크들
_tx_timer = None         # 큐를 비우는 GLib 타이머 id
_rx_buf = bytearray()    # 수신 조립 버퍼

_jobs = queue.Queue()    # 워커 스레드에 넘길 작업
_busy_lock = threading.Lock()
_busy = None             # 현재 워커가 처리 중인 명령 이름(없으면 None)


# ---------------------------------------------------------------------------
# 송신: JSON 한 줄 -> 20바이트 청크로 쪼개 Notify
# ---------------------------------------------------------------------------
def send_json(obj):
    """JSON 메시지 한 건을 호스트로 보낸다(GLib 메인루프 스레드에서만 호출)."""
    if not _state["notifying"]:
        return
    data = (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    for i in range(0, len(data), CHUNK):
        _tx_queue.append(data[i:i + CHUNK])
    _start_tx_timer()


def _start_tx_timer():
    global _tx_timer
    if _tx_timer is None and _tx_queue:
        _tx_timer = GLib.timeout_add(TX_INTERVAL_MS, _drain_tx)


def _drain_tx():
    """큐에서 청크 하나를 꺼내 Notify. 큐가 비면 타이머를 멈춘다."""
    global _tx_timer
    char = _state["tx_char"]
    if char is None or not _state["notifying"]:
        _tx_queue.clear()
        _tx_timer = None
        return False
    if not _tx_queue:
        _tx_timer = None
        return False
    char.set_value(list(_tx_queue.popleft()))
    return True


def _post(obj):
    """워커 스레드에서 호스트로 메시지를 보낼 때 쓰는 안전한 통로."""
    GLib.idle_add(_post_cb, obj)


def _post_cb(obj):
    send_json(obj)
    return False  # idle 콜백은 한 번만


# ---------------------------------------------------------------------------
# 수신: 청크 조립 -> JSON 명령 처리
# ---------------------------------------------------------------------------
def rx_write_callback(value, options):
    """호스트가 RX 캐릭터리스틱에 쓸 때 호출된다. value 는 int 리스트."""
    _state["last_rx"] = time.monotonic()  # 호스트가 살아 있다는 유일한 증거
    _rx_buf.extend(bytes(value))
    while True:
        idx = _rx_buf.find(b"\n")
        if idx < 0:
            break
        line = bytes(_rx_buf[:idx])
        del _rx_buf[:idx + 1]
        _handle_line(line)
    if len(_rx_buf) > RX_BUF_LIMIT:
        print("[RX] 조립 버퍼 초과 — 버립니다.")
        _rx_buf.clear()


def _handle_line(raw):
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return
    try:
        msg = json.loads(text)
        if not isinstance(msg, dict):
            raise ValueError("객체가 아님")
    except (ValueError, TypeError):
        # JSON 이 아니면 디버그용 원문으로 보고 그대로 되돌려준다.
        print(f"[RX] (raw) {text!r}")
        send_json({"ev": "echo", "msg": text})
        return

    cmd = msg.get("cmd", "")
    if cmd == "ping":
        # keepalive — 1초마다 온다. 받았다는 사실 자체가 목적이므로(위에서
        # last_rx 갱신) 응답도 로그도 없다. 답장하면 20바이트 청크 3개가
        # 매초 되돌아가 링크만 좀먹는다.
        return

    print(f"[RX] 명령: {cmd or text!r}")

    if cmd in ("scan", "status", "connect", "forget", "disconnect",
               "saved", "secret"):
        _enqueue(cmd, msg)
    else:
        send_json({"ev": "result", "cmd": cmd, "ok": False,
                   "msg": f"알 수 없는 명령: {cmd}"})


def _enqueue(cmd, msg, quiet=False):
    """워커에 작업을 넘긴다. 이미 다른 작업 중이면 거절한다."""
    with _busy_lock:
        running = _busy
    if running is not None:
        if not quiet:
            send_json({"ev": "result", "cmd": cmd, "ok": False,
                       "msg": f"'{running}' 작업이 진행 중입니다. 잠시 후 다시 시도하세요."})
        return False
    _jobs.put((cmd, msg, quiet))
    return True


# ---------------------------------------------------------------------------
# 워커 스레드 — nmcli 는 수 초씩 걸리므로 메인루프에서 떼어낸다.
# ---------------------------------------------------------------------------
def _worker():
    global _busy
    while True:
        cmd, msg, quiet = _jobs.get()
        with _busy_lock:
            _busy = cmd
        try:
            _run_job(cmd, msg, quiet)
        except wifi_manager.WifiError as exc:
            _post({"ev": "result", "cmd": cmd, "ok": False, "msg": str(exc)})
            print(f"[WIFI] {cmd} 실패: {exc}")
        except Exception as exc:  # noqa: BLE001 - 워커가 죽으면 안 된다
            _post({"ev": "result", "cmd": cmd, "ok": False,
                   "msg": f"내부 오류: {exc}"})
            print(f"[WIFI] {cmd} 예외: {exc!r}")
        finally:
            with _busy_lock:
                _busy = None
            _jobs.task_done()


def _run_job(cmd, msg, quiet):
    if cmd == "status":
        st = wifi_manager.status()
        _post({"ev": "status", **st})
        return

    if cmd == "scan":
        _post({"ev": "progress", "msg": "주변 Wi-Fi 를 검색하는 중…"})
        nets = wifi_manager.scan()
        # 화면에 필요한 필드만, 짧은 키로 — BLE 대역폭이 좁다.
        slim = [{"s": n["ssid"], "q": n["signal"],
                 "k": 1 if n["secure"] else 0,
                 "c": 1 if n["current"] else 0} for n in nets]
        _post({"ev": "scan", "nets": slim})
        _post({"ev": "result", "cmd": "scan", "ok": True,
               "msg": f"{len(slim)}개 발견"})
        return

    if cmd == "connect":
        ssid = (msg.get("ssid") or "").strip()
        psk = msg.get("psk") or None
        hidden = bool(msg.get("hidden"))
        st = wifi_manager.connect(
            ssid, psk, hidden,
            progress=lambda m: _post({"ev": "progress", "msg": m}),
        )
        _post({"ev": "status", **st})
        _post({"ev": "result", "cmd": "connect", "ok": True,
               "msg": f"'{st['ssid'] or ssid}' 연결됨 · IP {st['ip']}"})
        print(f"[WIFI] 연결 성공: {st['ssid']} {st['ip']}")
        return

    if cmd == "saved":
        # 저장된 비밀번호의 "길이"만 알려준다(화면에 ● 를 그 수만큼 찍기 위함).
        # 평문은 아래 'secret' 으로만, 사용자가 [표시] 를 누를 때만 나간다.
        ssid = (msg.get("ssid") or "").strip()
        secret = wifi_manager.saved_secret(ssid)
        _post({"ev": "saved", "ssid": ssid,
               "has": bool(secret), "len": len(secret or "")})
        _post({"ev": "result", "cmd": "saved", "ok": True,
               "msg": ("저장된 비밀번호 있음" if secret else "저장된 비밀번호 없음")})
        return

    if cmd == "secret":
        # 사용자가 [표시] 를 눌렀을 때만 실제 비밀번호를 보낸다.
        # 주의: BLE 링크는 암호화되어 있지 않다(README 의 '보안 주의' 참고).
        ssid = (msg.get("ssid") or "").strip()
        secret = wifi_manager.saved_secret(ssid)
        if secret is None:
            _post({"ev": "result", "cmd": "secret", "ok": False,
                   "msg": f"'{ssid}' 에 저장된 비밀번호가 없습니다."})
            return
        _post({"ev": "secret", "ssid": ssid, "psk": secret})
        _post({"ev": "result", "cmd": "secret", "ok": True, "msg": "표시"})
        print(f"[WIFI] '{ssid}' 저장된 비밀번호를 호스트로 전송(길이 {len(secret)})")
        return

    if cmd == "forget":
        ssid = (msg.get("ssid") or "").strip()
        removed = wifi_manager.forget(ssid)
        _post({"ev": "status", **wifi_manager.status()})
        _post({"ev": "result", "cmd": "forget", "ok": True,
               "msg": (f"{len(removed)}개 프로필 삭제" if removed else "삭제할 프로필 없음")})
        return

    if cmd == "disconnect":
        wifi_manager.disconnect()
        _post({"ev": "status", **wifi_manager.status()})
        _post({"ev": "result", "cmd": "disconnect", "ok": True, "msg": "Wi-Fi 연결을 끊었습니다."})
        return


# ---------------------------------------------------------------------------
# 주기 작업
# ---------------------------------------------------------------------------
def _heartbeat():
    """호스트 워치독용 하트비트. 이게 끊기면 웹앱이 재연결을 시작한다."""
    if _state["notifying"]:
        _state["beat"] += 1
        send_json({"ev": "hb", "n": _state["beat"]})
    return True


def _auto_status():
    """사용자 조작 없이도 화면의 Wi-Fi 정보를 최신으로 유지한다."""
    if _state["notifying"]:
        _enqueue("status", {}, quiet=True)  # 바쁘면 조용히 건너뜀
    return True


def _rx_watchdog():
    """1초마다 "지금 상태가 맞는지" 를 맞춰 놓는다.

    두 가지를 본다.
      1) 호스트가 붙어 있는데 조용하다  -> 죽은 링크다. 강제로 끊는다.
      2) 호스트가 없는데 광고가 안 떠 있다 -> 아무도 장비를 못 찾는다. 다시 올린다.
    각 사건이 일어난 자리에서 광고를 올리는 대신 여기 한 곳에서 상태를 맞추므로,
    등록이 한 번 실패해도 다음 초에 저절로 복구된다.
    """
    if not _state["last_rx"]:            # 붙어 있는 호스트가 없다
        # 창은 여기서만 닫는다. 호스트가 붙어 있는 동안은 이 분기에 오지 않으므로,
        # 설정 중에 3분이 지났다고 링크가 끊기는 일은 없다. 그 사람이 끝내고
        # 나간 뒤에 닫힌다.
        if ADV_WINDOW_S > 0 and time.monotonic() > _state["window_ends"]:
            if not _state["window_shut"]:
                _state["window_shut"] = True
                _state["adv_dirty"] = False
                _stop_advertising()
                print(f"[ADV] 광고 창({ADV_WINDOW_S:.0f}초)이 닫혔습니다. "
                      "다시 열려면 전원을 껐다 켜세요.")
            return True
        if _state["adv_dirty"]:
            _restart_advertising()
        return True

    # 구독을 마친 뒤에야 keepalive 를 기대할 수 있다. 그 전에는 GATT 준비 중일
    # 수 있으므로 더 너그럽게 기다린다.
    limit = RX_TIMEOUT_S if _state["notifying"] else CONNECT_GRACE_S
    quiet = time.monotonic() - _state["last_rx"]
    if quiet > limit:
        print(f"[WD] {quiet:.1f}초 동안 호스트 수신 없음(한계 {limit:.0f}초) "
              "— 링크를 강제로 끊습니다.")
        _drop_host()
    return True


def _drop_host():
    """죽은 링크를 물리적으로 끊는다. 뒷정리는 _reset_link 한 곳에서 한다."""
    dev = _state["device"]
    _reset_link()  # 여기서 광고 재등록 대상으로 표시된다
    if dev is None:
        return
    try:
        dev.disconnect()
    except Exception as exc:  # dbus 오류 종류가 다양해 폭넓게 잡는다
        print(f"[WD] 강제 해제 실패(무시): {exc}")


def _reset_link():
    """링크가 끝났을 때의 유일한 뒷정리 경로(워치독·해제 이벤트 공용)."""
    _state["device"] = None
    _state["notifying"] = False
    _state["last_rx"] = 0.0       # 이 값이 0 이면 "붙어 있는 호스트 없음"
    _state["adv_dirty"] = True    # 광고를 다시 올려야 한다 — 워치독이 처리
    _tx_queue.clear()
    _rx_buf.clear()


def tx_notify_callback(notifying, characteristic):
    """호스트가 TX 알림을 구독/해제할 때 호출된다."""
    _state["tx_char"] = characteristic
    _state["notifying"] = notifying
    print(f"[TX] 알림 구독 상태 -> {'ON' if notifying else 'OFF'}")
    if not notifying:
        _tx_queue.clear()
        _rx_buf.clear()  # 반쯤 받다 만 프레임을 다음 구독자에게 물려주지 않는다
        return
    # 구독 시점을 워치독 기준점으로 삼는다. on_connect 를 놓쳤더라도
    # 여기서부터는 호스트의 keepalive 를 기대할 수 있다.
    _state["last_rx"] = time.monotonic()
    # (재)구독 직후 신원과 현재 상태를 바로 밀어준다 — 호스트가 자리를
    # 비웠다 돌아와도 다음 주기를 기다리지 않고 즉시 화면이 채워진다.
    send_json({"ev": "hello", "name": LOCAL_NAME, "fw": FW_VERSION,
               "proto": PROTO_VERSION})
    _enqueue("status", {}, quiet=True)


def on_connect(device):
    print(f"[CONN] 연결됨: {getattr(device, 'address', device)}")
    _state["device"] = device
    _state["last_rx"] = time.monotonic()  # 첫 keepalive 까지의 유예


def on_disconnect(device=None):
    # 인자 1개짜리로 둔다 — bluezero 는 콜백의 파라미터 수를 보고 호출 방식을
    # 고르는데, 0개면 "deprecated" 경고를 찍는다.
    print("[CONN] 연결 해제됨")
    _reset_link()  # 광고 재등록은 워치독이 이어받는다


# ---------------------------------------------------------------------------
# 광고: 커널 BlueZ mgmt 소켓으로 직접 등록 (bluetoothd 우회)
# ---------------------------------------------------------------------------
# 이 컨트롤러(BCM43430, BT 4.x)는 bluetoothd 가 사용하는 확장 광고(Extended
# Advertising) mgmt 명령의 "Add Ext Adv Data"(0x0055)를 거부한다(Invalid
# Parameters 0x0d). 그래서 bluezero(bluetoothd) 로 광고를 등록하면 실패한다.
# GATT 서버 등록은 정상이므로, GATT 는 bluezero 로 두고 광고만 구형 mgmt
# API(MGMT_OP_ADD_ADVERTISING, LE Set Advertising Data 경로)로 분리한다.
#
# btmgmt(CLI)는 tty 가 없으면(nohup/백그라운드) 멈추므로, subprocess 대신
# 커널 mgmt control 소켓에 직접 명령을 보낸다. 등록한 광고 인스턴스는 이
# 소켓을 닫아도 커널에 유지되지만, 프로세스 수명 동안 소켓을 열어 둔다.
AF_BLUETOOTH = 31
BTPROTO_HCI = 1
HCI_CHANNEL_CONTROL = 3
HCI_DEV_NONE = 0xFFFF
MGMT_INDEX = 0  # hci0
ADV_INSTANCE = 1

MGMT_OP_ADD_ADVERTISING = 0x003E
MGMT_OP_REMOVE_ADVERTISING = 0x003F
MGMT_EV_CMD_COMPLETE = 0x0001
MGMT_EV_CMD_STATUS = 0x0002

# connectable | discoverable | add-flags-field-to-adv-data
_ADV_FLAGS = 0x01 | 0x02 | 0x08

# 광고 간격(0.625ms 단위). 160 = 100ms. 커널 기본값 0x0800(1280ms)은 너무
# 느려서 브라우저 선택창에 기기가 늦게 뜬다. 짧게 하면 거의 즉시 발견된다.
_ADV_INTERVAL = 160

_mgmt_sock = None  # 프로세스 수명 동안 유지되는 mgmt control 소켓


def _set_adv_interval() -> None:
    """광고 간격을 debugfs 로 단축한다(발견 속도↑). 실패해도 기본값으로 진행."""
    base = f"/sys/kernel/debug/bluetooth/hci{MGMT_INDEX}"
    for fname in ("adv_min_interval", "adv_max_interval"):
        try:
            with open(f"{base}/{fname}", "w") as fh:
                fh.write(str(_ADV_INTERVAL))
        except OSError:
            pass  # debugfs 없거나 권한 없으면 커널 기본 간격 사용


def _open_mgmt_socket():
    """커널 mgmt control 채널에 bind 된 소켓을 연다."""
    s = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
    # 이 Python 빌드의 socket 모듈은 HCI channel 인자를 지원하지 않아
    # (dev,) 형식만 받는다. control 채널에 bind 하려면 libc bind() 를 직접 호출.
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    addr = struct.pack("<HHH", AF_BLUETOOTH, HCI_DEV_NONE, HCI_CHANNEL_CONTROL)
    if libc.bind(s.fileno(), addr, len(addr)) != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))
    s.settimeout(5)
    return s


def _mgmt_cmd(opcode: int, params: bytes = b"") -> int:
    """mgmt 명령을 보내고 해당 명령의 완료 status(0=성공)를 돌려준다."""
    pkt = struct.pack("<HHH", opcode, MGMT_INDEX, len(params)) + params
    _mgmt_sock.send(pkt)
    while True:
        data = _mgmt_sock.recv(1024)
        ev, _idx, plen = struct.unpack("<HHH", data[:6])
        body = data[6:6 + plen]
        if ev in (MGMT_EV_CMD_COMPLETE, MGMT_EV_CMD_STATUS):
            cmd_opcode, status = struct.unpack("<HB", body[:3])
            if cmd_opcode == opcode:
                return status
        # 그 밖의 이벤트는 무시하고 계속 읽는다.


def _add_advertising() -> int:
    """광고 인스턴스를 (재)등록하고 mgmt status 를 돌려준다. 0 이면 성공."""
    # 남아 있는 우리 인스턴스만 제거(전체 0 은 같은 Pi 의 다른 BLE 서비스
    # 광고까지 중지시키므로 쓰지 않는다). 없으면 실패해도 무시.
    _mgmt_cmd(MGMT_OP_REMOVE_ADVERTISING, struct.pack("<B", ADV_INSTANCE))
    _set_adv_interval()  # ADD 전에 간격 설정해야 새 인스턴스에 반영됨

    name = LOCAL_NAME.encode("utf-8")
    # 이름을 "주 광고 데이터(adv_data)" 에 싣는다. 스캔응답에만 실으면 일부
    # Windows/Chrome Web Bluetooth 스택이 LocalName 을 못 잡아 기기가 안 보인다.
    # Flags AD(3B)는 커널이 add-flags-field 로 자동 추가하므로 여기선 이름만.
    # 이름 AD(2+11=13B) + Flags(3B) = 16B < 31B 한도.
    adv_data = bytes([len(name) + 1, 0x09]) + name  # Complete Local Name AD
    scan_rsp = b""
    params = (
        struct.pack("<BIHHBB", ADV_INSTANCE, _ADV_FLAGS, 0, 0,
                    len(adv_data), len(scan_rsp))
        + adv_data + scan_rsp
    )
    return _mgmt_cmd(MGMT_OP_ADD_ADVERTISING, params)


def _start_advertising() -> None:
    """구형 mgmt API 로 광고를 처음 등록한다(기동 시 1회)."""
    global _mgmt_sock
    _mgmt_sock = _open_mgmt_socket()
    status = _add_advertising()
    if status != 0:
        raise SystemExit(f"광고 등록 실패: mgmt status 0x{status:02x}")
    print(f"[ADV] 광고 등록 성공 (instance {ADV_INSTANCE})")


def _restart_advertising() -> None:
    """광고를 다시 올린다. 실패해도 죽지 않는다 — 워치독이 다음 초에 재시도."""
    if _mgmt_sock is None:
        return  # 아직 기동 중
    try:
        status = _add_advertising()
    except OSError as exc:
        print(f"[ADV] 재등록 실패(다음 초에 재시도): {exc}")
        return
    if status == 0:
        _state["adv_dirty"] = False
        print("[ADV] 광고 재등록 완료")
    else:
        print(f"[ADV] 재등록 실패(다음 초에 재시도): mgmt status 0x{status:02x}")


def _stop_advertising() -> None:
    """광고 인스턴스를 제거한다(종료 정리용)."""
    if _mgmt_sock is None:
        return
    try:
        _mgmt_cmd(MGMT_OP_REMOVE_ADVERTISING, struct.pack("<B", ADV_INSTANCE))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    # 사용 가능한 첫 번째 블루투스 어댑터 주소를 가져온다.
    adapters = list(adapter.Adapter.available())
    if not adapters:
        raise SystemExit(
            "블루투스 어댑터를 찾을 수 없습니다. "
            "'bluetoothctl list' 로 어댑터를 확인하고 "
            "'sudo systemctl status bluetooth' 로 서비스를 확인하세요."
        )
    adapter_addr = adapters[0].address

    # 이름 = "Pi-BLE-" + hostname (예: Pi-BLE-pi-tilt001).
    #
    # 접두사는 웹앱이 선택창을 거르는 기준이라 그대로 둔다 — 광고에는 서비스 UUID 가
    # 실리지 않으므로(31바이트 한도) 필터가 기댈 것이 이름뿐이다.
    #
    # 뒤에 hostname 을 붙이는 이유는 한 크레인에 BASE/MIDDLE/TOP 세 대가 붙기
    # 때문이다. MAC 뒷자리만으로는 어느 장비인지 알 수 없고, hostname 은 파일명의
    # SENSOR_ID·웹 화면 제목·e-paper 상단과 같은 문자열이다.
    #
    # 한도: Flags AD 3B + 이름 AD (2 + N) <= 31 이므로 이름은 26자까지.
    global LOCAL_NAME
    host = socket.gethostname()
    suffix = host if host else adapter_addr.replace(":", "")[-4:].upper()
    LOCAL_NAME = ("Pi-BLE-" + suffix)[:26]
    print(f"어댑터: {adapter_addr}, 로컬 이름: {LOCAL_NAME}")

    # Wi-Fi 쪽이 아예 준비 안 된 환경이면 여기서 미리 알려준다(BLE 는 계속 진행).
    try:
        print(f"Wi-Fi 인터페이스: {wifi_manager.wifi_iface()}")
    except wifi_manager.WifiError as exc:
        print(f"[경고] {exc} — Wi-Fi 명령은 실패합니다.")

    ble = peripheral.Peripheral(adapter_addr, local_name=LOCAL_NAME)

    ble.add_service(srv_id=1, uuid=NUS_SERVICE, primary=True)

    # RX: 호스트 -> Pi (Write). write-without-response 도 함께 허용.
    ble.add_characteristic(
        srv_id=1, chr_id=1, uuid=NUS_RX_CHAR,
        value=[], notifying=False,
        flags=["write", "write-without-response"],
        write_callback=rx_write_callback,
    )

    # TX: Pi -> 호스트 (Notify).
    ble.add_characteristic(
        srv_id=1, chr_id=2, uuid=NUS_TX_CHAR,
        value=[], notifying=False,
        flags=["notify"],
        notify_callback=tx_notify_callback,
    )

    ble.on_connect = on_connect
    ble.on_disconnect = on_disconnect

    # nmcli 작업 전담 스레드(메인루프를 막지 않기 위함).
    threading.Thread(target=_worker, name="wifi-worker", daemon=True).start()

    GLib.timeout_add(HEARTBEAT_MS, _heartbeat)
    GLib.timeout_add_seconds(STATUS_REFRESH_S, _auto_status)
    # 초 단위 타이머는 커널이 다른 깨어남과 묶어 처리한다(저전력 Pi 라 이득).
    # 광고 창은 여기서 시작한다 — 어댑터가 준비되고 광고가 실제로 올라간 시점이라,
    # 사용자가 장비를 찾을 수 있게 된 순간과 창의 시작이 같다.
    _state["window_ends"] = time.monotonic() + ADV_WINDOW_S
    if ADV_WINDOW_S > 0:
        print(f"[ADV] {ADV_WINDOW_S:.0f}초 동안 광고합니다. "
              "그 뒤에는 전원을 껐다 켜야 다시 열립니다.")
    else:
        print("[ADV] 광고 창 제한 없음 (VERTICRANE_BLE_WINDOW_S=0)")
    GLib.timeout_add_seconds(RX_WATCHDOG_S, _rx_watchdog)

    # --- publish() 분해 ---
    # bluezero 의 publish() 는 GATT 앱 등록과 광고 등록을 한꺼번에 하지만,
    # 이 컨트롤러에서는 광고 등록(register_advertisement)이 실패한다.
    # 그래서 publish() 가 하는 일 중 GATT 부분만 직접 수행하고, 광고는
    # mgmt 소켓으로 대체한다.
    for service in ble.services:
        ble.app.add_managed_object(service)
    for chars in ble.characteristics:
        ble.app.add_managed_object(chars)
    for desc in ble.descriptors:
        ble.app.add_managed_object(desc)
    if not ble.dongle.powered:
        ble.dongle.powered = True
    ble.srv_mng.register_application(ble.app, {})  # GATT 서버 등록(정상 동작)

    _start_advertising()  # 광고는 구형 mgmt API 로 (bluetoothd 우회)

    print(f"BLE 광고 시작. 웹앱에서 '{LOCAL_NAME}' 를 찾아 연결하세요.")
    print("(종료: Ctrl+C)")
    try:
        ble.mainloop.run()
    except KeyboardInterrupt:
        pass
    finally:
        ble.mainloop.quit()
        _stop_advertising()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n종료합니다.")
