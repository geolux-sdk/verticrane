# coding:UTF-8
# HTTP API and pages (sections 7 and 8).

from __future__ import annotations

import os
import tempfile
import time
from typing import Any, Iterator, Optional

from flask import (Flask, Response, current_app, jsonify, redirect,
                   render_template, request, stream_with_context, url_for)
from loguru import logger

import ahrs_file as af
import app_config
import filestore

CHUNK: int = 64 * 1024

# Requests that must not count as "the operator is here" (section 3). A probe
# from a health check or a browser fetching an icon is not someone collecting
# files, and treating it as such would suppress a whole measurement.
_IGNORED_PATHS: frozenset[str] = frozenset({"/favicon.ico", "/healthz"})


def _recorder() -> Any:
    return current_app.config["RECORDER"]


def _data_dir() -> str:
    return current_app.config["DATA_DIR"]


def _cfg() -> dict[str, Any]:
    return current_app.config["RECORDER_CFG"]


def _gap() -> float:
    return float(_cfg().get("merge_gap_tolerance_seconds", 2.0))


def _pin_ok() -> bool:
    pin: str = request.form.get("pin") or request.headers.get("X-PIN", "")
    return app_config.verify_pin(app_config.load(), pin)


def register(app: Flask) -> None:

    @app.before_request
    def _note_visitor() -> None:
        if request.path in _IGNORED_PATHS or request.path.startswith("/static/"):
            return
        rec: Any = _recorder()
        if rec is not None:
            rec.note_http_request()

    # -- pages ------------------------------------------------------------

    @app.get("/")
    def index() -> str:
        rec: Any = _recorder()
        return render_template(
            "index.html",
            status=rec.snapshot() if rec else None,
            files=filestore.list_files(_data_dir(), _gap()),
            stats=filestore.stats(_data_dir()),
            cfg=_cfg(),
        )

    @app.get("/settings")
    def settings() -> str:
        return render_template("settings.html", fields=_FIELDS, cfg=_cfg(),
                               status=_recorder().snapshot() if _recorder() else None,
                               device_time=time.strftime("%Y-%m-%d %H:%M:%S"),
                               message=request.args.get("m"),
                               error=request.args.get("e"))

    @app.post("/settings")
    def save_settings() -> Response:
        if not _pin_ok():
            return redirect(url_for("settings", e="PINCODE가 올바르지 않습니다."))

        cfg: dict[str, Any] = _cfg()
        staged: dict[str, Any] = {}
        for field in _FIELDS:
            raw: Optional[str] = request.form.get(field.key)
            if field.kind is bool:
                staged[field.key] = raw is not None
                continue
            if raw is None or raw.strip() == "":
                continue
            try:
                staged[field.key] = field.parse(raw.strip())
            except ValueError as exc:
                return redirect(url_for("settings", e="{0}: {1}".format(field.label, exc)))

        # Written only once every field validated, so a bad entry half way down
        # the form cannot leave the recorder running on a mixed configuration.
        stored: dict[str, Any] = app_config.load()
        stored.setdefault("recorder", {}).update(staged)
        try:
            app_config.save(stored)
        except OSError as exc:
            return redirect(url_for("settings", e="저장 실패: {0}".format(exc)))

        # The running recording keeps the limits it started with; the new values
        # apply from the next stability judgement (section 8).
        cfg.update(staged)
        logger.info("Settings changed: {}", ", ".join(sorted(staged)))
        return redirect(url_for("settings", m="저장했습니다."))

    # -- status -----------------------------------------------------------

    @app.get("/api/status")
    def api_status() -> Response:
        rec: Any = _recorder()
        if rec is None:
            return jsonify({"error": "recorder not attached"}), 503
        snap = rec.snapshot()
        return jsonify({
            "sensor_id": snap.sensor_id,
            "position": snap.position,
            "device_serial": snap.device_serial,
            "config_warnings": snap.config_warnings,
            "state": snap.state,
            "file": snap.file,
            "started_at": snap.started_at,
            "elapsed_s": round(snap.elapsed_s, 1),
            "samples": snap.samples,
            "blocks": snap.blocks,
            "tilt_pct": round(snap.tilt, 4) if snap.tilt is not None else None,
            "temp_c": snap.temp_c,
            "sensor_ok": snap.sensor_ok,
            "time_quality": snap.time_quality,
            "device_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "stability": snap.stability,
            "free_mb": round(snap.free_mb, 1),
            "error": snap.error,
        })

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify({"ok": True})

    # -- files ------------------------------------------------------------

    @app.get("/api/files")
    def api_files() -> Response:
        infos = filestore.list_files(_data_dir(), _gap())
        groups: dict[int, dict[str, Any]] = {}
        for info in infos:
            g = groups.setdefault(info.group, {"group": info.group, "files": 0,
                                               "bytes": 0, "duration_s": 0.0})
            g["files"] += 1
            g["bytes"] += info.size
            g["duration_s"] = round(g["duration_s"] + info.duration_s, 1)
        return jsonify({"files": [i.as_dict() for i in infos],
                        "groups": sorted(groups.values(), key=lambda g: g["group"]),
                        "stats": filestore.stats(_data_dir())})

    @app.get("/api/files/<path:filename>")
    def api_download(filename: str) -> Response:
        path: Optional[str] = af.safe_join(_data_dir(), filename)
        if path is None or not os.path.exists(path):
            return jsonify({"error": "not found"}), 404
        return _send([path], os.path.basename(path))

    @app.get("/api/groups/<int:group>")
    def api_download_group(group: int) -> Response:
        members = filestore.group_members(_data_dir(), group, _gap())
        if not members:
            return jsonify({"error": "not found"}), 404
        if len(members) == 1:
            return _send([members[0].path], members[0].name)

        # Merged into one valid .ahrsbin so the operator gets a single file for
        # a single measurement, named after its corrected start (section 5.4).
        fd, merged = tempfile.mkstemp(suffix=af.EXT, prefix="merge_")
        os.close(fd)
        try:
            af.merge([m.path for m in members], merged)
        except (OSError, af.FormatError) as exc:
            os.unlink(merged)
            logger.error("Merge of group {} failed: {}", group, exc)
            return jsonify({"error": str(exc)}), 500
        return _send([merged], members[0].name, cleanup=merged)

    @app.post("/api/files/<path:filename>/collected")
    def api_collected(filename: str) -> Response:
        """The operator's browser confirming it has the file on disk.

        This, not the end of the transfer, is what retires a recording. It is
        sent only after the whole body has been read and handed to the browser
        to save, so a connection that dropped part way through never gets here
        and the file stays in the list (section 7).
        """
        if not bool(_cfg().get("delete_after_download", True)):
            return jsonify({"retired": [], "reason": "disabled"})
        path: Optional[str] = af.safe_join(_data_dir(), filename)
        if path is None or not os.path.exists(path):
            return jsonify({"error": "not found"}), 404
        return jsonify({"retired": filestore.move_to_trash(
            _data_dir(), [os.path.basename(path)])})

    @app.post("/api/groups/<int:group>/collected")
    def api_group_collected(group: int) -> Response:
        if not bool(_cfg().get("delete_after_download", True)):
            return jsonify({"retired": [], "reason": "disabled"})
        members = filestore.group_members(_data_dir(), group, _gap())
        if not members:
            return jsonify({"error": "not found"}), 404
        # The operator received the merged whole, so the whole group retires.
        return jsonify({"retired": filestore.move_to_trash(
            _data_dir(), [m.name for m in members])})

    @app.delete("/api/files/<path:filename>")
    def api_delete(filename: str) -> Response:
        if not _pin_ok():
            return jsonify({"error": "PIN required"}), 403
        path: Optional[str] = af.safe_join(_data_dir(), filename)
        if path is None or not os.path.exists(path):
            return jsonify({"error": "not found"}), 404
        moved = filestore.move_to_trash(_data_dir(), [os.path.basename(path)])
        return jsonify({"deleted": moved})

    # -- recording control -------------------------------------------------

    @app.post("/api/record/start")
    def api_start() -> Response:
        rec: Any = _recorder()
        if rec is None:
            return jsonify({"error": "recorder not attached"}), 503
        started: bool = rec.request_manual_start()
        return jsonify({"ok": started, "state": rec.snapshot().state})

    @app.post("/api/record/stop")
    def api_stop() -> Response:
        rec: Any = _recorder()
        if rec is None:
            return jsonify({"error": "recorder not attached"}), 503
        stopped: bool = rec.request_manual_stop()
        return jsonify({"ok": stopped, "state": rec.snapshot().state})


