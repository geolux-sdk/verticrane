# coding:UTF-8
# AUTO_RUN recording loop and state machine (sections 3 and 6).
#
#   python recorder.py                      normal boot sequence
#   python recorder.py --seconds 120        stop after two minutes (bench test)
#   python recorder.py --force recording    skip the HTTP wait and the settle wait
#   python recorder.py --force maintenance  come up idle, as if someone connected
#
# Polls the sensor at 25 Hz, waits for the crane to settle, then appends 128-byte
# blocks of 25 samples each. Nothing here ever renames a file that is open: the
# timestamp gets fixed up during the next boot's recovery pass, because in the
# field the power simply drops and there is no shutdown to hook.
#
# The web layer calls note_http_request() when a valid request arrives; until it
# exists, --force stands in for it.

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger

import ahrs_file as af
import app_config
import filestore
import port_config
import read_status
import stability
from hwt9037_485 import HWT9037_485

DATA_DIR: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LAST_KNOWN: str = ".lastknown"

SAMPLE_RATE_HZ: int = 25
SAMPLE_PERIOD_S: float = 1.0 / SAMPLE_RATE_HZ
TEMP_REFRESH_S: float = 1.0
ANGLE_BLOCK: tuple[int, int] = (0x34, 15)
TEMP_REG: tuple[int, int] = (0x43, 1)
SERIAL_REG: tuple[int, int] = (0x7F, 6)

# Checked once per connection. Getting any of these wrong silently changes what
# the recorded angles mean, so it is worth saying so at the time rather than
# leaving the evidence in a file nobody reads until the measurement is over.
#
# The third column names the value, because a warning that only prints raw
# numbers is one nobody can act on -- and an expectation set to the wrong number
# teaches people to ignore the warning altogether.
EXPECTED_CONFIG: dict[int, tuple[str, int, dict[int, str]]] = {
    0x23: ("설치 방향", 0, {0: "수평", 1: "수직"}),
    # 6-axis keeps magnetic interference out of the attitude solution. A crane is
    # steel and motors, so the magnetometer cannot be trusted for heading.
    0x24: ("알고리즘", 1, {0: "9축", 1: "6축"}),
    # 10 Hz, not the factory default of 20 Hz. Sampling at 25 Hz assumes it:
    # anything faster aliases vibration down into the slow tilt band (section 1).
    0x1F: ("대역폭", 5, {3: "42 Hz", 4: "20 Hz", 5: "10 Hz", 6: "5 Hz"}),
}

# States, per section 3.
WAITING_HTTP: str = "waiting_http"
WAITING_STABLE: str = "waiting_stable"
RECORDING: str = "recording"
MAINTENANCE: str = "maintenance"

RECONNECT_MIN_S: float = 1.0
RECONNECT_MAX_S: float = 30.0


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------

