"""Log Wemos debug telemetry (port 4211) to wemos_debug.log, 1 line/s.
Flags reboots (uptime reset), loop stalls, and silence periods. Decodes the
firmware reset reason when present (27-byte format) and stays compatible with
the older 26-byte beacon.

Also logs Wi-Fi association transitions. Without them a quiet stretch in the
log is ambiguous: "no beacon because the transmitter is wedged" looks exactly
like "no beacon because the Pi has not joined SkeweredNet yet". The AP's BSSID
is pinned on the Pi's connection profile, so if NetworkManager scans before the
ESP is beaconing it backs off and retries -- a startup gap that has nothing to
do with the box. The LINK-UP/LINK-DOWN timestamps separate the two after the
fact.

Runs from boot via skewered-debug-logger.service. It deliberately does NOT wait
for the network: binding 0.0.0.0 succeeds while the link is still down, and
waiting would hide the very startup window this is meant to measure.
"""
import os
import socket
import struct
import subprocess
import time

LOG_PATH = os.environ.get("SKEWERED_DEBUG_LOG",
                          "/home/ben/skewered/wemos_debug.log")
LOG_MAX_BYTES = 32 * 1024 * 1024   # rotate to .1 past this (~4 days at 1 line/s)
SILENCE_AFTER = 30.0               # quiet seconds before SILENCE-BEGIN
POLL_S = 1.0                       # socket timeout
LINK_POLL_S = 2.0                  # how often to re-check wifi association

FMT_OLD = "<B6IB"      # 26 bytes: magic..stations
FMT_NEW = "<B6IBB"     # 27 bytes: + rst_reason
FMT_SH = "<B6IBBB"     # 28 bytes: + serial_reinits
LEN_OLD = struct.calcsize(FMT_OLD)
LEN_NEW = struct.calcsize(FMT_NEW)
LEN_SH = struct.calcsize(FMT_SH)

RST = {0: "power-on", 1: "HW-watchdog", 2: "EXCEPTION-crash",
       3: "SW-watchdog", 4: "ESP.restart", 5: "deep-sleep-wake",
       6: "external-reset"}


class Log:
    """Append-only log with size-based rotation.

    Rotation matters now that this runs from boot rather than being started by
    hand: previously the file only grew while someone was debugging.
    """

    def __init__(self, path):
        self.path = path
        self.f = open(path, "a", buffering=1)

    def write(self, msg):
        self.f.write("%.1f %s\n" % (time.time(), msg))
        if self.f.tell() > LOG_MAX_BYTES:
            self._rotate()

    def _rotate(self):
        try:
            self.f.close()
            os.replace(self.path, self.path + ".1")
        except OSError:
            pass   # a diagnostic tool must never die of its own housekeeping
        self.f = open(self.path, "a", buffering=1)


def pi_uptime():
    """Seconds since the Pi booted, or -1. Proves the capture covers boot."""
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError):
        return -1.0


def _iw(iface, *args):
    """Run `iw dev <iface> ...`, returning stdout ("" on any failure)."""
    try:
        r = subprocess.run(["iw", "dev", iface] + list(args),
                           capture_output=True, text=True, timeout=2)
        return r.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def wifi_ifaces():
    try:
        return sorted(n for n in os.listdir("/sys/class/net")
                      if n.startswith("wl"))
    except OSError:
        return []


def link_state(iface):
    """(associated, ssid, mode) for one interface. Never raises.

    In AP mode `iw ... link` reports "Not connected" even while the hotspot is
    beaconing, so the mode is checked first and AP is treated as up.
    """
    mode = None
    ssid = None
    for line in _iw(iface, "info").splitlines():
        line = line.strip()
        if line.startswith("type "):
            mode = line.split(None, 1)[1].strip()
        elif line.startswith("ssid "):
            ssid = line.split(None, 1)[1].strip()

    if mode == "AP":
        return ssid is not None, ssid, "AP"

    out = _iw(iface, "link")
    if out:
        if out.startswith("Not connected"):
            return False, None, mode or "managed"
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("SSID:"):
                ssid = line.split(":", 1)[1].strip()
        return True, ssid, mode or "managed"

    # iw missing or failed -- fall back to the kernel's carrier flag
    try:
        with open("/sys/class/net/%s/operstate" % iface) as f:
            return f.read().strip() == "up", None, mode or "?"
    except OSError:
        return False, None, mode or "?"


