# Notifications

A change is only useful if somebody hears about it. Delivery goes into a Matrix room shared with
other OSM mappers, through a small relay that runs next to changedetection.

## Why a relay and not Apprise

Apprise speaks Matrix natively (`matrixs://:<token>@matrix.org/%23<alias>:matrix.org?mode=off`) and
that is the obvious way to do this. It does not work against matrix.org: the homeserver delegates
authentication to MAS, so `/login` hands out **short-lived** access tokens paired with a refresh
token. Apprise stores one static string and cannot refresh, so the token eventually stops working
and every send fails with

```
401 {"errcode":"M_UNKNOWN_TOKEN","error":"Token is not active"}
```

Pasting a fresh token buys weeks, not a fix. Other public homeservers are no escape — MAS is
spreading — and a webhook bridge puts a third party between the watch and the room.

`charts/changedetection/files/matrix_relay.py` owns the session instead: it keeps the refresh
token, mints access tokens on demand, retries once on a mid-send `401`, and resolves the room alias
once. It is stdlib-only, has no DNS name and no TLS, and is reachable only inside the namespace.

**MAS refresh tokens are single use.** Every refresh returns a new one, so the state file is
rewritten atomically after each refresh and lives on its own PVC. That volume is the one piece of
state here that is not disposable: losing it means seeding again with the bot's password.

## How a notification travels

```
watch changes → changedetection → json://changedetection-matrix-relay:8099/notify → Matrix room
```

The URL carries no credential, which is why it lives in `deploy/global-settings.json` as
`application.notification_urls` rather than in each watch: one lever for all watches, and nothing
secret in a watch config. A watch that should stay quiet gets `notification_muted`; a watch that
needs its own wording overrides `notification_title` / `notification_body`.

`notification_format` is stored lower-case (`text`, `markdown`, `html`, `htmlcolor`) — the
capitalised label from the UI is not what the datastore holds.

## Seeding the session

The relay is deployed but silent until it has a session. Unseeded it answers `503` and picks the
state file up on the next request, so seeding needs no restart — and there is a running pod to copy
into, which a crash-looping relay would not give.

1. Check that [status.matrix.org](https://status.matrix.org) is green. `M_UNKNOWN` on a login is
   neither a rate limit (`M_LIMIT_EXCEEDED`) nor bad credentials (`M_FORBIDDEN`) but usually a
   homeserver incident, and everything else hangs off this session.

2. Mint the session. Needs the bot's password and a TTY; nothing is echoed or logged:

   ```bash
   python3 scripts/matrix_relay_seed.py --out ~/matrix_relay.json
   ```

   The seeder logs in with `refresh_token: true` and refuses to write a state file without one —
   that flag is the whole difference to a hand-rolled `/login` call, which yields a token that
   works today and dies later with nothing to renew it from. It prints `device_id`, `user_id` and
   `expires_in_ms`.

   If this fails with `403` / `M_UNSUPPORTED`, password login is closed for the account and the
   device-code grant is the way in (`https://account.matrix.org/oauth2/device`, token endpoint
   `https://account.matrix.org/oauth2/token`, dynamic client registration per MSC2966).

3. Copy it in and send a test message:

   ```bash
   kubectl -n changedetection cp ~/matrix_relay.json \
       "$(kubectl -n changedetection get pod -l app.kubernetes.io/name=changedetection-matrix-relay \
           -o name | cut -d/ -f2):/config/matrix_relay.json"
   kubectl -n changedetection exec deploy/changedetection-matrix-relay -- python3 -c \
       "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8099/health').read())"
   kubectl -n changedetection exec deploy/changedetection-matrix-relay -- python3 -c \
       "import json,urllib.request as u; print(u.urlopen(u.Request(
        'http://127.0.0.1:8099/notify', method='POST',
        headers={'Content-Type': 'application/json'},
        data=json.dumps({'message': 'relay online'}).encode())).read())"
   ```

   Test **through the running relay**, not with a second process. `matrix_relay.py --test` in a
   `kubectl exec` would open its own session on the same state file, and the refresh token it
   spends is single use — the server would keep the dead one and fail silently at the next
   change. `POST /notify` is the same path a notification takes, so a message in the room means
   delivery works end to end. `/health` reports `{"ok": true, "room_id": …}`.

   `--test` remains for a relay that is *not* running, which is how the session is verified
   before it ever reaches the cluster:

   ```bash
   python3 charts/changedetection/files/matrix_relay.py --state ~/matrix_relay.json --test "hi"
   ```

4. Delete the local copy — the tokens in it are live until the session is logged out.

**Arm delivery only once the relay answers `{"ok": true}`.** changedetection does not queue or
retry a notification: while `notification_urls` points at a relay without a session, every POST
comes back 502 and the change is gone, visible only as `last_notification_error` on the watch.
Rolling the relay out and arming the watches are therefore two commits, in that order.

The bot must have **joined** the room; sending into a room it is not in fails with `M_FORBIDDEN`
even though the alias resolves.

## Operating it

- **The liveness probe is TCP, not `/health`.** Health means the Matrix session works, and a
  homeserver incident is not something a restart fixes — probing it would take the only relay pod
  out of service for the duration of somebody else's outage.
- **One replica, `Recreate`.** Two relays refreshing in parallel spend each other's single-use
  refresh token.
- **Only changedetection may reach port 8099.** Anything that can post there can write into a room
  shared with other mappers, so unlike the UI the NetworkPolicy does not open it to the namespace.
- **Moving the instance:** copy the state PVC and nothing needs re-authenticating.
- **Noise is a filter problem, not a delivery problem.** A watch that alerts on a clock or a
  cookie banner is a filter to fix — `watch_audit.py` finds them — not a reason to mute delivery.
