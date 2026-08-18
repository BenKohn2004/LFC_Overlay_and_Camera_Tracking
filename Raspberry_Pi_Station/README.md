# Raspberry Pi Fencing Station

A self-contained recording and review station for a fencing strip, built on a
Raspberry Pi 5 with a 7" touchscreen (800x480) and a USB webcam. It pairs with
the Skewered UDP transmitter in [`../Skewered/`](../Skewered/)
(`8266_Skewered_Parser_and_Transmitter_UDP.ino`), which broadcasts the scoring
box state over Wi-Fi.

## What it does

- **Live view with overlay** — webcam video fullscreen on the touchscreen with
  scores, clock, hit/off-target lights, cards, and priority rendered from the
  box telemetry (styled after the OBS Favero overlay in this repo).
- **Automatic recording** — any box activity (light, clock, score change)
  starts recording; it stops 5 minutes after the last activity. No buttons.
  Video is 720p30 H.264 with the overlay burned in, written as crash-safe
  5-minute fragmented-MP4 segments. Oldest segments are auto-deleted when the
  disk runs low.
- **Touch database** — every touch (which lights, score before/after), card,
  and priority is logged to SQLite with its exact position in the video. A new
  bout starts whenever the score resets to 0-0; fencer names are stored
  per-bout.
- **On-screen bout browser** — tap `BOUTS` for a list of bouts (names, final
  score, touch count), tap a bout for its touches, tap a touch to watch a
  looping replay (a few seconds before the light to a couple after). `REPLAY`
  plays the latest touch instantly. Recording continues during playback, and a
  real touch at the strip interrupts any replay.
- **Name entry** — tap a nameplate for an on-screen keyboard with quick-select
  chips of recently used names.
- **Club logos** — a circular club logo sits between each nameplate and score
  plate (burned into recordings like the rest of the overlay). Tap it to open
  a picker: the club last associated with that fencer's name is offered first,
  "no club" and a US-flag option are always available, and typing filters the
  grid by club name. Picking a fencer name whose club is known applies the
  logo automatically.
- **Transmitter health telemetry** — the companion sketch broadcasts a
  1 Hz debug beacon (uptime, loop rate, serial byte counters, send failures,
  free heap) on port 4211; `debug_logger.py` records it so radio problems can
  be diagnosed after the fact.
- **Save to a network share** — from the bout browser, `SAVE ALL` (or per-bout
  `SAVE`) cuts each bout (first touch to last, capped at 20 min, via stream
  copy) and writes it to `~/skewered/exports`, which Samba publishes as
  `\\<pi-ip>\exports`. Files are named `Name vs Name - date (score).mp4`, with
  the touch list beside each one as a `.txt`. A per-bout `export_name` prevents
  duplicates, so `SAVE ALL` only writes bouts that have not been saved yet.
  `CLEAR` empties the share once the files have been copied off — nothing
  deletes them automatically, because the Pi cannot know when that happened.
  The station displays its own `\\ip\exports` address: the one thing needed to
  fetch the videos, and the one nobody can guess.

  Nothing on this box holds a cloud credential. Uploading is done by hand,
  from a machine that is already logged in.

## Files

| File | Purpose |
|---|---|
| `station.py` | The whole station: capture, overlay, recording, database, touch UI |
| `debug_logger.py` | Logs the transmitter's debug beacon (port 4211) + Wi-Fi association |
| `skewered-debug-logger.service` | systemd unit so the logger runs from boot, not from the desktop session |
| `start_station.sh` | Boot launcher: waits for the webcam, starts the station |
| `skewered-station.desktop` | XDG autostart entry (`~/.config/autostart/`) |
| `udp_listen.py`, `udp_watch.py` | Small debugging listeners for the telemetry |
| `make_logos.py` | Turns club logo images into uniform 128x128 circles |
| `ble_bridge.py` | Reads the box's BLE advertisements, re-emits them on UDP 4210 |
| `setup_share.sh` | One-time Samba setup for the exports share |
| `assets/` | Overlay art (same images the OBS overlay uses) |

