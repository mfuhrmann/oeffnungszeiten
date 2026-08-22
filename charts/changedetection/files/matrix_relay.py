#!/usr/bin/env python3
"""Matrix notification relay for changedetection.

Why this exists: matrix.org delegates authentication to MAS (next-gen auth), so
access tokens are short-lived OAuth tokens. Apprise stores one static string and
cannot refresh, which is why `matrixs://:<token>@matrix.org/...` eventually dies
with M_UNKNOWN_TOKEN. This relay owns the session instead: it keeps the refresh
token, mints access tokens on demand, and posts messages into the room.

changedetection talks to it over plain HTTP inside the namespace:

    notification_urls:  json://matrix-relay:8099/notify

Apprise's json:// posts {"title": ..., "message": ..., ...}; both fields are used.
No token travels that way, so the URL is not a secret and can live in the managed
global settings rather than in each watch.

State file (JSON), default /config/matrix_relay.json:

    {
      "homeserver":    "https://matrix-client.matrix.org",
      "room":          "#osm-fulda-openinghours:matrix.org",
      "refresh_token": "...",         # required; rotated on every refresh
      "access_token":  "...",         # optional seed, refreshed automatically
      "room_id":       "!abc:matrix.org"   # filled in on first resolve
    }

MAS refresh tokens are SINGLE USE: every refresh returns a new one, so the state
file is rewritten atomically after each refresh. Keep it on a persistent volume.
"""
import argparse
import html as html_mod
import json
import logging
import os
import re
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("matrix-relay")
STATE_PATH = os.environ.get("MATRIX_RELAY_STATE", "/config/matrix_relay.json")
# matrix.org rejects an event over 64 KiB outright, so an unusually large diff would not arrive
# at all, the one case where knowing that something changed matters most. Truncate well below
# the limit: the message is a pointer to the watch, not the record of the change.
MAX_BODY = 8000
# A line cap on top of the byte cap, because 8000 bytes is still a wall of text in a room, and
# because MAX_BODY drops the HTML body (truncated markup would be unbalanced), so the longest
# message would be the one that loses its links. Measured over 39 sends: median 1 diff line,
# p90 12, max 24. The worst case on record is a practice holiday notice at 47. 40 leaves every
# real message intact and still bounds what one broken filter can do.
MAX_LINES = 40
# Header lines of the notification body: "Webseite: <url>", "OpenStreetMap: <url>", or a bare
# URL (the global body, used by the few entries without an osm_id) which is labelled Webseite.
LINK_LINE = re.compile(r"^(?:([^:]{1,30}):\s*)?(https?://\S+)\s*$")
DEFAULT_LINK_LABEL = "Webseite"

# changedetection prefixes each changed line with (added) / (removed) / (changed).
DIFF_MARKER = re.compile(r"^\((added|removed|changed)\)\s?")
SIGIL = {"added": "+", "removed": "−", "changed": "~"}
COLOR = {"added": "#2e7d32", "removed": "#c62828", "changed": "#ef6c00"}


def format_message(title, message):
    r"""Render one notification as (plain_text, html).

    The body changedetection sends is the watch's notification_body: "Webseite: <url>", an
    "OpenStreetMap: <url>" line where the entry has an osm_id, then {{diff}}. Leading link
    lines become the header; the rest is the diff.

    Blank lines and the page's own indentation are dropped, the rest is kept up to MAX_LINES.
    Measured over 39 sends: median 1 diff line, p90 12, max 24, so the cap never touches a real
    message. MAX_BODY in send() is the second, coarser limit, because matrix.org rejects an
    oversized event outright.

    >>> plain, html = format_message("T", "Webseite: https://example.de/\n(added) Mo 9-17")
    >>> plain.splitlines()
    ['T', 'Webseite: https://example.de/', '', '+ Mo 9-17']
    >>> lang = "\n".join(f"(added) Zeile {i}" for i in range(50))
    >>> plain, html = format_message("T", lang)
    >>> plain.splitlines()[-1]
    '[…] 10 weitere Zeilen, vollständige Änderung im UI'
    >>> len(plain.splitlines())
    43
    >>> html.count("<br/>") == plain.count(chr(10)) - 1
    True
    """
    lines = message.splitlines()
    head = []
    while lines:
        m = LINK_LINE.match(lines[0].strip())
        if not m:
            break
        lines.pop(0)
        head.append((m.group(1) or DEFAULT_LINK_LABEL, m.group(2)))

    kept = []
    for raw in lines:
        stripped = raw.strip()
        m = DIFF_MARKER.match(stripped)
        text = DIFF_MARKER.sub("", stripped).strip()
        if not text:                       # blank, with or without a marker
            continue
        kept.append((m.group(1) if m else None, text))

    rest = 0
    if len(kept) > MAX_LINES:
        rest = len(kept) - MAX_LINES
        kept = kept[:MAX_LINES]

    text_parts = ([title] if title else []) + [f"{label}: {url}" for label, url in head] + [""]
    text_parts += [f"{SIGIL.get(kind, ' ')} {t}" for kind, t in kept]
    if rest:
        text_parts.append(f"[…] {rest} weitere Zeilen, vollständige Änderung im UI")

    def esc(s):
        return html_mod.escape(s, quote=False)

    html_parts = []
    if title:
        html_parts.append(f"<b>{esc(title)}</b>")
    for label, url in head:
        href = html_mod.escape(url, quote=True)
        html_parts.append(f'{esc(label)}: <a href="{href}">{esc(url)}</a>')
    for kind, t in kept:
        body = esc(t)
        if kind == "removed":
            body = f"<del>{body}</del>"
        if kind in COLOR:
            body = f'<font color="{COLOR[kind]}">{body}</font>'
        html_parts.append(body)
    if rest:
        html_parts.append(esc(f"[…] {rest} weitere Zeilen, vollständige Änderung im UI"))

    return "\n".join(text_parts), "<br/>".join(html_parts)


