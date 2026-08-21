#!/usr/bin/env python3
"""
no_watch.py — the block list: pages that are deliberately not watched, and when to look again.

`entries/` says which pages are watched. `no-watch.json` says which pages were looked at and
found to have nothing worth watching, and why. A page belongs in exactly one of them. Without
the second list every pass re-examines the same shop that has published no hours since 2026.

Keyed by the page, not by the map object: one page can carry several businesses, and whether
OSM knows them is a different question from whether the page publishes hours.

The reasons fall into two kinds, and `wieder_pruefen` carries the difference:

  * a property of the **business** — `no-hours-on-page`, `appointment-only`, `social-only`,
    `delivery-platform-only`, `today-only`, `site-unreachable` — gets a date.
    The question at that date is not "can we fetch it now" but **"has this business got its own
    page yet"**. That matters for the platform cases: a delivery microsite publishes delivery
    windows, and a social profile hides its hours behind a login wall — neither is fixed by
    fetching from somewhere else.
  * a property of **our instance** — `anti-bot`, `datacenter-block` — gets `on-relocation`.
    Time changes nothing there; the block is the same tomorrow. What changes it is the instance
    moving to a residential address, or the pinned user agent being bumped.
  * `never` is for what cannot move: `always-open` means the hours are known and constant.

What is **not** in this list is work nobody has done yet — a chain whose branch link was never
found, a `website` tag pointing at the wrong company, a page our own discovery picked badly.
Those keep counting as open.

    python3 scripts/no_watch.py                    # summary per reason
    python3 scripts/no_watch.py --faellig          # what is due today
    python3 scripts/no_watch.py --faellig --am 2027-03-01
    python3 scripts/no_watch.py --standortwechsel  # what a move would put back in play
"""
import argparse
import collections
import datetime
import json


def laden(pfad):
    with open(pfad, encoding="utf-8") as fh:
        return json.load(fh)["records"]


def main():
    ap = argparse.ArgumentParser(description="the block list")
    ap.add_argument("--datei", default="no-watch.json")
    ap.add_argument("--faellig", action="store_true", help="only what is due")
    ap.add_argument("--am", default=datetime.date.today().isoformat(), metavar="YYYY-MM-DD")
    ap.add_argument("--standortwechsel", action="store_true",
                    help="what an instance on a residential connection would put back in play")
    args = ap.parse_args()
    recs = laden(args.datei)

    if args.standortwechsel:
        treffer = [r for r in recs if r.get("recheck") == "on-relocation"]
    elif args.faellig:
        treffer = [r for r in recs
                   if (r.get("recheck") or "").startswith("20")
                   and r["recheck"] <= args.am]
    else:
        for grund, n in collections.Counter(r["reason"] for r in recs).most_common():
            print(f"{n:4}  {grund}")
        faellig = sum(1 for r in recs if (r.get("recheck") or "").startswith("20")
                      and r["recheck"] <= args.am)
        ereignis = sum(1 for r in recs if r.get("recheck") == "on-relocation")
        print(f"\n{len(recs)} pages · {faellig} due on {args.am} · "
              f"{ereignis} waiting on a change of address")
        return

    for r in sorted(treffer, key=lambda x: (x.get("recheck", ""), x.get("name", ""))):
        print(f"{r.get('recheck',''):<20} {r.get('reason',''):<18} "
              f"{(r.get('name') or '?')[:30]:<30} {r.get('url','')[:44]}")
    print(f"\n{len(treffer)} pages")


if __name__ == "__main__":
    main()