**Club logos:** drop images into `~/skewered/logos_src/` on the Pi — the
filename (minus extension) becomes the club name — and run
`python3 make_logos.py`. Each image is composited on a white disc, circled
with an antialiased mask and a thin ring, and written to `~/skewered/logos/`;
the station picks them up on next start. Near-square images fill the circle;
wide/tall ones are scaled to fit inside it so text is not clipped. (Logo image
files are not included in this repo — use your own clubs' art.)

## Setup (Raspberry Pi OS, Wayland desktop)

1. Copy this directory to `~/skewered/` on the Pi (with `assets/` inside).
2. Dependencies: `python3-pygame`, `numpy`, and `ffmpeg` (all present on the
   standard Raspberry Pi OS desktop image; OpenCV is not needed).
3. Join the Pi's Wi-Fi to the transmitter's `SkeweredNet` access point. Two
   settings matter for reliable UDP broadcast reception:
   `wifi.powersave = 2` (off) and the AP's BSSID pinned on the connection
   profile (stops roaming scans, which silently drop broadcasts).
4. Install the autostart entry:
   `cp skewered-station.desktop ~/.config/autostart/`
5. Install the telemetry logger so it runs from boot (see *Diagnosing startup
   problems* below for why this matters):
   ```
   sudo cp skewered-debug-logger.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now skewered-debug-logger
   ```
6. Reboot. The station starts fullscreen on the touchscreen.

Manual start:
`WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 python3 station.py`
Flags: `--secs N` (timed run), `--force-record` (record without box activity).
Exit: ESC, or press-and-hold the live view for 2 seconds.

Fencer names live in `~/skewered/fencers.txt` (line 1 = left, line 2 = right);
the on-screen editor maintains it for you.

## Sharing the videos (one-time)

Run `bash setup_share.sh` on the Pi. It installs Samba, shares only
`~/skewered/exports`, and prompts for the password Windows will ask for.

Windows refuses guest SMB connections by default, so the share needs a
password — it is local to Samba and unrelated to any online account. Only the
exports folder is shared, so an accidental drag in Explorer cannot reach the
recordings or the database.

From Windows open `\\<pi-ip>\exports`, or map it to a drive letter. The `WIFI`
button joins the Pi to a network so the share is reachable; at a venue with no
usable Wi-Fi the Pi can host its own hotspot and the laptop joins that instead,
which works the same way.

## Diagnosing startup problems

The transmitter sometimes has no data at the Pi on a cold start. That failure is
intermittent, so a single good boot after a change proves nothing — you need a
baseline failure rate to compare against, which is what the boot-time logging is
for.

`debug_logger.py` runs from boot as a systemd unit rather than from
`start_station.sh`, because that script waits up to 30 s for the webcam and the
startup window is exactly what needs recording. It writes `wemos_debug.log` with
one line per second, plus markers:

| Marker | Meaning |
|---|---|
| `LOGGER-START pi_uptime=N` | How far into the Pi's boot the capture began (small N = the window is covered) |
| `LINK-UP <if> ssid=...` | The Pi associated with the AP |
| `LINK-DOWN <if>` | The Pi lost/never had association |
| `SILENCE-BEGIN` / `SILENCE-END after Ns from=IP` | No beacon for 30 s, and when it returned |
| `REBOOT cause=...` | Transmitter restarted; cause from the ESP8266 reset reason |
| `LOOP-STALL` | `loop_count` did not advance between beacons |

`rx` and `valid` on each line are **cumulative** counters, so the per-second
delta tells you what the parser is actually seeing:

| Pattern after a cold boot | Diagnosis |
|---|---|
| `SILENCE-BEGIN` → `SILENCE-END` roughly bracketing `LINK-UP` | Wi-Fi association delay, not the box. The AP's BSSID is pinned, so NetworkManager backs off if it scans before the ESP is beaconing |
| Beacon flowing, `rx` climbing, `valid` flat | Bytes arriving but no valid frames — bus collision or line noise |
| Beacon flowing, `rx` frozen at 0 | Nothing reaching the UART — receiver tri-stated (DE/RE) or converter unpowered |
| Repeated `REBOOT cause=...` | Transmitter-side fault; chase that before the wiring |

Collect ~10 cold boots before changing anything, then change **one** thing per
batch.

### What the line probe means (calibrated 2026-08-02)

The transmitter reports `line=` whenever no valid frame has arrived for 5 s —
see the "RS-485 line probe" comment in the sketch. It was calibrated against
known physical states on the bench:

| `line=` | Verified condition |
|---|---|
| `LOW` | Pair open — cable to the Skewered Box unplugged or broken |
| `driven-high` | Pair connected and idling correctly (box may be off) |
| `FLOATING` | High-Z at the converter — tri-stated receiver or failed converter |
| `none` | Probe not running because valid frames are flowing |

So a silent link now identifies itself: `LOW` means go look at the cable,
`driven-high` means the cable is fine and nothing is being transmitted.

### Findings so far

Two weeks of history (134 boot sessions) plus a bench session ruled out more
than they confirmed:

- **Not Wi-Fi association.** The debug beacon arrives over the same radio while
  `rx` sits at zero, so the Pi is associated and the fault is on the serial side.
- **Not a jammed bus.** A jam would show `rx` climbing with `valid` flat;
  instead `rx` is flat at zero in nearly every bad session.
- **Not the UART wedging.** The serial self-heal fired 332 times and only 3 were
  followed by any valid frame. Re-initialising the port does not recover a link.
- **Not missing failsafe bias.** With the pair connected the line idles
  `driven-high` even with the box switched off, which is correct.
- **Not an obviously intermittent D6/D7 harness.** Deliberate tugging and
  flexing during a live stream produced no stall over ~60 s: a steady 200
  valid frames/s throughout.

The bench chain — box, converter, wiring, UART, firmware — is sound, and the
fault **did not reproduce**. Whatever it is, it is intermittent and needs to be
caught in the field, which is what the probe and boot-time logging are for.

Note when reading `rx`: the self-heal emits one spurious byte per re-init, so
`rx` creeping in lockstep with `sr` is an artifact, not line activity.

## Hardware notes

- **Pi 5 has no hardware video encoder** — encoding is software x264, so use a
  webcam that outputs **MJPEG** (the camera does the compression; the Pi's
  four cores handle 720p30 encode + overlay + display at ~30 fps).
- RS-485 from the scoring box needs proper **failsafe biasing** (~460-680 Ohm
  pull-up on A, pull-down on B). Weak bias (10 kOhm) lets the converter
  chatter garbage into the transmitter when the line idles.
- The transmitter sketch bounds its serial parsing per loop and broadcasts
  unconditionally — see the commit history in `../Skewered/` for the failure
  modes that motivated both.
