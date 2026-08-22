"""Fencing station: live view + recording + event DB + on-screen bout browser.

Pipeline: webcam MJPEG 1080p30 -> ffmpeg -> 1280x720 RGB -> overlay drawn ->
  [record: x264 5-min fMP4 segments]  [display: one of four modes below]

Modes: LIVE (video + REPLAY/BOUTS buttons) -> BOUTS (list) -> TOUCHES (list)
  -> PLAYBACK (clip: 10 s before touch to 3 s after; tap to dismiss).
Capture + recording continue in every mode; a new touch interrupts playback.

Recording control (hybrid): any telemetry activity starts recording and re-arms
a 5-minute idle timer. Auto-deletes oldest segments when the disk runs low.

Exit: ESC anywhere, or press-and-hold 2 s on the LIVE screen.
Flags: --force-record, --secs N.
"""
import os
import re
import shutil
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import time

import pygame

# Videos leave this box as files on a Samba share, not by uploading themselves.
# Nothing here holds a cloud credential: the Pi travels to clubs, and a stored
# refresh token would travel with it. Uploading is a deliberate human act, done
# from a machine that is already logged in.

# ---------------- config ----------------
FMT = "<B6s4I12?32s"          # 67 bytes: transmitters without hit age
FMT_AGE = "<B6s4I12?32sHH"    # 71 bytes: + left/right ms since the hit
FMT_V3 = "<B6s4I12?32sHHBB?"  # 74 bytes: + raw light status per side, hide_extra
LEN_PLAIN = struct.calcsize(FMT)
LEN_AGE = struct.calcsize(FMT_AGE)
LEN_V3 = struct.calcsize(FMT_V3)

# Raw 3-bit light status from the box (byte 06 of its state packet).
ST_OFF, ST_VALID, ST_NONVALID, ST_SHORT, ST_LATE = 0, 1, 2, 3, 4
# Statuses whose timing value is a real, ticking "milliseconds since the hit".
# For LATE the same field is a frozen lateness figure and for SHORT a duration,
# so neither may be used to back-date anything.
ST_AGE_MEANS_AGE = (ST_VALID, ST_NONVALID)

# The box's own display reads one higher than the value on the wire -- measured
# over four hits on both sides (271/272, 392/393, 378/379, 259/260). Match the
# box, so a referee comparing the two screens never sees them disagree.
LATE_DISPLAY_OFFSET = 1
UDP_PORT = 4210
REC_W, REC_H = 1280, 720
DISP_W, DISP_H = 800, 450
YOFF = 15
SC = REC_W / 1920.0
IDLE_STOP_SECS = 300
SEGMENT_SECS = 300
MIN_FREE_MB = 500
FPS = 30
CLIP_PRE, CLIP_POST = 4.0, 2.0

# Pin the mic by CARD name, never by index. The webcam (Redeagle) also presents
# an ALSA capture device, so hw:1,0-style indices reorder between boots and
# would silently record the camera's mic -- or nothing at all.
AUDIO_DEV = os.environ.get("SKEWERED_AUDIO_DEV", "hw:CARD=ME6S,DEV=0")
AUDIO_RATE = 48000

HOME = os.path.expanduser("~/skewered")
ADIR = os.path.join(HOME, "assets")
RECDIR = os.path.join(HOME, "recordings")
DBPATH = os.path.join(HOME, "skewered.db")

FLAG_NAMES = ["green", "red", "white_green", "white_red", "yellow_green",
              "yellow_red", "ycard_green", "ycard_red", "rcard_green",
              "rcard_red", "prio_left", "prio_right"]

MODE_LIVE, MODE_BOUTS, MODE_TOUCHES, MODE_PLAYBACK = "live", "bouts", "touches", "playback"
MODE_NAME = "name"
MODE_CLUB = "club"
ROWS_PER_PAGE = 6
NAME_MAX = 16
DEFAULT_NAMES = {"l": "LEFT FENCER", "r": "RIGHT FENCER"}
MODE_WIFI = "wifi"
MODE_WIFIPW = "wifipw"
MODE_CONFIRM = "confirm"    # generic yes/no gate in front of a destructive action
LOGODIR = os.path.join(os.path.expanduser("~/skewered"), "logos")
# Scratch space for the per-bout cuts. Intermediates only -- a combined export
# concatenates them and then deletes them.
UPLOAD_DIR = os.path.join(os.path.expanduser("~/skewered"), "uploads")
# What the Samba share points at. Finished, human-named files land here and
# stay until someone clears them: the Pi cannot know when they were copied off,
# so nothing deletes them automatically (see the CLEAR EXPORTS button).
EXPORT_DIR = os.path.join(os.path.expanduser("~/skewered"), "exports")
UPLOAD_PRE = 4.0            # lead-in before a bout's first touch
UPLOAD_POST = 4.0           # tail after its last touch
UPLOAD_MAX_SECS = 1200      # 20-minute cap per uploaded bout
BOX_SSID = "SkeweredNet"
# logo circle centers on the 1920x1080 canvas (gap between nameplate and score)
LOGO_CENTERS = {"l": (567, 950), "r": (1344, 950)}
LOGO_D = 120               # diameter in canvas px
PINNED_CLUBS = ["USA"]     # always offered in the picker (besides "None")

# ---------------- shared telemetry state ----------------
state = {"l": 0, "r": 0, "min": 0, "sec": 0, "flags": [False] * 12, "age": None,
         "hit_age": None, "status": (ST_OFF, ST_OFF), "hide_extra": False}
_lock = threading.Lock()
last_activity = [0.0]
pending_events = []
state_changes = []

# ts of a touch whose final score has not settled yet. A touch is written the
# moment its lights go out, but the score at that instant is unreliable: the
# referee enters the point either side of clearing the lights, and in this
# station's own history 53% of touches showed no score change by lights-out.
# So the score written then is a placeholder, corrected once it has definitely
# settled -- see settle_pending_touch().
pending_touch = [None]


def settle_pending_touch(l, r):
    """Correct the score on the touch that is waiting for one.

    Caller must hold _lock. Emits a `touch_score` event that the main thread
    turns into an UPDATE; events are created here but written there, so this
    keeps that boundary rather than reaching into the database.
    """
    if pending_touch[0] is None:
        return
    pending_events.append({"type": "touch_score", "ts": pending_touch[0],
                           "l0": 0, "r0": 0, "l1": l, "r1": r, "detail": ""})
    pending_touch[0] = None


def hit_time(now, status, hit_age):
    """Back-date a touch to when the hit physically happened.

    The first packet carrying a hit already reports it as 7-93 ms old (measured
    over five episodes), and transport jitter adds more on top, so arrival time
    is a poor timestamp for something judged in milliseconds. The box's own age
    field removes that error rather than averaging it.

    Only VALID and NONVALID carry a real age. For LATE the same bytes hold a
    frozen lateness figure and for SHORT a duration -- back-dating by either
    would move the touch to a time it did not happen.

    With two ages present the larger one is the earlier hit, and therefore the
    true start of the episode.
    """
    if not hit_age:
        return now
    ages = [hit_age[i] for i in (0, 1)
            if status[i] in ST_AGE_MEANS_AGE and hit_age[i]]
    return now - max(ages) / 1000.0 if ages else now


def note_special(episode, status, hit_age):
    """Fold lateness and the double-touch margin into the running episode."""
    for i, side in ((0, "left"), (1, "right")):
        if status[i] == ST_LATE and hit_age:
            # Frozen for the whole episode, so max() is just belt and braces.
            episode["late"][i] = max(episode["late"][i], hit_age[i])
            episode["lights"].add("late_" + side)
        elif status[i] == ST_SHORT:
            episode["lights"].add("short_" + side)

    # Both sides valid: the two ages tick together, so their difference is the
    # interval between the hits and the LARGER age landed first. Taken once, at
    # the first sighting -- once either age clamps at 999 the difference starts
    # shrinking and would understate the margin.
    if (hit_age and not episode["margin_set"] and all(hit_age)
            and all(s in ST_AGE_MEANS_AGE for s in status)):
        episode["margin"] = abs(hit_age[0] - hit_age[1])
        episode["first"] = "left" if hit_age[0] > hit_age[1] else "right"
        episode["margin_set"] = True


def udp_thread():
    prev = None
    episode = None
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", UDP_PORT))
    s.settimeout(1)
    while True:
        try:
            data, _ = s.recvfrom(1024)
        except socket.timeout:
            continue
        # Accept both payload versions so the transmitter and the Pi can be
        # upgraded in either order -- a length mismatch here drops EVERY packet
        # and the station just shows "NO BOX SIGNAL", which is a miserable way
        # to discover a version skew.
        if len(data) == LEN_V3:
            f = struct.unpack(FMT_V3, data)
            hit_age = (f[19], f[20])
            status = (f[21], f[22])   # left, right -- raw 3-bit box status
            hide_extra = f[23]
        elif len(data) == LEN_AGE:
            f = struct.unpack(FMT_AGE, data)
            hit_age = (f[19], f[20])
            # Source predates the raw status: infer what the booleans can show.
            # Late and short simply are not expressible here.
            status = (ST_VALID if f[7] else ST_NONVALID if f[9] else ST_OFF,
                      ST_VALID if f[6] else ST_NONVALID if f[8] else ST_OFF)
            hide_extra = False
        elif len(data) == LEN_PLAIN:
            f = struct.unpack(FMT, data)
            hit_age = None            # transmitter predates the hit-age field
            status = (ST_OFF, ST_OFF)
            hide_extra = False
        else:
            continue
        now = time.time()
        r, l, sec, mn = f[2], f[3], f[4], f[5]
        flags = list(f[6:18])
        with _lock:
            state["r"], state["l"] = r, l
            state["sec"], state["min"] = sec, mn
            state["flags"] = flags
            state["age"] = now
            state["hit_age"] = hit_age   # (left_ms, right_ms) or None
            state["status"] = status     # raw 3-bit light status per side
            state["hide_extra"] = hide_extra

            if prev is None:
                prev = (l, r, mn, sec, flags, status)
                continue
            pl, pr, pm, ps, pf, pst = prev
            if (l, r, mn, sec, flags, status) == (pl, pr, pm, ps, pf, pst):
                continue
            last_activity[0] = now
            state_changes.append((now, l, r, mn, sec,
                                  "".join("1" if x else "0" for x in flags)))

            if (l, r) == (0, 0) and (pl, pr) != (0, 0):
                # Trigger 2: the bout ended. Settle BEFORE recording the reset
                # -- (l, r) is already 0-0 here, so the final score only still
                # exists in the previous packet.
                settle_pending_touch(pl, pr)
                pending_events.append({"type": "bout_start", "ts": now,
                                       "l0": pl, "r0": pr, "l1": 0, "r1": 0,
                                       "detail": ""})

            # Late and short hits set no boolean flag, so the raw status has to
            # be consulted here or those touches never open an episode at all
            # -- which is what used to happen: a late hit was invisible.
            special = (ST_SHORT, ST_LATE)
            hit_now = any(flags[0:4]) or any(s in special for s in status)
            hit_prev = any(pf[0:4]) or any(s in special for s in pst)
            if hit_now and not hit_prev and episode is None:
                # Trigger 1: new lights. The score just before them is the
                # settled result of the previous touch -- which also makes each
                # touch's after-score equal the next one's before-score.
                settle_pending_touch(pl, pr)
                episode = {"ts": hit_time(now, status, hit_age),
                           "lights": set(), "l0": pl, "r0": pr,
                           "late": [0, 0], "margin": 0, "first": "",
                           "margin_set": False}
            if episode is not None:
                for i in range(4):
                    if flags[i]:
                        episode["lights"].add(FLAG_NAMES[i])
                note_special(episode, status, hit_age)
                if not hit_now:
                    # Write the touch now so a crash cannot lose it, but treat
                    # this score as provisional -- it is corrected by one of
                    # the three settle triggers.
                    pending_events.append({
                        "type": "touch", "ts": episode["ts"],
                        "l0": episode["l0"], "r0": episode["r0"],
                        "l1": l, "r1": r,
                        "detail": "+".join(sorted(episode["lights"])),
                        "late_l": episode["late"][0],
                        "late_r": episode["late"][1],
                        "margin_ms": episode["margin"],
                        "first_side": episode["first"]})
                    pending_touch[0] = episode["ts"]
                    episode = None

            for i in range(6, 12):
                if flags[i] and not pf[i]:
                    pending_events.append({
                        "type": "card" if i < 10 else "priority", "ts": now,
                        "l0": pl, "r0": pr, "l1": l, "r1": r,
                        "detail": FLAG_NAMES[i]})
            prev = (l, r, mn, sec, flags, status)