class MatrixSession:
    """Holds the Matrix session and refreshes it when the server rejects a token."""

    def __init__(self, path):
        self.path = path
        self.state = None
        self.mtime = None
        self.homeserver = "https://matrix-client.matrix.org"
        # Every request runs in its own thread against this one session, and a MAS refresh token
        # is single use: two threads refreshing in parallel would replay the same token, which
        # MAS may read as a compromised session and answer by revoking the whole family. Then
        # only re-seeding with the bot's password gets delivery back.
        self.lock = threading.RLock()

    # ---------------------------------------------------------------- state
    def load(self):
        """Read the state file, late and again whenever it changed underneath us.

        Deliberately not done in __init__: seeding copies the file into the running pod, and a
        relay that exited on an unseeded volume would leave nothing to copy into. Unseeded, it
        serves 503 and picks the file up on the next request without a restart.

        Re-reading on a changed mtime covers the other direction, a re-seed, or anything else
        that wrote a fresh single-use refresh token while this process held the spent one. In
        memory it would look fine until the next refresh, and fail with no visible cause.
        """
        with self.lock:
            try:
                mtime = os.stat(self.path).st_mtime_ns
            except FileNotFoundError:
                if self.state is not None:
                    return self.state          # deleted underneath us; memory still works
                raise
            if self.state is not None and mtime == self.mtime:
                return self.state
            with open(self.path) as fh:
                state = json.load(fh)
            if not state.get("refresh_token"):
                raise RuntimeError(f"{self.path}: refresh_token is required")
            self.homeserver = state.get(
                "homeserver", self.homeserver).rstrip("/")
            self.state, self.mtime = state, mtime
            return self.state

    def save(self):
        """Atomic write - a half-written state file would lose the session.

        fsync before the rename: the newest single-use refresh token is the only way back into
        the session, and a token that reached the page cache but not the disk is gone on a node
        crash. Failures are logged rather than raised, the token is already valid in memory, so
        a request that would otherwise have succeeded should not fail on top of it.
        """
        d = os.path.dirname(self.path) or "."
        try:
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".matrix_relay.", suffix=".json")
            with os.fdopen(fd, "w") as fh:
                json.dump(self.state, fh, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            self.mtime = os.stat(self.path).st_mtime_ns
        except OSError as e:
            log.error("could not persist the session to %s (%s) - it now exists only in memory "
                      "and is lost on restart; re-seed if the relay comes back unauthenticated",
                      self.path, e)

    # ------------------------------------------------------------- requests
    def _call(self, method, path, body=None, token=None, absolute=False):
        url = path if absolute else self.homeserver + path
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf8", "replace")
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"error": raw[:200]}

    def refresh(self, stale=None):
        """Exchange the refresh token for a fresh access token (compat endpoint).

        `stale` is the access token whose rejection triggered this. Under the lock the token may
        already have been replaced by whoever got there first, and spending a second refresh
        token for the same expiry is what the single-use rule punishes, so hand back what that
        thread minted instead of asking again.
        """
        with self.lock:
            state = self.load()
            if stale is not None and state.get("access_token") not in (None, stale):
                return state["access_token"]
            status, body = self._call("POST", "/_matrix/client/v3/refresh",
                                      {"refresh_token": state["refresh_token"]})
            if status != 200 or "access_token" not in body:
                raise RuntimeError(f"refresh failed: HTTP {status} {body}")
            state["access_token"] = body["access_token"]
            if body.get("refresh_token"):
                state["refresh_token"] = body["refresh_token"]
            self.save()
            log.info("refreshed access token (expires_in_ms=%s)", body.get("expires_in_ms"))
            return state["access_token"]

    def token(self):
        # Under the lock so that a cold start with several notifications in flight mints one
        # token rather than one per thread; the lock is reentrant, refresh() takes it again.
        with self.lock:
            return self.load().get("access_token") or self.refresh()

    def room_id(self):
        state = self.load()
        if state.get("room_id"):
            return state["room_id"]
        alias = urllib.parse.quote(state["room"], safe="")
        token = self.token()
        status, body = self._call("GET", f"/_matrix/client/v3/directory/room/{alias}",
                                  token=token)
        if status == 401:
            status, body = self._call("GET", f"/_matrix/client/v3/directory/room/{alias}",
                                      token=self.refresh(stale=token))
        if status != 200 or "room_id" not in body:
            raise RuntimeError(f"cannot resolve room: HTTP {status} {body}")
        with self.lock:
            self.state["room_id"] = body["room_id"]
            self.save()
        return body["room_id"]

    def send(self, text, html=None):
        """Send one m.text message, refreshing once if the token was rejected."""
        if len(text) > MAX_BODY:
            text = text[:MAX_BODY] + "\n[…] gekürzt, vollständige Änderung im UI"
        # Truncating markup would emit unbalanced tags, so an oversized formatted_body is
        # dropped instead, the plain body still carries the change.
        if html and len(html) > MAX_BODY:
            html = None
        content = {"msgtype": "m.text", "body": text}
        if html:
            content.update({"format": "org.matrix.custom.html", "formatted_body": html})
        room = urllib.parse.quote(self.room_id(), safe="")
        # One transaction ID for both attempts: it is the homeserver's idempotency key, so a
        # retry after a rejected token must not read as a second message. It has to be unique
        # across concurrent sends for the same reason, a clock-derived ID collides between two
        # threads in the same millisecond, and the homeserver then answers the second send with
        # the first one's event_id, silently dropping a notification.
        txn = "cd-" + uuid.uuid4().hex
        for attempt in (1, 2):
            token = self.token()
            status, body = self._call(
                "PUT", f"/_matrix/client/v3/rooms/{room}/send/m.room.message/{txn}",
                content, token=token)
            if status == 200:
                return body.get("event_id")
            if status == 401 and attempt == 1:
                log.info("token rejected (%s) - refreshing", body.get("errcode"))
                self.refresh(stale=token)
                continue
            raise RuntimeError(f"send failed: HTTP {status} {body}")