class TimeKeeper:
    """Works out what time it is on a board with no RTC (section 3).

    Never sets the system clock -- that needs privileges the service does not
    have. It keeps an internal offset instead, which is all the file naming
    needs. When NTP lands, the kernel clock becomes right and the offset is
    dropped; the difference between the two is the correction that goes into
    .timeinfo.
    """

    _SYNC_MARKER: str = "/run/systemd/timesync/synchronized"

    def __init__(self, data_dir: str, min_valid_year: int = 2026) -> None:
        self.data_dir: str = data_dir
        self.min_valid_year: int = min_valid_year
        self._offset: float = 0.0
        self.quality: str = af.QUALITY_UNSYNCED
        self.synced_at: Optional[float] = None
        self._lock = threading.Lock()
        self._restore()

    def now(self) -> float:
        return time.time() + self._offset

    def _restore(self) -> None:
        if self.is_ntp_synced():
            self.quality = af.QUALITY_SYNCED
            self.synced_at = time.time()
            logger.info("Clock is NTP-synchronised at boot")
            return

        kernel: float = time.time()
        saved: Optional[float] = self._read_last_known()
        if saved is not None and saved > kernel:
            # Restoring only ever moves the clock forward, so a recorded time
            # can be behind reality but never ahead of it.
            self._offset = saved - kernel
            logger.info("Restored last known time (+{:.0f}s behind reality by at "
                        "least the time spent powered off)", self._offset)

        year: int = time.localtime(self.now()).tm_year
        self.quality = af.QUALITY_INVALID if year < self.min_valid_year else af.QUALITY_UNSYNCED
        if self.quality == af.QUALITY_INVALID:
            logger.warning("Clock reads year {} (< {}); treating it as invalid",
                           year, self.min_valid_year)

    def is_ntp_synced(self) -> bool:
        # The marker file is the cheapest check; timedatectl is the fallback for
        # systems where timesyncd does not create it.
        if os.path.exists(self._SYNC_MARKER):
            return True
        try:
            out: str = subprocess.run(
                ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                capture_output=True, text=True, timeout=5).stdout.strip()
            return out.lower() == "yes"
        except (OSError, subprocess.SubprocessError):
            return False

    def poll_sync(self) -> Optional[float]:
        """Check for a fresh NTP sync. Returns the correction in seconds, or None.

        Only the first unsynced -> synced transition matters; once the clock is
        good, later NTP nudges are sub-second and are ignored.
        """
        with self._lock:
            if self.quality in af.TRUSTED_QUALITIES:
                return None
            if not self.is_ntp_synced():
                return None
            correction: float = time.time() - self.now()
            self._offset = 0.0
            self.quality = af.QUALITY_SYNCED
            self.synced_at = time.time()
            logger.info("NTP acquired; clock moved {:+.2f}s", correction)
            return correction

    def save_last_known(self) -> None:
        path: str = os.path.join(self.data_dir, LAST_KNOWN)
        tmp: str = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("{0:.3f}".format(self.now()))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("Could not save the last known time: {}", exc)

    def _read_last_known(self) -> Optional[float]:
        try:
            with open(os.path.join(self.data_dir, LAST_KNOWN), encoding="utf-8") as f:
                return float(f.read().strip())
        except (OSError, ValueError):
            return None


# --------------------------------------------------------------------------
# Sensor
# --------------------------------------------------------------------------

class SensorLink:
    """Keeps the Modbus link alive, reconnecting with a backoff on failure.

    A dropped serial link must never take the process down: the web server has
    to keep answering and the recording has to resume in the same file.
    """

    def __init__(self, port: str) -> None:
        self.port: str = port
        self.device: Optional[HWT9037_485] = None
        self.baud: Optional[int] = None
        self.reconnected: bool = False     # cleared once a block has reported it
        self.serial: str = ""
        self.config_warnings: list[str] = []
        self._backoff: float = RECONNECT_MIN_S
        self._next_attempt: float = 0.0

    def connect(self) -> bool:
        if self.device is not None:
            return True
        if time.monotonic() < self._next_attempt:
            return False
        device, baud = read_status.connectAutoBaud(self.port)
        if device is None:
            self._next_attempt = time.monotonic() + self._backoff
            logger.warning("Sensor not responding on {}; retrying in {:.0f}s",
                           self.port, self._backoff)
            self._backoff = min(self._backoff * 2.0, RECONNECT_MAX_S)
            return False
        device.verbose = False             # 25 Hz of Modbus logging is unreadable
        self.device, self.baud = device, baud
        self._backoff = RECONNECT_MIN_S
        self.reconnected = True
        logger.info("Sensor connected on {} at {} bps", self.port, baud)
        self._read_serial()
        self.config_warnings = self._verify_config()
        return True

    def _read_serial(self) -> None:
        """The sensor's own 12-character serial (0x7F~0x84).

        Decoded low byte first -- high byte first also yields a plausible
        string, which is why the wrong order goes unnoticed. See read_status.
        """
        if self.device is None:
            return
        try:
            if self.device.readReg(*SERIAL_REG) is not None:
                self.serial = read_status.decode_numberid(self.device) or ""
                logger.info("Sensor serial {}", self.serial)
        except Exception as exc:                          # noqa: BLE001
            logger.debug("Could not read the serial: {}", exc)

    def _verify_config(self) -> list[str]:
        """Compare the sensor's settings with what the recording assumes.

        Never refuses to record: data taken with an odd setting still beats no
        data, and a person can judge it later. But it must be said now, because
        by the end of a measurement it is too late to redo it.
        """
        warnings: list[str] = []
        if self.device is None:
            return warnings
        for reg, (label, expected, names) in EXPECTED_CONFIG.items():
            try:
                if self.device.readReg(reg, 1) is None:
                    continue
            except Exception:                             # noqa: BLE001
                continue
            actual: Optional[int] = self.device.registerData.get(reg)
            if actual is not None and actual != expected:
                msg: str = "{0}이(가) {1}입니다. {2}이어야 합니다 (0x{3:02X})".format(
                    label, names.get(actual, str(actual)),
                    names.get(expected, str(expected)), reg)
                warnings.append(msg)
                logger.warning("{}  -- dev/configure_sensor.py 를 실행하세요", msg)
        if not warnings:
            logger.info("Sensor configuration matches expectations")
        return warnings

    def drop(self) -> None:
        if self.device is not None:
            try:
                self.device.closeDevice()
            except Exception as exc:                      # noqa: BLE001 - never fatal
                logger.debug("Error closing the sensor: {}", exc)
        self.device = None
        self._next_attempt = time.monotonic() + self._backoff

    def read_angles(self) -> Optional[dict[str, float]]:
        """One angle block. None means the read failed and the link was dropped."""
        if self.device is None:
            return None
        try:
            if self.device.readReg(*ANGLE_BLOCK) is None:
                self.drop()
                return None
            return dict(self.device.deviceData)
        except Exception as exc:                          # noqa: BLE001
            logger.warning("Sensor read failed: {}", exc)
            self.drop()
            return None

    def read_temp(self) -> Optional[float]:
        if self.device is None:
            return None
        try:
            if self.device.readReg(*TEMP_REG) is None:
                return None
            return self.device.deviceData.get("Temp")
        except Exception as exc:                          # noqa: BLE001
            logger.debug("Temperature read failed: {}", exc)
            return None

    def close(self) -> None:
        self.drop()