# ---------------- database ----------------
def db_init():
    con = sqlite3.connect(DBPATH)
    # WAL, because the export worker writes from its own connection while the
    # main loop is reading bouts for the browser. The default rollback journal
    # lets a reader block a writer, which is exactly how "database is locked"
    # killed an export mid-cut. WAL allows one writer alongside readers, and it
    # is a property of the file, so every connection inherits it.
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass                            # not fatal; the default still works
    con.executescript("""
    CREATE TABLE IF NOT EXISTS sessions(
        id INTEGER PRIMARY KEY, start_ts REAL, end_ts REAL);
    CREATE TABLE IF NOT EXISTS files(
        id INTEGER PRIMARY KEY, session_id INT, idx INT, filename TEXT,
        start_offset REAL, end_offset REAL, deleted INT DEFAULT 0);
    CREATE TABLE IF NOT EXISTS bouts(
        id INTEGER PRIMARY KEY, session_id INT, start_ts REAL,
        session_offset REAL);
    CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY, session_id INT, bout_id INT, type TEXT,
        ts REAL, session_offset REAL, file_idx INT, file_offset REAL,
        l_before INT, r_before INT, l_after INT, r_after INT, detail TEXT);
    CREATE TABLE IF NOT EXISTS state_log(
        ts REAL, session_id INT, l INT, r INT, min INT, sec INT, flags TEXT);
    """)
    # export_name is the "already saved" marker that youtube_id used to be: the
    # filename this bout was written to on the share. youtube_id is kept, not
    # dropped -- it is the record of what was uploaded back when the station
    # uploaded things, and SQLite makes dropping columns awkward anyway.
    for col in ("l_name", "r_name", "youtube_id", "upload_path", "export_name"):
        try:
            con.execute("ALTER TABLE bouts ADD COLUMN %s TEXT DEFAULT ''" % col)
        except sqlite3.OperationalError:
            pass
    # Hit timing, added once the box's own millisecond fields were understood.
    # late_l/late_r are the box's lateness figure for a hit that arrived after
    # lockout; margin_ms is the gap between the two hits of a double touch and
    # first_side says which of them landed first.
    for col, decl in (("late_l", "INT DEFAULT 0"), ("late_r", "INT DEFAULT 0"),
                      ("margin_ms", "INT DEFAULT 0"),
                      ("first_side", "TEXT DEFAULT ''")):
        try:
            con.execute("ALTER TABLE events ADD COLUMN %s %s" % (col, decl))
        except sqlite3.OperationalError:
            pass
    con.execute("CREATE TABLE IF NOT EXISTS names_used("
                "name TEXT PRIMARY KEY, last_used REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS name_club("
                "name TEXT PRIMARY KEY, club TEXT, last_used REAL)")
    con.commit()
    return con


def write_names(l_name, r_name):
    with open(os.path.join(HOME, "fencers.txt"), "w") as fh:
        fh.write("%s\n%s\n" % (l_name, r_name))


def read_clubs():
    try:
        with open(os.path.join(HOME, "clubs.txt")) as fh:
            lines = [ln.strip() for ln in fh.readlines()] + ["", ""]
        return (lines[0], lines[1])
    except Exception:
        return ("", "")


def write_clubs(l_club, r_club):
    with open(os.path.join(HOME, "clubs.txt"), "w") as fh:
        fh.write("%s\n%s\n" % (l_club, r_club))


def read_names():
    try:
        with open(os.path.join(HOME, "fencers.txt")) as fh:
            lines = [ln.strip() for ln in fh.readlines()]
        return (lines[0] or "LEFT", lines[1] or "RIGHT")
    except Exception:
        return ("LEFT", "RIGHT")


def audio_ok(dev=AUDIO_DEV):
    """True if the mic is present and actually captures.

    Audio is OPTIONAL by design. If ffmpeg cannot open the ALSA device it
    exits, and it would take the whole recorder with it -- losing the video
    too. A mic that is unplugged, still enumerating, or renamed must degrade
    to video-only, not to no recording at all: this box autostarts at boot in
    a sports hall with nobody watching the console.
    """
    try:
        r = subprocess.run(
            ["arecord", "-D", dev, "-d", "1", "-f", "S16_LE",
             "-r", str(AUDIO_RATE), "-c", "1", "-t", "raw"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=6)
        return r.returncode == 0
    except Exception:
        return False


# ---------------- recorder ----------------
class Recorder:
    def __init__(self, con):
        self.con = con
        self.has_audio = False
        self.proc = None
        self.session_id = None
        self.session_start = None
        self.bout_id = None
        self.frames = 0
        self.seglist = os.path.join(RECDIR, "segments.csv")

    @property
    def active(self):
        return self.proc is not None

    def _new_bout(self, ts, offset):
        nm = read_names()
        cur = self.con.execute(
            "INSERT INTO bouts(session_id, start_ts, session_offset, l_name,"
            " r_name) VALUES(?,?,?,?,?)",
            (self.session_id, ts, offset, nm[0], nm[1]))
        self.bout_id = cur.lastrowid

    def start(self):
        os.makedirs(RECDIR, exist_ok=True)
        self.ensure_space()
        pending_touch[0] = None    # nothing from a previous session carries over
        now = time.time()
        cur = self.con.execute(
            "INSERT INTO sessions(start_ts, end_ts) VALUES(?,?)", (now, now))
        self.session_id = cur.lastrowid
        self.session_start = now
        self.frames = 0
        self._new_bout(now, 0.0)
        self.con.commit()
        pattern = os.path.join(RECDIR, "rec_%Y-%m-%d_%H-%M-%S.mp4")
        self.has_audio = audio_ok()
        # thread_queue_size on BOTH inputs. Without it on the rawvideo pipe the
        # ALSA reader is stalled by pipe backpressure, which is precisely the
        # stall that would cause capture xruns -- the audio would inherit the
        # video pipeline's jitter, which is the whole thing we are avoiding by
        # letting ffmpeg open the mic itself instead of piping it through the
        # capture loop.
        cmd = ["ffmpeg", "-loglevel", "error",
               "-thread_queue_size", "512",
               "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", "%dx%d" % (REC_W, REC_H), "-r", str(FPS), "-i", "-"]
        if self.has_audio:
            # -channels/-sample_rate, NOT -ac/-ar: those are codec options and
            # the ALSA demuxer ignores them, falling back to its default of 2
            # channels -- which this mono mic rejects outright, taking the
            # whole recorder down with it.
            cmd += ["-thread_queue_size", "1024",
                    "-f", "alsa", "-channels", "1",
                    "-sample_rate", str(AUDIO_RATE),
                    "-i", AUDIO_DEV]
        cmd += [
               # veryfast, not ultrafast. ultrafast was tried against the live
               # latency and halved the encoder's CPU (127% -> 55%) without
               # moving the loop rate at all (29.44 -> 29.47 fps), which is
               # what showed the encoder was never the bottleneck -- the
               # capture side was. It costs ~2x the file size, so there is no
               # reason to keep it.
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
               "-pix_fmt", "yuv420p", "-g", str(FPS * 2)]
        if self.has_audio:
            # Do NOT add -shortest here. It is the obvious fix for the fact
            # that the ALSA input never ends, and it DEADLOCKS: combined with
            # the segment muxer, ffmpeg stopped draining the video pipe
            # entirely -- capture loop blocked in anon_pipe_write, ffmpeg idle
            # in futex_do_wait, 36-byte output file after 3 minutes, while
            # ALSA sat there RUNNING and healthy with 224 s captured. Ending
            # the process is handled by SIGINT in stop() instead.
            cmd += ["-c:a", "aac", "-b:a", "128k"]
        cmd += [
               "-force_key_frames", "expr:gte(t,n_forced*%d)" % SEGMENT_SECS,
               "-f", "segment", "-segment_time", str(SEGMENT_SECS),
               "-reset_timestamps", "1", "-strftime", "1",
               "-segment_format", "mp4",
               "-segment_format_options", "movflags=+frag_keyframe+empty_moov",
               "-segment_list", self.seglist, "-segment_list_type", "csv",
               pattern]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL)

    def write(self, buf):
        if self.proc:
            try:
                self.proc.stdin.write(buf)
                self.frames += 1
            except BrokenPipeError:
                self.stop()

    def offset(self):
        return self.frames / float(FPS)

    def stop(self):
        if not self.proc:
            return
        try:
            self.proc.stdin.close()
            if self.has_audio:
                # EOF on stdin ends the video input but NOT the ALSA one, which
                # runs forever -- so ffmpeg would sit there until the timeout
                # below and get killed on every single stop. SIGINT is ffmpeg's
                # own clean-shutdown path (what Ctrl-C / 'q' does): it stops
                # reading inputs and writes the trailer.
                self.proc.send_signal(signal.SIGINT)
            self.proc.wait(timeout=15)
        except Exception:
            self.proc.kill()
        self.proc = None
        self.con.execute("UPDATE sessions SET end_ts=? WHERE id=?",
                         (time.time(), self.session_id))
        try:
            with open(self.seglist) as fh:
                for i, line in enumerate(fh):
                    parts = line.strip().rsplit(",", 2)
                    if len(parts) == 3:
                        self.con.execute(
                            "INSERT INTO files(session_id, idx, filename, "
                            "start_offset, end_offset) VALUES(?,?,?,?,?)",
                            (self.session_id, i, parts[0],
                             float(parts[1]), float(parts[2])))
            os.remove(self.seglist)
        except OSError:
            pass
        self.con.commit()

    def log_event(self, ev):
        if ev["type"] == "touch_score":
            # Correct a placeholder score in place. Matched on the touch's own
            # timestamp, so the row keeps its original session_offset and the
            # replay position is untouched.
            self.con.execute(
                "UPDATE events SET l_after=?, r_after=? WHERE type='touch'"
                " AND session_id=? AND ts=?",
                (ev["l1"], ev["r1"], self.session_id, ev["ts"]))
            self.con.commit()
            return
        off = ev["ts"] - self.session_start if self.session_start else 0
        if ev["type"] == "bout_start":
            self._new_bout(ev["ts"], off)
        self.con.execute(
            "INSERT INTO events(session_id, bout_id, type, ts, session_offset,"
            " file_idx, file_offset, l_before, r_before, l_after, r_after,"
            " detail, late_l, late_r, margin_ms, first_side)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.session_id, self.bout_id, ev["type"], ev["ts"], off,
             int(off // SEGMENT_SECS), off % SEGMENT_SECS,
             ev["l0"], ev["r0"], ev["l1"], ev["r1"], ev["detail"],
             # Only touch events carry these; cards, priorities and bout starts
             # leave them at zero.
             ev.get("late_l", 0), ev.get("late_r", 0),
             ev.get("margin_ms", 0), ev.get("first_side", "")))
        self.con.commit()

    def session_files(self):
        """Sorted absolute paths of the ACTIVE session's segment files."""
        if not self.session_start:
            return []
        out = []
        try:
            for f in sorted(os.listdir(RECDIR)):
                if not f.endswith(".mp4"):
                    continue
                p = os.path.join(RECDIR, f)
                if os.path.getmtime(p) >= self.session_start - 2:
                    out.append(p)
        except OSError:
            pass
        return out

    def free_mb(self):
        st = os.statvfs(RECDIR)
        return st.f_bavail * st.f_frsize / (1024 * 1024)

    def ensure_space(self):
        os.makedirs(RECDIR, exist_ok=True)
        while self.free_mb() < MIN_FREE_MB:
            row = self.con.execute(
                "SELECT id, filename FROM files WHERE deleted=0 "
                "ORDER BY id LIMIT 1").fetchone()
            if row is None:
                vids = sorted(os.path.join(RECDIR, f)
                              for f in os.listdir(RECDIR) if f.endswith(".mp4"))
                if not vids:
                    break
                os.remove(vids[0])
                continue
            try:
                os.remove(rec_path(row[1]))
            except OSError:
                pass
            self.con.execute("UPDATE files SET deleted=1 WHERE id=?", (row[0],))
            self.con.commit()


def rec_path(fn):
    """files-table names may be bare (ffmpeg's segment list strips the dir)."""
    return fn if os.path.isabs(fn) else os.path.join(RECDIR, fn)


def bout_video_range(con, bout_id):
    """(session_id, start_offset, end_offset) spanning first to last touch."""
    row = con.execute(
        "SELECT session_id, MIN(session_offset), MAX(session_offset) FROM"
        " events WHERE bout_id=? AND type='touch'", (bout_id,)).fetchone()
    if not row or row[1] is None:
        return None
    sid, first, last = row
    start = max(0.0, first - UPLOAD_PRE)
    end = min(last + UPLOAD_POST, start + UPLOAD_MAX_SECS)
    return (sid, start, end)


