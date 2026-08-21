#!/usr/bin/env python3
"""
cd_export.py — dump changedetection's operational config to a git-committable file.

Why this exists: the state that actually represents work lives in the Docker volume, not in
git. include_filters, fetch_backend, trigger_text, sort_text_alphabetically, notification
bodies, and the hand-built global_ignore_text patterns are all invisible to `git status`.
Losing the volume loses all of it, and data/<area>.json cannot restore any of it — by design,
since the 2026-07-28 ownership split put those fields in changedetection alone.

Two uses:
  backup      — commit the output; it is the only record of the filter work outside the volume
  round trip  — experiment with a filter in the UI, export, open a PR (CONCEPT.md)

Snapshot history is deliberately NOT exported. It is disposable: a re-created watch stores its
first fetch as a baseline and stays quiet until the hours actually change, so nothing is lost
but the record of what the hours used to be.

Read-only. Never writes to changedetection.

Examples:
  python3 scripts/cd_export.py                       # -> export/changedetection.json
  python3 scripts/cd_export.py --out /tmp/cd.json
  python3 scripts/cd_export.py --with-secrets        # keep notification_urls (LOCAL backups only)
"""
import argparse
import concurrent.futures as cf
import glob
import json
import os
import subprocess
import sys

import osm_cd_common as C

# Per-watch fields that are configuration. Everything else the API returns is runtime state
# (last_checked, previous_md5, check_count, viewed, …) and would churn the diff on every run.
WATCH_FIELDS = [
    "url", "title", "tag", "tags", "paused", "processor",
    "include_filters", "subtractive_selectors", "extract_text", "ignore_text",
    "text_should_not_be_present", "trigger_text", "extract_lines_containing",
    "sort_text_alphabetically", "remove_duplicate_lines", "trim_text_whitespace",
    "check_unique_lines", "strip_ignored_lines", "ignore_status_codes",
    "fetch_backend", "webdriver_delay", "webdriver_js_execute_code", "browser_steps",
    "method", "body", "headers", "proxy",
    "time_between_check", "time_between_check_use_default", "time_schedule_limit",
    "conditions", "conditions_match_logic",
    "notification_title", "notification_body", "notification_format",
    "notification_urls", "notification_muted", "filter_failure_notification_send",
]

# Global settings worth keeping. The 25 global_ignore_text patterns took real debugging and
# live nowhere else; the recheck interval is in `requests`.
APP_FIELDS = [
    "global_ignore_text", "global_subtractive_selectors", "ignore_whitespace",
    "render_anchor_tag_content", "empty_pages_are_a_change", "strip_ignored_lines",
    "ignore_status_codes", "fetch_backend", "webdriver_delay",
    "filter_failure_notification_threshold_attempts", "history_snapshot_max_length",
    "notification_title", "notification_body", "notification_format", "notification_urls",
    "scheduler_timezone_default", "base_url", "active_base_url", "tags",
]
REQUEST_FIELDS = ["time_between_check", "timeout", "workers", "jitter_seconds", "default_ua"]

# Never export these, with or without --with-secrets: they are pure credentials.
NEVER = {"api_access_token", "password", "rss_access_token"}
# Exported only with --with-secrets: an Apprise URL carries its credential in the password slot
# (matrixs://:<token>@…), so the default is to redact. The relay URL in use here holds no token,
# but a redacted export is the safe default for whatever is configured.
SECRET = {"notification_urls"}
REDACTED = "<redacted — export with --with-secrets for a local backup>"


def read_global_settings(container, with_secrets=False):
    """Global settings live in the volume, not the API.

    Delivery is configured globally, so `notification_urls` reaches the export through here and
    not only through the per-watch path — it needs the same redaction, or the summary line would
    promise one thing while the file holds another.
    """
    try:
        raw = subprocess.check_output(
            ["docker", "exec", container, "cat", "/datastore/changedetection.json"],
            text=True, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"warning: could not read global settings ({str(e)[:60]})", file=sys.stderr)
        return None
    s = json.loads(raw).get("settings", {})
    # `not v` stays verbatim: an empty list holds no secret, and redacting it into a string would
    # read as "delivery is armed" to anything that checks this field.
    app = {k: (v if with_secrets or k not in SECRET or not v else REDACTED)
           for k, v in s.get("application", {}).items()
           if k in APP_FIELDS and k not in NEVER}
    req = {k: v for k, v in s.get("requests", {}).items() if k in REQUEST_FIELDS}
    return {"application": app, "requests": req}


