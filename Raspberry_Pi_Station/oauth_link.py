"""One-time YouTube OAuth link-up (stdlib only).

Reads the Google 'Desktop app' client_secret JSON, opens the browser for the
user's one-time 'Allow', catches the loopback redirect, exchanges the code for
tokens, and writes yt_token.json (refresh_token + client id/secret) that the Pi
uploader can use forever.
"""
import glob
import http.server
import json
import os
import socket
import threading
import urllib.parse
import urllib.request
import webbrowser

SCOPE = "https://www.googleapis.com/auth/youtube.upload"
DL = os.path.join(os.path.expanduser("~"), "Downloads")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yt_token.json")

# newest first: if several client JSONs are in Downloads, use the latest one
secret_files = sorted(glob.glob(os.path.join(DL, "client_secret_*.json")),
                      key=os.path.getmtime, reverse=True)
if not secret_files:
    raise SystemExit("No client_secret_*.json in Downloads")
print("Using client:", os.path.basename(secret_files[0]))
with open(secret_files[0]) as fh:
    conf = json.load(fh)["installed"]
CLIENT_ID = conf["client_id"]
CLIENT_SECRET = conf["client_secret"]

# pick a free loopback port (Desktop clients allow any localhost port)
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
PORT = sock.getsockname()[1]
sock.close()
REDIRECT = "http://localhost:%d/" % PORT

auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": CLIENT_ID,
    "redirect_uri": REDIRECT,
    "response_type": "code",
    "scope": SCOPE,
    "access_type": "offline",
    # select_account forces the chooser so the right Google account (and so
    # the right YouTube channel) gets linked, not the browser's default one
    "prompt": "select_account consent",
})

result = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(q)
        # ignore favicon / stray hits with no oauth params
        if "code" not in params and "error" not in params:
            self.send_response(204)
            self.end_headers()
            return
        result.update({k: v[0] for k, v in params.items()})
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        msg = ("Authorization received. You can close this tab and return to "
               "Claude." if "code" in params else
               "Authorization failed: " + params.get("error", ["unknown"])[0])
        self.wfile.write(("<html><body style='font-family:sans-serif;"
                          "text-align:center;padding-top:60px'><h2>%s</h2>"
                          "</body></html>" % msg).encode())

    def log_message(self, *a):
        pass


httpd = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
print("Opening browser for YouTube authorization...")
print("If it doesn't open, paste this URL into your browser:\n%s\n" % auth_url)
print("Waiting for you to click Allow (up to 8 minutes)...", flush=True)
webbrowser.open(auth_url)
threading.Thread(target=httpd.serve_forever, daemon=True).start()

# wait up to 8 minutes for the redirect
import time
for _ in range(480):
    if result:
        break
    time.sleep(1)
httpd.shutdown()

if "code" not in result:
    raise SystemExit("No authorization code received: %r" % result)

# exchange code for tokens
data = urllib.parse.urlencode({
    "code": result["code"],
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "redirect_uri": REDIRECT,
    "grant_type": "authorization_code",
}).encode()
req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
with urllib.request.urlopen(req, timeout=30) as resp:
    tok = json.load(resp)

if "refresh_token" not in tok:
    raise SystemExit("No refresh_token returned (re-run; ensure prompt=consent)")

out = {
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "refresh_token": tok["refresh_token"],
    "scope": SCOPE,
}
with open(OUT, "w") as fh:
    json.dump(out, fh, indent=2)
print("SUCCESS: wrote", OUT)
print("refresh_token length:", len(tok["refresh_token"]))
