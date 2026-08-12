#!/usr/bin/env python3
"""
hours_filter.py — narrow watches to just their opening-hours block.

changedetection triggers on ANY text change on a page. To avoid false alerts
(owner names, banners, captchas, counters) this tool finds the opening-hours
element on each unfiltered watch and applies a CSS/XPath include-filter so only
real hours edits trigger.

How it works:
  Phase A (plain fetch)  — for each working, unfiltered watch: fetch the page,
                           locate a heading-anchored hours block (an element
                           under an "Öffnungszeiten"/"Opening hours" heading that
                           contains a time), build a heading-anchored XPath.
  Phase B (--render)     — for the ones Phase A couldn't solve (JS SPAs whose raw
                           HTML has no hours), render them through the Playwright
                           browser *inside the changedetection container* and run
                           the same finder on the rendered DOM. Those also get
                           switched to the html_webdriver fetch backend.
  Apply                  — set the filter, recheck, wait, then VERIFY the filtered
                           snapshot still contains a time; auto-revert anything that
                           would blind the watch. Never leaves a watch empty.

Safe: --dry-run finds and prints proposals without changing anything. Applying
only ever adds a filter (and, for rendered watches, switches the backend); a
failed verify reverts both.

Examples:
  python3 scripts/hours_filter.py --datastore <area>.json --dry-run
  python3 scripts/hours_filter.py --datastore <area>.json            # plain only
  python3 scripts/hours_filter.py --datastore <area>.json --render   # + JS SPAs
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import time

import lxml.html

import osm_cd_common as C

KW = ['Öffnungszeiten', 'ÖFFNUNGSZEITEN', 'öffnungszeiten', 'Öffnungszeit', 'ÖFFNUNGSZEIT',
      'Opening Hours', 'Opening hours', 'Opening Time', 'Opening times', 'opening-hours', 'Öffnung']
HEAD_TAGS = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'b', 'p', 'span', 'div', 'li', 'td']
TIME = re.compile(r'\d{1,2}[:.]\d{2}')

# render snippet executed INSIDE the changedetection container (has playwright)
RENDER_SNIPPET = r'''
import json,sys
from playwright.sync_api import sync_playwright
todo=json.load(open("/tmp/hf_todo.json"))
out={}
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("%(WS)s")
    ctx=b.new_context(locale="de-DE")
    for k,url in todo.items():
        try:
            pg=ctx.new_page(); pg.goto(url,wait_until="domcontentloaded",timeout=30000)
            pg.wait_for_timeout(3500); out[k]={"html":pg.content()[:900000]}; pg.close()
            print("OK "+k,file=sys.stderr)
        except Exception as e:
            out[k]={"err":str(e)[:80]}; print("FAIL "+k,file=sys.stderr)
    b.close()
json.dump(out,open("/tmp/hf_rendered.json","w"))
'''


def txt_of(el):
    return re.sub(r'\s+', ' ', el.text_content()).strip()


def propose_from_html(html):
    """Return a heading-anchored XPath to the hours block, or None."""
    try:
        doc = lxml.html.fromstring(html)
    except Exception:
        return None
    full = len(txt_of(doc))
    best = None
    for kw in KW:
        for tag in HEAD_TAGS:
            for el in doc.xpath(f'//{tag}[contains(normalize-space(.),"{kw}")]'):
                for cand in (el.getparent(), el):
                    if cand is None:
                        continue
                    t = txt_of(cand)
                    if not TIME.search(t) or len(t) < 10 or len(t) > 1400:
                        continue
                    if full and len(t) > 0.7 * full:
                        continue
                    rel = '/parent::*' if cand is el.getparent() else ''
                    xp = f'(//{tag}[contains(normalize-space(.),"{kw}")]){rel}'
                    sel = doc.xpath(xp)
                    if not sel:
                        continue
                    comb = ' '.join(txt_of(x) for x in sel)
                    if not TIME.search(comb) or len(comb) > 1700:
                        continue
                    if best is None or len(comb) < best[0]:
                        best = (len(comb), xp, comb[:100])
            if best:
                break
        if best:
            break
    return None if best is None else {'xpath': best[1], 'sample': best[2]}


def plain_fetch(url):
    import urllib.request
    req = urllib.request.Request(C.normalize_url(url), headers=C.UA)
    with urllib.request.urlopen(req, timeout=18) as r:
        enc = r.headers.get_content_charset() or 'utf-8'
        return r.read(700000).decode(enc, 'replace')


class Runtime:
    """How to copy a file into the changedetection pod/container and run it there.

    This route exists because changedetection is where Playwright lives, and it needs no
    published browser port — the snippet reaches the browser over the compose/cluster network.
    It is no longer the only route: the claim that once stood here, that a stdlib client cannot
    drive the browser, was wrong. sockpuppetbrowser has no REST endpoint (HTTP 426 everywhere)
    and does not speak the Playwright wire protocol, but the snippet below only uses
    `connect_over_cdp()` — plain CDP over a WebSocket, which `scripts/cdp_render.py` now does
    from the host in stdlib. Prefer that when the browser port is reachable; it needs no
    changedetection, no `cp`/`exec` rights, and works in CI.
    """

    def __init__(self, kind="docker", target="changedetection", namespace=None):
        self.kind, self.target, self.ns = kind, target, namespace
        if kind not in ("docker", "kubectl"):
            raise SystemExit(f"unknown runtime {kind!r} (docker|kubectl)")

    def _ns(self):
        return ["-n", self.ns] if self.ns else []

    def cp_in(self, local, remote):
        if self.kind == "docker":
            return ["docker", "cp", local, f"{self.target}:{remote}"]
        return ["kubectl", *self._ns(), "cp", local, f"{self.target}:{remote}"]

    def cp_out(self, remote, local):
        if self.kind == "docker":
            return ["docker", "cp", f"{self.target}:{remote}", local]
        return ["kubectl", *self._ns(), "cp", f"{self.target}:{remote}", local]

    def exec(self, *argv):
        if self.kind == "docker":
            return ["docker", "exec", self.target, *argv]
        return ["kubectl", *self._ns(), "exec", self.target, "--", *argv]

    def env(self, name, default=""):
        try:
            return subprocess.check_output(self.exec("printenv", name), text=True).strip() or default
        except Exception:
            return default


def render_in_container(container, ws, todo, runtime=None):
    """todo: {key: url}. Returns {key: html}, rendered by the browser the app already uses.

    `container` is kept as the first argument for backwards compatibility; pass `runtime` to
    run against Kubernetes instead of Docker.
    """
    rt = runtime or Runtime("docker", container)
    tmp = '/tmp/hf_render.py'
    with open('/tmp/_hf_render.py', 'w') as fh:
        fh.write(RENDER_SNIPPET % {'WS': ws})
    with open('/tmp/_hf_todo.json', 'w') as fh:
        json.dump(todo, fh)
    subprocess.run(rt.cp_in('/tmp/_hf_render.py', tmp), check=True)
    subprocess.run(rt.cp_in('/tmp/_hf_todo.json', '/tmp/hf_todo.json'), check=True)
    subprocess.run(rt.exec('python3', tmp), check=True)
    subprocess.run(rt.cp_out('/tmp/hf_rendered.json', '/tmp/_hf_rendered.json'), check=True)
    data = json.load(open('/tmp/_hf_rendered.json'))
    return {k: v['html'] for k, v in data.items() if 'html' in v}


def wait_queue(base_url, key, timeout=1500):
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        si = json.load(urllib.request.urlopen(
            urllib.request.Request(base_url.rstrip('/') + '/api/v1/systeminfo',
                                   headers={'x-api-key': key}), timeout=30))
        if si.get('queue_size', 0) == 0:
            return
        time.sleep(6)


def main():
    ap = argparse.ArgumentParser(description="Filter watches to their hours block")
    ap.add_argument("--datastore", required=True)
    ap.add_argument("--render", action="store_true",
                    help="also render JS SPAs via the container browser (Phase B)")
    ap.add_argument("--dry-run", action="store_true", help="find + print, change nothing")
    ap.add_argument("--base-url",
                    default=os.environ.get("CD_BASE_URL", "http://localhost:5000"),
                    help="changedetection API root; defaults to $CD_BASE_URL, so the same call "
                         "works in-cluster, on the VPS and through a tunnel "
                         "(see scripts/cd_env.sh)")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--container", default="changedetection")
    ap.add_argument("--wait-timeout", type=int, default=1500)
    args = ap.parse_args()

    store = C.load_datastore(args.datastore)
    api = C.CDIO(args.base_url, C.resolve_api_key(args.api_key, args.container))

    # CD owns include_filters/fetch_backend, but the list endpoint omits them —
    # fetch each watch individually to read them accurately.
    def _get(u):
        try:
            return u, api.get(u)
        except Exception:
            return u, None
    uuids = [r["cd_uuid"] for r in store["records"].values() if r.get("cd_uuid")]
    live = {}
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        for u, w in ex.map(_get, uuids):
            if w:
                live[u] = w

    # candidates = synced, working, not already filtered in CD
    cand = []
    for r in store["records"].values():
        u = r.get("cd_uuid")
        if not u or u not in live:
            continue
        w = live[u]
        if w.get("last_error") or w.get("include_filters"):
            continue
        cand.append(r)
    print(f"candidates (working, unfiltered): {len(cand)}")

    # Phase A: plain fetch
    proposals = {}   # osm_id -> {xpath, sample, render}

    def do_plain(rec):
        try:
            p = propose_from_html(plain_fetch(rec["watch_url"]))
        except Exception:
            p = None
        return rec["osm_id"], p
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        for oid, p in ex.map(do_plain, cand):
            if p:
                p["render"] = False
                proposals[oid] = p
    print(f"Phase A (plain fetch): {len(proposals)} filters found")

    # Phase B: render the leftovers through the container browser
    if args.render:
        left = {r["osm_id"]: r["watch_url"] for r in cand if r["osm_id"] not in proposals}
        print(f"Phase B: rendering {len(left)} pages via {args.container} …")
        try:
            ws = subprocess.check_output(
                ['docker', 'exec', args.container, 'printenv', 'PLAYWRIGHT_DRIVER_URL'],
                text=True).strip() or 'ws://playwright-chrome:3000'
            rendered = render_in_container(args.container, ws, left)
        except Exception as e:
            print(f"  render phase failed ({e}); skipping Phase B", file=sys.stderr)
            rendered = {}
        for oid, html in rendered.items():
            p = propose_from_html(html)
            if p:
                p["render"] = True
                proposals[oid] = p
        print(f"Phase B (rendered): total filters now {len(proposals)}")

    id2rec = {r["osm_id"]: r for r in store["records"].values()}
    if args.dry_run:
        print("\n[dry-run] proposed filters:")
        for oid, p in proposals.items():
            tag = "render" if p["render"] else "plain"
            print(f"  [{tag}] {id2rec[oid]['name'][:26]:26} | {p['sample'][:60]}")
        print(f"\n[dry-run] {len(proposals)} would be applied. Nothing changed.")
        return

    # Apply + verify + revert — all in CD; the datastore is never mutated here.
    orig_backend = {}   # oid -> backend before apply, so revert can restore it
    applied = []
    for oid, p in proposals.items():
        rec = id2rec[oid]
        body = {"include_filters": ["xpath:" + p["xpath"]]}
        orig_backend[oid] = live.get(rec["cd_uuid"], {}).get("fetch_backend") or "system"
        if p["render"]:
            body["fetch_backend"] = "html_webdriver"
        api.update(rec["cd_uuid"], **body)
        applied.append(oid)
    print(f"applied {len(applied)} filters; rechecking + verifying …")

    for oid in applied:
        api.recheck(id2rec[oid]["cd_uuid"])
    time.sleep(10)
    wait_queue(args.base_url, api.key, args.wait_timeout)
    time.sleep(5)

    live = api.list() or {}
    kept = rev = 0
    for oid in applied:
        uuid = id2rec[oid]["cd_uuid"]
        w = live.get(uuid, {})
        ok = not w.get("last_error")
        if ok:
            import urllib.request
            base = args.base_url.rstrip('/') + '/api/v1/watch/' + uuid
            h = json.load(urllib.request.urlopen(
                urllib.request.Request(base + '/history', headers={'x-api-key': api.key}), timeout=30))
            if not h:
                ok = False
            else:
                t = urllib.request.urlopen(urllib.request.Request(
                    base + '/history/' + sorted(h)[-1], headers={'x-api-key': api.key}),
                    timeout=30).read().decode('utf-8', 'replace')
                ok = bool(t.strip()) and bool(TIME.search(t))
        if ok:
            kept += 1
        else:
            api.update(uuid, include_filters=[], fetch_backend=orig_backend.get(oid, "system"))
            api.recheck(uuid)
            rev += 1
    print(f"\nKEPT (hours-filtered): {kept}   REVERTED (would blind): {rev}")


if __name__ == "__main__":
    main()
