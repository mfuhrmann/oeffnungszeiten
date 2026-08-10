#!/usr/bin/env python3
"""One-time seeder for the Matrix relay session (see charts/changedetection/files/matrix_relay.py).

Logs the bot in with `refresh_token: true` and writes the relay state file. Run
it once per homeserver account; afterwards matrix_relay.py rotates the refresh
token on its own and this script is only needed again if the session is revoked.

That flag is the whole point of this script over a hand-rolled `/login` curl: under MAS the
access token is short-lived, so a login without `refresh_token: true` yields a string that works
today and returns M_UNKNOWN_TOKEN later, with nothing to renew it from.

Credentials are prompted for interactively and never printed or logged; the
resulting state file holds the tokens and is written with mode 0600.

    python3 scripts/matrix_relay_seed.py --out /path/to/matrix_relay.json

Copy the result into the running relay pod with `kubectl cp`; it is read on the next request,
without a restart. See docs/notifications.md.
"""
import argparse
import getpass
import json
import os
import stat
import sys
import urllib.error
import urllib.request

HOMESERVER = "https://matrix-client.matrix.org"
ROOM = "#osm-fulda-openinghours:matrix.org"
USER = "fulda-timelord-bot"


def post(url, body):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"error": raw[:300]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="where to write the state file")
    ap.add_argument("--homeserver", default=HOMESERVER)
    ap.add_argument("--room", default=ROOM)
    ap.add_argument("--user", default=USER, help="bot localpart or full MXID")
    args = ap.parse_args()

    user = args.user or input("Matrix bot user (localpart or @user:server): ").strip()
    password = getpass.getpass("Matrix bot password (not echoed): ")
    if not user or not password:
        sys.exit("user and password are both required")

    status, body = post(args.homeserver.rstrip("/") + "/_matrix/client/v3/login", {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": user},
        "password": password,
        "refresh_token": True,
        "initial_device_display_name": "changedetection-fulda-relay",
    })

    if status != 200:
        errcode = body.get("errcode", "?")
        print(f"login failed: HTTP {status} {errcode} {body.get('error', '')}",
              file=sys.stderr)
        if status == 403 or errcode in ("M_UNSUPPORTED", "M_UNRECOGNIZED"):
            print("\nPassword login looks closed for this account (full OIDC).\n"
                  "The device-code grant is the fallback; that path is not "
                  "implemented here yet.", file=sys.stderr)
        sys.exit(1)

    if not body.get("refresh_token"):
        sys.exit("login succeeded but returned no refresh_token - the relay "
                 "cannot renew without one; check that the homeserver honours "
                 "refresh_token:true")

    state = {
        "homeserver": args.homeserver.rstrip("/"),
        "room": args.room,
        "refresh_token": body["refresh_token"],
        "access_token": body["access_token"],
    }
    # 0600 at creation, not by a chmod afterwards: between the two the live tokens would sit on
    # disk at whatever the umask allows, readable by every other user on the machine.
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                 stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as fh:
        json.dump(state, fh, indent=1)

    # Non-secret facts only: this finally measures the MAS short-lived-token claim.
    print(f"ok - wrote {args.out}")
    print(f"    device_id     {body.get('device_id')}")
    print(f"    user_id       {body.get('user_id')}")
    print(f"    expires_in_ms {body.get('expires_in_ms')}")


if __name__ == "__main__":
    main()