# --------------------------------------------------------------------------
# Download helper
# --------------------------------------------------------------------------

def _send(paths: list[str], display_name: str,
          cleanup: Optional[str] = None) -> Response:
    """Stream a recording out. Never retires it -- see api_collected.

    A finished generator only means the bytes reached the socket, not the
    operator. A file smaller than one chunk leaves here in a single write and
    the loop ends long before anything is delivered, so "the download finished"
    is not something this side can observe. The client says so instead.
    """
    total: int = sum(os.path.getsize(p) for p in paths)

    def generate() -> Iterator[bytes]:
        try:
            for path in paths:
                with open(path, "rb") as f:
                    while True:
                        chunk: bytes = f.read(CHUNK)
                        if not chunk:
                            break
                        yield chunk
        finally:
            if cleanup and os.path.exists(cleanup):
                os.unlink(cleanup)

    response = Response(stream_with_context(generate()),
                        mimetype="application/octet-stream")
    response.headers["Content-Length"] = str(total)
    response.headers["Content-Disposition"] = 'attachment; filename="{0}"'.format(display_name)
    return response


# --------------------------------------------------------------------------
# Editable settings (section 8)
# --------------------------------------------------------------------------

class Field:
    def __init__(self, key: str, label: str, kind: type, unit: str = "",
                 low: Optional[float] = None, high: Optional[float] = None,
                 note: str = "", choices: Optional[list[tuple[str, str]]] = None) -> None:
        self.key, self.label, self.kind = key, label, kind
        self.unit, self.low, self.high, self.note = unit, low, high, note
        # (value, label) pairs. A position typed by hand could be wrong and the
        # mistake would only surface once the file is unattributable.
        self.choices = choices

    def parse(self, raw: str) -> Any:
        if self.choices is not None:
            allowed = {value for value, _ in self.choices}
            if raw not in allowed:
                raise ValueError("{0} 중에서 골라야 합니다".format(", ".join(sorted(allowed))))
            return raw
        try:
            value: Any = self.kind(raw)
        except ValueError:
            raise ValueError("{0} 값이어야 합니다".format(
                "정수" if self.kind is int else "숫자"))
        if self.low is not None and value < self.low:
            raise ValueError("{0} 이상이어야 합니다".format(self.low))
        if self.high is not None and value > self.high:
            raise ValueError("{0} 이하여야 합니다".format(self.high))
        return value


