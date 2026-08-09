#!/usr/bin/env python3
"""
entries_sync.py — reconcile changedetection against entries/*.json (CONCEPT.md).

The entry files are the source of truth. Add a file -> new watch. Delete a file -> the watch
is deleted. Edit a file -> the watch is updated. That single mechanism replaces gone-detection,
the delete queue, adoption pools and absence records.

Git is authoritative here, which is a deliberate reversal of the 2026-07-28 ownership rule:
back then filters lived only in changedetection because the datastore could not express them.
Entry files can, so a second source of truth is no longer needed. The cost is that a filter
tweaked in the UI is reverted on the next sync — run `cd_export.py --split entries` to turn a
UI experiment into a commit instead.

Identity is the **slug** (the filename), mapped to a changedetection uuid by entries/.lock.json.
It cannot be the URL: eight pages here back two businesses each (Wiesenmühle restaurant +
Biergarten, Vonderau Museum ×2, Maritim ×2), so a URL-keyed sync could not tell which watch an
entry owns.

Changes nothing without --apply. Deletion is always targeted by uuid, never by tag — a tag
filter once wiped all 73 watches.

Examples:
  python3 scripts/entries_sync.py                     # plan only
  python3 scripts/entries_sync.py --apply
  python3 scripts/entries_sync.py --apply --prune     # also delete watches no entry claims
"""
import argparse
import concurrent.futures as cf
import glob
import json
import os
import sys

import osm_cd_common as C

# Entry field -> changedetection field. Everything listed is ENFORCED: if the entry and the
# watch disagree, the watch loses.
FIELD_MAP = {
    "url": "url",
    "fetch_backend": "fetch_backend",
    "sort_text_alphabetically": "sort_text_alphabetically",
    "trigger_text": "trigger_text",
    "subtractive_selectors": "subtractive_selectors",
    "extract_text": "extract_text",
    "text_should_not_be_present": "text_should_not_be_present",
    "webdriver_delay": "webdriver_delay",
    "name": "title",
}


def load_entries(d):
    out = {}
    for path in sorted(glob.glob(os.path.join(d, "*.json"))):
        slug = os.path.splitext(os.path.basename(path))[0]
        if slug.startswith("."):
            continue
        with open(path) as fh:
            out[slug] = json.load(fh)
    return out


def resolve_tags(api, names, cache, create=True):
    """Tag names -> uuids in THIS instance, creating any that do not exist yet.

    Tags are separate objects referenced by uuid, so the uuid differs per instance. Entries
    therefore carry names, and each instance maps them to its own uuids.
    """
    out = []
    for name in names:
        if name not in cache:
            if not create:
                continue          # plan mode: report against what exists, invent nothing
            cache[name] = api.tag_create(name)
            print(f"created tag {name} -> {str(cache[name])[:8]}")
        if cache.get(name):
            out.append(cache[name])
    return sorted(out)


def desired(entry, tag_uuids=None):
    """The watch fields this entry asserts."""
    want = {}
    if tag_uuids is not None:
        want["tags"] = tag_uuids
    for ef, wf in FIELD_MAP.items():
        if ef in entry:
            want[wf] = entry[ef]
    want["include_filters"] = [entry["filter"]] if entry.get("filter") else []
    return want


def differs(want, live):
    out = {}
    for k, v in want.items():
        cur = live.get(k)
        if k in ("include_filters", "trigger_text", "subtractive_selectors", "extract_text",
                 "text_should_not_be_present"):
            cur = [x for x in (cur or []) if str(x).strip()]
            v = [x for x in (v or []) if str(x).strip()]
        if k == "tags":
            cur = sorted(cur or [])
            v = sorted(v or [])
        if k == "fetch_backend":
            cur = cur or "system"
            v = v or "system"
        if cur != v:
            out[k] = (cur, v)
    return out


