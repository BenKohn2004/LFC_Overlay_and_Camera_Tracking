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
ROWS_PER_PAGE = 6
NAME_MAX = 16
DEFAULT_NAMES = {"l": "LEFT FENCER", "r": "RIGHT FENCER"}

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
    for col in ("l_name", "r_name"):
        try:
            con.execute("ALTER TABLE bouts ADD COLUMN %s TEXT DEFAULT ''" % col)
        except sqlite3.OperationalError:
            pass
    con.execute("CREATE TABLE IF NOT EXISTS names_used("
                "name TEXT PRIMARY KEY, last_used REAL)")
    con.commit()
    return con


def write_names(l_name, r_name):
    with open(os.path.join(HOME, "fencers.txt"), "w") as fh:
        fh.write("%s\n%s\n" % (l_name, r_name))


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
                os.remove(row[1])
            except OSError:
                pass
            self.con.execute("UPDATE files SET deleted=1 WHERE id=?", (row[0],))
            self.con.commit()


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
    if not row or row[2] or not os.path.exists(row[0]):
        return None, 0
    return row[0], start - row[1]


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

    dec = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-f", "v4l2",
         "-input_format", "mjpeg", "-video_size", "1920x1080",
         "-framerate", str(FPS), "-i", "/dev/video0",
         "-vf", "scale=%d:%d" % (REC_W, REC_H),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, bufsize=REC_W * REC_H * 3)
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
    play = None                # {"proc", "banner", "from_mode"}
    flash = None               # (text, until_ts)
    name_side = "l"            # which plate is being edited
    name_buf = ""
    name_chips = []            # quick-select names for the entry screen

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
                     WHERE b3.session_id=b.session_id)
            FROM bouts b ORDER BY b.id DESC""").fetchall()
        out = []
        for (bid, sid, ts, ln, rn, n, lf, rf, first_id, nbouts) in rows:
            when = time.strftime("%b %d %H:%M", time.localtime(ts))
            names = "%s vs %s" % (ln or "LEFT", rn or "RIGHT")
            if bid == first_id and nbouts > 1:
                names += "  (warm-up)"
            score = "%d - %d" % (lf, rf) if lf is not None else "-"
            out.append({"bout_id": bid, "title": names,
                        "sub": "%s   %d touches   final %s" % (when, n, score)})
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
        buf = dec.stdout.read(FRAME)
        if len(buf) < FRAME:
            break
        img = pygame.image.frombuffer(buf, (REC_W, REC_H), "RGB")
        surf.blit(img, (0, 0))

        with _lock:
            st = dict(state)
            st["flags"] = list(state["flags"])
            events = pending_events[:]
            del pending_events[:]
            slog = state_changes[:]
            del state_changes[:]
        now = time.time()
        active = now - last_activity[0] < IDLE_STOP_SECS and last_activity[0] > 0
        if active and not rec.active:
            rec.start()
        elif not active and rec.active:
            rec.stop()

        stale = st["age"] is None or now - st["age"] > 3
        fl = st["flags"]
        nm_now = read_names() if frames % 90 == 0 or frames == 0 else nm_now
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
                if len(name_buf) < NAME_MAX:
                    name_buf += action[1]
            elif action[0] == "bksp":
                name_buf = name_buf[:-1]
            elif action[0] == "clear":
                name_buf = ""
            elif action[0] == "pick_name":
                save_name(name_side, action[1])
                nm_now = read_names()
                mode = MODE_LIVE
            elif action[0] == "name_ok":
                if save_name(name_side, name_buf):
                    nm_now = read_names()
                    mode = MODE_LIVE
                else:
                    flash = ("Name is empty", now + 2)
            elif action[0] == "name_cancel":
                mode = MODE_LIVE

        # a real touch at the strip interrupts any replay
        if mode == MODE_PLAYBACK and new_touch:
            stop_playback(play)
            mode, play = MODE_LIVE, None

        # ---------- draw current mode ----------
        screen.fill((0, 0, 0))
        buttons = []

        if mode == MODE_LIVE:
            disp = pygame.transform.smoothscale(surf, (DISP_W, DISP_H))
            screen.blit(disp, (0, YOFF))
            buttons.append(Button((548, 2, 116, 40), "REPLAY", ("replay_last",)))
            buttons.append(Button((676, 2, 116, 40), "BOUTS", ("goto_bouts",)))
            # invisible tap zones over the nameplates -> name entry
            buttons.append(Button(plate_rect("nplate_l"), "", ("edit_name", "l")))
            buttons.append(Button(plate_rect("nplate_r"), "", ("edit_name", "r")))
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
    dec.terminate()
    pygame.quit()
    con.close()
    print("avg fps: %.1f  frames: %d" % (frames / (time.time() - t0), frames))


if __name__ == "__main__":
    main()
