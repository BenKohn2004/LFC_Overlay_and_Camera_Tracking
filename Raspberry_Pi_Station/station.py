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
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import time

import pygame

try:
    import yt_upload
except ImportError:          # station still runs without the uploader
    yt_upload = None

# ---------------- config ----------------
FMT = "<B6s4I12?32s"
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
UPLOAD_DIR = os.path.join(os.path.expanduser("~/skewered"), "uploads")
TOKEN_PATH = os.path.join(os.path.expanduser("~/skewered"), "yt_token.json")
UPLOAD_PRE = 4.0            # lead-in before a bout's first touch
UPLOAD_POST = 4.0           # tail after its last touch
UPLOAD_MAX_SECS = 1200      # 20-minute cap per uploaded bout
BOX_SSID = "SkeweredNet"
# logo circle centers on the 1920x1080 canvas (gap between nameplate and score)
LOGO_CENTERS = {"l": (567, 950), "r": (1344, 950)}
LOGO_D = 120               # diameter in canvas px
PINNED_CLUBS = ["USA"]     # always offered in the picker (besides "None")

# ---------------- shared telemetry state ----------------
state = {"l": 0, "r": 0, "min": 0, "sec": 0, "flags": [False] * 12, "age": None}
_lock = threading.Lock()
last_activity = [0.0]
pending_events = []
state_changes = []


def udp_thread():
    prev = None
    episode = None
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", UDP_PORT))
    s.settimeout(1)
    last_ka = 0.0
    while True:
        if time.time() - last_ka > 10:
            last_ka = time.time()
            try:
                s.sendto(b"ka", ("192.168.4.1", UDP_PORT))
            except OSError:
                pass
        try:
            data, _ = s.recvfrom(1024)
        except socket.timeout:
            continue
        if len(data) != struct.calcsize(FMT):
            continue
        f = struct.unpack(FMT, data)
        now = time.time()
        r, l, sec, mn = f[2], f[3], f[4], f[5]
        flags = list(f[6:18])
        with _lock:
            state["r"], state["l"] = r, l
            state["sec"], state["min"] = sec, mn
            state["flags"] = flags
            state["age"] = now

            if prev is None:
                prev = (l, r, mn, sec, flags)
                continue
            pl, pr, pm, ps, pf = prev
            if (l, r, mn, sec, flags) == (pl, pr, pm, ps, pf):
                continue
            last_activity[0] = now
            state_changes.append((now, l, r, mn, sec,
                                  "".join("1" if x else "0" for x in flags)))

            if (l, r) == (0, 0) and (pl, pr) != (0, 0):
                pending_events.append({"type": "bout_start", "ts": now,
                                       "l0": pl, "r0": pr, "l1": 0, "r1": 0,
                                       "detail": ""})

            hit_now = any(flags[0:4])
            hit_prev = any(pf[0:4])
            if hit_now and not hit_prev and episode is None:
                episode = {"ts": now, "lights": set(), "l0": pl, "r0": pr}
            if episode is not None:
                for i in range(4):
                    if flags[i]:
                        episode["lights"].add(FLAG_NAMES[i])
                if not hit_now:
                    pending_events.append({
                        "type": "touch", "ts": episode["ts"],
                        "l0": episode["l0"], "r0": episode["r0"],
                        "l1": l, "r1": r,
                        "detail": "+".join(sorted(episode["lights"]))})
                    episode = None

            for i in range(6, 12):
                if flags[i] and not pf[i]:
                    pending_events.append({
                        "type": "card" if i < 10 else "priority", "ts": now,
                        "l0": pl, "r0": pr, "l1": l, "r1": r,
                        "detail": FLAG_NAMES[i]})
            prev = (l, r, mn, sec, flags)


# ---------------- database ----------------
def db_init():
    con = sqlite3.connect(DBPATH)
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
    for col in ("l_name", "r_name", "youtube_id", "upload_path"):
        try:
            con.execute("ALTER TABLE bouts ADD COLUMN %s TEXT DEFAULT ''" % col)
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


