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
        # The status page polls itself while recording. Those requests must not
        # read as "the operator is here", or a browser left open in the vehicle
        # would stop a measurement the moment the WiFi came back in range.
        if request.args.get("auto"):
            return
        rec: Any = _recorder()
        if rec is not None:
            rec.note_http_request()

    # -- pages ------------------------------------------------------------

    @app.get("/")
    def index() -> str:
        rec: Any = _recorder()
        files = filestore.list_files(_data_dir(), _gap())
        return render_template(
            "index.html",
            status=rec.snapshot() if rec else None,
            files=files,
            # (anchor, count) per group. Built here rather than in the template
            # so the merged-download links are addressed by a file name, which
            # survives another group being retired underneath them.
            merge_groups=filestore.group_anchors(files),
            stats=filestore.stats(_data_dir()),
            cfg=_cfg(),
        )

    @app.get("/files")
    def browse() -> str:
        rel: str = request.args.get("path", "")
        listing = filestore.list_dir(_data_dir(), rel)
        if listing is None:
            listing = ("", [])
        here, entries = listing
        rec: Any = _recorder()
        return render_template("browse.html",
                               status=rec.snapshot() if rec else None,
                               here=here, entries=entries,
                               parent=filestore.parent_of(here),
                               stats=filestore.stats(_data_dir()),
                               cfg=_cfg())

    @app.get("/api/browse")
    def api_browse() -> Response:
        listing = filestore.list_dir(_data_dir(), request.args.get("path", ""))
        if listing is None:
            return jsonify({"error": "not found"}), 404
        here, entries = listing
        return jsonify({"path": here,
                        "parent": filestore.parent_of(here),
                        "entries": [e.as_dict() for e in entries]})

    @app.get("/api/raw/<path:rel>")
    def api_raw(rel: str) -> Response:
        """Any file on the card, byte for byte.

        Reading only, and it never retires anything: this is the way to look
        at what the file list deliberately leaves out, and a page for seeing
        the trash that tidied the trash as you looked would be its own bug.
        """
        path: Optional[str] = filestore.resolve_within(_data_dir(), rel)
        if path is None or not os.path.isfile(path):
            return jsonify({"error": "not found"}), 404
        return _send([path], os.path.basename(path))

    @app.get("/settings")
    def settings() -> str:
        groups: list[tuple[str, list[Field]]] = []
        for field in _FIELDS:
            if not groups or groups[-1][0] != field.group:
                groups.append((field.group, []))
            groups[-1][1].append(field)
        return render_template("settings.html", groups=groups, cfg=_cfg(),
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
        # The anchor, not the number, is what /api/groups/... takes: the number
        # is only here so a caller can still see how the files were split.
        for anchor, _count in filestore.group_anchors(infos):
            groups[anchor.group]["anchor"] = anchor.name
        return jsonify({"files": [i.as_dict() for i in infos],
                        "groups": sorted(groups.values(), key=lambda g: g["group"]),
                        "stats": filestore.stats(_data_dir())})

    @app.get("/api/files/<path:filename>")
    def api_download(filename: str) -> Response:
        # Resolved through locate(), so a recording that the collect POST has
        # already retired still comes back. Both requests leave the browser on
        # one click and there is no order between them; the operator asked for
        # a recording, and where it sits on the card is not their problem.
        path: Optional[str] = filestore.locate(_data_dir(), filename)
        if path is None:
            return jsonify({"error": "not found"}), 404
        return _send([path], os.path.basename(path))

    @app.get("/api/groups/<anchor>")
    def api_download_group(anchor: str) -> Response:
        members = filestore.group_containing(_data_dir(), anchor, _gap())
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
            logger.error("Merge of the group at {} failed: {}", anchor, exc)
            return jsonify({"error": str(exc)}), 500
        return _send([merged], members[0].name, cleanup=merged)

    @app.post("/api/files/<path:filename>/collected")
    def api_collected(filename: str) -> Response:
        """The operator's browser saying it has asked for the file.

        This, not the end of the transfer, is what retires a recording -- and
        it is sent on the click, in parallel with the download itself, because
        a native download is independent of the page and its completion is not
        something the page can observe.

        So this can land before, during or after the transfer, and none of the
        three is a problem: retiring is a move into the trash, _send() holds an
        open descriptor, and locate() finds a retired file anyway. The file
        keeps its days in the trash whichever way the transfer went (section 7).
        """
        if not bool(_cfg().get("delete_after_download", True)):
            return jsonify({"retired": [], "reason": "disabled"})
        path: Optional[str] = af.safe_join(_data_dir(), filename)
        if path is None or not os.path.exists(path):
            return jsonify({"error": "not found"}), 404
        return jsonify({"retired": filestore.move_to_trash(
            _data_dir(), [os.path.basename(path)])})

    @app.post("/api/groups/<anchor>/collected")
    def api_group_collected(anchor: str) -> Response:
        if not bool(_cfg().get("delete_after_download", True)):
            return jsonify({"retired": [], "reason": "disabled"})
        members = filestore.group_containing(_data_dir(), anchor, _gap())
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

    Every file is opened here, before there is a response, and the body is read
    from those descriptors rather than from the paths again. What retires a
    recording is a request that arrives *alongside* this one -- one click sends
    both -- so between sizing the body and yielding the first chunk the file can
    have been moved into the trash. A descriptor does not care; a path does, and
    caring showed up as a 404, a 500, or a body that stopped short of the
    Content-Length already promised.
    """
    handles: list[Any] = []
    try:
        for path in paths:
            handles.append(open(path, "rb"))
    except OSError as exc:
        for handle in handles:
            handle.close()
        if cleanup and os.path.exists(cleanup):
            os.unlink(cleanup)
        logger.error("Cannot open {} to send: {}", display_name, exc)
        return jsonify({"error": "not found"}), 404

    sizes: list[int] = [os.fstat(h.fileno()).st_size for h in handles]
    total: int = sum(sizes)
    start, end = _wanted_range(total)
    partial: bool = (start, end) != (0, total - 1)

    def generate() -> Iterator[bytes]:
        try:
            offset: int = 0            # where this file begins in the whole body
            for handle, size in zip(handles, sizes):
                if offset + size <= start:
                    offset += size     # entirely before the requested range
                    continue
                if offset > end:
                    break
                if start > offset:
                    handle.seek(start - offset)
                remaining: int = min(end, offset + size - 1) - max(start, offset) + 1
                while remaining > 0:
                    chunk: bytes = handle.read(min(CHUNK, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
                offset += size
        finally:
            for handle in handles:
                handle.close()
            # After the descriptors, not before: Windows refuses to unlink a
            # file that is still open, and dev/fake_card.py runs there.
            if cleanup and os.path.exists(cleanup):
                os.unlink(cleanup)

    response = Response(stream_with_context(generate()),
                        status=206 if partial else 200,
                        mimetype="application/octet-stream")
    response.headers["Content-Length"] = str(end - start + 1)
    response.headers["Accept-Ranges"] = "bytes"
    if partial:
        response.headers["Content-Range"] = "bytes {0}-{1}/{2}".format(start, end, total)
    response.headers["Content-Disposition"] = 'attachment; filename="{0}"'.format(display_name)
    return response


def _wanted_range(total: int) -> tuple[int, int]:
    """The byte range the client asked for, clamped to what exists.

    Whole-body on anything unparseable rather than a 416. The vehicle's WiFi
    drops as it moves off (section 1), so a transfer that got most of the way
    and has to start again is the normal case here, not an edge one -- and a
    client that asks awkwardly should still get its file.

    Only the single `bytes=N-M` form is honoured. Multipart ranges would mean a
    multipart body for no gain: nobody here wants two pieces of a recording.
    """
    header: str = request.headers.get("Range", "")
    if not header.startswith("bytes=") or "," in header:
        return 0, total - 1
    spec: str = header[len("bytes="):].strip()
    try:
        if spec.startswith("-"):                       # last N bytes
            start, end = max(total - int(spec[1:]), 0), total - 1
        else:
            first, _, last = spec.partition("-")
            start = int(first)
            end = int(last) if last else total - 1
    except ValueError:
        return 0, total - 1
    end = min(end, total - 1)
    if start > end or start < 0:
        return 0, total - 1
    return start, end


# --------------------------------------------------------------------------
# Editable settings (section 8)
# --------------------------------------------------------------------------

class Field:
    def __init__(self, key: str, label: str, kind: type, unit: str = "",
                 low: Optional[float] = None, high: Optional[float] = None,
                 note: str = "", choices: Optional[list[tuple[str, str]]] = None,
                 group: str = "") -> None:
        self.key, self.label, self.kind = key, label, kind
        self.unit, self.low, self.high, self.note = unit, low, high, note
        # Grouped on the page, so the one setting that must not be left alone
        # does not sit in a list of six that all look equally optional.
        self.group = group
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
    Field("sensor_flag", "설치 위치", str, "", group="설치",
          choices=[("unset", "미설정"), ("base", "BASE"),
                   ("middle", "MIDDLE"), ("top", "TOP")],
          note="크레인의 어느 높이인지. 파일명 앞에 붙습니다. "
               "지정하지 않으면 UNSET_ 이 되어 나중에 구분할 수 없습니다."),
    Field("http_wait_seconds", "접속 대기 시간", int, "초", 5, 600, group="기록",
          note="전원을 켠 뒤 이 시간 안에 접속하면 기록하지 않고 기다립니다. "
               "접속이 없으면 자동으로 기록을 시작합니다."),
    Field("segment_minutes", "파일 분할 주기", int, "분", 0, 1440, group="기록",
          note="0이면 전원이 꺼질 때까지 한 파일에 기록합니다. "
               "값을 주면 그 주기마다 파일을 끊습니다."),
    Field("delete_after_download", "다운로드한 파일 정리", bool, "", group="저장 공간",
          note="받은 파일을 목록에서 치웁니다. 아래 기간 동안 휴지통에 남습니다."),
    Field("trash_retention_days", "휴지통 보관 기간", int, "일", 0, 365, group="저장 공간",
          note="받은 파일을 이 기간이 지나면 실제로 지웁니다."),
    Field("min_free_mb", "최소 여유 공간", int, "MB", 50, 100000, group="저장 공간",
          note="이 아래로 내려가면 휴지통부터 비우고, 그래도 모자라면 기록을 멈춥니다."),
]

# What is NOT here: the stability limits, the sample rate and the fsync interval.
# Those are engineering decisions, not operator ones -- a stability limit set by
# hand either stops recordings from ever starting or starts them mid-swing, and
# a setting on this page is a setting somebody eventually changes. They live in
# config.json, reachable over SSH.