# --------------------------------------------------------------------------
# Recorder
# --------------------------------------------------------------------------

@dataclass
class Status:
    """Snapshot for /api/status and the e-paper panel."""
    sensor_id: str = ""
    position: str = "UNSET"
    device_serial: str = ""
    config_warnings: list[str] = field(default_factory=list)
    state: str = WAITING_HTTP
    file: Optional[str] = None
    started_at: Optional[float] = None
    elapsed_s: float = 0.0
    samples: int = 0
    blocks: int = 0
    tilt: Optional[float] = None
    roll: Optional[float] = None
    pitch: Optional[float] = None
    temp_c: Optional[float] = None
    sensor_ok: bool = False
    time_quality: str = af.QUALITY_UNSYNCED
    stability: dict[str, Any] = field(default_factory=dict)
    free_mb: float = 0.0
    error: Optional[str] = None


class Recorder:
    def __init__(self, port: str, cfg: dict[str, Any], data_dir: str = DATA_DIR,
                 fail_on_no_sensor: bool = False) -> None:
        self.cfg: dict[str, Any] = cfg
        self.data_dir: str = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

        self.state: str = WAITING_HTTP
        self.status = Status()
        self.stop_event = threading.Event()
        self._http_seen = threading.Event()
        self._want_start = threading.Event()
        self._want_stop = threading.Event()
        self._want_discard = threading.Event()
        self._lock = threading.Lock()
        self.fail_on_no_sensor: bool = fail_on_no_sensor

        self.time = TimeKeeper(data_dir, int(cfg["min_valid_year"]))
        self.sensor = SensorLink(port)
        self.monitor = stability.StabilityMonitor(
            stability.Limits.from_config({"recorder": cfg}))

        # The Pi's hostname is the device identity: already unique per unit,
        # already the address the operator types, and impossible to leave blank.
        self.sensor_id: str = socket.gethostname()[:16]
        self.boot_count: int = af.next_boot_count(data_dir)
        self.writer: Optional[af.Writer] = None
        self._pending: list[tuple[float, af.Sample]] = []
        self._temp: float = 0.0
        self._block_flags: int = 0
        self._record_start_mono: float = 0.0
        self._record_start_epoch: float = 0.0
        self._next_time_save: float = 0.0
        self._next_ntp_check: float = 0.0
        self._since_judged: int = 0
        self._next_purge: float = 0.0
        self._next_ip_check: float = 0.0
        self._panel_ip: Optional[str] = None
        self.panel: Optional[Any] = None
        self._unstable_since: float = 0.0
        self._last_temp_at: float = 0.0
        logger.info("Boot #{} as {} [{}] in {}", self.boot_count, self.sensor_id,
                    af.POSITION_NAMES.get(self.position, "UNSET"), self.data_dir)
        if self.position == af.POS_UNSET:
            logger.warning("SENSOR_FLAG is not set; recordings will be named UNSET_*")

    # -- external hooks ---------------------------------------------------

    def note_http_request(self) -> None:
        """A valid request arrived. Someone is here, so this is not a measurement.

        A recording in progress gets discarded. Reaching the device means being
        beside it, which is the vehicle, not the crane -- the recording started
        only because nobody connected inside the boot window, and it is of a box
        sitting on a bench.

        Discarded means moved to the trash, not deleted: the judgement is about
        what belongs in the operator's list, and it should not be the kind of
        judgement that destroys data if it is wrong.

        The status page's own polling is exempt (it carries ?auto=1), or a
        browser left open in the vehicle would throw away a real measurement the
        moment the WiFi came back into range.
        """
        if not self._http_seen.is_set():
            logger.info("HTTP request seen; auto-recording is suppressed")
        self._http_seen.set()
        if self.state == RECORDING:
            logger.info("Operator connected while recording; discarding this recording")
            self._want_discard.set()

    @property
    def position(self) -> int:
        # Read from config each time, so changing it on the settings page takes
        # effect from the next recording without a restart (section 8).
        return af.POSITION_VALUES.get(
            str(self.cfg.get("sensor_flag", "unset")).lower(), af.POS_UNSET)

    def request_manual_start(self) -> bool:
        """Web asked to record now. Still waits for the sensor to settle.

        The settle wait is not skipped: starting mid-swing would put unusable
        data at the head of the file, which section 4 forbids outright.
        """
        if self.state in (RECORDING, WAITING_STABLE):
            return False
        self._want_start.set()
        return True

    def request_manual_stop(self) -> bool:
        if self.state != RECORDING:
            return False
        self._want_stop.set()
        return True

    def snapshot(self) -> Status:
        with self._lock:
            self.status.state = self.state
            self.status.sensor_id = self.sensor_id
            self.status.position = af.POSITION_NAMES.get(self.position, "UNSET")
            self.status.device_serial = self.sensor.serial
            self.status.config_warnings = list(self.sensor.config_warnings)
            self.status.sensor_ok = self.sensor.device is not None
            self.status.time_quality = self.time.quality
            self.status.free_mb = _free_mb(self.data_dir)
            if self._record_start_mono:
                self.status.elapsed_s = time.monotonic() - self._record_start_mono
            return self.status

    def request_stop(self) -> None:
        self.stop_event.set()

    # -- boot -------------------------------------------------------------

    def recover_leftovers(self) -> list[str]:
        """Step 1 of section 3: finish what the last power cut interrupted."""
        try:
            recovered: list[str] = af.recover_all(self.data_dir)
        except OSError as exc:
            logger.error("Recovery pass failed: {}", exc)
            return []
        if recovered:
            logger.info("Recovered {} file(s) from the previous run", len(recovered))
        return recovered

    def wait_for_http(self, seconds: float) -> bool:
        """Hold before recording. True if an operator connected (-> maintenance).

        Two separate reasons to wait, and they must not be confused:

        **The mount delay always applies.** The operator powers the device on at
        the bottom and then climbs the tower crane to fit it. Recording during
        the climb would put the ascent at the head of the file, so nothing starts
        until they have had time to get up there and mount it. No network is
        involved -- and no network is exactly the case where this matters.

        **The HTTP window only applies when there is a network.** It gives an
        operator standing beside the device a chance to connect before recording
        starts. With nothing associated nobody can connect, so waiting out that
        window would be time spent for no one.
        """
        mount: float = float(self.cfg.get("mount_delay_seconds", 60))
        if self._hold(mount, "설치 유예"):
            return True

        if not _link_up():
            logger.info("No network link; going straight to the measurement")
            return False

        # Associated: let DHCP finish, then give a full window from the moment
        # the address actually landed. Counting from boot is what made the
        # operator miss it while the WiFi was still coming up.
        self._await_address(float(self.cfg.get("network_wait_seconds", 90)))
        return self._hold(seconds, "접속 대기")

    def _hold(self, seconds: float, reason: str) -> bool:
        """Wait, polling the sensor. True if an operator connected meanwhile.

        Polling throughout means the stability window is already warm whichever
        way the wait ends, so recording can begin the moment the hold expires.
        """
        if seconds <= 0:
            return self._http_seen.is_set()
        logger.info("{} {:.0f}s", reason, seconds)
        deadline: float = time.monotonic() + seconds
        while time.monotonic() < deadline and not self.stop_event.is_set():
            if self._http_seen.is_set():
                return True
            self._poll_once(record=False)
        return self._http_seen.is_set()

    def _note_address(self) -> None:
        """Wake the panel when the address it prints changes.

        The panel is drawn on events, so a network that comes back while the
        device sits idle has to be pushed at it -- with nothing polling, the
        footer would otherwise keep saying NO NETWORK until something else
        happened to change the frame.

        Watching the address rather than the link is deliberate: the footer
        prints an address, so associating with an AP is not yet news.
        """
        ip: Optional[str] = _local_ip()
        if ip == self._panel_ip:
            return
        self._panel_ip = ip
        if self.panel is not None:
            self.panel.refresh_now()

    def _await_address(self, limit: float) -> None:
        """Once associated, give DHCP time to hand out an address."""
        deadline: float = time.monotonic() + limit
        while time.monotonic() < deadline and not self.stop_event.is_set():
            if self._http_seen.is_set() or _local_ip() is not None:
                return
            self._poll_once(record=False)
        logger.warning("Associated but no address after {:.0f}s", limit)

    # -- main loop --------------------------------------------------------

    def run(self, force: Optional[str] = None, run_seconds: Optional[float] = None) -> int:
        self.recover_leftovers()

        if not self.sensor.connect() and self.fail_on_no_sensor:
            logger.error("No sensor on {}", self.sensor.port)
            return 1

        if force == MAINTENANCE:
            self._set_state(MAINTENANCE)
        elif force in (RECORDING, WAITING_STABLE):
            self._set_state(WAITING_STABLE)
            if force == RECORDING:
                self._start_recording()
        else:
            self._set_state(WAITING_HTTP)
            if self.wait_for_http(float(self.cfg["http_wait_seconds"])):
                self._set_state(MAINTENANCE)
            else:
                self._set_state(WAITING_STABLE)

        deadline: Optional[float] = (time.monotonic() + run_seconds) if run_seconds else None
        try:
            while not self.stop_event.is_set():
                if deadline and time.monotonic() >= deadline:
                    logger.info("Reached the requested run time")
                    break
                self._tick()
        except KeyboardInterrupt:
            logger.info("Interrupted")
        finally:
            self._shutdown()
        return 0

    def _tick(self) -> None:
        now_mono: float = time.monotonic()

        if now_mono >= self._next_ntp_check:
            self._next_ntp_check = now_mono + float(self.cfg["ntp_retry_interval_seconds"])
            self._on_ntp_poll()

        if now_mono >= self._next_time_save:
            self._next_time_save = now_mono + float(self.cfg["time_save_interval_seconds"])
            self.time.save_last_known()

        if now_mono >= self._next_purge:
            self._next_purge = now_mono + 3600.0
            filestore.purge_trash(self.data_dir, float(self.cfg["trash_retention_days"]))

        # Manual control arrives as a flag rather than a direct call: the web
        # runs on another thread, and only this one may touch the open writer.
        if self._want_discard.is_set():
            self._want_discard.clear()
            if self.state == RECORDING:
                self._stop_recording(discard=True)
                self._set_state(MAINTENANCE)
        if self._want_stop.is_set():
            self._want_stop.clear()
            if self.state == RECORDING:
                logger.info("Stop requested from the web")
                self._stop_recording()
                self._set_state(MAINTENANCE)
        if self._want_start.is_set():
            self._want_start.clear()
            if self.state not in (RECORDING, WAITING_STABLE):
                logger.info("Start requested from the web")
                self.monitor.reset()
                self._set_state(WAITING_STABLE)

        if self.state == MAINTENANCE:
            # Idle on purpose: someone is here to collect files, so do not add
            # SD-card writes underneath them.
            time.sleep(0.2)
            return

        self._poll_once(record=(self.state == RECORDING))

        # Judging costs a few hundred trig calls, so do it once per block rather
        # than once per sample. A 5 s window does not need a 25 Hz opinion.
        self._since_judged += 1
        if self._since_judged < af.SAMPLES_PER_BLOCK:
            return
        self._since_judged = 0

        verdict = self.monitor.evaluate()
        self.status.stability = verdict.as_dict()

        if self.state == WAITING_STABLE:
            if verdict.stable:
                logger.info("Settled: {}", verdict.summary())
                self._start_recording()
            return

        if self.state == RECORDING and not verdict.stable:
            # Marks the blocks written from here until it settles again.
            self._block_flags |= af.FLAG_UNSTABLE
            if not self._unstable_since:
                self._unstable_since = time.monotonic()
                logger.warning("Unstable while recording: {}", verdict.reason)
            if self.cfg["stop_on_unstable"]:
                self._stop_recording()
                self._set_state(WAITING_STABLE)
        elif self.state == RECORDING:
            if self._unstable_since:
                logger.info("Settled again after {:.1f}s",
                            time.monotonic() - self._unstable_since)
            self._unstable_since = 0.0

    def _poll_once(self, record: bool) -> None:
        started: float = time.monotonic()
        # Here rather than in _tick: the boot holds spin on _poll_once and skip
        # _tick entirely, and that is precisely when the address first lands.
        if started >= self._next_ip_check:
            self._next_ip_check = started + float(
                self.cfg.get("ip_check_interval_seconds", 10))
            self._note_address()
        if not self.sensor.connect():
            time.sleep(0.2)
            return

        data: Optional[dict[str, float]] = self.sensor.read_angles()
        if data is None:
            self._block_flags |= af.FLAG_READ_FAILED
            return

        if self.sensor.reconnected:
            self._block_flags |= af.FLAG_RECONNECTED
            self.sensor.reconnected = False

        roll, pitch, yaw = data.get("AngX"), data.get("AngY"), data.get("AngZ")
        if roll is None or pitch is None or yaw is None:
            self._block_flags |= af.FLAG_READ_FAILED
            return

        # Everything one poll returns gets kept: the 15 registers arrive in a
        # single transaction, so dropping any of it saves no time, only data.
        sample = af.Sample(
            acc=(data.get("AccX", 0.0), data.get("AccY", 0.0), data.get("AccZ", 0.0)),
            gyro=(data.get("AsX", 0.0), data.get("AsY", 0.0), data.get("AsZ", 0.0)),
            mag=(data.get("HX", 0.0), data.get("HY", 0.0), data.get("HZ", 0.0)),
            roll=roll, pitch=pitch, yaw=yaw,
        )
        self.status.tilt = sample.tilt_pct
        self.status.roll = roll
        self.status.pitch = pitch
        self.monitor.add(stability.Sample(
            t=started, roll=roll, pitch=pitch, yaw=yaw,
            acc=sample.acc, gyro=sample.gyro,
        ))

        if started - self._last_temp_at >= TEMP_REFRESH_S:
            temp: Optional[float] = self.sensor.read_temp()
            if temp is not None:
                self._temp = temp
                self.status.temp_c = temp
            self._last_temp_at = started

        if record:
            self._append_sample(started, sample)

        # Pace to a steady rate; the sensor bandwidth is 10 Hz so 25 Hz keeps a
        # guard band above Nyquist.
        remaining: float = SAMPLE_PERIOD_S - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)

    # -- block assembly ---------------------------------------------------

    def _append_sample(self, mono: float, sample: af.Sample) -> None:
        elapsed_ms: float = (mono - self._record_start_mono) * 1000.0
        self._pending.append((elapsed_ms, sample))
        if len(self._pending) >= af.SAMPLES_PER_BLOCK:
            self._flush_block()

    def _flush_block(self) -> None:
        # A block is 25 samples, not one second: it goes out when it is full,
        # however long that took (section 5.2). Anything short is dropped.
        if self.writer is None:
            return
        if len(self._pending) < af.SAMPLES_PER_BLOCK:
            if self._pending:
                logger.info("Discarding {} sample(s) that did not fill a block",
                            len(self._pending))
                self._pending.clear()
            return
        first_ms: float = self._pending[0][0]
        last_ms: float = self._pending[-1][0]
        block = af.Block(
            elapsed_ms=int(first_ms),
            samples=[sample for _, sample in self._pending],
            temp_c=self._temp,
            duration_ms=int(last_ms - first_ms),
            flags=self._block_flags,
        )
        try:
            self.writer.write_block(block)
        except OSError as exc:
            logger.error("Write failed: {}", exc)
            self.status.error = str(exc)
            self._stop_recording()
            return
        self.status.samples += block.count
        self.status.blocks += 1
        self._pending.clear()
        self._block_flags = 0
        self._check_disk()
        self._maybe_segment()

    # -- recording lifecycle ----------------------------------------------

    def _start_recording(self) -> None:
        self._record_start_epoch = self.time.now()
        self._record_start_mono = time.monotonic()
        name: str = af.build_filename(self._record_start_epoch, self.time.quality,
                                      self.position, self.boot_count, partial=True)
        path: str = os.path.join(self.data_dir, name)
        header = af.Header(
            start_epoch=self._record_start_epoch,
            sensor_id=self.sensor_id,
            position=self.position,
            device_serial=self.sensor.serial,
            sample_rate_hz=SAMPLE_RATE_HZ,
        )
        try:
            self.writer = af.Writer(
                path, header, float(self.cfg["record_fsync_interval_seconds"]))
        except OSError as exc:
            logger.error("Cannot open {}: {}", name, exc)
            self.status.error = str(exc)
            return
        self._write_timeinfo(path)
        self.status.file = name
        self.status.started_at = self._record_start_epoch
        self.status.samples = 0
        self.status.blocks = 0
        self._set_state(RECORDING)

    def _stop_recording(self, discard: bool = False) -> None:
        if self.writer is None:
            return
        self._flush_block()
        path: str = self.writer.path
        try:
            self.writer.close()
        except OSError as exc:
            logger.error("Error closing {}: {}", os.path.basename(path), exc)
        self.writer = None
        logger.info("Stopped recording {} ({} blocks, {} samples)",
                    os.path.basename(path), self.status.blocks, self.status.samples)
        # Same finalising logic the next boot would have run, but this file was
        # closed in an orderly way, so it does not carry the .recovered mark.
        final: Optional[str] = None
        try:
            final = af.recover_partial(path, mark_recovered=False)
        except (OSError, af.FormatError, ValueError) as exc:
            logger.error("Could not finalise {}: {}", os.path.basename(path), exc)

        # The recording is over; stop reporting its name as the current file.
        # Leaving it set made /api/status look like a recording was still open.
        self.status.file = None

        if discard and final is not None:
            # Finalised first so what lands in the trash is a valid, readable
            # file -- recoverable by hand if this turns out to have been wrong.
            moved = filestore.move_to_trash(self.data_dir, [os.path.basename(final)])
            if moved:
                logger.info("Discarded to trash: {}", moved[0])
        self._record_start_mono = 0.0

    def _maybe_segment(self) -> None:
        minutes: float = float(self.cfg["segment_minutes"])
        if minutes <= 0 or self._record_start_mono == 0.0:
            return
        if time.monotonic() - self._record_start_mono >= minutes * 60.0:
            logger.info("Segment boundary at {:g} min", minutes)
            self._stop_recording()
            self._start_recording()

    # -- time -------------------------------------------------------------

    def _on_ntp_poll(self) -> None:
        correction: Optional[float] = self.time.poll_sync()
        if correction is None:
            return
        # The recording itself is untouched. Only the sidecar changes; the file
        # gets its corrected name during the next boot's recovery (section 3).
        if self.writer is not None:
            self._record_start_epoch += correction
            self._write_timeinfo(self.writer.path, correction)
            logger.info("Recorded a {:+.2f}s correction for {}",
                        correction, os.path.basename(self.writer.path))

    def _write_timeinfo(self, path: str, correction: float = 0.0) -> None:
        info: dict[str, Any] = {
            "device_start_epoch": self._record_start_epoch - correction,
            "quality": self.time.quality,
            "source": "ntp",
            "boot_count": self.boot_count,
            "original_filename": os.path.basename(path),
        }
        if correction:
            info["corrected_start_epoch"] = self._record_start_epoch
            info["offset_seconds"] = correction
            info["applied_at_elapsed_ms"] = int(
                (time.monotonic() - self._record_start_mono) * 1000.0)
        elif self.time.quality in af.TRUSTED_QUALITIES:
            # Trusted from the very start: record it so recovery can drop the
            # .unsynced marker without needing a correction to have happened.
            info["corrected_start_epoch"] = self._record_start_epoch
            info["offset_seconds"] = 0.0
        try:
            af.write_timeinfo(path, info)
        except OSError as exc:
            logger.warning("Could not write .timeinfo: {}", exc)

    # -- housekeeping -----------------------------------------------------

    def _check_disk(self) -> None:
        free: float = _free_mb(self.data_dir)
        if free >= float(self.cfg["min_free_mb"]):
            return
        # Files already collected are the cheapest thing to give up; losing the
        # recording is the expensive outcome (section 11).
        needed: int = int((float(self.cfg["min_free_mb"]) - free) * 1024 * 1024)
        purged: int = filestore.purge_trash(
            self.data_dir, float(self.cfg["trash_retention_days"]), max(needed, 0))
        if purged and _free_mb(self.data_dir) >= float(self.cfg["min_free_mb"]):
            logger.warning("Freed space by purging {} trashed file(s)", purged)
            return

        logger.error("Only {:.0f} MB free (< {} MB); stopping the recording",
                     free, self.cfg["min_free_mb"])
        self.status.error = "디스크 여유 부족"
        self._stop_recording()
        self._set_state(MAINTENANCE)

    def _set_state(self, state: str) -> None:
        if state != self.state:
            logger.info("State {} -> {}", self.state, state)
            if self.panel is not None:
                self.panel.refresh_now()
        self.state = state
        self.status.state = state

    def _shutdown(self) -> None:
        if self.panel is not None:
            self.panel.stop()
        self._stop_recording()
        self.time.save_last_known()
        self.sensor.close()
        logger.info("Recorder stopped")