# Fields that belong in an entry file (CONCEPT.md). Deliberately smaller than the backup
# export: an entry describes what to watch, not every knob changedetection happens to store.
ENTRY_FIELDS = ["url", "filter", "fetch_backend", "sort_text_alphabetically", "trigger_text",
                "subtractive_selectors", "extract_text", "text_should_not_be_present",
                "webdriver_delay", "lang"]


def slugify(name, url):
    import re as _re
    import unicodedata
    base = name or url or "watch"
    base = (base.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
                .replace("Ä", "ae").replace("Ö", "oe").replace("Ü", "ue").replace("ß", "ss"))
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    base = _re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower()
    return base[:60] or "watch"


def write_entries(watches, store_by_uuid, outdir, samples, tag_names=None):
    """One file per watch. The slug is the identity, so it must be stable and unique."""
    os.makedirs(outdir, exist_ok=True)
    seen, written = {}, 0
    for uuid, w in watches.items():
        rec = store_by_uuid.get(uuid, {})
        # The watch title is the specific one: "Pappert (haimbacher str)" rather than the
        # brand name every branch shares.
        name = w.get("title") or rec.get("name") or ""
        if not name:
            from urllib.parse import urlparse
            name = urlparse(w.get("url", "")).netloc or w.get("url", "")
        slug = slugify(name, w.get("url"))
        if slug in seen:                      # two businesses, same name (Aldi Süd, Subway…)
            n = 2
            while f"{slug}-{n}" in seen:
                n += 1
            slug = f"{slug}-{n}"
        seen[slug] = uuid
        filt = (w.get("include_filters") or [None])[0]
        entry = {"schema": 1, "name": name, "url": w.get("url")}
        if filt:
            entry["filter"] = filt
        for f in ENTRY_FIELDS:
            if f in ("url", "filter", "lang"):
                continue
            if w.get(f):
                entry[f] = w[f]
        if rec.get("osm_id") and not str(rec["osm_id"]).startswith("manual/"):
            entry["osm_id"] = rec["osm_id"]
        if rec.get("note"):
            entry["note"] = rec["note"]
        # Tag NAMES, never uuids: a tag uuid means nothing in another instance, which is how
        # a rebuild lost every per-category grouping (fulda-bakery, fulda-doctors, …).
        names = sorted(n for n in ((tag_names or {}).get(t) for t in (w.get("tags") or []))
                       if n and not n.startswith("__"))
        if names:
            entry["tags"] = names
        if samples.get(uuid):
            entry["captured_sample"] = samples[uuid]
        entry["added"] = rec.get("first_seen") or C.today()
        with open(os.path.join(outdir, slug + ".json"), "w") as fh:
            json.dump(entry, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
        written += 1

    # Remove entry files this run did not write. Without this, renaming a slug leaves the old
    # file behind and the next sync sees it as a new watch to create.
    for path in glob.glob(os.path.join(outdir, "*.json")):
        if os.path.splitext(os.path.basename(path))[0] not in seen:
            os.remove(path)
            print(f"  removed stale entry {os.path.basename(path)}", file=sys.stderr)

    # slug -> uuid lock. Needed because the URL is NOT unique: eight pages here back two
    # businesses each (Wiesenmühle restaurant + Biergarten, Vonderau ×2, Maritim ×2), so a
    # URL-keyed sync could not tell which watch an entry owns. Same reason cd_sync has an
    # adoption pool rather than a url->uuid map.
    with open(os.path.join(outdir, ".lock.json"), "w") as fh:
        json.dump(seen, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return written, seen


def main():
    ap = argparse.ArgumentParser(description="Export changedetection config for git")
    ap.add_argument("--out", default="export/changedetection.json")
    ap.add_argument("--split", metavar="DIR",
                    help="also write one entry file per watch (CONCEPT.md model)")
    ap.add_argument("--with-secrets", action="store_true",
                    help="include notification_urls — LOCAL backups only, they contain tokens")
    ap.add_argument("--no-globals", action="store_true")
    ap.add_argument("--base-url",
                    default=os.environ.get("CD_BASE_URL", "http://localhost:5000"),
                    help="changedetection API root; defaults to $CD_BASE_URL, so the same call "
                         "works in-cluster, on the VPS and through a tunnel "
                         "(see scripts/cd_env.sh)")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--container", default="changedetection")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    api = C.CDIO(args.base_url, C.resolve_api_key(args.api_key, args.container))
    uuids = list((api.list() or {}).keys())
    print(f"exporting {len(uuids)} watches …", file=sys.stderr)

    def one(u):
        try:
            return u, api.get(u)
        except Exception as e:
            return u, {"_error": str(e)[:100]}

    watches = {}
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for u, w in ex.map(one, uuids):
            if "_error" in w:
                print(f"  {u[:8]}: {w['_error']}", file=sys.stderr)
                continue
            rec = {}
            for f in WATCH_FIELDS:
                if f not in w:
                    continue
                v = w[f]
                # drop empties so the diff shows only what is actually configured
                if v in (None, "", [], {}, False):
                    continue
                if f in SECRET and not args.with_secrets:
                    v = REDACTED
                rec[f] = v
            watches[u] = rec

    out = {"watches": dict(sorted(watches.items(), key=lambda kv: (
        (kv[1].get("title") or kv[1].get("url") or "").lower(), kv[0])))}
    if not args.no_globals:
        g = read_global_settings(args.container, args.with_secrets)
        if g:
            out["global_settings"] = g

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")

    if args.split:
        store_by_uuid, samples = {}, {}
        # captured_sample is what makes an entry reviewable in a diff without fetching
        for u in watches:
            try:
                import urllib.request
                base = args.base_url.rstrip('/') + '/api/v1/watch/' + u
                req = urllib.request.Request(base + '/history', headers={'x-api-key': api.key})
                hist = json.load(urllib.request.urlopen(req, timeout=20))
                if hist:
                    ts = sorted(hist, key=lambda s: int(s) if str(s).isdigit() else 0)[-1]
                    req = urllib.request.Request(base + '/history/' + ts,
                                                 headers={'x-api-key': api.key})
                    txt = urllib.request.urlopen(req, timeout=20).read().decode('utf-8', 'replace')
                    samples[u] = " ".join(txt.split())[:200]
            except Exception:
                pass
        tag_names = {u: t.get('title') for u, t in (api.tags() or {}).items()}
        n, seen = write_entries(watches, store_by_uuid, args.split, samples, tag_names)
        print(f"wrote {n} entry files to {args.split}/")
        if len(seen) != n:
            print("warning: slug collision", file=sys.stderr)

    filtered = sum(1 for r in watches.values() if r.get("include_filters"))
    # Delivery is configured globally, so a fully armed instance has no per-watch URL at all.
    # Counting only those would report "0 with notifications" on exactly the instance where
    # every watch alerts — the opposite of what this line is read for.
    global_armed = bool(out.get("global_settings", {}).get("application", {})
                        .get("notification_urls"))
    armed = sum(1 for r in watches.values()
                if r.get("notification_urls")
                or (global_armed and not r.get("notification_muted")))
    pats = len((out.get("global_settings", {}).get("application", {})
                .get("global_ignore_text") or []))
    print(f"wrote {args.out}: {len(watches)} watches, {filtered} with a filter, "
          f"{armed} with notifications, {pats} global ignore patterns")
    if not args.with_secrets:
        print("notification_urls redacted, per watch and global.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
