#!/usr/bin/env python3
"""
wifi_manager.py — NetworkManager(nmcli) 래퍼

BLE 로 받은 명령을 실제 Wi-Fi 동작으로 바꿔주는 계층. BLE 와는 완전히
분리되어 있어서 Pi 에서 단독 실행으로 동작을 확인할 수 있다:

    sudo ./venv/bin/python wifi_manager.py scan
    sudo ./venv/bin/python wifi_manager.py status
    sudo ./venv/bin/python wifi_manager.py connect <SSID> [비밀번호]

주의: 여기 함수들은 모두 블로킹(수 초~수십 초)이다. BLE 쪽에서는 반드시
워커 스레드에서 호출해야 GLib 메인루프(하트비트·알림)가 멈추지 않는다.
"""

import json
import os
import re
import subprocess
import sys

# nmcli 는 활성화 완료까지 기다려 준다(-w). 아래 값은 그보다 넉넉하게 잡은
# subprocess 자체의 상한이다(nmcli 가 매달리는 최악의 경우 대비).
SCAN_TIMEOUT = 40
CONNECT_WAIT = 45          # nmcli -w 에 넘길 값
CONNECT_TIMEOUT = 70       # subprocess 상한 (CONNECT_WAIT + 여유)
QUICK_TIMEOUT = 15

# 새로 연결한 프로필이 재부팅 후에도 우선 붙도록 주는 우선순위.
# (기존 netplan 프로필 등은 보통 0 이라 이쪽이 이긴다.)
AUTOCONNECT_PRIORITY = 10


class WifiError(Exception):
    """사용자에게 그대로 보여줄 수 있는 Wi-Fi 작업 실패."""


# ---------------------------------------------------------------------------
# nmcli 실행 / 파싱
# ---------------------------------------------------------------------------
def _run(args, timeout=QUICK_TIMEOUT):
    """nmcli 를 실행하고 (rc, stdout, stderr) 를 돌려준다."""
    # 에러 메시지 매칭(비밀번호 오류 판정 등)을 안정적으로 하려고 C 로케일 고정.
    env = dict(os.environ, LC_ALL="C", LANG="C")
    try:
        proc = subprocess.run(
            ["nmcli"] + args,
            capture_output=True, text=True, errors="replace",
            timeout=timeout, env=env,
        )
    except FileNotFoundError:
        raise WifiError("nmcli 가 없습니다. NetworkManager 를 설치하세요.")
    except subprocess.TimeoutExpired:
        raise WifiError(f"nmcli 응답 없음({timeout}초 초과): nmcli {' '.join(args)}")
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _terse(line):
    """nmcli -t 한 줄을 필드 리스트로 분해한다.

    terse 모드는 필드를 ':' 로 잇고, 값 안의 ':' 와 '\\' 는 백슬래시로
    이스케이프한다. SSID 에 ':' 가 들어간 경우를 위해 직접 파싱한다.
    """
    fields, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    fields.append("".join(cur))
    return fields


def wifi_iface():
    """첫 번째 Wi-Fi 인터페이스 이름(보통 wlan0)."""
    rc, out, err = _run(["-t", "-f", "DEVICE,TYPE", "device"])
    if rc != 0:
        raise WifiError(f"장치 목록 조회 실패: {err or out}")
    for line in out.splitlines():
        parts = _terse(line)
        if len(parts) >= 2 and parts[1] == "wifi":
            return parts[0]
    raise WifiError("Wi-Fi 인터페이스를 찾을 수 없습니다.")