def make_handler(session):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code, payload):
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):  # noqa: N802 - health check
            if self.path.rstrip("/") in ("/health", ""):
                try:
                    self._reply(200, {"ok": True, "room_id": session.room_id()})
                except Exception as e:
                    self._reply(503, {"ok": False, "error": str(e)[:200]})
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw or b"{}")
            except ValueError:
                payload = {"message": raw.decode("utf8", "replace")}
            title = (payload.get("title") or "").strip()
            message = (payload.get("message") or payload.get("body") or "").strip()
            text, formatted = format_message(title, message)
            if not text.strip():
                self._reply(400, {"error": "empty notification"})
                return
            try:
                event_id = session.send(text, html=formatted)
            except Exception as e:
                log.error("send failed: %s", e)
                self._reply(502, {"error": str(e)[:300]})
                return
            # Line counts in and out: how verbose real diffs get decides whether a cap is
            # needed at all. Deciding that from measurements, not from one bad example.
            diff_lines = sum(1 for ln in text.splitlines() if ln[:1] in ("+", "−", "~"))
            log.info("sent %s (%d chars, %d diff lines from %d raw)",
                     event_id, len(text), diff_lines, len(message.splitlines()))
            self._reply(200, {"ok": True, "event_id": event_id})

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=STATE_PATH)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--test", metavar="TEXT", help="send one message and exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    session = MatrixSession(args.state)
    if args.test:
        print("event_id:", session.send(args.test))
        return
    try:
        log.info("relay listening on %s:%d -> room %s", args.host, args.port,
                 session.load().get("room"))
    except Exception as e:
        log.warning("relay listening on %s:%d, but no usable session yet (%s) - "
                    "seed %s; it is picked up without a restart",
                    args.host, args.port, e, args.state)
    ThreadingHTTPServer((args.host, args.port), make_handler(session)).serve_forever()


if __name__ == "__main__":
    main()
