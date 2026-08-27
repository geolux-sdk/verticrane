# coding:UTF-8
# Operator web interface for the AUTO_RUN recorder (sections 7 and 8).
#
# Runs inside the recorder process on its own thread, so the status it reports
# is the live object rather than a copy passed through a file or a socket.
#
# Everything here is written for an internal network: the vehicle's WiFi with
# one operator on it. Listing and downloading need no credentials; changing
# settings or deleting a file needs the PIN that already guards /setup.

from __future__ import annotations

from typing import Any, Optional

from flask import Flask
from loguru import logger

from web.routes import register


def create_app(recorder: Any, data_dir: str, cfg: dict[str, Any]) -> Flask:
    app = Flask(__name__)
    app.config["RECORDER"] = recorder
    app.config["DATA_DIR"] = data_dir
    app.config["RECORDER_CFG"] = cfg
    # Korean filenames and messages must survive JSON encoding intact.
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False
    register(app)
    return app


def serve(recorder: Any, data_dir: str, cfg: dict[str, Any]) -> Optional[Any]:
    """Start the web server on a daemon thread. Never fatal to the recording.

    A recorder that cannot serve pages is still doing its job; one that dies
    because a port was busy has lost the measurement.
    """
    import threading
    from werkzeug.serving import make_server

    port: int = int(cfg.get("http_port", 8080))
    app: Flask = create_app(recorder, data_dir, cfg)
    try:
        server = make_server("0.0.0.0", port, app, threaded=True)
    except OSError as exc:
        logger.error("Web server could not bind port {}: {}", port, exc)
        return None

    thread = threading.Thread(target=server.serve_forever, name="web", daemon=True)
    thread.start()
    logger.info("Web interface on http://0.0.0.0:{}", port)
    return server
