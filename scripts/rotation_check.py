#!/usr/bin/env python3
"""
rotation_check.py — did the hours change, or did the page just reorder itself?

Many pages start their opening-hours table at TODAY. The lines then move on every check, the
diff shows the same times twice (once plain, once behind `(into)`), and the notification looks
like a business changed its hours when nothing happened.

This tells the two apart WITHOUT fetching the page. A direct fetch proves nothing, because the
rotation depends on the time of day it is fetched. Only the stored snapshots show it. Per
snapshot it compares two checksums:

    raw differs + sorted identical   -> rotation, `sort_text_alphabetically` fixes it
    raw and sorted both differ       -> a real change, go and read it

Read-only unless --fix is given, which writes the flag into the matching file in entries/ —
the source the hourly sync reconciles from. A UI edit would be gone at the next run.

Examples:
  python3 scripts/rotation_check.py                    # sweep every watch, only findings
  python3 scripts/rotation_check.py --uuid 2af7778f…   # one watch, with its checksum table
  python3 scripts/rotation_check.py --fix              # set the flag in entries/ for ROTATION
  python3 scripts/rotation_check.py --all              # list the quiet ones too
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import sys
import time
import urllib.request

import entries_sync as E
import osm_cd_common as C

ROTATION, SETTLED, CHANGE, DUPLICATE, QUIET, NEW = (
    "ROTATION", "SETTLED", "CHANGE", "DUPLICATE", "quiet", "new")
ACTIONABLE = (ROTATION, DUPLICATE, CHANGE)

VERDICT_TEXT = {
    ROTATION: "table reorders daily, watch does not sort — set sort_text_alphabetically",
    SETTLED:  "the one alarm that follows switching sorting on; the next check is silent",
    CHANGE:   "the sorted text differs too — a real change, read the diff",
    DUPLICATE: "sorted, but a line appears twice — sorting hides order, not duplication; "
               "narrow the filter (FILTERS.md case 7)",
    QUIET:    "nothing moved",
    NEW:      "no change recorded yet — a watch stores a snapshot when it fires",
}


def md5(s):
    return hashlib.md5(s.encode("utf-8", "replace")).hexdigest()


def sorted_md5(text):
    return md5("\n".join(sorted(text.splitlines())))


def history(base_url, key, uuid, last):
    """[(timestamp, text)] oldest first, at most `last` entries. Empty on any API error."""
    base = base_url.rstrip("/") + "/api/v1/watch/" + uuid

    def _get(path):
        req = urllib.request.Request(base + path, headers={"x-api-key": key})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    try:
        stamps = sorted(json.loads(_get("/history")), key=lambda s: int(s))
    except Exception:
        return []
    out = []
    for ts in stamps[-last:]:
        try:
            out.append((int(ts), _get("/history/" + ts)))
        except Exception:
            pass
    return out


def judge(watch, snaps):
    """-> (verdict, [{ts, raw, sorted, self_sorted, dupes}])"""
    rows = [{"ts": ts,
             "raw": md5(text),
             "sorted": sorted_md5(text),
             "self_sorted": text.splitlines() == sorted(text.splitlines()),
             "dupes": len(text.splitlines()) != len(set(text.splitlines()))}
            for ts, text in snaps]
    if len(rows) < 2:
        return NEW, rows
    if len({r["raw"] for r in rows}) == 1:
        return QUIET, rows
    if len({r["sorted"] for r in rows}) > 1:
        return CHANGE, rows
    # every snapshot holds the same lines in a different order
    if not watch.get("sort_text_alphabetically"):
        return ROTATION, rows
    # sorting is on, so changedetection stores sorted text — anything still moving is either
    # the one re-baseline against the last unsorted snapshot, or a line that repeats.
    if rows[-1]["dupes"]:
        return DUPLICATE, rows
    if rows[-1]["self_sorted"] and not all(r["self_sorted"] for r in rows[:-1]):
        return SETTLED, rows
    return DUPLICATE, rows


def line_diff(snaps):
    """What the last two snapshots differ in, as sets of lines — the question an alarm
    raises. Order is deliberately ignored here: a pure reordering shows up empty."""
    old = set(snaps[-2][1].splitlines())
    new = set(snaps[-1][1].splitlines())
    return sorted(old - new), sorted(new - old)


def entry_for(watch, entries):
    """The entries/ file this watch belongs to: URL first, title where a URL carries two."""
    url = E.norm_url(watch.get("url"))
    hits = [s for s, e in entries.items() if E.norm_url(e.get("url")) == url]
    if len(hits) > 1:
        title = E.norm_name(watch.get("title"))
        named = [s for s in hits if E.norm_name(entries[s].get("name")) == title]
        if named:
            return named[0]
    return hits[0] if len(hits) == 1 else None


def set_sorting(path):
    with open(path) as fh:
        entry = json.load(fh)
    if entry.get("sort_text_alphabetically"):
        return False
    entry["sort_text_alphabetically"] = True
    with open(path, "w") as fh:
        json.dump(entry, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    return True


def day(ts):
    return time.strftime("%d.%m.", time.localtime(ts))


def main():
    ap = argparse.ArgumentParser(
        description="Tell a daily reordering apart from a real change, from the stored "
                    "snapshots alone")
    ap.add_argument("--uuid", action="append", help="check only this watch (repeatable)")
    ap.add_argument("--last", type=int, default=4, metavar="N",
                    help="how many snapshots per watch to compare (default: %(default)s)")
    ap.add_argument("--all", action="store_true",
                    help="also list watches with nothing to report")
    ap.add_argument("--fix", action="store_true",
                    help="write sort_text_alphabetically into the entries/ file of every "
                         "ROTATION finding (the source; a UI edit is undone by the sync)")
    ap.add_argument("--entries", default="entries")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--base-url",
                    default=os.environ.get("CD_BASE_URL", "http://localhost:5000"),
                    help="changedetection API root; defaults to $CD_BASE_URL "
                         "(see scripts/cd_env.sh)")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--container", default="changedetection")
    args = ap.parse_args()

    api = C.CDIO(args.base_url, C.resolve_api_key(args.api_key, args.container))
    entries = E.load_entries(args.entries)
    uuids = args.uuid or list((api.list() or {}).keys())
    if not uuids:
        sys.exit("no watches found")
    print(f"comparing the last {args.last} snapshots of {len(uuids)} "
          f"watch{'es' if len(uuids) != 1 else ''} (no page is fetched) …", file=sys.stderr)

    def one(u):
        try:
            w = api.get(u)
        except Exception as e:
            return {"uuid": u, "name": u[:8], "verdict": CHANGE, "rows": [],
                    "why": f"API error: {e}", "entry": None}
        snaps = history(args.base_url, api.key, u, args.last)
        verdict, rows = judge(w, snaps)
        gone, came = line_diff(snaps) if len(snaps) > 1 else ([], [])
        return {"uuid": u, "name": w.get("title") or w.get("url", "")[:40],
                "url": w.get("url", ""), "verdict": verdict, "rows": rows,
                "why": VERDICT_TEXT[verdict], "entry": entry_for(w, entries),
                "gone": gone, "came": came}

    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        found = list(ex.map(one, uuids))
    order = {ROTATION: 0, DUPLICATE: 1, CHANGE: 2, SETTLED: 3, QUIET: 4, NEW: 5}
    found.sort(key=lambda r: (order[r["verdict"]], r["name"].lower()))

    # A CHANGE is what a working watch does, so it is not a finding on its own; it is
    # printed when a run is aimed at one watch, which is the triage-an-alarm case.
    shown = found if (args.all or args.uuid) else [
        r for r in found if r["verdict"] in (ROTATION, DUPLICATE, SETTLED)]
    if args.json:
        print(json.dumps(shown, ensure_ascii=False, indent=2))
    else:
        for r in shown:
            print(f"\n{r['verdict']:9} {r['name']}")
            print(f"          {r['why']}")
            if r["entry"]:
                print(f"          entries/{r['entry']}.json")
            for row in r["rows"]:
                print(f"          {day(row['ts'])}  raw {row['raw'][:8]}  "
                      f"sorted {row['sorted'][:8]}"
                      f"{'  (already sorted)' if row['self_sorted'] else ''}"
                      f"{'  (repeated line)' if row['dupes'] else ''}")
            if r["verdict"] == CHANGE and (args.uuid or args.all):
                for line in r["gone"]:
                    print(f"          - {line.strip()[:100]}")
                for line in r["came"]:
                    print(f"          + {line.strip()[:100]}")
        counts = {}
        for r in found:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        print(f"\n{len(found)} watch{'es' if len(found) != 1 else ''}: "
              f"{counts.get(ROTATION, 0)} rotating unsorted, "
              f"{counts.get(DUPLICATE, 0)} sorted but still moving, "
              f"{counts.get(SETTLED, 0)} settling after sorting was switched on, "
              f"{counts.get(CHANGE, 0)} with a real change, "
              f"{counts.get(QUIET, 0) + counts.get(NEW, 0)} unchanged.")
        if not shown:
            print("Nothing to act on. A watch that fires again is a real change: "
                  "rotation_check.py --uuid <uuid> shows the differing lines.")

    if args.fix:
        changed = []
        for r in found:
            if r["verdict"] != ROTATION:
                continue
            if not r["entry"]:
                print(f"no entry file matches {r['name']} — fix it by hand", file=sys.stderr)
                continue
            if set_sorting(os.path.join(args.entries, r["entry"] + ".json")):
                changed.append(r["entry"])
        if changed:
            print(f"\nwrote sort_text_alphabetically into {len(changed)} entr"
                  f"{'y' if len(changed) == 1 else 'ies'}: {', '.join(changed)}\n"
                  f"Commit them; the sync applies it within the hour. Expect exactly one more "
                  f"alarm per watch — the first sorted snapshot runs against the last "
                  f"unsorted one.")
    return 1 if any(r["verdict"] in (ROTATION, DUPLICATE) for r in found) else 0


if __name__ == "__main__":
    sys.exit(main())
