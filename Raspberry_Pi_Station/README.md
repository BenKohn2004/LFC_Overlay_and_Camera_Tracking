# Raspberry Pi Fencing Station

A self-contained recording and review station for a fencing strip, built on a
Raspberry Pi 5 with a 7" touchscreen (800x480) and a USB webcam. It reads the
scoring box directly over **Bluetooth LE** — the box advertises its whole state,
so there is no data cable, no RS-485 converter and no transmitter board.
`ble_bridge.py` decodes those advertisements and re-emits them on UDP 4210 for
`station.py`.

The earlier Wi-Fi route (an ESP8266 parsing the box's RS-485 output and
broadcasting from its own access point) is kept in [`../Skewered/`](../Skewered/)
for reference, but is not used.

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
- **Hit timing from the box** — the state packet carries, per side, the
  milliseconds since that fencer's hit. Touches are back-dated by it, so their
  timestamps reflect when the hit happened rather than when the packet arrived,
  and clip positions follow. A late hit shows its lateness on the overlay in a
  purple box over that side's lamp, matching the number the scoring box shows.
- **Save to a USB drive** — from the bout browser, `SAVE ALL` (or per-bout
  `SAVE`) cuts each bout (first touch to last, capped at 20 min, via stream
  copy) to `~/skewered/exports`, named `Name vs Name - date (score).mp4` with
  the touch list beside it as a `.txt`. Plug in a USB drive and the `USB`
  button lights up; tapping it copies everything across, flushes, and unmounts
  so the drive can be pulled safely. A per-bout `export_name` prevents
  duplicates, so `SAVE ALL` only writes bouts that have not been saved, and
  copying twice skips files already on the drive. `CLEAR` empties the staging
  folder once the files are away — nothing deletes them automatically, because
  the Pi cannot know when they were copied.

  There is no network in this path at all: no address to find, no password, no
  drivers, and nothing to configure on the computer receiving the files. That
  is deliberate — it is the step a new user has to get through unaided.

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
| `hit_capture.py` | Logs decoded BLE advertisements (in Troubleshooting_Tools) |
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

## Getting the videos off

Plug a USB drive into the Pi. The desktop automounts it, the `USB` button on
the bouts screen becomes active, and tapping it copies every saved video and
its `.txt` onto the drive under `Skewered/`, then unmounts it and says SAFE TO
REMOVE. Nothing else is required, on the Pi or on the computer.

Notes: the button is greyed out until a drive is mounted, which is the whole
instruction. Files already on the drive are skipped, so copying twice is
harmless. Each file is written to a `.part` name and renamed on completion, so
a drive pulled mid-copy leaves an obvious partial rather than a truncated video
that looks whole. FAT32 drives cannot hold a file of 4 GB or more — that only
matters for a very long combined video, and the copy says so rather than
failing silently.

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
