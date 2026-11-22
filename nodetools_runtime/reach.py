"""ICMP reachability check for BMC addresses."""

import subprocess


def ping(ip, timeout=1.0):
    """Return True if ``ip`` answers ICMP, False otherwise, None if no address.

    Mirrors the legacy ``nodeping``: a single flood probe
    (``ping -M do -f -c 1 -W 1 -w 1``), which needs the same privileges the
    original script did.
    """
    if not ip:
        return None
    wait = max(1, int(round(timeout)))
    cmd = ["ping", "-M", "do", "-f", "-c", "1", "-W", str(wait), "-w", str(wait), ip]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=wait + 2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0
