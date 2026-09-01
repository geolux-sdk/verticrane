# coding:UTF-8
# A card full of recordings, and the operator's web pages served over it, on a
# machine with no Pi and no sensor attached.
#
#   python dev/fake_card.py --serve
#   python dev/fake_card.py --scenario collected --serve
#
# Everything the file list, the download and the settings pages do lives in
# web/ and filestore.py, and neither touches the serial port or the panel. So
# this is not a simulation of those pages: it is those pages, over a directory
# built to look like a card that has come off a crane.
#
# What it cannot show is the sensor loop -- for that the suites in test_*.py
# already build a Recorder on a port that will never open.

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger

import ahrs_file as af
import app_config
import filestore
import recorder as rec_mod

DEFAULT_DIR: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_card")


# --------------------------------------------------------------------------
# Writing recordings that look like they came off a crane
# --------------------------------------------------------------------------

def _sample(roll: float, pitch: float) -> af.Sample:
    return af.Sample(acc=(0.0, 0.0, 1.0), gyro=(0.0, 0.0, 0.0),
                     mag=(0.0, 0.0, 0.0), roll=roll, pitch=pitch, yaw=0.0)


def write_recording(path: str, start_epoch: float, seconds: int,
                    position: int = af.POS_TOP) -> str:
    """One recording of `seconds` crane time. A block is a second (25 Hz x 25).

    The tilt walks slowly rather than sitting still, so a file opened in the
    analysis tools shows something other than a flat line.
    """
    header = af.Header(start_epoch=start_epoch, sensor_id="fakecard",
                       position=position, device_serial="FAKE-0001",
                       sample_rate_hz=rec_mod.SAMPLE_RATE_HZ)
    writer = af.Writer(path, header, 3600.0)
    for i in range(seconds):
        roll: float = -0.22 + 0.01 * ((i // 60) % 5)
        pitch: float = 0.105 + 0.005 * ((i // 90) % 4)
        samples = [_sample(roll, pitch) for _ in range(af.SAMPLES_PER_BLOCK)]
        writer.write_block(af.Block(elapsed_ms=i * 1000, samples=samples,
                                    temp_c=24.5, duration_ms=1000))
    writer.close()
    return path


def build(data_dir: str, scenario: str) -> None:
    """Lay out a card for one of the situations the bug report could have been.

    Recordings are placed back to back where they are meant to group: the list
    joins two files when the gap between them is under the tolerance, so the
    start of the second has to be the end of the first.
    """
    if os.path.isdir(data_dir):
        shutil.rmtree(data_dir)
    os.makedirs(data_dir)

    now: float = time.time()
    trash: str = filestore.trash_path(data_dir)
    corrupt: str = os.path.join(data_dir, filestore.CORRUPT_DIR)

    if scenario == "fresh":
        # Two segments of one measurement, then an unrelated one an hour later.
        write_recording(os.path.join(data_dir, "TOP_004.dat"), now - 7200, 1800)
        write_recording(os.path.join(data_dir, "TOP_005.dat"), now - 5400, 1200)
        write_recording(os.path.join(data_dir, "BASE_001.dat"), now - 600, 300,
                        position=af.POS_BASE)

    elif scenario == "powercut":
        # What the card looks like between the power coming back and recovery
        # finishing: the whole measurement is still a .partial, so the list is
        # empty even though the data is right there. Serve this one WITHOUT
        # --recover to see the page the operator reported.
        write_recording(os.path.join(data_dir, "TOP_006.dat.partial"), now - 28800, 28800)

    elif scenario == "collected":
        # Nothing left to collect, and the trash holding both a file that was
        # downloaded and one that was discarded because someone connected --
        # which the empty-list message today reports as the same thing.
        os.makedirs(trash)
        write_recording(os.path.join(trash, "TOP_002.dat"), now - 20000, 900)
        write_recording(os.path.join(trash, "TOP_003.dat"), now - 10000, 600)

    elif scenario == "corrupt":
        # A recording quarantined at boot. Nothing on any page says it exists.
        os.makedirs(corrupt)
        write_recording(os.path.join(corrupt, "TOP_002.dat.partial"), now - 9000, 400)
        write_recording(os.path.join(data_dir, "TOP_003.dat"), now - 3600, 1500)

    elif scenario == "empty":
        pass

    else:
        raise SystemExit("unknown scenario: {0}".format(scenario))

    with open(os.path.join(data_dir, af.SLOT_FILE), "w", encoding="utf-8") as f:
        f.write("6")

    for info in filestore.list_files(data_dir):
        logger.info("listed  {}  {}  {:.0f}s  group {}",
                    info.name, info.position, info.duration_s, info.group)
    logger.info("card at {} ({})", data_dir, filestore.stats(data_dir))


# --------------------------------------------------------------------------
# A recorder that reports a state without owning a sensor
# --------------------------------------------------------------------------

class FakeRecorder:
    """Enough of a Recorder for the pages to render their live half.

    The web layer only ever asks for a snapshot and tells it that someone has
    arrived, so those two are the whole surface. Keeping it here rather than
    importing the real one means the pages can be seen in a state -- recording,
    say -- without waiting for a state machine to walk into it.
    """

    def __init__(self, state: str = rec_mod.MAINTENANCE, data_dir: str = "") -> None:
        self.state = state
        self.data_dir = data_dir
        self.visits = 0

    def snapshot(self) -> rec_mod.Status:
        status = rec_mod.Status()
        status.state = self.state
        status.sensor_id = "fakecard"
        status.position = "TOP"
        status.device_serial = "FAKE-0001"
        status.sensor_ok = True
        status.tilt = 0.4255
        status.roll, status.pitch = -0.22, 0.105
        status.temp_c = 24.5
        status.free_mb = filestore.free_mb(self.data_dir or ".")
        if self.state == rec_mod.RECORDING:
            status.file = "TOP_006.dat.partial"
            status.started_at = time.time() - 1234
            status.elapsed_s = 1234.0
            status.samples, status.blocks = 30850, 1234
        return status

    def note_http_request(self) -> None:
        self.visits += 1
        logger.info("visit #{} (the real recorder would discard a recording here)",
                    self.visits)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a fake card and serve the pages over it.")
    parser.add_argument("--data-dir", default=DEFAULT_DIR)
    parser.add_argument("--scenario", default="fresh",
                        choices=["fresh", "powercut", "collected", "corrupt", "empty"])
    parser.add_argument("--keep", action="store_true",
                        help="Serve the directory as it is instead of rebuilding it.")
    parser.add_argument("--recover", action="store_true",
                        help="Finalise leftover .partial files, as boot does.")
    parser.add_argument("--state", default=rec_mod.MAINTENANCE,
                        choices=[rec_mod.WAITING_HTTP, rec_mod.WAITING_STABLE,
                                 rec_mod.RECORDING, rec_mod.MAINTENANCE])
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    app_config.setup_logging()
    data_dir: str = os.path.abspath(args.data_dir)

    if not args.keep:
        build(data_dir, args.scenario)
    if args.recover:
        logger.info("recovered {}", af.recover_all(data_dir))

    if not args.serve:
        return 0

    import web
    cfg = app_config.load()["recorder"]
    app = web.create_app(FakeRecorder(args.state, data_dir), data_dir, cfg)
    logger.info("http://127.0.0.1:{}/  (Ctrl-C to stop)", args.port)
    # Bound to the loopback on purpose: this card is made up, and a page saying
    # a crane is at 0.4% has no business being reachable from the office LAN.
    app.run(host="127.0.0.1", port=args.port, threaded=True, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