def poll_links(log, seen):
    """Log association changes for every wifi interface."""
    for iface in wifi_ifaces():
        up, ssid, mode = link_state(iface)
        if seen.get(iface) == (up, ssid):
            continue
        seen[iface] = (up, ssid)
        if up:
            log.write("LINK-UP %s mode=%s ssid=%s" % (iface, mode, ssid or "?"))
        else:
            log.write("LINK-DOWN %s mode=%s" % (iface, mode))


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 4211))
    s.settimeout(POLL_S)

    log = Log(LOG_PATH)
    log.write("LOGGER-START pi_uptime=%.1fs" % pi_uptime())

    prev_uptime = None
    prev_loops = None
    prev_reinits = None
    silent_since = None
    # All interval maths uses the monotonic clock. The Pi has no working RTC
    # (it boots at 1970 and systemd-timesyncd restores an approximate time), so
    # the wall clock steps -- by days -- the first time the Pi reaches the
    # internet during a YouTube upload. Wall time is still what gets logged, so
    # lines stay human-readable, but a step must not corrupt gap measurements.
    last_beacon = time.monotonic()
    links = {}
    next_link_poll = 0.0
    ref_wall, ref_mono = time.time(), time.monotonic()

    while True:
        now = time.monotonic()

        # Announce clock steps: timestamps either side of one are not comparable.
        wall = time.time()
        drift = (wall - ref_wall) - (now - ref_mono)
        if abs(drift) > 2.0:
            log.write("CLOCK-STEP %+.1fs (wall clock jumped; earlier timestamps "
                      "are on the old clock)" % drift)
        ref_wall, ref_mono = wall, now

        if now >= next_link_poll:
            poll_links(log, links)
            next_link_poll = now + LINK_POLL_S

        try:
            data, addr = s.recvfrom(256)
        except socket.timeout:
            if silent_since is None and time.monotonic() - last_beacon > SILENCE_AFTER:
                silent_since = last_beacon
                log.write("SILENCE-BEGIN")
            continue

        if data[:1] != b"\xdd":
            continue

        rst = None
        reinits = None
        if len(data) == LEN_SH:
            m = struct.unpack(FMT_SH, data)
            rst = m[8]
            reinits = m[9]
        elif len(data) == LEN_NEW:
            m = struct.unpack(FMT_NEW, data)
            rst = m[8]
        elif len(data) == LEN_OLD:
            m = struct.unpack(FMT_OLD, data)
        else:
            continue

        last_beacon = time.monotonic()
        if silent_since is not None:
            log.write("SILENCE-END after %.0fs  from=%s"
                      % (last_beacon - silent_since, addr[0]))
            silent_since = None

        _, up, loops, rx, valid, fails, heap, stations = m[:8]
        notes = []
        if prev_uptime is not None and up < prev_uptime:
            why = ("  cause=" + RST.get(rst, "reason-%d" % rst)) if rst is not None else ""
            notes.append("REBOOT" + why)
        if prev_loops is not None and loops == prev_loops:
            notes.append("LOOP-STALL")
        if (reinits is not None and prev_reinits is not None
                and reinits > prev_reinits and up >= prev_uptime):
            notes.append("SERIAL-REINIT")
        prev_uptime, prev_loops, prev_reinits = up, loops, reinits
        sr = ("" if reinits is None else " sr=%d" % reinits)
        log.write("up=%dms loops=%d rx=%d valid=%d fails=%d heap=%d sta=%d%s %s"
                  % (up, loops, rx, valid, fails, heap, stations, sr,
                     " ".join(notes)))


if __name__ == "__main__":
    main()