def main():
    ap = argparse.ArgumentParser(description="Reconcile changedetection against entries/")
    ap.add_argument("--entries", default="entries")
    ap.add_argument("--lock", help="slug->uuid map (default: <entries>/.lock.json). One lock "
                                   "per changedetection INSTANCE — a local cluster and "
                                   "production hold different uuids for the same entries, so "
                                   "sharing a lock orphans one of them.")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--prune", action="store_true",
                    help="delete watches that no entry claims (needs --apply)")
    ap.add_argument("--base-url", default="http://localhost:5000")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--container", default="changedetection")
    ap.add_argument("--tag", default="fulda", help="tag for newly created watches")
    ap.add_argument("--interval-days", type=int, default=3)
    args = ap.parse_args()

    api = C.CDIO(args.base_url, C.resolve_api_key(args.api_key, args.container))
    entries = load_entries(args.entries)
    lock_path = args.lock or os.path.join(args.entries, ".lock.json")
    lock = json.load(open(lock_path)) if os.path.exists(lock_path) else {}
    live_uuids = set((api.list() or {}).keys())

    def get(u):
        try:
            return u, api.get(u)
        except Exception:
            return u, None
    live = {}
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        for u, w in ex.map(get, sorted(live_uuids)):
            if w:
                live[u] = w

    tag_cache = {t.get("title"): u for u, t in (api.tags() or {}).items() if t.get("title")}
    _resolved = {}

    def tags_for(entry):
        names = entry.get("tags") or []
        if not names:
            return None
        key = tuple(names)
        if key not in _resolved:
            _resolved[key] = resolve_tags(api, names, tag_cache, create=args.apply)
        return _resolved[key]

    create, update, delete = [], [], []
    for slug, entry in entries.items():
        uuid = lock.get(slug)
        if not uuid or uuid not in live:
            create.append((slug, entry))
            continue
        d = differs(desired(entry, tags_for(entry)), live[uuid])
        if d:
            update.append((slug, uuid, d))

    claimed = {lock[s] for s in entries if s in lock}
    for slug, uuid in lock.items():
        if slug not in entries and uuid in live:
            delete.append((slug, uuid))
    orphans = [u for u in live if u not in claimed and
               u not in {uu for _s, uu in delete}]

    print(f"entries {len(entries)} · live watches {len(live)}")
    print(f"create {len(create)} · update {len(update)} · delete {len(delete)} · "
          f"unclaimed {len(orphans)}")
    for slug, entry in create[:20]:
        print(f"  + {slug}  {entry.get('url','')[:60]}")
    uuid_to_name = {u: t.get("title") for u, t in (api.tags() or {}).items()}
    for slug, _u, d in update[:20]:
        for k, (cur, new) in d.items():
            if k == "tags":     # uuids are unreadable; the entry speaks in names
                cur = [uuid_to_name.get(x, x[:8]) for x in (cur or [])]
                new = [uuid_to_name.get(x, x[:8]) for x in (new or [])] or \
                      (entries[slug].get("tags") or [])
            print(f"  ~ {slug}  {k}: {str(cur)[:38]!r} -> {str(new)[:38]!r}")
    for slug, uuid in delete[:20]:
        print(f"  - {slug}  ({uuid[:8]})")
    for u in orphans[:10]:
        print(f"  ? unclaimed {u[:8]}  {live[u].get('title') or live[u].get('url','')[:50]}")

    if not args.apply:
        print("\n(plan only — pass --apply)")
        return 0

    for slug, entry in create:
        uuid = api.add(entry["url"], args.tag, args.interval_days)
        api.update(uuid, **desired(entry, tags_for(entry)))
        lock[slug] = uuid
        print(f"created {slug} -> {uuid[:8]}")
    for slug, uuid, d in update:
        api.update(uuid, **{k: v for k, (_c, v) in d.items()})
        print(f"updated {slug}")
    for slug, uuid in delete:
        api.delete(uuid)          # ALWAYS targeted by uuid, never by tag
        lock.pop(slug, None)
        print(f"deleted {slug}")
    if args.prune:
        for u in orphans:
            api.delete(u)
            print(f"pruned unclaimed {u[:8]}")

    with open(lock_path, "w") as fh:
        json.dump(lock, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("lock updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
