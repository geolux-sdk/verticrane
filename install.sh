#!/usr/bin/env bash
# Provision a fresh Raspberry Pi Zero 2 W as a verticrane recorder.
#
#   sudo apt install -y git
#   git clone https://github.com/geolux-sdk/verticrane.git
#   cd verticrane && ./install.sh
#
#   ./install.sh --dry-run    show what would change, touch nothing
#
# Safe to run again: every step checks before it acts. Run it as the normal
# user, not with sudo -- the virtual environment has to belong to you, and the
# steps that need root ask for it themselves.
#
# Two things this deliberately does NOT do:
#   - set the hostname, which becomes the SENSOR_ID stamped into every recording
#   - reboot, after changing the boot configuration
# Both are decisions, not chores.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT}"

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

APT_PACKAGES=(python3-venv python3-full fonts-nanum bluez python3-dbus python3-gi)
BOOT_CONFIG=/boot/firmware/config.txt
BOOT_CMDLINE=/boot/firmware/cmdline.txt

reboot_needed=0
relogin_needed=0
todo=()

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '   \033[32mok\033[0m   %s\n' "$*"; }
act()  { printf '   \033[33m->\033[0m   %s\n' "$*"; }
warn() { printf '   \033[31m!!\033[0m   %s\n' "$*"; }

run() {
    if [ "${DRY}" -eq 1 ]; then
        printf '        (dry-run) %s\n' "$*"
        return 0
    fi
    "$@"
}

# --------------------------------------------------------------------------
say "확인"

if [ "${EUID:-$(id -u)}" -eq 0 ]; then
    warn "sudo 로 실행하지 마세요. 가상환경이 root 소유가 되면 도구가 동작하지 않습니다."
    exit 1
fi
ok "일반 사용자로 실행 중 ($(whoami))"

if [ ! -f "${BOOT_CONFIG}" ]; then
    warn "${BOOT_CONFIG} 이 없습니다 — 라즈베리파이가 아닌 것 같습니다."
    warn "이 스크립트는 라즈베리파이 전용입니다."
    exit 1
fi
ok "라즈베리파이 ($(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown))"

if ! sudo -n true 2>/dev/null; then
    echo
    echo "   apt 설치와 서비스 등록에 sudo 가 필요합니다. 암호를 물어볼 수 있습니다."
    sudo -v || { warn "sudo 인증 실패"; exit 1; }
fi

# --------------------------------------------------------------------------
say "시스템 패키지"

missing=()
for pkg in "${APT_PACKAGES[@]}"; do
    if dpkg -s "${pkg}" >/dev/null 2>&1; then
        ok "${pkg}"
    else
        missing+=("${pkg}")
    fi
done

