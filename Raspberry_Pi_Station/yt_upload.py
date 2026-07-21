"""Minimal YouTube resumable uploader — stdlib only (no pip needed).

The Pi has no internet except during uploads, so this deliberately avoids
google-api-python-client. Uses the refresh token produced by the one-time
OAuth link-up on the PC.

Chunked + resumable: a dropped connection resumes from the last acknowledged
byte instead of restarting the whole video.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = ("https://www.googleapis.com/upload/youtube/v3/videos"
              "?uploadType=resumable&part=snippet,status")
CHUNK = 4 * 1024 * 1024          # 4 MB per PUT
CATEGORY_SPORTS = "17"
# Unaudited API projects can only create private videos; after YouTube's
# (free) audit this can become "public".
DEFAULT_PRIVACY = "private"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Resumable uploads answer 308 'Resume Incomplete' — not a redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


class UploadError(Exception):
    pass


def access_token(token_path):
    with open(token_path) as fh:
        tok = json.load(fh)
    data = urllib.parse.urlencode({
        "client_id": tok["client_id"],
        "client_secret": tok["client_secret"],
        "refresh_token": tok["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["access_token"]
    except urllib.error.HTTPError as e:
        raise UploadError("token refresh failed: %s %s"
                          % (e.code, e.read()[:200]))


def start_session(at, title, description, size, privacy=DEFAULT_PRIVACY):
    meta = {
        "snippet": {"title": title[:100], "description": description[:4000],
                    "categoryId": CATEGORY_SPORTS},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    req = urllib.request.Request(UPLOAD_URL, data=json.dumps(meta).encode(),
                                 method="POST")
    req.add_header("Authorization", "Bearer " + at)
    req.add_header("Content-Type", "application/json; charset=UTF-8")
    req.add_header("X-Upload-Content-Length", str(size))
    req.add_header("X-Upload-Content-Type", "video/mp4")
    try:
        with _opener.open(req, timeout=60) as r:
            loc = r.headers.get("Location")
    except urllib.error.HTTPError as e:
        body = e.read()[:400].decode(errors="replace")
        if e.code == 403 and "quota" in body.lower():
            raise UploadError("quota exceeded")
        raise UploadError("start failed: %s %s" % (e.code, body))
    if not loc:
        raise UploadError("no upload session URL returned")
    return loc


def upload(path, title, description="", token_path=None,
           privacy=DEFAULT_PRIVACY, progress=None):
    """Upload one file. Returns the YouTube video id. Raises UploadError."""
    size = os.path.getsize(path)
    at = access_token(token_path)
    session = start_session(at, title, description, size, privacy)
    sent = 0
    with open(path, "rb") as fh:
        while sent < size:
            fh.seek(sent)
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            end = sent + len(chunk) - 1
            req = urllib.request.Request(session, data=chunk, method="PUT")
            req.add_header("Content-Length", str(len(chunk)))
            req.add_header("Content-Range",
                           "bytes %d-%d/%d" % (sent, end, size))
            try:
                with _opener.open(req, timeout=600) as r:
                    body = r.read()
                    try:
                        return json.loads(body).get("id")
                    except ValueError:
                        raise UploadError("bad final response")
            except urllib.error.HTTPError as e:
                if e.code == 308:                       # resume incomplete
                    rng = e.headers.get("Range")        # e.g. bytes=0-4194303
                    if rng and "-" in rng:
                        sent = int(rng.split("-")[1]) + 1
                    else:
                        sent = end + 1
                    if progress:
                        progress(sent, size)
                    continue
                if e.code in (500, 502, 503, 504):      # transient: retry
                    time.sleep(3)
                    continue
                body = e.read()[:400].decode(errors="replace")
                raise UploadError("upload failed: %s %s" % (e.code, body))
            except (urllib.error.URLError, OSError) as e:
                # connection dropped: ask the server where to resume
                time.sleep(3)
                probe = urllib.request.Request(session, data=b"", method="PUT")
                probe.add_header("Content-Range", "bytes */%d" % size)
                try:
                    with _opener.open(probe, timeout=60):
                        pass
                except urllib.error.HTTPError as pe:
                    if pe.code == 308:
                        rng = pe.headers.get("Range")
                        sent = (int(rng.split("-")[1]) + 1
                                if rng and "-" in rng else sent)
                        continue
                raise UploadError("connection lost: %s" % e)
    raise UploadError("upload ended without a video id")
