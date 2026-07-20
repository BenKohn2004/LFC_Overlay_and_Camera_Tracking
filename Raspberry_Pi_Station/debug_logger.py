"""Log Wemos debug telemetry (port 4211) to wemos_debug.log, 1 line/s.
Flags reboots (uptime reset), loop stalls, and silence periods."""
import socket, struct, time

FMT = "<B6IB"
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
    if len(data) != struct.calcsize(FMT) or data[0] != 0xDD:
        continue
    if silent_since is not None:
        log.write("%.1f SILENCE-END after %.0fs\n" % (time.time(), time.time() - silent_since))
        silent_since = None
    _, up, loops, rx, valid, fails, heap, stations = struct.unpack(FMT, data)
    notes = []
    if prev_uptime is not None and up < prev_uptime:
        notes.append("REBOOT")
    if prev_loops is not None and loops == prev_loops:
        notes.append("LOOP-STALL")
    prev_uptime, prev_loops = up, loops
    log.write("%.1f up=%dms loops=%d rx=%d valid=%d fails=%d heap=%d sta=%d %s\n"
              % (time.time(), up, loops, rx, valid, fails, heap, stations,
                 " ".join(notes)))
