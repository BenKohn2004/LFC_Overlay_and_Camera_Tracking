"""Log Wemos debug telemetry (port 4211) to wemos_debug.log, 1 line/s.
Flags reboots (uptime reset), loop stalls, and silence periods. Decodes the
firmware reset reason when present (27-byte format) and stays compatible with
the older 26-byte beacon."""
import socket
import struct
import time

FMT_OLD = "<B6IB"      # 26 bytes: magic..stations
FMT_NEW = "<B6IBB"     # 27 bytes: + rst_reason
LEN_OLD = struct.calcsize(FMT_OLD)
LEN_NEW = struct.calcsize(FMT_NEW)

RST = {0: "power-on", 1: "HW-watchdog", 2: "EXCEPTION-crash",
       3: "SW-watchdog", 4: "ESP.restart", 5: "deep-sleep-wake",
       6: "external-reset"}

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 4211))
s.settimeout(30)
log = open("/home/ben/skewered/wemos_debug.log", "a", buffering=1)
prev_uptime = None
prev_loops = None
silent_since = None
log.write("%.1f LOGGER-START\n" % time.time())
while True:
    try:
        data, _ = s.recvfrom(256)
    except socket.timeout:
        if silent_since is None:
            silent_since = time.time()
            log.write("%.1f SILENCE-BEGIN\n" % time.time())
        continue
    if data[:1] != b"\xdd":
        continue
    rst = None
    if len(data) == LEN_NEW:
        m = struct.unpack(FMT_NEW, data)
        rst = m[8]
    elif len(data) == LEN_OLD:
        m = struct.unpack(FMT_OLD, data)
    else:
        continue
    if silent_since is not None:
        log.write("%.1f SILENCE-END after %.0fs\n"
                  % (time.time(), time.time() - silent_since))
        silent_since = None
    _, up, loops, rx, valid, fails, heap, stations = m[:8]
    notes = []
    if prev_uptime is not None and up < prev_uptime:
        why = ("  cause=" + RST.get(rst, "reason-%d" % rst)) if rst is not None else ""
        notes.append("REBOOT" + why)
    if prev_loops is not None and loops == prev_loops:
        notes.append("LOOP-STALL")
    prev_uptime, prev_loops = up, loops
    log.write("%.1f up=%dms loops=%d rx=%d valid=%d fails=%d heap=%d sta=%d %s\n"
              % (time.time(), up, loops, rx, valid, fails, heap, stations,
                 " ".join(notes)))
