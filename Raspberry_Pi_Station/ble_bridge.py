#!/usr/bin/env python3
"""Receive Skewered box state over BLE and re-emit it as the transmitter's UDP
packet, so station.py needs no changes at all.

The scoring box advertises its full state in the manufacturer payload of every
BLE advertisement (manufacturer ID 0x0E88, short name "SkF:<strip>"). That is
the same State data packet the box sends down the RS-485 line, so the entire
wired chain -- RJ11 cable, MAX485 converter, ESP8266 transmitter, its SoftAP,
and the BSSID pinning that goes with it -- is optional. This bridge stands in
for all of it and speaks the identical 71-byte payload on UDP 4210.

Uses BlueZ over D-Bus (python3-dbus + PyGObject, both already present on
Raspberry Pi OS). No pip installs, no bleak.

Two details that will bite anyone modifying this:

* The box's BLE address is **Non-Resolvable Random** -- it rotates, and BlueZ
  creates a fresh device object each time it does. Never key on the address.
  This matches on the manufacturer ID, which is what stays constant. (Note this
  is the exact opposite of the Wi-Fi path, where the AP's BSSID must be pinned.)

* `DuplicateData` must be true in the discovery filter. Without it the adapter
  suppresses byte-identical advertisements, so an idle box -- unchanged score,
  unchanged clock -- goes almost silent: measured 4 reports in 18 s filtered
  versus ~6-13 Hz unfiltered.

Run:  python3 ble_bridge.py [--verbose]
"""
import argparse
import socket
import struct
import sys
import time

import dbus
import dbus.mainloop.glib
from gi.repository import GLib

# ---------------- Configuration ----------------
MANUF_ID = 0x0E88          # Skewered's Bluetooth manufacturer ID
ADAPTER = "hci0"
UDP_TARGET = ("127.0.0.1", 4210)

# The box names itself: it advertises the short name "SkF:<strip id>", so the
# name in customMessage comes from the box rather than from a constant that has
# to be edited per unit (the transmitter firmware needed BOX_NAME recompiled
# into it for each strip). Only used until the first advertisement carrying a
# Name arrives -- BlueZ sometimes delivers ManufacturerData before Name.
DEFAULT_BOX_NAME = "SkF:?"
NAME_PREFIX = "SkF:"
HEARTBEAT_MS = 100         # match the transmitter's 10 Hz floor
STALE_S = 5.0              # stop emitting after this long with no advertisement
CLEANUP_S = 60.0           # drop rotated-address device objects this old

# The transmitter's FaveroMessage (71 bytes) plus the raw 3-bit light status
# per side and the box's hide_extra_hits flag -> 74 bytes.
#
# The booleans in the original payload can only express valid and non-valid.
# Short/whipover (3) and late (4) have no boolean, so they used to arrive as
# "no light at all" -- a late hit was invisible to the station. Sending the raw
# status keeps every present and future value intact (5-7 are reserved) instead
# of inventing a flag per light type.
FMT_AGE = "<B6s4I12?32sHH"       # 71: what the ESP8266 transmitter sent
FMT_V3 = "<B6s4I12?32sHHBB?"     # 74: + left_status, right_status, hide_extra
PACKET_LEN = struct.calcsize(FMT_V3)
assert struct.calcsize(FMT_AGE) == 71, struct.calcsize(FMT_AGE)
assert PACKET_LEN == 74, PACKET_LEN

# The advertised state packet is the serial frame minus its 0xEE prefix, so
# every index here is the transmitter firmware's index minus one.
#   00 config flags   01 match info    02-04 clock      05 raw strip input
#   06 basic lights   07-09 special light info (hit age)
#   10-11 scores      12 penalty cards
STATE_LEN = 13