if [ ${#missing[@]} -gt 0 ]; then
    act "설치: ${missing[*]}"
    # python3-dbus and python3-gi have to come from apt: pip cannot build
    # PyGObject on this board.
    run sudo apt-get update -qq
    run sudo apt-get install -y "${missing[@]}" || warn "일부 패키지 설치 실패 — 위 로그를 확인하세요"
fi

# --------------------------------------------------------------------------
say "시리얼 포트 (센서)"

# The sensor speaks Modbus at 115200 on GPIO14/15. Two things are in the way on
# a stock image: the UART is off, and the serial console owns the port. A third
# is subtler -- Bluetooth takes the good PL011 and leaves GPIO the mini UART,
# whose baud rate follows the core clock and drops frames at 115200.
#
# miniuart-bt swaps that around: the sensor keeps PL011, Bluetooth moves to the
# mini UART, which is ample for the BLE provisioning channel.
backup_boot() {
    [ -f "${BOOT_CONFIG}.verticrane-bak" ] && return 0
    act "백업: ${BOOT_CONFIG}.verticrane-bak"
    run sudo cp "${BOOT_CONFIG}" "${BOOT_CONFIG}.verticrane-bak"
}

if grep -qE '^enable_uart=1' "${BOOT_CONFIG}"; then
    ok "enable_uart=1"
else
    backup_boot
    act "enable_uart=1 추가"
    run sudo sh -c "printf '\nenable_uart=1\n' >> ${BOOT_CONFIG}"
    reboot_needed=1
fi

if grep -qE '^dtoverlay=miniuart-bt' "${BOOT_CONFIG}"; then
    ok "dtoverlay=miniuart-bt (센서는 PL011, 블루투스는 mini UART)"
elif grep -qE '^dtoverlay=disable-bt' "${BOOT_CONFIG}"; then
    backup_boot
    act "dtoverlay=disable-bt -> miniuart-bt (블루투스를 살리면서 센서는 PL011 유지)"
    run sudo sed -i 's/^dtoverlay=disable-bt/dtoverlay=miniuart-bt/' "${BOOT_CONFIG}"
    reboot_needed=1
else
    backup_boot
    act "dtoverlay=miniuart-bt 추가"
    run sudo sh -c "printf 'dtoverlay=miniuart-bt\n' >> ${BOOT_CONFIG}"
    reboot_needed=1
fi

if grep -q 'console=serial0' "${BOOT_CMDLINE}" 2>/dev/null; then
    act "시리얼 콘솔 제거 (포트를 점유합니다)"
    run sudo cp "${BOOT_CMDLINE}" "${BOOT_CMDLINE}.verticrane-bak"
    run sudo sed -i 's/console=serial0,[0-9]* //' "${BOOT_CMDLINE}"
    reboot_needed=1
else
    ok "시리얼 콘솔 없음"
fi

# --------------------------------------------------------------------------
say "SPI (e-paper)"

# The panel is an SSD1681 on SPI0. Without the bus the recorder still records,
# but the device loses the only display it has.
if grep -qE '^dtparam=spi=on' "${BOOT_CONFIG}"; then
    ok "dtparam=spi=on"
else
    backup_boot
    act "dtparam=spi=on 추가"
    run sudo sh -c "printf 'dtparam=spi=on\n' >> ${BOOT_CONFIG}"
    reboot_needed=1
fi

# --------------------------------------------------------------------------
say "장치 접근 권한"

# dialout for the sensor, spi and gpio for the panel. Missing any of them fails
# at open() with a permission error that reads like broken wiring.
for grp in dialout spi gpio; do
    if ! getent group "${grp}" >/dev/null 2>&1; then
        warn "${grp} 그룹이 없습니다 — 관련 장치가 아직 없는 것일 수 있습니다"
        continue
    fi
    if id -nG "$(whoami)" | tr ' ' '\n' | grep -qx "${grp}"; then
        ok "${grp} 그룹"
    else
        act "${grp} 그룹에 추가"
        run sudo usermod -aG "${grp}" "$(whoami)"
        relogin_needed=1
    fi
done

if [ "${relogin_needed}" -eq 1 ]; then
    todo+=("그룹이 바뀌었습니다. 다시 로그인해야 장치 권한이 적용됩니다 (재부팅해도 됩니다)")
fi

# --------------------------------------------------------------------------
say "파이썬 환경"

if [ "${DRY}" -eq 1 ]; then
    act "./install_requirements.sh"
else
    ./install_requirements.sh || { warn "파이썬 의존성 설치 실패"; exit 1; }
fi

if [ -x .venv/bin/python ] && .venv/bin/python -c 'import bluezero' 2>/dev/null; then
    ok "bluezero"
elif [ "${DRY}" -eq 1 ]; then
    act "pip install bluezero==0.9.1"
else
    act "bluezero 설치"
    .venv/bin/pip install --quiet "bluezero==0.9.1" || warn "bluezero 설치 실패 — BLE 는 동작하지 않습니다"
fi

# --------------------------------------------------------------------------
say "서비스"

install_unit() {
    local src="$1" name="$2"
    if systemctl is-enabled --quiet "${name}" 2>/dev/null; then
        act "${name} 갱신"
    else
        act "${name} 등록"
    fi
    run sudo cp "${src}" /etc/systemd/system/
    run sudo systemctl daemon-reload
    run sudo systemctl enable "${name}" >/dev/null
}

install_unit verticrane-recorder.service verticrane-recorder
install_unit bt/systemd/pi-bt-wifi-setup.service pi-bt-wifi-setup

if systemctl is-enabled --quiet verticrane-dashboard 2>/dev/null; then
    act "옛 대시보드 서비스 해제 (같은 시리얼 포트를 다툽니다)"
    run sudo systemctl disable --now verticrane-dashboard
fi

# --------------------------------------------------------------------------
say "남은 일"

host="$(hostname)"
case "${host}" in
    raspberrypi|localhost|"")
        todo+=("호스트명이 '${host}' 입니다. 이 이름이 기록 파일마다 찍히는 SENSOR_ID 이고 접속 이름입니다:
            sudo hostnamectl set-hostname pi-tilt001")
        ;;
    *) ok "호스트명 ${host} (= SENSOR_ID)" ;;
esac

if [ "${reboot_needed}" -eq 1 ]; then
    todo+=("부팅 설정을 바꿨습니다. 재부팅해야 센서와 블루투스가 살아납니다:
            sudo reboot")
fi

todo+=("재부팅 뒤 센서를 설정합니다 (최초 1회):
            .venv/bin/python dev/configure_sensor.py --baud 115200")
todo+=("설치 위치를 지정합니다. 안 하면 파일명이 UNSET_ 로 시작해 나중에 구분할 수 없습니다:
            http://<장비IP>:8080/settings")

echo
for i in "${!todo[@]}"; do
    printf '   %d. %s\n' "$((i + 1))" "${todo[$i]}"
done

echo
if [ "${DRY}" -eq 1 ]; then
    echo "   (dry-run — 아무것도 바꾸지 않았습니다)"
else
    echo "   점검:  ./test.sh --no-hardware"
    echo "   상태:  systemctl status verticrane-recorder pi-bt-wifi-setup"
fi
echo