# ---------------------------------------------------------------------------
# 스캔
# ---------------------------------------------------------------------------
def scan(rescan=True):
    """주변 AP 목록. [{ssid, signal, secure, security, current}] (신호 내림차순)"""
    args = ["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list"]
    if rescan:
        args += ["--rescan", "yes"]
    rc, out, err = _run(args, timeout=SCAN_TIMEOUT)
    if rc != 0:
        raise WifiError(f"스캔 실패: {err or out}")

    best = {}
    for line in out.splitlines():
        parts = _terse(line)
        if len(parts) < 4:
            continue
        in_use, ssid, signal, security = parts[0], parts[1], parts[2], parts[3]
        if not ssid:
            continue  # 히든 AP — SSID 를 직접 입력해서 연결한다
        try:
            signal = int(signal)
        except ValueError:
            signal = 0
        net = {
            "ssid": ssid,
            "signal": signal,
            "secure": bool(security),         # 비밀번호가 필요한가
            "security": security or "열림",
            "current": in_use.strip() == "*",
        }
        # 같은 SSID 가 채널마다 여러 번 잡힌다 — 신호 센 쪽만 남긴다.
        old = best.get(ssid)
        if old is None or net["signal"] > old["signal"]:
            net["current"] = net["current"] or (old or {}).get("current", False)
            best[ssid] = net
        elif net["current"]:
            old["current"] = True

    return sorted(best.values(), key=lambda n: -n["signal"])


# ---------------------------------------------------------------------------
# 상태
# ---------------------------------------------------------------------------
def _profile_ssid(profile):
    """연결 프로필 이름 -> 실제 SSID (프로필 이름과 다를 수 있음)."""
    if not profile or profile == "--":
        return ""
    rc, out, _ = _run(["-t", "-f", "802-11-wireless.ssid", "connection", "show", profile])
    if rc != 0:
        return ""
    parts = _terse(out.strip())
    return parts[1] if len(parts) >= 2 else ""


def status():
    """현재 Wi-Fi 상태와 IP 정보."""
    iface = wifi_iface()
    fields = ("GENERAL.STATE,GENERAL.CONNECTION,GENERAL.HWADDR,"
              "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS")
    rc, out, err = _run(["-t", "-f", fields, "device", "show", iface])
    if rc != 0:
        raise WifiError(f"상태 조회 실패: {err or out}")

    info, dns = {}, []
    for line in out.splitlines():
        parts = _terse(line)
        if len(parts) < 2:
            continue
        key, val = parts[0], ":".join(parts[1:])
        if key.startswith("IP4.DNS"):
            if val and val != "--":
                dns.append(val)
        else:
            info[key] = val

    # GENERAL.STATE 는 "100 (connected)" 형태 — 괄호 안의 단어만 쓴다.
    raw_state = info.get("GENERAL.STATE", "")
    m = re.search(r"\((.+)\)", raw_state)
    state = m.group(1) if m else raw_state

    profile = info.get("GENERAL.CONNECTION", "")
    if profile == "--":
        profile = ""
    ip = info.get("IP4.ADDRESS[1]", "")
    if ip == "--":
        ip = ""
    gateway = info.get("IP4.GATEWAY", "")
    if gateway == "--":
        gateway = ""

    return {
        "iface": iface,
        "state": state,                  # connected / disconnected / connecting…
        "connected": state == "connected" and bool(ip),
        "profile": profile,
        "ssid": _profile_ssid(profile),
        "ip": ip.split("/")[0] if ip else "",
        "cidr": ip,
        "gateway": gateway,
        "dns": dns,
        "mac": info.get("GENERAL.HWADDR", ""),
        "hostname": os.uname().nodename,
    }


# ---------------------------------------------------------------------------
# 연결 / 해제 / 삭제
# ---------------------------------------------------------------------------
def _profiles_for_ssid(ssid):
    """해당 SSID 를 쓰는 저장된 연결 프로필 이름들."""
    rc, out, _ = _run(["-t", "-f", "NAME,TYPE", "connection", "show"])
    if rc != 0:
        return []
    names = []
    for line in out.splitlines():
        parts = _terse(line)
        if len(parts) >= 2 and parts[1] in ("802-11-wireless", "wifi"):
            if _profile_ssid(parts[0]) == ssid:
                names.append(parts[0])
    return names


