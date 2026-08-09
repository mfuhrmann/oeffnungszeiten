#!/usr/bin/env python3
"""Write the audit verdict into the entry files as a tag, so it shows up in the web UI.

RED/AMBER/green exists only while watch_audit.py runs — changedetection stores no such field
and has no per-watch notes field to put an explanation in. The one thing the UI *does* show
and filter by is tags, so that is where the verdict goes:

    prüfen      the audit found something and no entry explains it -> someone must look
    geprüft-ok  the audit found something, but the entry's note says why that is correct

Green watches carry neither tag. Category tags (fulda-bakery, …) are never touched.

The tags live in the entry files, not in changedetection, because entries_sync enforces tags
from git — a tag set directly in the UI would be reverted on the next sync. Run this, then
sync:

    python3 scripts/audit_tags.py --base-url http://localhost:5000 --api-key "$KEY"
    python3 scripts/entries_sync.py --lock entries/.lock.<instance>.json --apply
"""
import argparse
import json
import os
import subprocess
import sys

MANAGED = ("prüfen", "geprüft-ok")
HERE = os.path.dirname(os.path.abspath(__file__))
ENTRIES = os.path.join(os.path.dirname(HERE), "entries")


def audit(base_url, api_key, lock_path):
    """Run the audit and return {slug: (verdict, [issues])}."""
    cmd = [sys.executable, os.path.join(HERE, "watch_audit.py"), "--json"]
    if base_url:
        cmd += ["--base-url", base_url]
    if api_key:
        cmd += ["--api-key", api_key]
    out = subprocess.run(cmd, capture_output=True, text=True)
    # exit 1 means "at least one RED", which is a finding, not a failure — only an empty
    # stdout tells us the run itself broke.
    if not out.stdout.strip():
        sys.exit(f"watch_audit failed:\n{out.stderr[-800:]}")
    by_uuid = {u: s for s, u in json.load(open(lock_path)).items()}
    return {by_uuid[r["uuid"]]: (r["verdict"], r["issues"])
            for r in json.loads(out.stdout) if r["uuid"] in by_uuid}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entries", default=ENTRIES)
    ap.add_argument("--lock", required=True, help="the instance lock, e.g. entries/.lock.k3s.json")
    ap.add_argument("--base-url")
    ap.add_argument("--api-key")
    ap.add_argument("--apply", action="store_true", help="write the entry files")
    args = ap.parse_args()

    verdicts = audit(args.base_url, args.api_key, args.lock)
    changed = counts = 0
    summary = {"prüfen": [], "geprüft-ok": []}

    for slug, (verdict, issues) in sorted(verdicts.items()):
        path = os.path.join(args.entries, slug + ".json")
        if not os.path.exists(path):
            continue
        entry = json.load(open(path))
        tags = [t for t in (entry.get("tags") or []) if t not in MANAGED]

        if verdict != "green":
            want = "geprüft-ok" if entry.get("note") else "prüfen"
            tags.append(want)
            summary[want].append(f"{slug}: {'; '.join(issues)[:70]}")
            counts += 1

        if tags != (entry.get("tags") or []):
            changed += 1
            if args.apply:
                entry["tags"] = tags
                json.dump(entry, open(path, "w"), indent=1, ensure_ascii=False, sort_keys=True)
                open(path, "a").write("\n")

    for tag in MANAGED:
        if summary[tag]:
            print(f"\n{tag} ({len(summary[tag])})")
            for line in summary[tag]:
                print("  " + line)
    print(f"\n{len(verdicts)} watches · {counts} not green · {changed} entry file(s) "
          f"{'updated' if args.apply else 'would change'}")
    if not args.apply:
        print("(dry run — pass --apply, then run entries_sync --apply)")


if __name__ == "__main__":
    main()