def decode(p):
    """13-byte advertised state packet -> field dict.

    Mirrors decodePriority/decodeTimer/decodeLights/decodeHitAge/decodeScores/
    decodeCards in the transmitter firmware, bit for bit.
    """
    prio = (p[1] >> 6) & 0x03
    priority_left = prio == 0x01
    priority_right = prio == 0x02

    # Clock: low 2 bits of the flags byte are the high bits of a 10-bit count.
    # Bit 3 means the box switched to centiseconds (time remaining <= 10 s).
    total = ((p[2] & 0x03) << 8) | p[3]
    if p[2] & 0x08:
        total //= 100
    minutes, seconds = divmod(total, 60)

    left_status = (p[6] >> 3) & 0x07
    right_status = p[6] & 0x07

    # Milliseconds since the hit on each side, 0 = no hit. Two 10-bit values
    # packed across three bytes.
    age_l = ((p[7] & 0x7F) << 3) | ((p[8] >> 5) & 0x07)
    age_r = ((p[8] & 0x07) << 7) | ((p[9] >> 1) & 0x7F)

    # MSB of each score byte flags "most recent touch"; mask it off.
    left_score = p[10] & 0x7F
    right_score = p[11] & 0x7F

    # Card nibbles are PP|NN -- mask to 2 bits so an active p-card does not
    # hide the normal card underneath it.
    left_card = p[12] & 0x03
    right_card = (p[12] >> 4) & 0x03

    return {
        # Raw 3-bit status per side: 0 off, 1 valid, 2 non-valid,
        # 3 short/whipover, 4 late, 5-7 reserved. Passed through untouched so
        # the station can act on values this bridge has never seen.
        "left_status": left_status,
        "right_status": right_status,
        # Set when the box is configured NOT to show extra hit timing. The
        # protocol defines it so repeater displays can honour that setting,
        # which is exactly what the overlay is.
        "hide_extra": bool(p[6] & 0x40),
        "left_score": left_score,
        "right_score": right_score,
        "seconds": seconds,
        "minutes": minutes,
        "green": right_status == 1,
        "red": left_status == 1,
        "white_green": right_status == 2,
        "white_red": left_status == 2,
        "yellow_card_green": right_card == 0x01,
        "yellow_card_red": left_card == 0x01,
        "red_card_green": right_card == 0x02,
        "red_card_red": left_card == 0x02,
        "priority_left": priority_left,
        "priority_right": priority_right,
        "age_l": age_l,
        "age_r": age_r,
    }


def build_packet(s, box_name=DEFAULT_BOX_NAME):
    """Field dict -> the 74-byte wire format (the transmitter's 71 + statuses).

    The first 71 bytes are byte-identical to what the ESP8266 sent, so a
    receiver that only knows the old layout still reads scores, clock, lights
    and hit age correctly from the prefix.
    """
    name = box_name.encode()[:32].ljust(32, b"\x00")
    return struct.pack(
        FMT_V3,
        0,                      # msgType: the firmware leaves this zeroed too
        b"\x00" * 6,            # mac: unused by station.py; no Wi-Fi source here
        s["right_score"], s["left_score"], s["seconds"], s["minutes"],
        s["green"], s["red"], s["white_green"], s["white_red"],
        False, False,           # yellow_green / yellow_red: unused, as in firmware
        s["yellow_card_green"], s["yellow_card_red"],
        s["red_card_green"], s["red_card_red"],
        s["priority_left"], s["priority_right"],
        name,
        s["age_l"], s["age_r"],
        s["left_status"], s["right_status"], s["hide_extra"],
    )