def saved_secret(ssid):
    """저장된 프로필에 들어 있는 Wi-Fi 비밀번호(PSK). 없으면 None.

    root 로 실행할 때만 값이 나온다(`--show-secrets`). 호출부에서 이 값을
    함부로 로그에 찍지 말 것 — 평문 비밀번호다.
    """
    for name in _profiles_for_ssid(ssid):
        rc, out, _ = _run(["--show-secrets", "-t", "-f",
                           "802-11-wireless-security.psk",
                           "connection", "show", name])
        if rc != 0 or not out:
            continue
        parts = _terse(out.splitlines()[0])
        if len(parts) >= 2 and parts[1] and parts[1] != "--":
            return parts[1]
    return None


def forget(ssid):
    """저장된 프로필 삭제(비밀번호가 바뀐 AP 재설정용)."""
    removed = []
    for name in _profiles_for_ssid(ssid):
        rc, _, _ = _run(["connection", "delete", name])
        if rc == 0:
            removed.append(name)
    return removed


def disconnect():
    """현재 Wi-Fi 연결만 끊는다(프로필은 남김)."""
    iface = wifi_iface()
    rc, out, err = _run(["device", "disconnect", iface], timeout=QUICK_TIMEOUT)
    if rc != 0:
        raise WifiError(f"연결 해제 실패: {err or out}")
    return True


def _looks_like_auth_error(text):
    """비밀번호(시크릿) 문제로 실패한 것으로 보이는가.

    'key-mgmt: property is missing' 은 보안 AP 에 비밀번호 없이 붙으려 할 때
    nmcli 가 보안 섹션만 있고 key-mgmt 가 빠진 반쪽 프로필을 만들며 내는 오류다.
    """
    low = text.lower()
    return ("secrets were required" in low or "no secrets" in low
            or "key-mgmt" in low or "802.1x supplicant" in low
            or "authentication" in low)


def _security_of(ssid):
    """스캔 캐시에서 이 SSID 의 보안 방식을 찾는다.

    "" = 개방망, "WPA2" 등 = 비밀번호 필요, None = 목록에 없음(히든 등).
    """
    rc, out, _ = _run(["-t", "-f", "SSID,SECURITY", "device", "wifi", "list"])
    if rc != 0:
        return None
    for line in out.splitlines():
        parts = _terse(line)
        if len(parts) >= 2 and parts[0] == ssid:
            return parts[1]
    return None


def _drop_new_profiles(ssid, before):
    """실패한 시도가 새로 만든 프로필만 지운다(기존 정상 프로필은 보존)."""
    for name in _profiles_for_ssid(ssid):
        if name not in before:
            _run(["connection", "delete", name])


def _pin_autoconnect(ssid):
    """방금 붙은 프로필을 재부팅 후에도 우선 자동연결하도록 표시."""
    iface = wifi_iface()
    rc, out, _ = _run(["-t", "-f", "GENERAL.CONNECTION", "device", "show", iface])
    profile = ""
    if rc == 0 and out:
        parts = _terse(out.splitlines()[0])
        if len(parts) >= 2:
            profile = parts[1]
    if not profile or profile == "--":
        return
    _run(["connection", "modify", profile,
          "connection.autoconnect", "yes",
          "connection.autoconnect-priority", str(AUTOCONNECT_PRIORITY)])


