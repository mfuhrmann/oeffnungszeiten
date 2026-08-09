#!/usr/bin/env python3
"""
validate_entries.py — check entry files before they reach changedetection.

Runs in CI on every pull request, and locally whenever you like. Two levels:

  structural (always)  — schema, required fields, slug matches filename, URL shape,
                         no duplicate slug→URL confusion, filter looks like a filter
  live (--live)        — fetch the page and confirm the filter still captures opening hours

The live level is the one that matters. A watch pointed at a page with no hours is silent
forever and looks perfectly healthy; that failure hid 77 blind watches here before anyone
noticed. Catching it in review is much cheaper than catching it in an audit six weeks later.

Exit code 1 if any entry fails. Warnings do not fail the run.

Examples:
  python3 scripts/validate_entries.py                      # structural, all entries
  python3 scripts/validate_entries.py --live               # also fetch every page (slow)
  python3 scripts/validate_entries.py --live --only a.json b.json   # just the changed ones
"""
import argparse
import glob
import json
import os
import re
import sys
from urllib.parse import urlparse

REQUIRED = ["schema", "name", "url"]
KNOWN = {"schema", "name", "url", "filter", "fetch_backend", "sort_text_alphabetically",
         "trigger_text", "subtractive_selectors", "extract_text",
         "text_should_not_be_present", "webdriver_delay", "tag", "lang", "osm_id",
         "note", "captured_sample", "added", "tags"}
BACKENDS = {"system", "html_requests", "html_webdriver"}
FILTER_PREFIX = ("xpath:", "xpath1:", "json:", "jq:", "css:", "//", "#", ".", "/")
# Absolute XPaths break on the next site edit. This exact shape blinded a watch here once and
# was removed in 7caa701; the UI Visual Selector still emits it, so it is worth rejecting.
ABSOLUTE_XPATH = re.compile(r'^xpath1?:/html(/|\[)|^/html(/|\[)')
BRITTLE = re.compile(r'elementor-element-|fl-(?:node|icon|module|rich)-|hype-obj-'
                     r'|[0-9a-f]{8,}|css-[a-z0-9]{5,}')

# A long hex string is page-builder output when it names a class or an id, and a stable
# business key when it names a record: tredy's store finder pins the Fulda shop with
# data-store-id="0193073ab79d7cfbbd6281eed32c6db3", which identifies the branch and survives
# a redesign. Only the attribute tells the two apart, so exempt data-* selectors.
STABLE_DATA_ATTR = re.compile(r'@data-[a-z-]+\s*=')


def check_structure(path, e, slugs):
    errs, warns = [], []
    slug = os.path.splitext(os.path.basename(path))[0]

    for f in REQUIRED:
        if f not in e:
            errs.append(f"missing required field '{f}'")
    if e.get("schema") != 1:
        errs.append(f"unsupported schema {e.get('schema')!r} (expected 1)")

    unknown = set(e) - KNOWN
    if unknown:
        warns.append(f"unknown field(s): {', '.join(sorted(unknown))}")

    url = e.get("url", "")
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        errs.append(f"url must be http(s): {url!r}")
    if not p.netloc:
        errs.append(f"url has no host: {url!r}")

    if slug in slugs:
        errs.append(f"duplicate slug (also {slugs[slug]})")
    slugs[slug] = path

    be = e.get("fetch_backend")
    if be and be not in BACKENDS:
        errs.append(f"fetch_backend {be!r} not one of {sorted(BACKENDS)}")

    filt = e.get("filter")
    if filt:
        # A bare token is a valid CSS type selector — `address` is an HTML5 element and
        # `app-opening-hours-table` is a custom element, both legitimate filters.
        bare_css = re.match(r'^[a-zA-Z][\w-]*$', filt)
        if not filt.startswith(FILTER_PREFIX) and not bare_css:
            warns.append(f"filter has no recognised prefix: {filt[:40]!r}")
        if ABSOLUTE_XPATH.search(filt):
            errs.append("absolute XPath — breaks on the next site edit; anchor on text, "
                        "a stable class, or an id instead")
        if BRITTLE.search(filt) and not STABLE_DATA_ATTR.search(filt):
            warns.append("selector contains a generated class/id; it will change when the "
                         "site is next edited")
    else:
        warns.append("no filter — the whole page is watched, so any banner will alert")

    if not e.get("captured_sample"):
        # An error, not a warning: CI does not fetch the page, so this string is the only
        # evidence anyone has that the filter grabbed opening hours rather than a news box.
        errs.append("no captured_sample — nothing in the diff shows what the filter captures")
    return errs, warns


