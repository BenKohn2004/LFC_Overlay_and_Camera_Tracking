#!/usr/bin/env python3
"""Log every distinct Skewered BLE advertisement, decoded, to study hit timing.

Passive: it attaches to the same BlueZ signals the bridge already triggers, so
it can run alongside skewered-ble-bridge without disturbing it. It starts
discovery itself only if nothing else has.

Logs raw light STATUS values (0-4), not the booleans the bridge exposes, so
short/whipover (3) and late (4) are visible -- those are exactly the cases where
the timing bytes stop meaning "age" and start meaning something else.

Output: a line per payload change, plus /tmp/hits.csv for analysis.
    t_ms   monotonic since start
    dt     ms since the previous change (how fast the field really ticks)
    Lst/Rst  light status 0-4
    ageL/ageR  the 10-bit timing values
    run/exp/brk/lock  clock running, expired, on break, lockout started

Run:  python3 hit_capture.py [seconds]
"""
import sys
import time

import dbus
import dbus.mainloop.glib
from gi.repository import GLib

MANUF_ID = 0x0E88
ADAPTER = "hci0"
CSV = "/tmp/hits.csv"

LIGHT = {0: "-", 1: "VALID", 2: "nonval", 3: "SHORT", 4: "LATE"}


def decode(p):
    return {
        # byte 00 config flags: 0000 R P L S
        "reviewing": bool(p[0] & 0x08),
        "preview": bool(p[0] & 0x04),
        "lockout": bool(p[0] & 0x02),
        "sleep": bool(p[0] & 0x01),
        # byte 01: PP WW pppp
        "priority": (p[1] >> 6) & 0x03,
        "weapon": (p[1] >> 4) & 0x03,
        "period": p[1] & 0x0F,
        # byte 02 flags + 10-bit time, byte 04 passivity
        "expired": bool(p[2] & 0x20),
        "on_break": bool(p[2] & 0x10),
        "centis": bool(p[2] & 0x08),
        "running": bool(p[2] & 0x04),
        "time_raw": ((p[2] & 0x03) << 8) | p[3],
        "passivity": p[4],
        # byte 05 raw strip: 0 X SS F VV
        "strip_raw": p[5],
        # byte 06: 0 H LLL RRR  -- keep the raw 3-bit status
        "hide_extra": bool(p[6] & 0x40),
        "left_status": (p[6] >> 3) & 0x07,
        "right_status": p[6] & 0x07,
        # bytes 07-09: two 10-bit timing values
        "age_l": ((p[7] & 0x7F) << 3) | ((p[8] >> 5) & 0x07),
        "age_r": ((p[8] & 0x07) << 7) | ((p[9] >> 1) & 0x7F),
        # bytes 10-11: score + sticky "scored most recently" high bit
        "left_score": p[10] & 0x7F,
        "right_score": p[11] & 0x7F,
        "left_last": bool(p[10] & 0x80),
        "right_last": bool(p[11] & 0x80),
        # byte 12: per side, 2 bits normal card then 2 bits p-card
        "card_l": p[12] & 0x03,
        "pcard_l": (p[12] >> 2) & 0x03,
        "card_r": (p[12] >> 4) & 0x03,
        "pcard_r": (p[12] >> 6) & 0x03,
    }


class Capture:
    def __init__(self):
        self.t0 = time.monotonic()
        self.last_payload = None
        self.last_t = None
        self.n = 0
        self.csv = open(CSV, "w", buffering=1)
        self.csv.write("t_ms,dt_ms,hex,Lst,Rst,ageL,ageR,scoreL,scoreR,"
                       "lastL,lastR,run,exp,brk,lock,passivity,period,weapon,"
                       "strip_raw\n")
        print("t_ms     dt    raw                          Lst    Rst    "
              "ageL ageR  score  flags")

    def on_payload(self, raw):
        payload = bytes(bytearray(raw))
        if len(payload) < 13 or payload == self.last_payload:
            return
        now = time.monotonic()
        t_ms = (now - self.t0) * 1000.0
        dt = (now - self.last_t) * 1000.0 if self.last_t else 0.0
        self.last_payload, self.last_t = payload, now
        self.n += 1

        d = decode(payload)
        flags = "".join(c for c, v in (
            ("R", d["running"]), ("E", d["expired"]), ("B", d["on_break"]),
            ("L", d["lockout"]), ("V", d["reviewing"])) if v) or "-"
        print("%7.0f %5.0f  %s  %-6s %-6s %4d %4d  %2d-%-2d  %s"
              % (t_ms, dt, payload.hex(),
                 LIGHT.get(d["left_status"], d["left_status"]),
                 LIGHT.get(d["right_status"], d["right_status"]),
                 d["age_l"], d["age_r"],
                 d["left_score"], d["right_score"], flags))
        self.csv.write("%.0f,%.0f,%s,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,"
                       "%d,%d,%d,%d\n"
                       % (t_ms, dt, payload.hex(),
                          d["left_status"], d["right_status"],
                          d["age_l"], d["age_r"],
                          d["left_score"], d["right_score"],
                          d["left_last"], d["right_last"],
                          d["running"], d["expired"], d["on_break"],
                          d["lockout"], d["passivity"], d["period"],
                          d["weapon"], d["strip_raw"]))


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    cap = Capture()

    def grab(props):
        raw = props.get(dbus.UInt16(MANUF_ID)) or props.get(MANUF_ID)
        if raw is not None:
            cap.on_payload(raw)

    def on_props(interface, changed, invalidated, path=None):
        if interface == "org.bluez.Device1" and "ManufacturerData" in changed:
            grab(changed["ManufacturerData"])

    def on_added(path, interfaces):
        dev = interfaces.get("org.bluez.Device1")
        if dev and "ManufacturerData" in dev:
            grab(dev["ManufacturerData"])

    bus.add_signal_receiver(on_props, signal_name="PropertiesChanged",
                            dbus_interface="org.freedesktop.DBus.Properties",
                            path_keyword="path")
    bus.add_signal_receiver(on_added, signal_name="InterfacesAdded",
                            dbus_interface="org.freedesktop.DBus.ObjectManager")

    obj = bus.get_object("org.bluez", "/org/bluez/" + ADAPTER)
    props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
    adapter = dbus.Interface(obj, "org.bluez.Adapter1")
    started = False
    if not props.Get("org.bluez.Adapter1", "Discovering"):
        adapter.SetDiscoveryFilter({"Transport": dbus.String("le"),
                                    "DuplicateData": dbus.Boolean(True)})
        adapter.StartDiscovery()
        started = True

    loop = GLib.MainLoop()
    GLib.timeout_add_seconds(int(secs), loop.quit)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        if started:
            try:
                adapter.StopDiscovery()
            except dbus.DBusException:
                pass
        print("\n%d distinct payloads -> %s" % (cap.n, CSV))


if __name__ == "__main__":
    main()
