"""ICMP reachability check for BMC addresses."""

import subprocess


def ping(ip, timeout=1.0, count=1):
    """Return True if ``ip`` answers ICMP, False otherwise, None if no address."""
    if not ip:
        return None
    wait = max(1, int(round(timeout)))
    cmd = ["ping", "-n", "-c", str(count), "-W", str(wait), ip]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=wait * count + 2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0