# ---------------- recorder ----------------
class Recorder:
    def __init__(self, con):
        self.con = con
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
        now = time.time()
        cur = self.con.execute(
            "INSERT INTO sessions(start_ts, end_ts) VALUES(?,?)", (now, now))
        self.session_id = cur.lastrowid
        self.session_start = now
        self.frames = 0
        self._new_bout(now, 0.0)
        self.con.commit()
        pattern = os.path.join(RECDIR, "rec_%Y-%m-%d_%H-%M-%S.mp4")
        cmd = ["ffmpeg", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", "%dx%d" % (REC_W, REC_H), "-r", str(FPS), "-i", "-",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
               "-pix_fmt", "yuv420p", "-g", str(FPS * 2),
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
        off = ev["ts"] - self.session_start if self.session_start else 0
        if ev["type"] == "bout_start":
            self._new_bout(ev["ts"], off)
        self.con.execute(
            "INSERT INTO events(session_id, bout_id, type, ts, session_offset,"
            " file_idx, file_offset, l_before, r_before, l_after, r_after,"
            " detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.session_id, self.bout_id, ev["type"], ev["ts"], off,
             int(off // SEGMENT_SECS), off % SEGMENT_SECS,
             ev["l0"], ev["r0"], ev["l1"], ev["r1"], ev["detail"]))
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
    return subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-f", "v4l2",
         "-input_format", "mjpeg", "-video_size", "1920x1080",
         "-framerate", str(FPS), "-i", CAM_DEV,
         "-vf", "scale=%d:%d" % (REC_W, REC_H),
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
    last_space_check = 0.0
    running = True

    mode = MODE_LIVE
    buttons = []
    bout_rows, bout_page = [], 0
    touch_rows, touch_page = [], 0
    cur_bout = None            # (bout_id, title)
    confirm = None             # {title, lines, yes_label, yes, from} in MODE_CONFIRM
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
    upload_job = {"active": False, "done": 0, "total": 0, "ok": 0, "fail": 0,
                  "finished": False, "current": "", "pct": 0, "error": ""}

    def start_exports(bout_ids):
        if export_job["active"] or not bout_ids:
            return
        export_job.update(active=True, done=0, total=len(bout_ids), ok=0,
                          fail=0, finished=False)

        def worker():
            c2 = sqlite3.connect(DBPATH, timeout=10)
            for bid in bout_ids:
                path, err = export_bout(c2, rec, bid)
                export_job["done"] += 1
                if path:
                    export_job["ok"] += 1
                else:
                    export_job["fail"] += 1
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

    def pending_upload_bouts():
        return [(r[0], r[1]) for r in con.execute(
            "SELECT id, upload_path FROM bouts WHERE youtube_id='' AND"
            " upload_path<>'' ORDER BY id")]

    def start_uploads():
        if upload_job["active"] or yt_upload is None:
            return False
        items = pending_upload_bouts()
        items = [(b, p) for (b, p) in items if os.path.exists(p)]
        if not items:
            return False
        upload_job.update(active=True, done=0, total=len(items), ok=0,
                          fail=0, finished=False, current="", pct=0, error="")

        def worker():
            c2 = sqlite3.connect(DBPATH, timeout=10)
            for bid, path in items:
                title = bout_upload_title(c2, bid)
                upload_job["current"] = title
                upload_job["pct"] = 0

                def prog(sent, total, _j=upload_job):
                    _j["pct"] = int(sent * 100 / max(1, total))

                try:
                    vid = yt_upload.upload(
                        path, title, description=bout_description(c2, bid),
                        token_path=TOKEN_PATH, progress=prog)
                    c2.execute("UPDATE bouts SET youtube_id=? WHERE id=?",
                               (vid or "uploaded", bid))
                    c2.commit()
                    upload_job["ok"] += 1
                    try:
                        os.remove(path)     # cut file no longer needed
                    except OSError:
                        pass
                except Exception as e:
                    upload_job["error"] = str(e)[:70]
                    upload_job["fail"] += 1
                    upload_job["done"] += 1
                    if "quota" in str(e).lower():
                        break               # stop; resume another day
                    continue
                upload_job["done"] += 1
            c2.close()
            upload_job["active"] = False
            upload_job["finished"] = True
        threading.Thread(target=worker, daemon=True).start()
        return True

    def uploadable_bouts():
        return [r[0] for r in con.execute(
            "SELECT b.id FROM bouts b WHERE b.youtube_id='' AND EXISTS"
            " (SELECT 1 FROM events e WHERE e.bout_id=b.id AND"
            "  e.type='touch') ORDER BY b.id")]

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
                   b.youtube_id
            FROM bouts b ORDER BY b.id DESC""").fetchall()
        out = []
        for (bid, sid, ts, ln, rn, n, lf, rf, first_id, nbouts, ytid) in rows:
            when = time.strftime("%b %d %H:%M", time.localtime(ts))
            names = "%s vs %s" % (ln or "LEFT", rn or "RIGHT")
            if bid == first_id and nbouts > 1:
                names += "  (warm-up)"
            score = "%d - %d" % (lf, rf) if lf is not None else "-"
            mark = ("   [skipped]" if ytid == "skipped"
                    else ("   [uploaded]" if ytid else ""))
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
                "from_mode": from_mode, "last": None}

    def stop_playback(p):
        if p and p["proc"]:
            try:
                p["proc"].kill()
            except Exception:
                pass

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
                tapped = e.pos
            elif e.type == pygame.MOUSEBUTTONUP:
                touch_t = None
        if mode == MODE_LIVE and touch_t and time.time() - touch_t > 2.0:
            running = False

        action = None
        if tapped:
            if mode == MODE_PLAYBACK:
                action = ("dismiss",)
            else:
                for b in buttons:
                    if b.enabled and b.rect.collidepoint(tapped):
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
            elif action[0] == "ask_delete_bout":
                n_touch = con.execute(
                    "SELECT COUNT(*) FROM events WHERE bout_id=? AND type='touch'",
                    (action[1],)).fetchone()[0]
                confirm = {
                    "title": "Delete this bout?",
                    "lines": [cur_bout["title"],
                              "%d touches will be removed." % n_touch,
                              "Video segments are kept (shared with other bouts)."],
                    "yes_label": "DELETE",
                    "yes": ("do_delete_bout", action[1]),
                    "from": MODE_TOUCHES}
                mode = MODE_CONFIRM
            elif action[0] == "confirm_no":
                mode = confirm["from"] if confirm else MODE_BOUTS
                confirm = None
            elif action[0] == "confirm_yes":
                act = confirm["yes"] if confirm else None
                confirm = None
                if act and act[0] == "do_delete_bout":
                    delete_bout(act[1])
                    bout_rows, bout_page = load_bouts(), 0
                    cur_bout = None
                    mode = MODE_BOUTS
                    flash = ("Bout deleted", now + 2)
                else:
                    mode = MODE_BOUTS
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
                if ids:
                    start_exports(ids)
                    flash = ("Preparing %d bout video(s)..." % len(ids), now + 3)
                else:
                    flash = ("Nothing new to upload", now + 2)
            elif action[0] == "upload_one":
                bid = action[1]
                already = con.execute("SELECT youtube_id FROM bouts WHERE"
                                      " id=?", (bid,)).fetchone()
                if already and already[0]:
                    flash = ("Already uploaded", now + 2)
                else:
                    start_exports([bid])
                    flash = ("Preparing bout video...", now + 3)
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
                if ok and start_uploads():
                    flash = ("Connected - uploading...", now + 5)
                elif ok:
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
        if mode == MODE_PLAYBACK and new_touch:
            stop_playback(play)
            mode, play = MODE_LIVE, None

        # export batch finished -> report, then route to wifi / youtube setup
        if export_job["finished"]:
            export_job["finished"] = False
            msg = "Prepared %d video(s)" % export_job["ok"]
            if export_job["fail"]:
                msg += ", %d failed" % export_job["fail"]
            if export_job["ok"] and not os.path.exists(TOKEN_PATH):
                flash = (msg + " - YouTube not linked yet", now + 5)
            elif export_job["ok"] and not internet_up():
                flash = (msg + " - pick a wifi network", now + 4)
                wifi_page = 0
                start_wifi_scan()
                mode = MODE_WIFI
            elif export_job["ok"] and start_uploads():
                flash = (msg + " - uploading...", now + 4)
            else:
                flash = (msg, now + 4)

        # upload batch finished
        if upload_job["finished"]:
            upload_job["finished"] = False
            m = "Uploaded %d video(s)" % upload_job["ok"]
            if upload_job["fail"]:
                m += " - %d failed (%s)" % (upload_job["fail"],
                                            upload_job["error"])
            flash = (m, now + 8)

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
            if upload_job["active"]:
                screen.blit(font_sml.render(
                    "Uploading %d/%d  %d%%" % (upload_job["done"] + 1,
                                               upload_job["total"],
                                               upload_job["pct"]),
                    True, (140, 235, 150)), (540, 18))
            elif export_job["active"]:
                screen.blit(font_sml.render(
                    "Cutting %d/%d..." % (export_job["done"],
                                          export_job["total"]),
                    True, (255, 210, 120)), (560, 18))
            elif mode == MODE_BOUTS:
                buttons.append(Button((430, 4, 104, 40), "WIFI",
                                      ("wifi_open",)))
                buttons.append(Button((560, 4, 172, 40), "UPLOAD ALL",
                                      ("upload_all",)))
            else:
                # Left of UPLOAD, same slot the WIFI button uses on the bouts
                # screen. Both live in this branch, so neither is offered while
                # an upload or export is running.
                buttons.append(Button((430, 4, 104, 40), "DELETE",
                                      ("ask_delete_bout", cur_bout["bout_id"])))
                buttons.append(Button((560, 4, 172, 40), "UPLOAD",
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
            c = confirm or {"title": "", "lines": [], "yes_label": "OK"}
            pygame.draw.rect(screen, (40, 20, 20), (60, 90, 680, 250))
            pygame.draw.rect(screen, (200, 70, 70), (60, 90, 680, 250), 3)
            t = font_head.render(c["title"], True, (255, 210, 210))
            screen.blit(t, t.get_rect(center=(400, 130)))
            ly = 180
            for line in c["lines"]:
                s = font_row.render(line, True, (235, 235, 245))
                screen.blit(s, s.get_rect(center=(400, ly)))
                ly += 32
            # CANCEL on the left and wider: the safe choice should be the easy
            # one to hit on a small touchscreen.
            buttons.append(Button((92, 272, 300, 52), "CANCEL", ("confirm_no",)))
            buttons.append(Button((420, 272, 288, 52), c["yes_label"],
                                  ("confirm_yes",)))

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
            pbuf = play["proc"].stdout.read(PFRAME)
            if len(pbuf) < PFRAME:
                # clip ended: loop it (restart the decode; user exits by tap)
                stop_playback(play)
                play["proc"] = subprocess.Popen(play["cmd"],
                                                stdout=subprocess.PIPE,
                                                bufsize=PFRAME)
                pimg = play["last"]      # hold last frame over the seek gap
            else:
                pimg = pygame.image.frombuffer(pbuf, (DISP_W, DISP_H), "RGB")
                play["last"] = pimg
            if pimg is not None:
                screen.blit(pimg, (0, YOFF))
            ban = pygame.Surface((800, 30), pygame.SRCALPHA)
            ban.fill((120, 30, 30, 200))
            screen.blit(ban, (0, 0))
            t = font_ui.render(play["banner"] + "   (tap to close)", True,
                               (255, 230, 230))
            screen.blit(t, t.get_rect(center=(400, 15)))

        if mode != MODE_PLAYBACK:
            for b in buttons:
                if b.label:
                    b.draw(screen, font_ui)
        if flash and now < flash[1]:
            t = font_ui.render(flash[0], True, (255, 210, 120))
            screen.blit(t, t.get_rect(center=(400, 460)))

        frames += 1
        pygame.display.flip()

    stop_playback(play)
    rec.stop()
    close_camera(dec)          # dec is None when running without a camera
    pygame.quit()
    con.close()
    print("avg fps: %.1f  frames: %d" % (frames / (time.time() - t0), frames))


if __name__ == "__main__":
    main()
