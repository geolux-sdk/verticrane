"""Network information shared by the recorder and panel."""

import socket
from typing import Optional


def local_ip() -> Optional[str]:
    """Ask the routing table for the source IP without sending a packet."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None