def check_live(e, lang_default="de", browser_ws=None):
    """Fetch the page and confirm the filter still yields opening hours.

    An entry asking for `fetch_backend: html_webdriver` says its hours only exist after
    JavaScript runs, so checking it with a plain fetch proves nothing — 40 of 277 entries here
    are in that class and used to pass on a shrug ("may be anti-bot or JS-only"). With a browser
    reachable (`--browser-ws`, or a service container in CI) those are rendered and checked like
    any other; without one they still only warn, because a contributor without Docker should not
    be blocked from proposing one.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import filter_wizard as W
    import hours_lang as L
    import lxml.html

    errs, warns = [], []
    needs_browser = e.get("fetch_backend") == "html_webdriver"
    if needs_browser and browser_ws:
        import cdp_render
        try:
            html = cdp_render.render(e["url"], ws_url=browser_ws)
        except Exception as exc:
            warns.append(f"render failed ({str(exc)[:60]}) — check the sample by eye")
            return errs, warns
    else:
        if needs_browser:
            warns.append("needs a browser (html_webdriver) and none was given — the filter is "
                         "NOT verified; pass --browser-ws to check it")
        try:
            html = W.fetch_plain(e["url"])
        except Exception as exc:
            warns.append(f"could not fetch ({str(exc)[:60]}) — may be anti-bot or JS-only")
            return errs, warns

    lang = e.get("lang", lang_default)
    filt = e.get("filter", "")
    if filt.startswith("json:"):
        warns.append("JSON-LD filter not evaluated offline; check the sample by eye")
        return errs, warns
    if not filt:
        return errs, warns

    xp = filt.split(":", 1)[1] if filt.startswith(("xpath:", "xpath1:")) else None
    if not xp:
        warns.append("only XPath filters are checked offline")
        return errs, warns
    try:
        doc = W.strip_noise(lxml.html.fromstring(html))
        sel = doc.xpath(xp)
    except Exception as exc:
        errs.append(f"filter is not valid XPath / did not evaluate: {str(exc)[:70]}")
        return errs, warns
    if not sel:
        if needs_browser and browser_ws:
            # The page was rendered exactly as changedetection will render it, so an empty
            # match is not a "maybe" any more.
            errs.append("filter matched nothing in the RENDERED page — the filter is wrong")
        else:
            warns.append("filter matched nothing in a plain fetch — expected if the page needs "
                         "a browser, otherwise the filter is wrong")
        return errs, warns

    text = L.clean(" ".join(W.txt_of(x) for x in sel))
    if not L.time_matches(text):
        errs.append("filter captures no time at all — this watch could never fire")
    elif not L.weekdays_any(text, lang):
        warns.append("times captured but no weekday named — may not be an hours block")
    return errs, warns


def main():
    ap = argparse.ArgumentParser(description="Validate entry files")
    ap.add_argument("--entries", default="entries")
    ap.add_argument("--live", action="store_true", help="also fetch each page")
    ap.add_argument("--only", nargs="*", help="validate just these files (CI: changed files)")
    ap.add_argument("--browser-ws", default=os.environ.get("BROWSER_WS"), metavar="WS_URL",
                    help="browser for html_webdriver entries, e.g. ws://localhost:3000 "
                         "(with --live). Without it those entries are only warned about.")
    ap.add_argument("--browser-wait", type=int, default=60, metavar="SECONDS",
                    help="wait this long for the browser to accept connections (CI service "
                         "containers race with Chrome's startup)")
    args = ap.parse_args()

    browser_ws = None
    if args.live and args.browser_ws:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import cdp_render
        product = cdp_render.wait_for_browser(args.browser_ws, timeout=args.browser_wait)
        if product:
            browser_ws = args.browser_ws
            print(f"browser: {product} at {args.browser_ws}")
        else:
            # Do not fail the run: a missing browser must not turn into a red PR for someone
            # whose entry needs no rendering at all.
            print(f"warn: no browser at {args.browser_ws} — html_webdriver entries stay "
                  f"unverified")

    paths = args.only or sorted(glob.glob(os.path.join(args.entries, "*.json")))
    paths = [p for p in paths if not os.path.basename(p).startswith(".")]
    if not paths:
        print("no entry files to validate")
        return 0

    slugs, failed, warned = {}, 0, 0
    for path in paths:
        try:
            with open(path) as fh:
                e = json.load(fh)
        except Exception as exc:
            print(f"FAIL {path}: not valid JSON — {exc}")
            failed += 1
            continue
        errs, warns = check_structure(path, e, slugs)
        if args.live and not errs:
            le, lw = check_live(e, browser_ws=browser_ws)
            errs += le
            warns += lw
        for m in errs:
            print(f"FAIL {os.path.basename(path)}: {m}")
        for m in warns:
            print(f"warn {os.path.basename(path)}: {m}")
        failed += 1 if errs else 0
        warned += 1 if warns and not errs else 0

    print(f"\n{len(paths)} entries · {failed} failed · {warned} with warnings")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