def export_bout(con, rec, bout_id):
    """Cut one bout (first to last touch) into UPLOAD_DIR via stream copy.
    Returns (path, None) or (None, reason)."""
    rng = bout_video_range(con, bout_id)
    if rng is None:
        return None, "no touches"
    sid, start, end = rng
    segs = []
    if rec.active and rec.session_id == sid:
        for i, p in enumerate(rec.session_files()):
            segs.append((i * SEGMENT_SECS, (i + 1) * SEGMENT_SECS, p))
    else:
        for idx, fn, so, eo, deleted in con.execute(
                "SELECT idx, filename, start_offset, end_offset, deleted FROM"
                " files WHERE session_id=? ORDER BY idx", (sid,)):
            if deleted or not os.path.exists(rec_path(fn)):
                if eo > start and so < end:
                    return None, "footage deleted"
                continue
            segs.append((so, eo, rec_path(fn)))
    use = [s for s in segs if s[1] > start and s[0] < end]
    if not use:
        return None, "footage missing"
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    out = os.path.join(UPLOAD_DIR, "bout_%04d.mp4" % bout_id)
    lst = os.path.join(UPLOAD_DIR, "concat_%d.txt" % bout_id)
    with open(lst, "w") as fh:
        for so, eo, fn in use:
            fh.write("file '%s'\n" % fn.replace("'", "'\\''"))
    try:
        r = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe",
             "0", "-i", lst, "-ss", "%.2f" % (start - use[0][0]),
             "-t", "%.2f" % (end - start), "-c", "copy",
             "-movflags", "+faststart", out],
            capture_output=True, timeout=300)
    finally:
        try:
            os.remove(lst)
        except OSError:
            pass
    if r.returncode != 0 or not os.path.exists(out):
        return None, "cut failed"
    con.execute("UPDATE bouts SET upload_path=? WHERE id=?", (out, bout_id))
    con.commit()
    return out, None


# Frame-arrow auto-repeat. 4 loop iterations at 30 fps is ~7.5 frames/sec,
# matching the 25% playback speed.
STEP_HOLD_S = 0.4         # hold this long before scrubbing starts
STEP_REPEAT_EVERY = 4     # loop iterations between repeats

YT_DESC_MAX = 5000        # YouTube's hard description limit
YT_DESC_MARGIN = 250      # stay clear of it
YT_MIN_CHAPTER = 10.0     # a chapter shorter than this voids the WHOLE list


_FS_BAD = set('/\\:*?"<>|')


def safe_filename(name):
    """Make a title usable as a filename on the share.

    The share is read from Windows, so the reserved characters are Windows'
    rather than Linux's -- a colon in `Name vs Name - date (5 - 3)` copies
    across as a broken file otherwise.
    """
    out = "".join("-" if c in _FS_BAD else c for c in name)
    out = " ".join(out.split())          # collapse whitespace, strip ends
    return out[:120] or "bout"


def publish(src, title, description=None):
    """Move a finished cut onto the share under a human-readable name.

    The filename IS the interface here -- nobody browsing a folder in Explorer
    can tell what `bout_0007.mp4` contains. Returns the bare filename, which is
    what gets recorded as the bout's export marker.

    The touch list is written beside it as a .txt. It used to become the
    YouTube description; with the upload done by hand it is still the useful
    part, ready to paste in.
    """
    os.makedirs(EXPORT_DIR, exist_ok=True)
    base = safe_filename(title)
    name = base + ".mp4"
    dst = os.path.join(EXPORT_DIR, name)
    n = 2
    while os.path.exists(dst):           # never clobber an earlier export
        name = "%s (%d).mp4" % (base, n)
        dst = os.path.join(EXPORT_DIR, name)
        n += 1
    try:
        os.replace(src, dst)             # same filesystem: atomic rename
    except OSError:
        try:
            shutil.copy2(src, dst)       # different device -- copy and drop
            os.remove(src)
        except OSError:
            return None
    if description:
        try:
            with open(os.path.splitext(dst)[0] + ".txt", "w") as fh:
                fh.write(description)
        except OSError:
            pass                         # the video is what matters
    return name


def exports_on_share():
    """(file count, total bytes) currently sitting on the share."""
    n = b = 0
    try:
        for f in os.listdir(EXPORT_DIR):
            if f.endswith(".mp4"):
                n += 1
                b += os.path.getsize(os.path.join(EXPORT_DIR, f))
    except OSError:
        pass
    return n, b


def clear_exports():
    """Delete everything on the share and forget the export markers.

    Clearing the markers as well is deliberate: once a file is gone, the bout
    is genuinely unsaved again, and SAVE ALL should offer it. A marker pointing
    at a file that no longer exists would quietly hide it forever.
    """
    n = 0
    try:
        for f in os.listdir(EXPORT_DIR):
            if not f.endswith((".mp4", ".txt")):
                continue
            try:
                os.remove(os.path.join(EXPORT_DIR, f))
                if f.endswith(".mp4"):
                    n += 1               # count videos, not their sidecars
            except OSError:
                pass
    except OSError:
        pass
    con = sqlite3.connect(DBPATH, timeout=10)
    con.execute("UPDATE bouts SET export_name=''")
    con.commit()
    con.close()
    return n


FAT32_MAX = 4 * 1024 ** 3 - 1   # the largest file a FAT32 stick can hold