def _link_up() -> bool:
    """True when some interface other than loopback is associated.

    /sys/class/net/*/carrier is 1 once WiFi has joined an AP, well before DHCP
    has finished, which is exactly the distinction needed here.
    """
    try:
        names: list[str] = os.listdir("/sys/class/net")
    except OSError:
        # Not Linux (a development machine). Fall back to asking for an address,
        # rather than silently reporting "no network" for want of a sysfs path.
        return _local_ip() is not None
    for name in names:
        if name == "lo":
            continue
        try:
            with open(os.path.join("/sys/class/net", name, "carrier")) as f:
                if f.read().strip() == "1":
                    return True
        except OSError:
            continue          # down interfaces refuse the read; that is a "no"
    return False


def _local_ip() -> Optional[str]:
    # No packet is sent; this just asks the kernel which source address a route
    # to the outside would use. None means nothing is up yet.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def _free_mb(path: str) -> float:
    try:
        return shutil.disk_usage(path).free / (1024.0 * 1024.0)
    except OSError:
        return 0.0


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="AUTO_RUN tilt recorder.")
    port_config.add_port_argument(parser)
    parser.add_argument("--force", choices=[WAITING_STABLE, RECORDING, MAINTENANCE],
                        help="Skip the HTTP wait and start in this state.")
    parser.add_argument("--seconds", type=float,
                        help="Stop after this long (bench testing).")
    parser.add_argument("--http-wait", type=float,
                        help="Override http_wait_seconds.")
    parser.add_argument("--data-dir", default=DATA_DIR)
    parser.add_argument("--require-sensor", action="store_true",
                        help="Exit non-zero if the sensor cannot be reached.")
    parser.add_argument("--no-web", action="store_true",
                        help="Do not start the HTTP server.")
    parser.add_argument("--no-panel", action="store_true",
                        help="Do not drive the e-paper panel.")
    parser.add_argument("--port-http", type=int, help="Override http_port.")
    args = parser.parse_args()

    cfg: dict[str, Any] = app_config.load()["recorder"]
    if args.http_wait is not None:
        cfg["http_wait_seconds"] = args.http_wait

    if args.port_http is not None:
        cfg["http_port"] = args.port_http

    rec = Recorder(port_config.resolve_port(args.port), cfg, args.data_dir,
                   fail_on_no_sensor=args.require_sensor)

    if not args.no_web:
        try:
            import web
            web.serve(rec, os.path.abspath(args.data_dir), cfg)
        except ImportError as exc:
            # No Flask on this machine: keep recording rather than refusing to
            # start. The measurement matters more than the web page.
            logger.error("Web interface unavailable ({}); recording anyway", exc)

    if not args.no_panel:
        try:
            import eink_panel
            rec.panel = eink_panel.PanelThread(rec, cfg)
            rec.panel.start()
        except Exception as exc:                          # noqa: BLE001
            logger.error("Panel unavailable ({}); recording anyway", exc)

    # systemd sends SIGTERM on stop; finish the current block rather than
    # leaving a torn tail for the next boot to trim.
    def _handle(signum: int, _frame: object) -> None:
        logger.info("Signal {} received", signum)
        rec.request_stop()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    return rec.run(force=args.force, run_seconds=args.seconds)


if __name__ == "__main__":
    sys.exit(main())
