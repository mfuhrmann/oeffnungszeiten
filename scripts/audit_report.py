#!/usr/bin/env python3
"""
audit_report.py — post the watches that are actually broken into Matrix.

Three states send no notification of their own, verified in the running image: a fetch error
only sets `last_error` (worker.py), an empty filter result is swallowed because
`empty_pages_are_a_change` is false (processor.py), and an over-wide `global_ignore_text` stops
the checksum from moving at all. A watch in any of them looks exactly like a watch on a page
that simply has not changed.

`watch_audit.py`'s RED verdict covers all three, plus a filter that was blind from the start.
AMBER does not: it is the known and explained material — two watches deliberately sharing a
page, a practice that only publishes two weekdays — which is why this reports RED only and
therefore stays quiet on its own. Nothing is sent when there is nothing to report.

Runs from the sync CronJob's image and reads CD_BASE_URL and CHANGEDETECTION_API_KEY like every
other script here.

    python3 scripts/audit_report.py --dry-run    # print what would be sent
    python3 scripts/audit_report.py --relay http://changedetection-matrix-relay:8099/notify
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def audit(base_url, api_key):
    """Run watch_audit.py and return its rows. Exit 1 there means 'found a RED', not a crash."""
    cmd = [sys.executable, os.path.join(HERE, "watch_audit.py"), "--json"]
    if base_url:
        cmd += ["--base-url", base_url]
    if api_key:
        cmd += ["--api-key", api_key]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if not out.stdout.strip():
        sys.exit(f"watch_audit failed:\n{out.stderr[-800:]}")
    return json.loads(out.stdout)


def compose(rows):
    """-> (title, body) for the relay, or None when there is nothing worth sending."""
    red = [r for r in rows if r.get("verdict") == "red"]
    if not red:
        return None
    title = (f"{len(red)} Watch braucht Aufmerksamkeit" if len(red) == 1
             else f"{len(red)} Watches brauchen Aufmerksamkeit")
    lines = []
    for r in sorted(red, key=lambda r: r.get("name") or ""):
        # The relay treats a leading "Label: <url>" line as a header link, so the URL goes last
        # on its own line and the reason above it.
        lines.append(f"(changed) {r.get('name')}: {'; '.join(r.get('issues') or [])}")
        lines.append(f"Webseite: {r.get('url')}")
    lines.append(f"(von {len(rows)} Watches insgesamt)")
    return title, "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Report RED watches into Matrix via the relay")
    ap.add_argument("--base-url", default=os.environ.get("CD_BASE_URL", "http://localhost:5000"))
    ap.add_argument("--api-key", default=os.environ.get("CHANGEDETECTION_API_KEY"))
    ap.add_argument("--relay", default=os.environ.get(
        "RELAY_URL", "http://changedetection-matrix-relay:8099/notify"))
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending")
    args = ap.parse_args()

    rows = audit(args.base_url, args.api_key)
    message = compose(rows)
    if message is None:
        print(f"{len(rows)} watches, no RED — nothing sent")
        return
    title, body = message

    if args.dry_run:
        print(title)
        print(body)
        return

    payload = json.dumps({"title": title, "message": body}).encode()
    req = urllib.request.Request(args.relay, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"sent: HTTP {r.status} {r.read().decode()[:200]}")


if __name__ == "__main__":
    main()