_FIELDS: list[Field] = [
    Field("sensor_flag", "설치 위치", str, "",
          choices=[("unset", "미설정"), ("base", "BASE"),
                   ("middle", "MIDDLE"), ("top", "TOP")],
          note="크레인의 어느 높이인지. 파일명 앞에 붙고, 미설정이면 UNSET_ 이 됩니다."),
    Field("contact_face", "접촉면", str, "",
          choices=[("bottom", "아랫면"), ("top", "윗면"),
                   ("left", "왼쪽"), ("right", "오른쪽")],
          note="구조물에 닿는 면. 패널 그림에 반영됩니다."),
    Field("http_wait_seconds", "HTTP 대기 시간", int, "초", 5, 600,
          "부팅 후 이 시간 안에 접속이 있으면 자동 기록을 하지 않습니다."),
    Field("stability_window_seconds", "안정화 판정 창", float, "초", 1, 120,
          "최근 이 시간만큼의 데이터로 판정합니다."),
    Field("stability_min_samples", "최소 샘플 수", int, "개", 10, 10000,
          "창 안에 이만큼은 있어야 판정합니다. 25 Hz 기준 100개 = 4초."),
    Field("gyro_rms_max_dps", "자이로 RMS 기준", float, "°/s", 0.001, 100,
          "실측 정지 상태는 0.07 이하입니다."),
    Field("accel_std_max_g", "가속도 표준편차 기준", float, "g", 0.0001, 10,
          "실측 정지 상태는 0.002 이하입니다."),
    Field("attitude_std_max_deg", "자세각 표준편차 기준", float, "°", 0.001, 90,
          "실측 정지 상태는 0.06 이하입니다."),
    Field("record_fsync_interval_seconds", "강제 동기화 주기", float, "초", 0.1, 60,
          "전원이 끊겼을 때 잃는 최대 시간입니다."),
    Field("segment_minutes", "파일 분할 주기", int, "분", 0, 1440,
          "0이면 전원이 꺼질 때까지 한 파일에 기록합니다."),
    Field("slope_threshold_pct", "기울기 경보 임계값", float, "%", 0.001, 100,
          "패널에 ALARM을 띄우는 기준입니다."),
    Field("trash_retention_days", "휴지통 보관 기간", int, "일", 0, 365,
          "다운로드한 파일을 이 기간 뒤에 실제로 지웁니다."),
    Field("min_free_mb", "최소 여유 공간", int, "MB", 50, 100000,
          "이 아래로 내려가면 휴지통을 비우고, 그래도 모자라면 기록을 멈춥니다."),
    Field("stop_on_unstable", "불안정 시 기록 중단", bool, "",
          note="보통은 꺼 둡니다. 흔들림 자체가 기록해야 할 현상입니다."),
    Field("delete_after_download", "다운로드한 파일 정리", bool, "",
          note="받은 파일을 목록에서 치웁니다. 휴지통에 보관 기간 동안 남습니다."),
]