class Bridge:
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.last_state = None
        self.last_adv = 0.0        # time.monotonic of the last advertisement
        self.seen = {}             # dbus path -> last seen monotonic
        self.count_adv = 0
        self.count_tx = 0
        # Held across address rotations: the box keeps its name when its random
        # address changes, so the last name seen stays valid for the new object.
        self.box_name = DEFAULT_BOX_NAME

    def note_name(self, name):
        if name and str(name).startswith(NAME_PREFIX):
            self.box_name = str(name)

    # ---- advertisement handling ----

    def on_manufacturer_data(self, path, mdata):
        raw = mdata.get(dbus.UInt16(MANUF_ID)) or mdata.get(MANUF_ID)
        if raw is None:
            return
        payload = bytes(bytearray(raw))
        if len(payload) < STATE_LEN:
            return

        self.seen[path] = time.monotonic()
        self.count_adv += 1
        try:
            state = decode(payload)
        except (IndexError, ValueError) as e:
            if self.verbose:
                print("decode failed on %s: %s" % (payload.hex(), e))
            return

        self.last_state = state
        self.last_adv = time.monotonic()
        self.send(state)

        if self.verbose:
            print("%s %s  L%-2d R%-2d  %d:%02d  age L%d R%d  %s"
                  % (self.box_name, payload.hex(),
                     state["left_score"], state["right_score"],
                     state["minutes"], state["seconds"],
                     state["age_l"], state["age_r"],
                     "".join(k[0].upper() for k in
                             ("green", "red", "white_green", "white_red")
                             if state[k]) or "-"))

    def send(self, state):
        try:
            self.sock.sendto(build_packet(state, self.box_name), UDP_TARGET)
            self.count_tx += 1
        except OSError as e:
            if self.verbose:
                print("send failed: %s" % e)

    # ---- timers ----

    def heartbeat(self):
        """Re-emit the last state at the transmitter's 10 Hz floor.

        station.py ages the last packet to decide whether the box is live, so
        going quiet is how signal loss is meant to look -- hence the staleness
        check rather than repeating a frozen state forever.
        """
        if self.last_state and time.monotonic() - self.last_adv < STALE_S:
            self.send(self.last_state)
        return True

    def cleanup(self, bus):
        """Remove device objects left behind as the box's random address rotates.

        Without this BlueZ accumulates one stale object per rotation for as long
        as the station runs.
        """
        now = time.monotonic()
        stale = [p for p, t in self.seen.items() if now - t > CLEANUP_S]
        if not stale:
            return True
        try:
            adapter = dbus.Interface(
                bus.get_object("org.bluez", "/org/bluez/" + ADAPTER),
                "org.bluez.Adapter1")
            for path in stale:
                try:
                    adapter.RemoveDevice(path)
                except dbus.DBusException:
                    pass
                self.seen.pop(path, None)
        except dbus.DBusException:
            pass
        return True

    def stats(self):
        print("adv=%d tx=%d  last_adv=%.1fs ago"
              % (self.count_adv, self.count_tx,
                 time.monotonic() - self.last_adv if self.last_adv else -1))
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true",
                    help="print every decoded advertisement")
    ap.add_argument("--stats", action="store_true",
                    help="print counters once a second")
    args = ap.parse_args()

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    bridge = Bridge(verbose=args.verbose)

    obj = bus.get_object("org.bluez", "/org/bluez/" + ADAPTER)
    adapter = dbus.Interface(obj, "org.bluez.Adapter1")
    props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")

    if not props.Get("org.bluez.Adapter1", "Powered"):
        props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))

    def on_props_changed(interface, changed, invalidated, path=None):
        if interface != "org.bluez.Device1":
            return
        # Name can arrive in a different signal than ManufacturerData, so take
        # it whenever it shows up rather than only alongside a payload.
        bridge.note_name(changed.get("Name") or changed.get("Alias"))
        if "ManufacturerData" in changed:
            bridge.on_manufacturer_data(path, changed["ManufacturerData"])

    def on_iface_added(path, interfaces):
        dev = interfaces.get("org.bluez.Device1")
        if not dev:
            return
        bridge.note_name(dev.get("Name") or dev.get("Alias"))
        if "ManufacturerData" in dev:
            bridge.on_manufacturer_data(path, dev["ManufacturerData"])

    bus.add_signal_receiver(
        on_props_changed, signal_name="PropertiesChanged",
        dbus_interface="org.freedesktop.DBus.Properties",
        path_keyword="path")
    bus.add_signal_receiver(
        on_iface_added, signal_name="InterfacesAdded",
        dbus_interface="org.freedesktop.DBus.ObjectManager")

    # Anything already discovered before we attached the signal handlers.
    mgr = dbus.Interface(bus.get_object("org.bluez", "/"),
                         "org.freedesktop.DBus.ObjectManager")
    for path, ifaces in mgr.GetManagedObjects().items():
        dev = ifaces.get("org.bluez.Device1")
        if not dev:
            continue
        bridge.note_name(dev.get("Name") or dev.get("Alias"))
        if "ManufacturerData" in dev:
            bridge.on_manufacturer_data(path, dev["ManufacturerData"])

    try:
        adapter.SetDiscoveryFilter({
            "Transport": dbus.String("le"),
            "DuplicateData": dbus.Boolean(True),
        })
        adapter.StartDiscovery()
    except dbus.DBusException as e:
        sys.exit("could not start discovery (try the bluetooth group or "
                 "sudo): %s" % e)

    GLib.timeout_add(HEARTBEAT_MS, bridge.heartbeat)
    GLib.timeout_add_seconds(int(CLEANUP_S), bridge.cleanup, bus)
    if args.stats:
        GLib.timeout_add_seconds(1, bridge.stats)

    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            adapter.StopDiscovery()
        except dbus.DBusException:
            pass


if __name__ == "__main__":
    main()
