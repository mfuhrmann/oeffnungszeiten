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


DOCS = ("https://github.com/mfuhrmann/oeffnungszeiten/blob/main/docs/"
        "notifications.md#was-eine-nachricht-verlangt")

# A finding names what is broken; a reader still has to know what to do about it. The audit
# already distinguishes the causes, so each gets its first move here — the doc link at the end
# carries the long version.
ACTIONS = (
    ("fetch error: Error - 403",
     "Erst die Kennung: antwortet die Seite von der VPS mit aktuellem Chrome-UA mit 200, ist "
     "requests.default_ua veraltet. Sonst von hier und von der VPS vergleichen - nur die VPS "
     "403 heisst Rechenzentrums-Sperre, Watch entfernen."),
    ("fetch error: Error - 404",
     "Die Seite gibt es nicht mehr. Nachfolger suchen, sonst Watch entfernen."),
    ("fetch error: Error - 5",
     "Serverfehler beim Anbieter. Einen Durchgang abwarten; bleibt es, andere Seite suchen."),
    ("fetch error",
     "Abruf scheitert. Seite von hier und von der VPS mit gleicher Kennung testen."),
    ("no opening hours on this page at all",
     "Die Seite fuehrt keine Zeiten mehr. Bessere Seite suchen (/kontakt, /oeffnungszeiten, "
     "Filialseite), sonst Watch entfernen - blind meldet er nie etwas."),
    ("times found but no weekday named",
     "Der Filter fasst nur einen Teil des Blocks. Neue Kandidaten: filter_wizard.py --uuid"),
    ("weekday(s) captured",
     "Die restlichen Tage stehen im Nachbarelement. Gemeinsamen Vorfahren waehlen: "
     "filter_wizard.py --uuid"),
    ("every day shows the same",
     "Das ist die Theme-Vorgabe, nicht die Zeit des Betriebs. Sichtbare Zeiten auf der Seite "
     "suchen."),
    ("the same hours are captured",
     "Der Filter sitzt zu hoch und trifft mehrere Kopien. Engeres Element waehlen."),
    ("discarded by the global ignore",
     "global_ignore_text verschluckt echte Zeilen. Muster in deploy/global-settings.json engen."),
    ("captures text identical to",
     "Zwei Watches fangen denselben Text - einer haengt am falschen Element. Je Betrieb ein "
     "eigener Anker, etwa auf die Filialadresse (FILTERS.md Fall 12)."),
    ("no filter",
     "Ohne Filter alarmiert jedes Banner. Filter setzen: filter_wizard.py --uuid"),
    ("changed", "Verdaechtig haeufig. Diff ansehen: bewegt sich dort eine Uhr statt der Zeiten?"),
    ("paused", "Watch ist pausiert und prueft nichts."),
)


def action_for(issues):
    """The first move for a finding, or None when nothing matches."""
    for issue in issues:
        for needle, what in ACTIONS:
            if needle in issue:
                return what
    return None


def compose(rows):
    """-> (title, body) for the relay, or None when there is nothing worth sending."""
    red = [r for r in rows if r.get("verdict") == "red"]
    if not red:
        return None
    title = (f"{len(red)} Watch braucht Aufmerksamkeit" if len(red) == 1
             else f"{len(red)} Watches brauchen Aufmerksamkeit")
    # Group by what has to be done, so one 403 wave costs one instruction line and not one per
    # watch. Findings without a known move come last, under a heading that says so.
    lines, last = [], object()
    for r in sorted(red, key=lambda r: (action_for(r.get("issues") or []) or "zzz",
                                        r.get("name") or "")):
        what = action_for(r.get("issues") or [])
        if what != last:
            lines.append(f"Zu tun: {what}" if what else
                         "Zu tun: unbekannter Befund - Diff und Filter von Hand ansehen.")
            last = what
        # The relay treats a leading "Label: <url>" line as a header link, so the URL goes last
        # on its own line and the reason above it.
        lines.append(f"(changed) {r.get('name')}: {'; '.join(r.get('issues') or [])}")
        lines.append(f"Webseite: {r.get('url')}")
    lines.append(f"Was die Befunde bedeuten: {DOCS}")
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