def usb_target():
    """The mounted removable drive to copy onto, or None.

    Reads the mount point from lsblk rather than guessing a path under /media.
    The desktop automounter appends a digit when an old mount directory is
    still lying around, so a stick labelled ESD-USB can appear as ESD-USB5 --
    the label is not the directory name and must not be used to build a path.
    """
    try:
        out = subprocess.run(
            ["lsblk", "-Pno", "NAME,RM,TYPE,MOUNTPOINT,LABEL,FSTYPE"],
            capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        f = dict(re.findall(r'(\w+)="([^"]*)"', line))
        mp = f.get("MOUNTPOINT", "")
        # RM=1 is the kernel's removable flag -- this is what keeps the SD card
        # and its boot partition out of the running.
        if (f.get("RM") == "1" and f.get("TYPE") == "part"
                and mp.startswith(("/media/", "/mnt/"))):
            try:
                free = shutil.disk_usage(mp).free
            except OSError:
                continue
            return {"mount": mp, "dev": "/dev/" + f.get("NAME", ""),
                    "label": f.get("LABEL") or f.get("NAME", "USB"),
                    "free": free, "fstype": f.get("FSTYPE", "")}
    return None


def usb_copy(target, on_progress=None):
    """Copy everything on the share onto the stick.

    Returns (copied, skipped, errors). Files already present at the same size
    are skipped, so copying twice is harmless and a half-finished copy can be
    repeated without duplicating work.
    """
    dest = os.path.join(target["mount"], "Skewered")
    copied = skipped = 0
    errors = []
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as e:
        return 0, 0, [("", "cannot write to the drive: %s" % e)]

    try:
        names = sorted(f for f in os.listdir(EXPORT_DIR)
                       if f.endswith((".mp4", ".txt")))
    except OSError:
        names = []

    for i, name in enumerate(names):
        src = os.path.join(EXPORT_DIR, name)
        dst = os.path.join(dest, name)
        if on_progress:
            on_progress(i, len(names), name)
        try:
            size = os.path.getsize(src)
        except OSError:
            continue
        if os.path.exists(dst) and os.path.getsize(dst) == size:
            skipped += 1
            continue
        # FAT32 cannot hold a file of 4 GiB or more, and a long combined video
        # can exceed that. Say so plainly rather than failing mid-copy.
        if target["fstype"] == "vfat" and size > FAT32_MAX:
            errors.append((name, "too big for FAT32 (%.1f GB)" % (size / 1e9)))
            continue
        if size > shutil.disk_usage(target["mount"]).free:
            errors.append((name, "not enough space on the drive"))
            continue
        try:
            # Copy to a temporary name first: a stick pulled mid-write then
            # leaves an obvious .part file rather than a truncated video that
            # looks complete.
            tmp = dst + ".part"
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            copied += 1
        except OSError as e:
            errors.append((name, str(e)[:40]))
            try:
                os.remove(tmp)
            except OSError:
                pass
    return copied, skipped, errors


def usb_eject(target):
    """Flush and unmount, so the stick can be pulled safely.

    Without this the copy may still be sitting in the kernel's write cache --
    the files look written, the drive light is off, and yanking it truncates
    them anyway.
    """
    try:
        subprocess.run(["sync"], timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        r = subprocess.run(["udisksctl", "unmount", "-b", target["dev"]],
                           capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def clip_duration(path):
    """Length of a cut clip in seconds, or None if ffprobe cannot tell."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out)
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def hms(t):
    """YouTube timestamp: M:SS below an hour, H:MM:SS at or above."""
    t = max(0, int(t))
    h, m, s = t // 3600, (t % 3600) // 60, t % 60
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def bout_chapter_label(con, bout_id):
    row = con.execute(
        "SELECT l_name, r_name,"
        " (SELECT e.l_after FROM events e WHERE e.bout_id=bouts.id AND"
        "  e.type='touch' ORDER BY e.id DESC LIMIT 1),"
        " (SELECT e.r_after FROM events e WHERE e.bout_id=bouts.id AND"
        "  e.type='touch' ORDER BY e.id DESC LIMIT 1)"
        " FROM bouts WHERE id=?", (bout_id,)).fetchone()
    ln, rn, lf, rf = row
    score = " (%d-%d)" % (lf, rf) if lf is not None else ""
    return "%s vs %s%s" % (ln or "Left", rn or "Right", score)


def combined_title(con, bout_ids):
    q = ",".join("?" * len(bout_ids))
    lo, hi = con.execute("SELECT MIN(start_ts), MAX(start_ts) FROM bouts"
                         " WHERE id IN (%s)" % q, bout_ids).fetchone()
    first = time.strftime("%Y-%m-%d", time.localtime(lo))
    last = time.strftime("%Y-%m-%d", time.localtime(hi))
    span = first if first == last else "%s to %s" % (first, last)
    return "Fencing - %d bouts - %s" % (len(bout_ids), span)


def combined_description(con, parts, total):
    """Bout chapters, then per-touch timestamps for as long as they fit.

    Two YouTube rules drive the shape of this:

    1. Chapters must start at 0:00, be in order, and every one must last at
       least 10 s. Break any of those and YouTube silently drops the entire
       chapter bar rather than just the offending entry.
    2. A timestamp is only treated as a chapter when it BEGINS a line.

    22% of consecutive touches in this database are less than 10 s apart, so
    touches cannot be chapters. They are written mid-line instead ("1 - 0 at
    4:21"), which keeps them clickable while leaving the chapter list to the
    bouts alone.
    """
    chapters, blocks, off = [], [], 0.0
    for bid, _path, dur in parts:
        label = bout_chapter_label(con, bid)
        chapters.append((off, label))
        rng = bout_video_range(con, bid)
        items = []
        if rng:
            _sid, cstart, _cend = rng
            for so, l, r, d in con.execute(
                    "SELECT session_offset, l_after, r_after, detail FROM"
                    " events WHERE bout_id=? AND type='touch' ORDER BY id",
                    (bid,)):
                items.append((off + max(0.0, so - cstart),
                              "%d - %d (%s)" % (l, r, (d or "").replace("_", " "))))
        blocks.append((label, items))
        off += dur

    kept = []
    for o, label in chapters:
        if not kept or o - kept[-1][0] >= YT_MIN_CHAPTER:
            kept.append((o, label))
    while len(kept) > 1 and total - kept[-1][0] < YT_MIN_CHAPTER:
        kept.pop()          # a too-short final chapter voids the list too

    lines = ["Bouts:"]
    lines += ["%s %s" % (hms(o), label) for o, label in kept]
    tail = ["", "Recorded automatically by the fencing strip station."]
    budget = YT_DESC_MAX - YT_DESC_MARGIN - len("\n".join(lines + tail))

    body, used = ["", "Touches:"], 0
    for label, items in blocks:
        chunk = [label] + ["   %s at %s" % (txt, hms(o)) for o, txt in items]
        n = sum(len(x) + 1 for x in chunk)
        if used + n > budget:
            body.append("(touch list truncated - description length limit)")
            break
        body += chunk
        used += n
    return "\n".join(lines + body + tail)


def build_combined(con, rec, bout_ids):
    """Cut every bout, then concatenate them into one video.

    Returns (path, title, description, used_ids, errors).
    """
    parts, errors = [], []
    for bid in bout_ids:
        path, err = export_bout(con, rec, bid)
        if not path:
            errors.append((bid, err))
            continue
        dur = clip_duration(path)
        if dur is None:
            errors.append((bid, "unreadable clip"))
            continue
        parts.append((bid, path, dur))
    if not parts:
        return None, None, None, [], errors

    out = os.path.join(UPLOAD_DIR,
                       "combined_%s.mp4" % time.strftime("%Y%m%d_%H%M%S"))
    lst = os.path.join(UPLOAD_DIR, "concat_combined.txt")
    with open(lst, "w") as fh:
        for _bid, p, _d in parts:
            fh.write("file '%s'\n" % p.replace("'", "'\\''"))
    try:
        r = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
             "-i", lst, "-c", "copy", "-movflags", "+faststart", out],
            capture_output=True, timeout=3600)
    finally:
        try:
            os.remove(lst)
        except OSError:
            pass
    if r.returncode != 0 or not os.path.exists(out):
        return None, None, None, [], errors + [(0, "combine failed")]

    total = sum(d for _b, _p, d in parts)
    ids = [b for b, _p, _d in parts]
    return out, combined_title(con, ids), combined_description(con, parts, total), ids, errors


def bout_upload_title(con, bout_id):
    row = con.execute(
        "SELECT start_ts, l_name, r_name,"
        " (SELECT e.l_after FROM events e WHERE e.bout_id=bouts.id AND"
        "  e.type='touch' ORDER BY e.id DESC LIMIT 1),"
        " (SELECT e.r_after FROM events e WHERE e.bout_id=bouts.id AND"
        "  e.type='touch' ORDER BY e.id DESC LIMIT 1)"
        " FROM bouts WHERE id=?", (bout_id,)).fetchone()
    ts, ln, rn, lf, rf = row
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    score = " (%d-%d)" % (lf, rf) if lf is not None else ""
    return "%s vs %s - %s%s" % (ln or "Left", rn or "Right", when, score)


def bout_description(con, bout_id):
    rows = con.execute(
        "SELECT l_after, r_after, detail FROM events WHERE bout_id=? AND"
        " type='touch' ORDER BY id", (bout_id,)).fetchall()
    lines = ["Touches:"]
    for i, (l, r, d) in enumerate(rows, 1):
        lines.append("%d.  %d - %d   (%s)" % (i, l, r, d.replace("_", " ")))
    lines.append("")
    lines.append("Recorded automatically by the fencing strip station.")
    return "\n".join(lines)


def internet_up():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3).close()
        return True
    except OSError:
        return False


def wifi_scan():
    """[(ssid, signal, secured)] sorted by signal, deduped, box AP excluded."""
    try:
        r = subprocess.run(
            ["sudo", "-n", "nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY",
             "dev", "wifi", "list", "--rescan", "yes"],
            capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return []
    nets = {}
    for line in r.stdout.splitlines():
        parts = line.rsplit(":", 2)
        if len(parts) != 3 or not parts[0] or parts[0] == BOX_SSID:
            continue
        ssid, sig, sec = parts[0], int(parts[1] or 0), parts[2]
        if ssid not in nets or nets[ssid][0] < sig:
            nets[ssid] = (sig, bool(sec.strip()))
    return sorted(((s, v[0], v[1]) for s, v in nets.items()),
                  key=lambda x: -x[1])


def wifi_connect(ssid, password):
    cmd = ["sudo", "-n", "nmcli", "dev", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def reconnect_box():
    subprocess.run(["sudo", "-n", "nmcli", "con", "up", BOX_SSID],
                   capture_output=True, timeout=30)


def clip_source(con, rec, session_id, session_offset):
    """(filepath, local_offset) for a clip starting CLIP_PRE before the event,
    or (None, 0) if the footage is gone."""
    start = max(0.0, session_offset - CLIP_PRE)
    if rec.active and rec.session_id == session_id:
        files = rec.session_files()
        idx = int(start // SEGMENT_SECS)
        if idx < len(files):
            return files[idx], start - idx * SEGMENT_SECS
        return None, 0
    row = con.execute(
        "SELECT filename, start_offset, deleted FROM files WHERE session_id=?"
        " AND start_offset<=? AND end_offset>?",
        (session_id, start, start)).fetchone()
    if not row or row[2] or not os.path.exists(rec_path(row[0])):
        return None, 0
    return rec_path(row[0]), start - row[1]


# ---------------- overlay ----------------
def make_overlay_assets():
    def load(name, w, h, sx, sy):
        img = pygame.image.load(os.path.join(ADIR, name)).convert_alpha()
        size = (max(1, round(w * sx * SC)), max(1, round(h * sy * SC)))
        return pygame.transform.smoothscale(img, size)

    def cv(x, y):
        return (round(x * SC), round(y * SC))

    return {
        "plate_l":  (load("Blue Rectangle.png", 1200, 300, 0.140, 0.867), cv(640, 820)),
        "plate_r":  (load("Blue Rectangle.png", 1200, 300, 0.140, 0.867), cv(1093, 820)),
        "nplate_l": (load("Blue Rectangle.png", 1200, 300, 0.400, 0.867), cv(21, 820)),
        "nplate_r": (load("Blue Rectangle.png", 1200, 300, 0.394, 0.867), cv(1427, 820)),
        "red":      (load("Red Rectangle PS.png", 900, 300, 0.354, 0.530), cv(289, 935)),
        "green":    (load("Green Rectangle PS.png", 900, 300, 0.354, 0.530), cv(1312, 935)),
        "white_r":  (load("White Rectangle PS.png", 900, 300, 0.358, 0.563), cv(15, 930)),
        "white_g":  (load("White Rectangle PS.png", 900, 300, 0.358, 0.563), cv(1583, 930)),
        "rcard_r":  (load("Red Rectangle PS.png", 900, 300, 0.078, 0.457), cv(108, 772)),
        "rcard_g":  (load("Red Rectangle PS.png", 900, 300, 0.078, 0.457), cv(1742, 772)),
        "ycard_r":  (load("Yellow Card.png", 120, 150, 0.450, 0.413), cv(48, 812)),
        "ycard_g":  (load("Yellow Card.png", 120, 150, 0.450, 0.413), cv(1818, 812)),
        "prio_l":   (load("Red Rectangle.png", 240, 120, 0.550, 0.550), cv(183, 809)),
        "prio_r":   (load("Green Rectangle.png", 240, 120, 0.550, 0.550), cv(1605, 809)),
    }


LATE_OUTLINE = (168, 80, 255)    # purple, distinct from every lamp colour
LATE_FILL = (0, 0, 0)
LATE_TEXT = (255, 255, 255)


def draw_late_box(surf, item, font, ms):
    """Draw the lateness box directly over one side's lamp.

    A late hit belongs to that side and reads best exactly where the eye
    already looks for its light, so this occupies the lamp's rectangle rather
    than sitting near it. The number is the box's own figure, so the overlay
    and the scoring box never disagree.

    Sized from the image's OPAQUE area, not its surface: the lamp PNGs carry
    transparent padding, so get_size() is bigger than the rectangle anyone
    actually sees and would leave the black box overhanging the light.
    """
    img, pos = item
    rect = img.get_bounding_rect().move(pos[0], pos[1])
    pygame.draw.rect(surf, LATE_FILL, rect)
    pygame.draw.rect(surf, LATE_OUTLINE, rect, max(6, rect.height // 7))
    t = font.render(str(int(ms)), True, LATE_TEXT)
    surf.blit(t, t.get_rect(center=rect.center))


def center_in(surf, text_surf, item):
    img, pos = item
    r = text_surf.get_rect(center=(pos[0] + img.get_width() // 2,
                                   pos[1] + img.get_height() // 2))
    surf.blit(text_surf, r)


# ---------------- UI helpers ----------------
class Button:
    def __init__(self, rect, label, tag, enabled=True):
        self.rect = pygame.Rect(rect)
        self.label = label
        self.tag = tag
        self.enabled = enabled

    def draw(self, surf, font):
        bg = (40, 44, 70, 215) if self.enabled else (35, 35, 40, 180)
        fg = (235, 235, 245) if self.enabled else (110, 110, 120)
        s = pygame.Surface(self.rect.size, pygame.SRCALPHA)
        s.fill(bg)
        pygame.draw.rect(s, (90, 95, 140), s.get_rect(), 2)
        surf.blit(s, self.rect.topleft)
        t = font.render(self.label, True, fg)
        surf.blit(t, t.get_rect(center=self.rect.center))


# Overridable so the no-camera path can be exercised on a machine that has a
# camera, without fighting the running station for the v4l2 device.
CAM_DEV = os.environ.get("SKEWERED_CAM_DEV", "/dev/video0")
CAM_RETRY_SECS = 5.0     # how often to look for a camera that is not there yet


def open_camera():
    """Start the capture ffmpeg, or return None if there is no camera.

    Returning None puts the station in review mode: the live view is a
    placeholder and recording is disabled, but bout browsing, replay, name
    entry and uploads all work. That is the point -- reviewing a session
    afterwards should not require dragging the camera back out.
    """
    if not os.path.exists(CAM_DEV):
        return None
    # Capture natively at the size we actually use. Previously this grabbed
    # 1920x1080 and scaled every frame down to 1280x720 -- discarding 55% of
    # the pixels immediately after paying to decode them. Measured on this Pi:
    # 111% of a core for 1080p+scale versus 39.8% for native 720p, at the same
    # frame rate. That is ~70% of a core returned to the capture loop.
    return subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-f", "v4l2",
         "-input_format", "mjpeg",
         "-video_size", "%dx%d" % (REC_W, REC_H),
         "-framerate", str(FPS), "-i", CAM_DEV,
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, bufsize=REC_W * REC_H * 3)


def close_camera(dec):
    if not dec:
        return
    try:
        dec.kill()
        dec.wait(timeout=2)
    except Exception:
        pass


def main():
    force_record = "--force-record" in sys.argv
    deadline = None
    for i, a in enumerate(sys.argv):
        if a == "--secs":
            deadline = time.time() + float(sys.argv[i + 1])

    threading.Thread(target=udp_thread, daemon=True).start()
    con = db_init()
    rec = Recorder(con)

    pygame.init()
    screen = pygame.display.set_mode((800, 480), pygame.FULLSCREEN)
    pygame.mouse.set_visible(False)
    IM = make_overlay_assets()
    fs = round(44 * REC_W / 800.0)
    font_score = pygame.font.SysFont("dejavusans", fs, bold=True)
    font_clock = pygame.font.SysFont("dejavusansmono", round(fs * 0.9), bold=True)
    font_name = pygame.font.SysFont("dejavusans", round(fs * 0.5), bold=True)
    font_sml = pygame.font.SysFont("dejavusans", 14)
    font_ui = pygame.font.SysFont("dejavusans", 20, bold=True)
    font_row = pygame.font.SysFont("dejavusans", 22)
    font_head = pygame.font.SysFont("dejavusans", 24, bold=True)
    CLOCK_CENTER = (REC_W // 2, round(950 * REC_H / 1080.0))

    dec = open_camera()
    next_cam_try = time.time() + CAM_RETRY_SECS
    # Without a camera the blocking frame read no longer paces the loop, so it
    # has to be clocked explicitly or it spins a core flat.
    clock = pygame.time.Clock()
    FRAME = REC_W * REC_H * 3
    PFRAME = DISP_W * DISP_H * 3

    if force_record:
        last_activity[0] = time.time()

    surf = pygame.Surface((REC_W, REC_H))
    frames, t0 = 0, time.time()
    touch_t = None
    touch_pos = None       # position of the press, held for its whole duration
    hold_tick = 0          # loop iterations since auto-repeat began
    last_space_check = 0.0
    running = True

    mode = MODE_LIVE
    buttons = []
    bout_rows, bout_page = [], 0
    touch_rows, touch_page = [], 0
    cur_bout = None            # (bout_id, title)
    confirm = None             # {title, lines, choices, from} in MODE_CONFIRM
    play = None                # {"proc", "banner", "from_mode"}
    flash = None               # (text, until_ts)
    name_side = "l"            # which plate is being edited
    name_buf = ""
    name_chips = []            # quick-select names for the entry screen
    club_side = "l"            # which logo circle is being edited
    club_buf = ""              # picker filter text
    clubs_now = read_clubs()
    wifi_nets = []             # scan results for the picker
    wifi_page = 0
    wifi_target = ""           # SSID awaiting a password
    wifi_pw = ""
    wifi_shift = False
    wifi_sym = False
    wifi_scan_busy = [False]
    export_job = {"active": False, "done": 0, "total": 0, "ok": 0,
                  "fail": 0, "finished": False}

    usb_job = {"active": False, "done": 0, "total": 0, "copied": 0,
               "skipped": 0, "finished": False, "errors": [], "label": "",
               "ejected": False, "current": ""}

    # usb_target() shells out to lsblk, and the button below is rebuilt every
    # frame -- at 30 fps that is 30 process spawns a second competing with the
    # capture loop. Two seconds is far quicker than anyone plugs in a drive.
    usb_cache = {"at": 0.0, "target": None}

    def usb_present():
        if time.time() - usb_cache["at"] > 2.0:
            usb_cache["target"] = usb_target()
            usb_cache["at"] = time.time()
        return usb_cache["target"]

    def start_usb_copy():
        """Copy the saved videos onto a plugged-in drive, then unmount it."""
        if usb_job["active"]:
            return False
        target = usb_target()
        if not target:
            return False
        usb_job.update(active=True, done=0, total=0, copied=0, skipped=0,
                       finished=False, errors=[], label=target["label"],
                       ejected=False, current="")

        def worker():
            def prog(i, n, name):
                usb_job["done"], usb_job["total"] = i, n
                usb_job["current"] = name
            copied, skipped, errors = usb_copy(target, prog)
            usb_job["copied"], usb_job["skipped"] = copied, skipped
            usb_job["errors"] = errors
            # Always unmount, even if some files failed: whatever did copy is
            # only safe on the drive once the cache is flushed.
            usb_job["ejected"] = usb_eject(target)
            usb_job["active"] = False
            usb_job["finished"] = True
        threading.Thread(target=worker, daemon=True).start()
        return True

    def start_exports(bout_ids, combine=False):
        """Cut the bouts and put the result on the share.

        Two shapes: one file per bout, or every bout concatenated into a single
        file. Both end with `publish()` and an export_name written to each bout,
        which is what stops the same bout being exported twice.
        """
        if export_job["active"] or not bout_ids:
            return
        export_job.update(active=True, done=0, total=len(bout_ids), ok=0,
                          fail=0, finished=False, combine=combine,
                          ids=list(bout_ids), error="")

        def worker():
            c2 = sqlite3.connect(DBPATH, timeout=30)
            try:
                if combine:
                    path, title, desc, used, errs = build_combined(
                        c2, rec, bout_ids)
                    export_job["done"] = len(bout_ids)
                    export_job["fail"] = len(errs)
                    if not path:
                        export_job["error"] = "combine failed"
                    else:
                        name = publish(path, title, desc)
                        if name:
                            for bid in used:
                                c2.execute("UPDATE bouts SET export_name=?"
                                           " WHERE id=?", (name, bid))
                            c2.commit()
                            export_job["ok"] = 1
                        else:
                            export_job["error"] = "could not write to share"
                    # The per-bout cuts were only ever inputs to the concat.
                    for bid in bout_ids:
                        p = os.path.join(UPLOAD_DIR, "bout_%04d.mp4" % bid)
                        try:
                            os.remove(p)
                        except OSError:
                            pass
                else:
                    for bid in bout_ids:
                        path, _err = export_bout(c2, rec, bid)
                        name = publish(path, bout_upload_title(c2, bid),
                                       bout_description(c2, bid)) \
                            if path else None
                        export_job["done"] += 1
                        if name:
                            c2.execute("UPDATE bouts SET export_name=?"
                                       " WHERE id=?", (name, bid))
                            c2.commit()
                            export_job["ok"] += 1
                        else:
                            export_job["fail"] += 1
            finally:
                c2.close()
                export_job["active"] = False
                export_job["finished"] = True
        threading.Thread(target=worker, daemon=True).start()

    def start_wifi_scan():
        wifi_scan_busy[0] = True

        def worker():
            nets = wifi_scan()
            wifi_nets[:] = nets
            wifi_scan_busy[0] = False
        threading.Thread(target=worker, daemon=True).start()

    def uploadable_bouts():
        return [r[0] for r in con.execute(
            "SELECT b.id FROM bouts b WHERE b.export_name='' AND EXISTS"
            " (SELECT 1 FROM events e WHERE e.bout_id=b.id AND"
            "  e.type='touch') ORDER BY b.id")]

    def uploadable_bouts_latest_day():
        """(ids, label) for pending bouts on the most recent day that has any.

        "Today" is deliberately the latest day with pending bouts rather than
        the actual calendar today: after an evening session the Pi is often
        not touched until the next morning, and an empty selection would be
        useless. Grouping uses local dates, not a rolling 24 hours, so a
        session that ran past midnight splits -- which matches how people
        talk about which night a bout happened on.
        """
        ids = uploadable_bouts()
        if not ids:
            return [], "-"
        q = ",".join("?" * len(ids))
        rows = con.execute("SELECT id, start_ts FROM bouts WHERE id IN (%s)" % q,
                           ids).fetchall()
        days = {}
        for bid, ts in rows:
            days.setdefault(time.strftime("%Y-%m-%d", time.localtime(ts)),
                            []).append(bid)
        latest = max(days)
        return sorted(days[latest]), latest

    def wifi_kb_rows():
        if wifi_sym:
            return [list("1234567890"), list("!@#$%^&*()"),
                    list("-_=+[]{}:;"), list("'\",.<>/?\\|")]
        rows = [list("qwertyuiop"), list("asdfghjkl"), list("zxcvbnm")]
        if wifi_shift:
            rows = [[c.upper() for c in r] for r in rows]
        return [list("1234567890")] + rows

    DSCALE = DISP_W / float(REC_W)

    def plate_rect(key):
        img, pos = IM[key]
        return pygame.Rect(round(pos[0] * DSCALE), round(pos[1] * DSCALE) + YOFF,
                           round(img.get_width() * DSCALE),
                           round(img.get_height() * DSCALE))

    def load_chips(side):
        chips = [DEFAULT_NAMES[side]]
        for (n,) in con.execute("SELECT name FROM names_used "
                                "ORDER BY last_used DESC LIMIT 12"):
            if n and n not in chips:
                chips.append(n)
            if len(chips) >= 10:
                break
        return chips

    def save_name(side, raw):
        name = raw.strip()[:NAME_MAX]
        if not name:
            return False
        l, r = read_names()
        if side == "l":
            l = name
        else:
            r = name
        write_names(l, r)
        if name not in DEFAULT_NAMES.values():
            con.execute("INSERT OR REPLACE INTO names_used(name, last_used) "
                        "VALUES(?,?)", (name, time.time()))
        if rec.active and rec.bout_id:
            col = "l_name" if side == "l" else "r_name"
            con.execute("UPDATE bouts SET %s=? WHERE id=?" % col,
                        (name, rec.bout_id))
        con.commit()
        return True

    KB_ROWS = [list("QWERTYUIOP"), list("ASDFGHJKL"),
               list("ZXCVBNM") + ["'", "-", "BKSP"]]

    # ---- club logos ----
    OV_D = round(LOGO_D * SC)          # overlay diameter (720p space)
    UI_D = 84                          # picker-grid diameter (display space)
    logos = {}                         # club -> {"ov": Surface, "ui": Surface}
    if os.path.isdir(LOGODIR):
        for f in sorted(os.listdir(LOGODIR)):
            if f.endswith(".png"):
                img = pygame.image.load(os.path.join(LOGODIR, f)).convert_alpha()
                logos[f[:-4]] = {
                    "ov": pygame.transform.smoothscale(img, (OV_D, OV_D)),
                    "ui": pygame.transform.smoothscale(img, (UI_D, UI_D))}

    def make_ring(d, width, alpha):
        s = pygame.Surface((d, d), pygame.SRCALPHA)
        pygame.draw.circle(s, (225, 225, 235, alpha), (d // 2, d // 2),
                           d // 2 - 1, width)
        return s

    ring_ov = make_ring(OV_D, 3, 110)      # faint empty state on the overlay
    ring_ui = make_ring(UI_D, 3, 200)      # "None" option in the picker

    def logo_ov_pos(side):
        cx, cy = LOGO_CENTERS[side]
        return (round(cx * SC) - OV_D // 2, round(cy * SC) - OV_D // 2)

    def logo_tap_rect(side):
        cx, cy = LOGO_CENTERS[side]
        d = round(LOGO_D * SC * DSCALE) + 12
        return pygame.Rect(round(cx * SC * DSCALE) - d // 2,
                           round(cy * SC * DSCALE) + YOFF - d // 2, d, d)

    def club_for_name(name):
        row = con.execute("SELECT club FROM name_club WHERE name=?",
                          (name,)).fetchone()
        return row[0] if row else None

    def save_club(side, club, fencer_name):
        l, r = read_clubs()
        if side == "l":
            l = club
        else:
            r = club
        write_clubs(l, r)
        if fencer_name and fencer_name not in DEFAULT_NAMES.values():
            con.execute("INSERT OR REPLACE INTO name_club(name, club,"
                        " last_used) VALUES(?,?,?)",
                        (fencer_name, club, time.time()))
            con.commit()

    def apply_known_club(side, fencer_name):
        club = club_for_name(fencer_name)
        if club is not None:
            l, r = read_clubs()
            if side == "l":
                l = club
            else:
                r = club
            write_clubs(l, r)

    def club_options(side, filt):
        """Ordered picker options: suggestion, None, pinned, then the rest."""
        out = []
        sug = club_for_name(nm_now[0 if side == "l" else 1])
        if sug and sug in logos:
            out.append(sug)
        out.append("")                          # the empty / no-club option
        for p in PINNED_CLUBS:
            if p in logos and p not in out:
                out.append(p)
        rest = [c for c in sorted(logos) if c not in out]
        if filt:
            rest = [c for c in rest if filt.lower() in c.lower()]
        return out + rest

    def flush_pending_touch():
        """Trigger 3: settle a touch left hanging when the session ends.

        Without this the last touch of every session keeps its placeholder --
        practice finishes, the box is switched off, and neither of the other
        two triggers ever fires. Must run while the recorder is still active,
        since that is what writes events.
        """
        with _lock:
            ts = pending_touch[0]
            pending_touch[0] = None
            lf, rf = state["l"], state["r"]
        if ts is not None and rec.active:
            rec.log_event({"type": "touch_score", "ts": ts, "l0": 0, "r0": 0,
                           "l1": lf, "r1": rf, "detail": ""})

    def delete_bout(bout_id):
        """Remove a bout and its touches from the database.

        Deliberately does NOT delete the recording segments. Segments are
        time-based files shared by every bout in the same session, so removing
        them would blank out the neighbouring bouts too. Disk is already
        managed by Recorder.ensure_space(), which trims the oldest segments
        when space runs low.

        The per-bout export in UPLOAD_DIR is ours alone, so that does go.
        """
        row = con.execute("SELECT upload_path FROM bouts WHERE id=?",
                          (bout_id,)).fetchone()
        con.execute("DELETE FROM events WHERE bout_id=?", (bout_id,))
        con.execute("DELETE FROM bouts WHERE id=?", (bout_id,))
        con.commit()
        for p in (row[0] if row else "",
                  os.path.join(UPLOAD_DIR, "bout_%04d.mp4" % bout_id)):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def wipe_stats():
        """(bouts, touches, not_yet_uploaded, video_bytes) for the confirm text."""
        nb = con.execute("SELECT COUNT(*) FROM bouts").fetchone()[0]
        nt = con.execute("SELECT COUNT(*) FROM events WHERE type='touch'").fetchone()[0]
        nu = con.execute("SELECT COUNT(*) FROM bouts b WHERE b.export_name='' AND"
                         " EXISTS (SELECT 1 FROM events e WHERE e.bout_id=b.id"
                         " AND e.type='touch')").fetchone()[0]
        vb = 0
        for d in (RECDIR, UPLOAD_DIR):
            try:
                for f in os.listdir(d):
                    if f.endswith(".mp4"):
                        vb += os.path.getsize(os.path.join(d, f))
            except OSError:
                pass
        return nb, nt, nu, vb

    def delete_all(with_video):
        """Clear the bout history. With with_video, reclaim the footage too.

        Single-bout delete deliberately spares the recordings because segments
        are shared between bouts in a session. That reasoning does not apply
        here: if every bout goes, no segment is referenced by anything, so the
        footage can be reclaimed. Kept either way: fencer names, club logos and
        the share settings -- this clears recordings, not configuration.
        """
        con.execute("DELETE FROM events")
        con.execute("DELETE FROM bouts")
        if with_video:
            con.execute("DELETE FROM state_log")
            con.execute("DELETE FROM files")
            con.execute("DELETE FROM sessions")
        con.commit()
        dirs = [UPLOAD_DIR] + ([RECDIR] if with_video else [])
        for d in dirs:
            try:
                for f in os.listdir(d):
                    if f.endswith(".mp4") or f.endswith(".csv"):
                        try:
                            os.remove(os.path.join(d, f))
                        except OSError:
                            pass
            except OSError:
                pass

    def load_bouts():
        rows = con.execute("""
            SELECT b.id, b.session_id, b.start_ts, b.l_name, b.r_name,
                   (SELECT COUNT(*) FROM events e
                     WHERE e.bout_id=b.id AND e.type='touch') AS touches,
                   (SELECT e.l_after FROM events e WHERE e.bout_id=b.id
                     AND e.type='touch' ORDER BY e.id DESC LIMIT 1),
                   (SELECT e.r_after FROM events e WHERE e.bout_id=b.id
                     AND e.type='touch' ORDER BY e.id DESC LIMIT 1),
                   (SELECT MIN(b2.id) FROM bouts b2
                     WHERE b2.session_id=b.session_id),
                   (SELECT COUNT(*) FROM bouts b3
                     WHERE b3.session_id=b.session_id),
                   b.export_name
            FROM bouts b ORDER BY b.id DESC""").fetchall()
        out = []
        for (bid, sid, ts, ln, rn, n, lf, rf, first_id, nbouts, exported) in rows:
            when = time.strftime("%b %d %H:%M", time.localtime(ts))
            names = "%s vs %s" % (ln or "LEFT", rn or "RIGHT")
            if bid == first_id and nbouts > 1:
                names += "  (warm-up)"
            score = "%d - %d" % (lf, rf) if lf is not None else "-"
            mark = "   [saved]" if exported else ""
            out.append({"bout_id": bid, "title": names,
                        "sub": "%s   %d touches   final %s%s"
                               % (when, n, score, mark)})
        return out

    def load_touches(bout_id):
        rows = con.execute("""
            SELECT id, session_id, session_offset, ts, l_after, r_after, detail
            FROM events WHERE bout_id=? AND type='touch' ORDER BY id""",
            (bout_id,)).fetchall()
        out = []
        for i, (eid, sid, off, ts, l, r, detail) in enumerate(rows):
            src, _ = clip_source(con, rec, sid, off)
            out.append({"event_id": eid, "session_id": sid, "offset": off,
                        "available": src is not None,
                        "label": "#%-3d  %d - %d" % (i + 1, l, r),
                        "sub": "%s   %s" % (
                            time.strftime("%H:%M:%S", time.localtime(ts)),
                            detail.replace("_", " "))})
        return out

    def start_playback(session_id, offset, banner, from_mode):
        src, local = clip_source(con, rec, session_id, offset)
        if src is None:
            return None
        cmd = ["ffmpeg", "-loglevel", "error", "-ss", "%.2f" % local,
               "-i", src, "-t", "%.1f" % (CLIP_PRE + CLIP_POST),
               "-vf", "scale=%d:%d" % (DISP_W, DISP_H),
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=PFRAME)
        return {"proc": proc, "cmd": cmd, "banner": banner,
                "from_mode": from_mode, "last": None,
                # slow-motion state; frames is None until slow-mo is entered
                "speed": None, "paused": False, "frames": None,
                "idx": 0, "tick": 0, "menu": False, "pending": None}

    def load_clip_frames(p):
        """Decode the whole clip into memory for slow-motion playback.

        Normal replay streams from ffmpeg one frame per loop, which cannot be
        rewound -- so stepping backwards needs the frames held. A clip is 6 s
        at 800x450, about 194 MB, against 7 GB free on this Pi. Paid only when
        slow motion is entered, so ordinary replay is untouched.
        """
        try:
            proc = subprocess.Popen(p["cmd"], stdout=subprocess.PIPE,
                                    bufsize=PFRAME)
        except OSError:
            return None
        out = []
        while True:
            buf = proc.stdout.read(PFRAME)
            if len(buf) < PFRAME:
                break
            out.append(pygame.image.frombuffer(buf, (DISP_W, DISP_H), "RGB"))
        try:
            proc.kill()
        except Exception:
            pass
        return out or None

    def stop_playback(p):
        if p and p.get("proc"):
            try:
                p["proc"].kill()
            except Exception:
                pass
        if p:
            p["frames"] = None      # release the slow-motion buffer

    def last_touch():
        return con.execute(
            "SELECT session_id, session_offset FROM events WHERE type='touch'"
            " ORDER BY id DESC LIMIT 1").fetchone()

    while running:
        if deadline and time.time() > deadline:
            break

        # ---------- input ----------
        tapped = None
        for e in pygame.event.get():
            if e.type == pygame.QUIT or \
               (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                running = False
            elif e.type == pygame.MOUSEBUTTONDOWN:
                touch_t = time.time()
                touch_pos = e.pos       # kept for the whole press, not just the tap
                tapped = e.pos
            elif e.type == pygame.MOUSEBUTTONUP:
                touch_t = None
                touch_pos = None
                hold_tick = 0
        if mode == MODE_LIVE and touch_t and time.time() - touch_t > 2.0:
            running = False

        action = None
        if tapped:
            for b in buttons:
                if b.enabled and b.rect.collidepoint(tapped):
                    action = b.tag
                    break
            # Playback keeps "tap anywhere to close", but only where no
            # control was hit -- otherwise the slow-motion bar is unusable.
            if action is None and mode == MODE_PLAYBACK:
                action = ("dismiss",)

        # Hold a frame arrow to scrub. The panel reports a sustained press --
        # the 2 s hold-to-exit on the live view already depends on it. Repeat
        # only after STEP_HOLD_S so an ordinary tap is exactly one frame.
        if (action is None and touch_t and touch_pos and mode == MODE_PLAYBACK
                and time.time() - touch_t > STEP_HOLD_S):
            hold_tick += 1
            if hold_tick % STEP_REPEAT_EVERY == 0:
                for b in buttons:
                    if (b.enabled and b.tag and b.tag[0] == "slow_step"
                            and b.rect.collidepoint(touch_pos)):
                        action = b.tag
                        break

        # ---------- capture + overlay + record (every mode) ----------
        # A short read means the capture died (camera unplugged, or it was
        # never there). Previously that broke the loop and the whole station
        # exited -- a camera glitch mid-tournament killed the app for the rest
        # of the day, since the XDG autostart only runs once at login. Now it
        # drops to review mode and keeps retrying.
        buf = dec.stdout.read(FRAME) if dec else b""
        if dec and len(buf) < FRAME:
            close_camera(dec)
            dec = None
            next_cam_try = time.time() + CAM_RETRY_SECS

        if dec:
            img = pygame.image.frombuffer(buf, (REC_W, REC_H), "RGB")
            surf.blit(img, (0, 0))
        else:
            # No camera: placeholder frame, and pace the loop ourselves.
            surf.fill((16, 16, 20))
            clock.tick(FPS)
            if time.time() >= next_cam_try:
                dec = open_camera()
                next_cam_try = time.time() + CAM_RETRY_SECS

        with _lock:
            st = dict(state)
            st["flags"] = list(state["flags"])
            events = pending_events[:]
            del pending_events[:]
            slog = state_changes[:]
            del state_changes[:]
        now = time.time()
        # Recording requires a camera. Without this gate, box activity while
        # the camera is detached would record a blank screen and write
        # sessions, bouts and touch events into the database pointing at video
        # that shows nothing -- polluting the bout browser and eating disk.
        active = (now - last_activity[0] < IDLE_STOP_SECS and last_activity[0] > 0
                  and dec is not None)
        if active and not rec.active:
            rec.start()
        elif not active and rec.active:
            flush_pending_touch()      # settle before the session closes
            rec.stop()

        stale = st["age"] is None or now - st["age"] > 3
        fl = st["flags"]
        nm_now = read_names() if frames % 90 == 0 or frames == 0 else nm_now
        clubs_now = read_clubs() if frames % 90 == 0 or frames == 0 else clubs_now
        for k in ("plate_l", "plate_r", "nplate_l", "nplate_r"):
            surf.blit(*IM[k])
        if fl[1]: surf.blit(*IM["red"])
        if fl[0]: surf.blit(*IM["green"])
        if fl[3]: surf.blit(*IM["white_r"])
        if fl[2]: surf.blit(*IM["white_g"])
        # A late hit lights nothing above -- its status is neither valid nor
        # non-valid -- so this box occupies an otherwise empty slot rather than
        # competing with a lamp. Suppressed when the box itself is configured
        # to hide extra hit timing; the protocol defines that flag so repeater
        # displays follow the box's setting, and this overlay is one.
        if not st["hide_extra"]:
            for i, key in ((0, "red"), (1, "green")):
                if st["status"][i] == ST_LATE and st["hit_age"]:
                    draw_late_box(surf, IM[key], font_name,
                                  st["hit_age"][i] + LATE_DISPLAY_OFFSET)
        if fl[7]: surf.blit(*IM["ycard_r"])
        if fl[6]: surf.blit(*IM["ycard_g"])
        if fl[9]: surf.blit(*IM["rcard_r"])
        if fl[8]: surf.blit(*IM["rcard_g"])
        if fl[10]: surf.blit(*IM["prio_l"])
        if fl[11]: surf.blit(*IM["prio_r"])
        center_in(surf, font_score.render(str(st["l"]), True, (255, 255, 255)), IM["plate_l"])
        center_in(surf, font_score.render(str(st["r"]), True, (255, 255, 255)), IM["plate_r"])
        center_in(surf, font_name.render(nm_now[0], True, (255, 255, 255)), IM["nplate_l"])
        center_in(surf, font_name.render(nm_now[1], True, (255, 255, 255)), IM["nplate_r"])
        for side, club in (("l", clubs_now[0]), ("r", clubs_now[1])):
            if club and club in logos:
                surf.blit(logos[club]["ov"], logo_ov_pos(side))
            else:
                surf.blit(ring_ov, logo_ov_pos(side))
        clk = font_clock.render("%d:%02d" % (st["min"], st["sec"]), True,
                                (150, 150, 160) if stale else (255, 255, 255))
        surf.blit(clk, clk.get_rect(center=CLOCK_CENTER))

        new_touch = any(ev["type"] == "touch" for ev in events)
        if rec.active:
            rec.write(pygame.image.tobytes(surf, "RGB"))
            for ev in events:
                rec.log_event(ev)
            for row in slog:
                con.execute("INSERT INTO state_log VALUES(?,?,?,?,?,?,?)",
                            (row[0], rec.session_id) + row[1:])
            if now - last_space_check > 60:
                last_space_check = now
                rec.ensure_space()
                con.execute("UPDATE sessions SET end_ts=? WHERE id=?",
                            (now, rec.session_id))
                con.commit()

        # ---------- mode transitions from taps ----------
        if action:
            if action == ("dismiss",):
                stop_playback(play)
                mode, play = play["from_mode"], None
            elif action[0] == "goto_bouts":
                bout_rows, bout_page = load_bouts(), 0
                mode = MODE_BOUTS
            elif action[0] == "replay_last":
                lt = last_touch()
                p = start_playback(lt[0], lt[1], "REPLAY - LAST TOUCH",
                                   MODE_LIVE) if lt else None
                if p:
                    mode, play = MODE_PLAYBACK, p
                else:
                    flash = ("No replay available", now + 2)
            elif action[0] == "back_live":
                mode = MODE_LIVE
            elif action[0] == "back_bouts":
                mode = MODE_BOUTS
            elif action[0] == "page":
                if mode == MODE_BOUTS:
                    bout_page = max(0, min(bout_page + action[1],
                                           (len(bout_rows) - 1) // ROWS_PER_PAGE))
                else:
                    touch_page = max(0, min(touch_page + action[1],
                                            (len(touch_rows) - 1) // ROWS_PER_PAGE))
            elif action[0] == "slow_menu":
                play["menu"] = bool(action[1])
            elif action[0] == "slow_set":
                # Decode on the NEXT iteration so "Loading slow motion..."
                # actually reaches the screen first -- the decode blocks the
                # loop for a second or so and would otherwise look like a hang.
                play["pending"] = action[1]
                play["menu"] = False
            elif action[0] == "slow_normal":
                play["frames"] = None
                play["speed"] = None
                play["paused"] = False
                play["menu"] = False
                try:
                    play["proc"].kill()
                except Exception:
                    pass
                play["proc"] = subprocess.Popen(play["cmd"],
                                                stdout=subprocess.PIPE,
                                                bufsize=PFRAME)
            elif action[0] == "slow_toggle":
                play["paused"] = not play["paused"]
            elif action[0] == "slow_step":
                play["paused"] = True
                if play["frames"]:
                    play["idx"] = ((play["idx"] + action[1])
                                   % len(play["frames"]))
            elif action[0] == "ask_delete_all":
                nb, nt, nu, vb = wipe_stats()
                if not nb:
                    flash = ("No bouts to delete", now + 2)
                else:
                    lines = ["%d bouts, %d touches" % (nb, nt)]
                    # The one fact that prevents the expensive mistake.
                    if nu:
                        lines.append("%d NOT YET SAVED to the share" % nu)
                    else:
                        lines.append("all saved to the share")
                    lines.append("Video on disk: %.1f GB" % (vb / 1e9))
                    confirm = {
                        "title": "Delete ALL bouts?",
                        "lines": lines,
                        "choices": [("BOUTS", ("do_delete_all", 0)),
                                    ("+ VIDEO", ("do_delete_all", 1))],
                        "from": MODE_BOUTS}
                    mode = MODE_CONFIRM
            elif action[0] == "usb_copy":
                n, _b = exports_on_share()
                if not n:
                    flash = ("Nothing saved yet - use SAVE first", now + 3)
                elif not usb_target():   # live check, not the cache
                    flash = ("No USB drive found - plug one in", now + 3)
                elif start_usb_copy():
                    flash = ("Copying to USB...", now + 4)
            elif action[0] == "ask_clear_exports":
                n, b = exports_on_share()
                if not n:
                    flash = ("Share is already empty", now + 2)
                else:
                    confirm = {
                        "title": "Clear the share?",
                        "lines": ["%d file(s), %.1f GB" % (n, b / 1e9),
                                  "Copy them to a computer FIRST -",
                                  "this cannot be undone."],
                        "choices": [("CLEAR", ("do_clear_exports",))],
                        "from": MODE_BOUTS}
                    mode = MODE_CONFIRM
            elif action[0] == "do_clear_exports":
                confirm = None
                n = clear_exports()
                bout_rows, bout_page = load_bouts(), 0
                flash = ("Cleared %d file(s) from the share" % n, now + 4)
                mode = MODE_BOUTS
            elif action[0] == "do_delete_all":
                confirm = None
                with_video = bool(action[1])
                delete_all(with_video)
                bout_rows, bout_page = load_bouts(), 0
                touch_rows, cur_bout = [], None
                mode = MODE_BOUTS
                flash = ("All bouts deleted" + (" and video reclaimed"
                                                if with_video else ""), now + 3)
            elif action[0] == "ask_delete_bout":
                n_touch = con.execute(
                    "SELECT COUNT(*) FROM events WHERE bout_id=? AND type='touch'",
                    (action[1],)).fetchone()[0]
                confirm = {
                    "title": "Delete this bout?",
                    "lines": [cur_bout["title"],
                              "%d touches will be removed." % n_touch,
                              "Video segments are kept (shared with other bouts)."],
                    "choices": [("DELETE", ("do_delete_bout", action[1]))],
                    "from": MODE_TOUCHES}
                mode = MODE_CONFIRM
            elif action[0] == "confirm_no":
                mode = confirm["from"] if confirm else MODE_BOUTS
                confirm = None
            elif action[0] == "do_delete_bout":
                confirm = None
                delete_bout(action[1])
                bout_rows, bout_page = load_bouts(), 0
                cur_bout = None
                mode = MODE_BOUTS
                flash = ("Bout deleted", now + 2)
            elif action[0] == "open_bout":
                cur_bout = action[1]
                touch_rows, touch_page = load_touches(cur_bout["bout_id"]), 0
                mode = MODE_TOUCHES
            elif action[0] == "play_touch":
                t = action[1]
                p = start_playback(t["session_id"], t["offset"],
                                   "REPLAY  " + t["label"].strip(), MODE_TOUCHES)
                if p:
                    mode, play = MODE_PLAYBACK, p
                else:
                    flash = ("Clip no longer on disk", now + 2)
            elif action[0] == "edit_name":
                name_side = action[1]
                name_buf = ""
                name_chips = load_chips(name_side)
                mode = MODE_NAME
            elif action[0] == "key":
                if mode == MODE_CLUB:
                    club_buf += action[1]
                elif len(name_buf) < NAME_MAX:
                    name_buf += action[1]
            elif action[0] == "bksp":
                if mode == MODE_CLUB:
                    club_buf = club_buf[:-1]
                elif mode == MODE_WIFIPW:
                    wifi_pw = wifi_pw[:-1]
                else:
                    name_buf = name_buf[:-1]
            elif action[0] == "clear":
                if mode == MODE_CLUB:
                    club_buf = ""
                elif mode == MODE_WIFIPW:
                    wifi_pw = ""
                else:
                    name_buf = ""
            elif action[0] == "pick_name":
                save_name(name_side, action[1])
                apply_known_club(name_side, action[1].strip()[:NAME_MAX])
                nm_now = read_names()
                clubs_now = read_clubs()
                mode = MODE_LIVE
            elif action[0] == "name_ok":
                if save_name(name_side, name_buf):
                    apply_known_club(name_side, name_buf.strip()[:NAME_MAX])
                    nm_now = read_names()
                    clubs_now = read_clubs()
                    mode = MODE_LIVE
                else:
                    flash = ("Name is empty", now + 2)
            elif action[0] == "name_cancel":
                mode = MODE_LIVE
            elif action[0] == "edit_club":
                club_side = action[1]
                club_buf = ""
                mode = MODE_CLUB
            elif action[0] == "upload_all":
                ids = uploadable_bouts()
                if not ids:
                    flash = ("Nothing new to upload", now + 2)
                else:
                    day_ids, day_label = uploadable_bouts_latest_day()
                    ch = [("ALL  (%d)" % len(ids), ("do_upload_all", "all"))]
                    # Only offer the day option when it is a genuine subset.
                    if day_ids and len(day_ids) < len(ids):
                        ch.append(("%s  (%d)" % (day_label, len(day_ids)),
                                   ("do_upload_all", "day")))
                    confirm = {
                        "title": "Combine and upload which bouts?",
                        "lines": ["They become ONE video with chapters,",
                                  "so the whole backlog costs one upload.",
                                  "Most recent day: %s" % day_label],
                        "choices": ch, "danger": False, "from": MODE_BOUTS}
                    mode = MODE_CONFIRM
            elif action[0] == "do_upload_all":
                confirm = None
                mode = MODE_BOUTS
                ids = (uploadable_bouts() if action[1] == "all"
                       else uploadable_bouts_latest_day()[0])
                if ids:
                    start_exports(ids, combine=True)
                    flash = ("Saving %d bout(s) as one video..." % len(ids),
                             now + 3)
                else:
                    flash = ("Nothing new to save", now + 2)
            elif action[0] == "upload_one":
                bid = action[1]
                already = con.execute("SELECT export_name FROM bouts WHERE"
                                      " id=?", (bid,)).fetchone()
                if already and already[0]:
                    flash = ("Already saved: %s" % already[0][:34], now + 3)
                else:
                    start_exports([bid])
                    flash = ("Saving bout to the share...", now + 3)
            elif action[0] == "wifi_open":
                wifi_page = 0
                start_wifi_scan()
                mode = MODE_WIFI
            elif action[0] == "wifi_rescan":
                if not wifi_scan_busy[0]:
                    start_wifi_scan()
            elif action[0] == "wifi_page":
                wifi_page = max(0, min(wifi_page + action[1],
                                       max(0, (len(wifi_nets) - 1)
                                           // ROWS_PER_PAGE)))
            elif action[0] == "wifi_pick":
                ssid, secured = action[1]
                if secured:
                    wifi_target, wifi_pw = ssid, ""
                    wifi_shift = wifi_sym = False
                    mode = MODE_WIFIPW
                else:
                    flash = ("Connecting to %s..." % ssid, now + 30)
                    ok = wifi_connect(ssid, None)
                    flash = (("Connected to %s" % ssid) if ok else
                             "Connection failed", now + 3)
                    mode = MODE_BOUTS
            elif action[0] == "wifi_ok":
                flash = ("Connecting to %s..." % wifi_target, now + 45)
                ok = wifi_connect(wifi_target, wifi_pw)
                if ok:
                    flash = ("Connected to %s" % wifi_target, now + 4)
                else:
                    flash = ("Wrong password or connection failed", now + 4)
                mode = MODE_BOUTS if ok else MODE_WIFI
            elif action[0] == "wifi_box":
                reconnect_box()
                flash = ("Reconnecting to box wifi...", now + 3)
                mode = MODE_LIVE
            elif action[0] == "wifi_cancel":
                mode = MODE_WIFI if mode == MODE_WIFIPW else MODE_BOUTS
            elif action[0] == "wkey":
                if len(wifi_pw) < 63:
                    wifi_pw += action[1]
            elif action[0] == "wshift":
                wifi_shift = not wifi_shift
            elif action[0] == "wsym":
                wifi_sym = not wifi_sym
            elif action[0] == "pick_club":
                fencer = nm_now[0 if club_side == "l" else 1]
                save_club(club_side, action[1], fencer)
                clubs_now = read_clubs()
                mode = MODE_LIVE

        # a real touch at the strip interrupts any replay
        # Deferred slow-motion load: the "Loading" frame has now been shown.
        if mode == MODE_PLAYBACK and play and play["pending"]:
            div, play["pending"] = play["pending"], None
            fr = load_clip_frames(play)
            if fr:
                play["frames"], play["speed"] = fr, div
                play["idx"], play["tick"], play["paused"] = 0, 0, False
                try:
                    play["proc"].kill()   # streaming copy no longer needed
                except Exception:
                    pass
            else:
                flash = ("Could not load slow motion", now + 2)

        if mode == MODE_PLAYBACK and new_touch:
            stop_playback(play)
            mode, play = MODE_LIVE, None

        # export batch finished -> say what landed on the share
        if export_job["finished"]:
            export_job["finished"] = False
            msg = "Saved %d video(s)" % export_job["ok"]
            if export_job["fail"]:
                msg += ", %d failed" % export_job["fail"]
            if export_job.get("error"):
                msg += " - " + export_job["error"]
            if export_job["ok"]:
                t = usb_target()
                msg += ("  -  tap USB to copy to %s" % t["label"] if t
                        else "  -  plug in a USB drive to copy them off")
            flash = (msg, now + 8)

        # usb copy finished -> report, and say whether it is safe to pull out
        if usb_job["finished"]:
            usb_job["finished"] = False
            m = "Copied %d file(s) to %s" % (usb_job["copied"], usb_job["label"])
            if usb_job["skipped"]:
                m += ", %d already there" % usb_job["skipped"]
            if usb_job["errors"]:
                m += " - %d failed (%s)" % (len(usb_job["errors"]),
                                            usb_job["errors"][0][1])
            m += ".  SAFE TO REMOVE" if usb_job["ejected"] else                  ".  Wait before removing - could not eject"
            flash = (m, now + 12)

        # ---------- draw current mode ----------
        screen.fill((0, 0, 0))
        buttons = []

        if mode == MODE_LIVE:
            disp = pygame.transform.smoothscale(surf, (DISP_W, DISP_H))
            screen.blit(disp, (0, YOFF))
            # Say so loudly. The old behaviour was to exit outright, which was
            # at least obvious; sitting in review mode is quieter, and nobody
            # should discover at the end of a session that nothing recorded.
            if dec is None:
                s = font_head.render("NO CAMERA - REVIEW MODE", True, (255, 190, 60))
                screen.blit(s, s.get_rect(center=(DISP_W // 2, YOFF + DISP_H // 2 - 16)))
                s2 = font_sml.render("Recording disabled. Bouts, replay and upload still work.",
                                     True, (200, 200, 210))
                screen.blit(s2, s2.get_rect(center=(DISP_W // 2, YOFF + DISP_H // 2 + 18)))
            buttons.append(Button((548, 2, 116, 40), "REPLAY", ("replay_last",)))
            buttons.append(Button((676, 2, 116, 40), "BOUTS", ("goto_bouts",)))
            # invisible tap zones over the nameplates -> name entry
            buttons.append(Button(plate_rect("nplate_l"), "", ("edit_name", "l")))
            buttons.append(Button(plate_rect("nplate_r"), "", ("edit_name", "r")))
            # tap zones over the club logo circles -> logo picker
            buttons.append(Button(logo_tap_rect("l"), "", ("edit_club", "l")))
            buttons.append(Button(logo_tap_rect("r"), "", ("edit_club", "r")))
            if rec.active:
                pygame.draw.circle(screen, (255, 40, 40), (14, 22), 7)
                screen.blit(font_sml.render(
                    "REC %d:%02d" % (rec.offset() // 60, rec.offset() % 60),
                    True, (255, 80, 80)), (26, 14))
            if stale:
                s = font_sml.render("NO BOX SIGNAL", True, (255, 80, 80))
                screen.blit(s, s.get_rect(center=(400, 8)))

        elif mode in (MODE_BOUTS, MODE_TOUCHES):
            rows = bout_rows if mode == MODE_BOUTS else touch_rows
            page = bout_page if mode == MODE_BOUTS else touch_page
            title = "Bouts" if mode == MODE_BOUTS else cur_bout["title"]
            back = ("back_live",) if mode == MODE_BOUTS else ("back_bouts",)
            screen.blit(font_head.render(title, True, (235, 235, 245)), (108, 10))
            buttons.append(Button((4, 4, 96, 40), "BACK", back))
            if usb_job["active"]:
                screen.blit(font_sml.render(
                    "Copying to USB %d/%d..." % (usb_job["done"] + 1,
                                                 max(1, usb_job["total"])),
                    True, (140, 235, 150)), (520, 18))
            elif export_job["active"]:
                screen.blit(font_sml.render(
                    "Cutting %d/%d..." % (export_job["done"],
                                          export_job["total"]),
                    True, (255, 210, 120)), (560, 18))
            elif mode == MODE_BOUTS:
                # Disabled while recording: wiping the footage would pull files
                # out from under the running encoder.
                # USB is the way videos leave this box, so it sits next to
                # SAVE and is greyed out until a drive is actually mounted --
                # that greying is the whole instruction for a new user.
                usb_now = usb_present()
                buttons.append(Button((244, 4, 108, 40), "DEL ALL",
                                      ("ask_delete_all",),
                                      enabled=not rec.active))
                buttons.append(Button((358, 4, 70, 40), "WIFI",
                                      ("wifi_open",)))
                buttons.append(Button((434, 4, 84, 40), "CLEAR",
                                      ("ask_clear_exports",)))
                buttons.append(Button((524, 4, 76, 40), "USB",
                                      ("usb_copy",),
                                      enabled=bool(usb_now) and
                                      not usb_job["active"]))
                buttons.append(Button((606, 4, 126, 40), "SAVE ALL",
                                      ("upload_all",)))
            else:
                # Left of SAVE, same slot the WIFI button uses on the bouts
                # screen. Both live in this branch, so neither is offered while
                # an export is running.
                buttons.append(Button((430, 4, 104, 40), "DELETE",
                                      ("ask_delete_bout", cur_bout["bout_id"])))
                buttons.append(Button((560, 4, 172, 40), "SAVE",
                                      ("upload_one", cur_bout["bout_id"])))
            y = 52
            for r in rows[page * ROWS_PER_PAGE:(page + 1) * ROWS_PER_PAGE]:
                avail = r.get("available", True)
                tag = ("open_bout", r) if mode == MODE_BOUTS else ("play_touch", r)
                b = Button((4, y, 728, 60), "", tag, enabled=avail)
                b.draw(screen, font_row)
                main_c = (235, 235, 245) if avail else (110, 110, 120)
                sub_c = (160, 165, 190) if avail else (90, 90, 100)
                label = r.get("title", r.get("label", ""))
                screen.blit(font_row.render(label, True, main_c), (16, y + 6))
                screen.blit(font_sml.render(r["sub"], True, sub_c), (16, y + 36))
                buttons.append(b)
                y += 64
            if not rows:
                screen.blit(font_row.render("Nothing here yet", True,
                                            (150, 150, 160)), (16, 60))
            npages = max(1, (len(rows) + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)
            if npages > 1:
                buttons.append(Button((740, 52, 56, 180), "UP", ("page", -1),
                                      enabled=page > 0))
                buttons.append(Button((740, 244, 56, 180), "DN", ("page", 1),
                                      enabled=page < npages - 1))
                screen.blit(font_sml.render("%d/%d" % (page + 1, npages), True,
                                            (150, 150, 160)), (744, 436))

        elif mode == MODE_CONFIRM:
            c = confirm or {"title": "", "lines": [], "choices": []}
            danger = c.get("danger", True)
            bg = (40, 20, 20) if danger else (20, 30, 42)
            edge = (200, 70, 70) if danger else (90, 150, 210)
            pygame.draw.rect(screen, bg, (60, 90, 680, 250))
            pygame.draw.rect(screen, edge, (60, 90, 680, 250), 3)
            t = font_head.render(c["title"], True,
                                 (255, 210, 210) if danger else (215, 230, 250))
            screen.blit(t, t.get_rect(center=(400, 130)))
            ly = 180
            for line in c["lines"]:
                s = font_row.render(line, True, (235, 235, 245))
                screen.blit(s, s.get_rect(center=(400, ly)))
                ly += 30
            # CANCEL always sits leftmost: the safe choice should be the easy
            # one to hit on a small touchscreen. Remaining choices share the
            # rest of the row.
            ch = c["choices"]
            buttons.append(Button((92, 272, 180, 52), "CANCEL", ("confirm_no",)))
            if ch:
                x, w = 288, (420 - 8 * (len(ch) - 1)) // len(ch)
                for label, act in ch:
                    buttons.append(Button((x, 272, w, 52), label, act))
                    x += w + 8

        elif mode == MODE_NAME:
            side_label = "LEFT" if name_side == "l" else "RIGHT"
            screen.blit(font_head.render("%s fencer name:" % side_label, True,
                                         (235, 235, 245)), (8, 10))
            field = name_buf + ("_" if (frames // 15) % 2 == 0 else " ")
            screen.blit(font_head.render(field, True, (255, 255, 160)), (280, 10))
            buttons.append(Button((690, 4, 106, 40), "CANCEL", ("name_cancel",)))
            # quick-select chips: side default first, then recent names
            for i, chip in enumerate(name_chips[:10]):
                cx, cy = 4 + (i % 5) * 159, 56 + (i // 5) * 48
                b = Button((cx, cy, 155, 44), "", ("pick_name", chip))
                b.draw(screen, font_sml)
                t = font_sml.render(chip[:14], True, (200, 220, 255))
                screen.blit(t, t.get_rect(center=b.rect.center))
                buttons.append(b)
            # keyboard
            for ri, row in enumerate(KB_ROWS):
                ky = 160 + ri * 78
                kx0 = (800 - len(row) * 80) // 2
                for ci, key in enumerate(row):
                    kx = kx0 + ci * 80
                    if key == "BKSP":
                        buttons.append(Button((kx, ky, 76, 72), "<-", ("bksp",)))
                    else:
                        buttons.append(Button((kx, ky, 76, 72), key,
                                              ("key", key)))
            buttons.append(Button((8, 394, 150, 78), "CLEAR", ("clear",)))
            buttons.append(Button((166, 394, 420, 78), "SPACE", ("key", " ")))
            buttons.append(Button((594, 394, 198, 78), "OK", ("name_ok",)))

        elif mode == MODE_CLUB:
            side_label = "LEFT" if club_side == "l" else "RIGHT"
            screen.blit(font_head.render("%s club:" % side_label, True,
                                         (235, 235, 245)), (8, 8))
            filt = club_buf + ("_" if (frames // 15) % 2 == 0 else " ")
            screen.blit(font_head.render(filt, True, (255, 255, 160)), (200, 8))
            buttons.append(Button((690, 4, 106, 38), "CANCEL", ("name_cancel",)))
            options = club_options(club_side, club_buf)[:12]
            for i, club in enumerate(options):
                cx = 6 + (i % 6) * 132
                cy = 46 + (i // 6) * 112
                b = Button((cx, cy, 128, 104), "", ("pick_club", club))
                if club and club in logos:
                    screen.blit(logos[club]["ui"], (cx + 22, cy))
                    label = club
                else:
                    screen.blit(ring_ui, (cx + 22, cy))
                    label = "None"
                t = font_sml.render(label[:17], True, (210, 215, 235))
                screen.blit(t, t.get_rect(center=(cx + 64, cy + 94)))
                buttons.append(b)
            for ri, row in enumerate(KB_ROWS):
                ky = 274 + ri * 50
                kx0 = (800 - len(row) * 80) // 2
                for ci, key in enumerate(row):
                    kx = kx0 + ci * 80
                    if key == "BKSP":
                        buttons.append(Button((kx, ky, 76, 46), "<-", ("bksp",)))
                    else:
                        buttons.append(Button((kx, ky, 76, 46), key,
                                              ("key", key)))
            buttons.append(Button((8, 426, 190, 48), "CLEAR", ("clear",)))
            buttons.append(Button((206, 426, 586, 48), "SPACE", ("key", " ")))

        elif mode == MODE_WIFI:
            screen.blit(font_head.render("Wi-Fi networks", True,
                                         (235, 235, 245)), (108, 10))
            buttons.append(Button((4, 4, 96, 40), "BACK", ("wifi_cancel",)))
            buttons.append(Button((470, 4, 110, 40), "RESCAN",
                                  ("wifi_rescan",)))
            buttons.append(Button((590, 4, 202, 40), "BOX WIFI",
                                  ("wifi_box",)))
            if wifi_scan_busy[0]:
                screen.blit(font_row.render("Scanning...", True,
                                            (150, 150, 160)), (16, 70))
            else:
                y = 52
                for (ssid, sig, secured) in wifi_nets[
                        wifi_page * ROWS_PER_PAGE:
                        (wifi_page + 1) * ROWS_PER_PAGE]:
                    b = Button((4, y, 728, 60), "",
                               ("wifi_pick", (ssid, secured)))
                    b.draw(screen, font_row)
                    screen.blit(font_row.render(ssid[:34], True,
                                                (235, 235, 245)), (16, y + 6))
                    screen.blit(font_sml.render(
                        "signal %d   %s" % (sig, "secured" if secured
                                            else "open"),
                        True, (160, 165, 190)), (16, y + 36))
                    buttons.append(b)
                    y += 64
                if not wifi_nets:
                    screen.blit(font_row.render("No networks found", True,
                                                (150, 150, 160)), (16, 70))
                npages = max(1, (len(wifi_nets) + ROWS_PER_PAGE - 1)
                             // ROWS_PER_PAGE)
                if npages > 1:
                    buttons.append(Button((740, 52, 56, 180), "UP",
                                          ("wifi_page", -1),
                                          enabled=wifi_page > 0))
                    buttons.append(Button((740, 244, 56, 180), "DN",
                                          ("wifi_page", 1),
                                          enabled=wifi_page < npages - 1))

        elif mode == MODE_WIFIPW:
            screen.blit(font_ui.render("Password for %s:" % wifi_target[:24],
                                       True, (235, 235, 245)), (8, 8))
            pw = wifi_pw + ("_" if (frames // 15) % 2 == 0 else " ")
            screen.blit(font_ui.render(pw[-40:], True, (255, 255, 160)),
                        (8, 44))
            buttons.append(Button((690, 4, 106, 36), "CANCEL",
                                  ("wifi_cancel",)))
            for ri, row in enumerate(wifi_kb_rows()):
                ky = 84 + ri * 66
                kx0 = (800 - len(row) * 80) // 2
                for ci, key in enumerate(row):
                    buttons.append(Button((kx0 + ci * 80, ky, 76, 62), key,
                                          ("wkey", key)))
            ky = 84 + 4 * 66
            buttons.append(Button((4, ky, 100, 62), "SHIFT", ("wshift",)))
            buttons.append(Button((112, ky, 100, 62),
                                  "abc" if wifi_sym else "?#+", ("wsym",)))
            buttons.append(Button((220, ky, 240, 62), "SPACE", ("wkey", " ")))
            buttons.append(Button((468, ky, 100, 62), "<-", ("bksp",)))
            buttons.append(Button((576, ky, 100, 62), "CLR", ("clear",)))
            buttons.append(Button((684, ky, 110, 62), "OK", ("wifi_ok",)))

        elif mode == MODE_PLAYBACK:
            if play["frames"]:
                # Slow motion: the loop already runs at 30 fps, so showing each
                # frame for N iterations IS 1/N speed -- no re-encode, no seek.
                if not play["paused"]:
                    play["tick"] += 1
                    if play["tick"] % play["speed"] == 0:
                        play["idx"] = (play["idx"] + 1) % len(play["frames"])
                pimg = play["frames"][play["idx"]]
            else:
                pbuf = play["proc"].stdout.read(PFRAME)
                if len(pbuf) < PFRAME:
                    # clip ended: loop it (restart the decode; user exits by tap)
                    try:
                        play["proc"].kill()
                    except Exception:
                        pass
                    play["proc"] = subprocess.Popen(play["cmd"],
                                                    stdout=subprocess.PIPE,
                                                    bufsize=PFRAME)
                    pimg = play["last"]  # hold last frame over the seek gap
                else:
                    pimg = pygame.image.frombuffer(pbuf, (DISP_W, DISP_H), "RGB")
                    play["last"] = pimg
            if pimg is not None:
                screen.blit(pimg, (0, YOFF))
            ban = pygame.Surface((800, 30), pygame.SRCALPHA)
            ban.fill((120, 30, 30, 200))
            screen.blit(ban, (0, 0))
            hint = "" if play["frames"] else "   (tap to close)"
            t = font_ui.render(play["banner"] + hint, True, (255, 230, 230))
            screen.blit(t, t.get_rect(center=(400, 15)))

            # ---- control bar: overlaid on the video (no shrink), directly
            # under the 30 px replay banner. The top of frame is usually
            # ceiling; the bottom is feet, which matter in fencing.
            BY = 32
            # The bar only earns its screen space once slow motion is engaged
            # and there is status to carry. During ordinary review the lone
            # SLOW MOTION button draws its own background, so the picture
            # stays clear.
            if play["pending"] or play["menu"] or play["frames"]:
                bar = pygame.Surface((800, 56), pygame.SRCALPHA)
                bar.fill((0, 0, 0, 150))
                screen.blit(bar, (0, BY))
            if play["pending"]:
                s = font_ui.render("Loading slow motion...", True, (255, 230, 160))
                screen.blit(s, s.get_rect(center=(400, BY + 28)))
            elif play["menu"]:
                for i, (lab, div) in enumerate((("10%", 10), ("25%", 4),
                                                ("50%", 2))):
                    buttons.append(Button((12 + i * 118, BY + 6, 110, 44), lab,
                                          ("slow_set", div)))
                buttons.append(Button((660, BY + 6, 130, 44), "CANCEL",
                                      ("slow_menu", False)))
            elif play["frames"]:
                buttons.append(Button((12, BY + 6, 128, 44),
                                      "PLAY" if play["paused"] else "PAUSE",
                                      ("slow_toggle",)))
                # Stepping implies pausing -- frame-by-frame is somewhere you
                # fall into from slow motion, not a separate mode to arm.
                buttons.append(Button((148, BY + 6, 74, 44), "<",
                                      ("slow_step", -1)))
                buttons.append(Button((230, BY + 6, 74, 44), ">",
                                      ("slow_step", 1)))
                pct = {10: "10%", 4: "25%", 2: "50%"}.get(play["speed"], "")
                s = font_ui.render("%s   frame %d/%d" % (pct, play["idx"] + 1,
                                                        len(play["frames"])),
                                   True, (235, 235, 245))
                screen.blit(s, s.get_rect(midleft=(320, BY + 28)))
                buttons.append(Button((612, BY + 6, 178, 44), "< NORMAL",
                                      ("slow_normal",)))
            else:
                buttons.append(Button((612, BY + 6, 178, 44), "SLOW MOTION",
                                      ("slow_menu", True)))

        for b in buttons:
            if b.label:
                b.draw(screen, font_ui)
        if flash and now < flash[1]:
            t = font_ui.render(flash[0], True, (255, 210, 120))
            screen.blit(t, t.get_rect(center=(400, 460)))

        frames += 1
        pygame.display.flip()

    stop_playback(play)
    flush_pending_touch()      # and again on a clean exit
    rec.stop()
    close_camera(dec)          # dec is None when running without a camera
    pygame.quit()
    con.close()
    print("avg fps: %.1f  frames: %d" % (frames / (time.time() - t0), frames))


if __name__ == "__main__":
    main()
