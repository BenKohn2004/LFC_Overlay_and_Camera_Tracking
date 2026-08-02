#!/usr/bin/env python3
"""Classify transmitter boot sessions in wemos_debug.log.

    python3 analyze_debug_log.py ~/skewered/wemos_debug.log

The beacon's rx_bytes/valid_packets are cumulative since the ESP booted, so a
drop in `up` marks a new session. Within a session `up` (the ESP's millis) is
the trustworthy clock -- the Pi has no working RTC and its wall clock steps.

Each session is classified by what the parser actually saw:

  OK           valid frames arrived -- the link worked
  RX-NO-VALID  bytes arrived but no frame ever passed the checksum, i.e. noise
               or a collision on the RS-485 pair
  RX-DEAD      no bytes at all

RX-DEAD is ambiguous on its own: a switched-off scoring box looks exactly like
a broken link. The firmware's line probe (`line=` in the log) is what separates
them -- see the "RS-485 line probe" comment in the transmitter sketch.
"""
import re
import sys
from collections import Counter
from statistics import median

LINE = re.compile(
    r"^(\d+\.\d+) up=(\d+)ms loops=(\d+) rx=(\d+) valid=(\d+) fails=(\d+) "
    r"heap=(\d+) sta=(\d+)(?: sr=(\d+))?(?: line=(\S+))?\s*(.*)$")


def load(path):
    rows = []
    for ln in open(path):
        m = LINE.match(ln)
        if not m:
            continue
        ts, up, loops, rx, valid, fails, heap, sta, sr, line, notes = m.groups()
        rows.append(dict(ts=float(ts), up=int(up), loops=int(loops), rx=int(rx),
                         valid=int(valid), fails=int(fails), heap=int(heap),
                         sta=int(sta), sr=int(sr) if sr else None,
                         line=line, notes=notes.strip()))
    return rows


def split_sessions(rows):
    sessions, cur = [], []
    for r in rows:
        if cur and r["up"] < cur[-1]["up"]:
            sessions.append(cur)
            cur = []
        cur.append(r)
    if cur:
        sessions.append(cur)
    return sessions


def classify(s):
    if s[-1]["valid"] > s[0]["valid"]:
        return "OK"
    return "RX-NO-VALID" if s[-1]["rx"] > s[0]["rx"] else "RX-DEAD"


def main(path):
    rows = load(path)
    sessions = split_sessions(rows)
    print("%d telemetry rows -> %d transmitter boot sessions\n"
          % (len(rows), len(sessions)))

    causes = Counter()
    for r in rows:
        m = re.search(r"cause=(\S+)", r["notes"])
        if m:
            causes[m.group(1)] += 1
    print("reboot causes:", dict(causes) or "(none recorded)")

    # Sessions under 20 s are usually logger restarts rather than real boots.
    real = [s for s in sessions if (s[-1]["up"] - s[0]["up"]) >= 20000]
    c = Counter(classify(s) for s in real)
    bad = c["RX-DEAD"] + c["RX-NO-VALID"]
    print("sessions >=20 s: %d   OK=%d  RX-DEAD=%d  RX-NO-VALID=%d  (%.0f%% no data)"
          % (len(real), c["OK"], c["RX-DEAD"], c["RX-NO-VALID"],
             100.0 * bad / (len(real) or 1)))

    # How long after the ESP boots does the first valid frame appear?
    delays = []
    for s in sessions:
        if s[0]["up"] > 5000 or classify(s) != "OK":
            continue
        base = s[0]["valid"]
        for r in s:
            if r["valid"] > base:
                delays.append(r["up"] / 1000.0)
                break
    if delays:
        delays.sort()
        slow = [d for d in delays if d > 10]
        print("\ntime from ESP boot to first valid frame (n=%d): "
              "median %.1fs  p90 %.1fs  max %.1fs"
              % (len(delays), median(delays),
                 delays[int(0.9 * (len(delays) - 1))], delays[-1]))
        print("  slower than 10 s: %d (%.0f%%) %s"
              % (len(slow), 100.0 * len(slow) / len(delays),
                 [round(x) for x in slow]))

    # The line probe is the whole point: it says whether silence is a fault.
    probes = Counter(r["line"] for r in rows if r["line"])
    if probes:
        print("\nline probe states:", dict(probes))
        print("  FLOATING/LOW while idle => wiring fault (broken link or"
              " tri-stated receiver)")
        print("  driven-high while idle  => converter healthy, box simply off")
    else:
        print("\nline probe: no data (firmware predates the probe)")

    # Does the serial re-init workaround actually recover anything?
    rescue = stayed = 0
    for s in sessions:
        for i, r in enumerate(s):
            if "SERIAL-REINIT" not in r["notes"]:
                continue
            after = s[i + 1:i + 31]
            if after and after[-1]["valid"] > r["valid"]:
                rescue += 1
            else:
                stayed += 1
    if rescue or stayed:
        print("\nserial re-inits: %d recovered data within 30 s, %d did not"
              % (rescue, stayed))

    print("\n%-5s %-8s %-12s %10s %10s %6s %s"
          % ("#", "dur", "class", "rx", "valid", "sr", "first_valid"))
    for i, s in enumerate(sessions):
        if (s[-1]["up"] - s[0]["up"]) < 20000:
            continue
        t_fv, base = "NEVER", s[0]["valid"]
        for r in s:
            if r["valid"] > base:
                t_fv = "up=%.0fs" % (r["up"] / 1000.0)
                break
        print("%-5d %-8s %-12s %10d %10d %6s %s"
              % (i, "%.0fs" % ((s[-1]["up"] - s[0]["up"]) / 1000.0),
                 classify(s), s[-1]["rx"] - s[0]["rx"],
                 s[-1]["valid"] - s[0]["valid"],
                 "-" if s[-1]["sr"] is None else s[-1]["sr"], t_fv))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