def connect(ssid, psk=None, hidden=False, progress=None):
    """SSID 에 연결한다. 성공하면 status() 를 돌려주고, 실패하면 WifiError.

    progress: 진행 상황을 알려줄 콜백(문자열 1개). BLE 쪽에서 화면에 흘린다.
    """
    if not ssid:
        raise WifiError("SSID 가 비어 있습니다.")
    say = progress or (lambda _msg: None)
    iface = wifi_iface()
    before = _profiles_for_ssid(ssid)   # 이 시도 전에 이미 있던 프로필

    def _finish():
        say("연결됨 — IP 주소 확인 중…")
        _pin_autoconnect(ssid)
        st = status()
        if not st["ip"]:
            raise WifiError("연결은 됐지만 IP 를 받지 못했습니다(DHCP 확인 필요).")
        return st

    # --- 비밀번호 없이 들어온 경우 ---
    # 그대로 `device wifi connect` 를 때리면, 보안 AP 일 때 nmcli 가 보안 섹션만
    # 있고 key-mgmt 가 빠진 반쪽 프로필을 만들며 실패한다
    # ("802-11-wireless-security.key-mgmt: property is missing").
    # 그래서 여기서 갈래를 나눈다.
    if not psk:
        if before:
            # 저장된 설정이 있으면 그것으로 올린다(가장 확실한 경로).
            say(f"저장된 설정으로 '{ssid}' 연결 중…")
            rc, out, err = _run(
                ["-w", str(CONNECT_WAIT), "connection", "up", before[0], "ifname", iface],
                timeout=CONNECT_TIMEOUT,
            )
            if rc == 0:
                return _finish()
            msg = (err or out or "").splitlines()[-1] if (err or out) else ""
            if _looks_like_auth_error(msg):
                raise WifiError(
                    "저장된 비밀번호로 연결하지 못했습니다. 비밀번호를 입력해 다시 시도하세요."
                )
            raise WifiError(msg or "저장된 설정으로 연결하지 못했습니다.")

        security = _security_of(ssid)
        if security:   # 보안 AP 인데 비밀번호도 저장된 설정도 없음 → 미리 막는다
            raise WifiError(
                f"'{ssid}' 는 비밀번호가 필요한 네트워크({security})입니다. "
                "비밀번호를 입력하세요."
            )
        # security == "" (개방망) 또는 None(목록에 없음/히든) → 아래 일반 경로

    def _attempt():
        args = ["-w", str(CONNECT_WAIT), "device", "wifi", "connect", ssid]
        if psk:
            args += ["password", psk]
        if hidden:
            args += ["hidden", "yes"]
        args += ["ifname", iface]
        return _run(args, timeout=CONNECT_TIMEOUT)

    say(f"'{ssid}' 에 연결 중…")
    rc, out, err = _attempt()

    # 저장된 프로필의 옛 비밀번호가 남아 실패하는 경우가 흔하다.
    # 프로필을 지우고 방금 받은 비밀번호로 한 번만 다시 시도한다.
    if rc != 0 and psk and _looks_like_auth_error(err or out) and before:
        say("저장된 옛 설정 때문에 실패 — 프로필을 지우고 다시 시도합니다.")
        forget(ssid)
        before = []
        rc, out, err = _attempt()

    if rc != 0:
        # 실패한 시도가 남긴 반쪽 프로필은 다음 시도까지 망치므로 치운다.
        _drop_new_profiles(ssid, before)
        msg = (err or out or "알 수 없는 오류").splitlines()[-1]
        if "key-mgmt" in msg.lower():
            raise WifiError(f"'{ssid}' 연결에 비밀번호가 필요합니다. 비밀번호를 입력하세요.")
        if _looks_like_auth_error(msg):
            raise WifiError(f"인증 실패 — 비밀번호를 확인하세요. ({msg})")
        raise WifiError(msg)

    return _finish()


# ---------------------------------------------------------------------------
# 단독 실행(디버그용)
# ---------------------------------------------------------------------------
def _main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    try:
        if cmd == "scan":
            result = scan()
        elif cmd == "status":
            result = status()
        elif cmd == "connect":
            result = connect(argv[2], argv[3] if len(argv) > 3 else None,
                             progress=lambda m: print("…", m, file=sys.stderr))
        elif cmd == "saved":
            # 평문 비밀번호를 콘솔에 뿌리지 않도록 길이만 보여준다.
            secret = saved_secret(argv[2])
            result = {"has": bool(secret), "len": len(secret or "")}
        elif cmd == "forget":
            result = forget(argv[2])
        elif cmd == "disconnect":
            result = disconnect()
        else:
            print(f"알 수 없는 명령: {cmd}", file=sys.stderr)
            return 1
    except WifiError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
