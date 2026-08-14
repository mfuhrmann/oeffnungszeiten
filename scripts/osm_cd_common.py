"""
Shared helpers for the OSM -> changedetection.io workflow.

Two scripts use this module:
  osm_harvest.py  — Overpass query -> datastore JSON (source of truth, keyed by OSM id)
  cd_sync.py      — datastore -> changedetection watches (idempotent reconcile)
"""
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# browser-like UA for crawling business sites (many block non-browser UAs)
UA = {"User-Agent": "Mozilla/5.0 (compatible; changedetection-setup/1.0)"}
# Overpass rejects UAs containing "Mozilla"/"(compatible;" (HTTP 406) — use a plain one
OVERPASS_UA = {"User-Agent": "osm-cd-sync/1.0"}

# pattern -> score, used to pick the best "opening hours" subpage from a homepage.
# Stems, not whole words: heitz-draut.de links its hours as `oeffnung.php` behind an image
# button with no alt text, scored zero on the full word and was never opened — while the page
# holds exactly what we look for. Measured over 157 pages the scan had written off, the stem
# finds two more and costs nothing.
# The lookbehind matters as much: "Eröffnung der e-Bike Welt" and "Neueröffnungen" are news, and
# without it they outrank the real contact page.
LINK_PRIORITY = [
    (r"(?<!er)(?<!neuer)(?:oe|ö|o)ffnungszeiten", 5), (r"opening", 5),
    (r"(?<!er)(?<!neuer)(?:oe|ö|o)ffnung", 4),
    (r"sprechzeit|sprechstunde(?!n?video)|betreuungszeit|(?:geschäfts|geschaefts|büro|buero)zeit", 4),
    (r"kontakt", 3), (r"contact", 3),
    (r"anfahrt", 2), (r"standort", 2),
    (r"impressum", 1),
]
MIN_REPOINT_SCORE = 2

# substrings in a fetch error meaning the domain is dead (never worth Playwright)
DEAD_ERROR_SIGNATURES = ["404", "Name or service not known", "ConnectionPool"]

DEFAULT_OVERPASS = "https://overpass-api.de/api/interpreter"


def today():
    return time.strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# Overpass
# --------------------------------------------------------------------------- #
def _overpass_get(ql, overpass_url, timeout=90, retries=5):
    """Run one Overpass QL query with retry on load-related errors."""
    url = overpass_url + "?" + urllib.parse.urlencode({"data": ql})
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=OVERPASS_UA)
            with urllib.request.urlopen(req, timeout=timeout + 30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 502, 503, 504):
                wait = 5 * (attempt + 1)
                print(f"      Overpass {e.code}, retry in {wait}s "
                      f"({attempt + 1}/{retries}) …", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, ValueError) as e:
            last = e
            wait = 5 * (attempt + 1)
            print(f"      Overpass error ({e}), retry in {wait}s …", file=sys.stderr)
            time.sleep(wait)
    raise SystemExit(f"ERROR: Overpass kept failing: {last}")


def area_selector(area, admin_level, rel_id=None, wikidata=None):
    """Return an Overpass 'area...->.a;' statement plus a human description."""
    if rel_id:
        return f"area({3600000000 + int(rel_id)})->.a;", f"relation {rel_id}"
    if wikidata:
        return f'area["wikidata"="{wikidata}"]->.a;', f"wikidata {wikidata}"
    return (f'area["name"="{area}"]["admin_level"="{admin_level}"]->.a;',
            f'name={area} admin_level={admin_level}')


def check_area_ambiguity(area, admin_level, overpass_url):
    """Warn if name+admin_level matches more than one administrative relation."""
    ql = (f"[out:json][timeout:25];"
          f'rel["name"="{area}"]["admin_level"="{admin_level}"]'
          f'["boundary"="administrative"];out tags;')
    try:
        d = _overpass_get(ql, overpass_url, timeout=25)
    except SystemExit:
        return  # non-fatal
    rels = d.get("elements", [])
    if len(rels) > 1:
        print(f"  WARNING: '{area}' admin_level={admin_level} matches "
              f"{len(rels)} relations:", file=sys.stderr)
        for e in rels:
            t = e.get("tags", {})
            print(f"    rel {e['id']}  pop={t.get('population','?')}  "
                  f"wikidata={t.get('wikidata','?')}", file=sys.stderr)
        print("  -> disambiguate with --rel-id or --wikidata to be precise.",
              file=sys.stderr)


def _idna_host(u):
    """Punycode a non-ASCII host ('physio-münsterfeld.de'). urllib sends the raw
    UTF-8 host and the server answers 400, which reads exactly like a dead site --
    the record then gets an empty watch_url and silently drops out of monitoring."""
    try:
        parts = urllib.parse.urlsplit(u)
        host = parts.hostname
        if not host or host.isascii():
            return u
        netloc = host.encode("idna").decode("ascii")
        if parts.port:
            netloc += f":{parts.port}"
        if parts.username:
            cred = parts.username + (f":{parts.password}" if parts.password else "")
            netloc = f"{cred}@{netloc}"
        return urllib.parse.urlunsplit(parts._replace(netloc=netloc))
    except Exception:
        return u


def normalize_url(u):
    """OSM website tags are often schemeless ('www.x.de') or protocol-relative
    ('//x.de'); changedetection rejects those with HTTP 400. Add https://.
    Non-ASCII hosts are punycoded for the same reason (see _idna_host)."""
    u = u.strip()
    if u.startswith("//"):
        u = "https:" + u
    elif not re.match(r"(?i)^https?://", u):
        u = "https://" + u
    return _idna_host(u)


def overpass_category(area_stmt, key, val, overpass_url):
    """Query one filter. val=None means key-existence (e.g. shop=*), and the
    category is then taken from each element's own tag value (shop=bakery ->
    category 'bakery'). Returns {osm_id, name, category, website, source_key}."""
    kv = f'["{key}"="{val}"]' if val is not None else f'["{key}"]'
    ql = (
        "[out:json][timeout:120];\n"
        f"{area_stmt}\n(\n"
        f'  nwr{kv}["website"](area.a);\n'
        f'  nwr{kv}["contact:website"](area.a);\n'
        ");\nout center tags;\n"
    )
    d = _overpass_get(ql, overpass_url, timeout=120)
    out = []
    for e in d.get("elements", []):
        t = e.get("tags", {})
        w = t.get("website") or t.get("contact:website")
        if not w:
            continue
        category = val if val is not None else (t.get(key) or key)
        out.append({
            "osm_id": f"{e['type']}/{e['id']}",
            "name": t.get("name", "(no name)"),
            "category": category,
            "website": normalize_url(w),
            "source_key": key,
        })
    return out


# --------------------------------------------------------------------------- #
# Subpage discovery
# --------------------------------------------------------------------------- #
def _fetch(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        enc = r.headers.get_content_charset() or "utf-8"
        return r.read(400000).decode(enc, "replace"), r.geturl(), r.headers.get_content_type()


def _score_link(href, text):
    blob = (href + " " + text).lower()
    best = 0
    for pattern, w in LINK_PRIORITY:
        if re.search(pattern, blob):
            best = max(best, w)
    return best


def discover_subpage(url):
    """Best hours/contact subpage URL for a reachable site.

    Returns the input url when the site is reachable but has no better subpage.
    Returns "" when the site is UNREACHABLE (dead domain, DNS fail, 4xx/5xx, or
    anti-bot block) — the caller then leaves watch_url blank, so an object with no
    reachable website is simply not monitored (no flag; retried next harvest, and
    auto-revives if the website tag is later fixed)."""
    try:
        html, final, ctype = _fetch(url)
    except Exception:
        return ""          # unreachable -> no monitoring target
    if "html" not in (ctype or ""):
        return url         # reachable but non-HTML (pdf, …) -> watch as-is
    host = urllib.parse.urlparse(final).netloc
    best = (0, None)
    for href, inner in re.findall(
        r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S
    ):
        text = re.sub(r"<[^>]+>", " ", inner)
        s = _score_link(href, text)
        if s > best[0]:
            full = urllib.parse.urljoin(final, href)
            if urllib.parse.urlparse(full).netloc == host:
                best = (s, full)
    if best[1] and best[0] >= MIN_REPOINT_SCORE:
        return best[1]
    return url


def discover_many(urls, workers=12):
    """Map url -> discovered watch_url, concurrently."""
    result = {}
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for u, target in zip(urls, ex.map(discover_subpage, urls)):
            result[u] = target
    return result


# --------------------------------------------------------------------------- #
# changedetection API
# --------------------------------------------------------------------------- #
class CDIO:
    def __init__(self, base_url, api_key):
        self.base = base_url.rstrip("/") + "/api/v1"
        self.key = api_key

    def _req(self, path, method="GET", body=None):
        headers = {"x-api-key": self.key}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else None

    def list(self):
        # NB: the list endpoint does NOT return include_filters and misreports
        # fetch_backend — use get(uuid) when you need those fields accurately.
        return self._req("/watch")

    def get(self, uuid):
        """Full watch object (include_filters, fetch_backend, … are accurate here)."""
        return self._req(f"/watch/{uuid}")

    def delete(self, uuid):
        """Delete ONE watch by uuid. Only ever called targeted-by-uuid — never by
        tag string (a tag filter once matched everything and wiped all watches)."""
        return self._req(f"/watch/{uuid}", "DELETE")

    def add(self, url, tag, interval_days, fetch_backend=None):
        body = {
            "url": url,
            "tag": tag,
            # use_default MUST be False or changedetection ignores the per-watch
            # interval and falls back to the global recheck time.
            "time_between_check_use_default": False,
            "time_between_check": {"weeks": 0, "days": interval_days, "hours": 0,
                                   "minutes": 0, "seconds": 0},
        }
        if fetch_backend and fetch_backend != "system":
            body["fetch_backend"] = fetch_backend
        res = self._req("/watch", "POST", body)
        return res if isinstance(res, str) else (res or {}).get("uuid")

    def update(self, uuid, **fields):
        return self._req(f"/watch/{uuid}", "PUT", fields)

    def recheck(self, uuid):
        return self._req(f"/watch/{uuid}?recheck=1")

    def tags(self):
        """{uuid: {title: ...}}. Tags are separate objects; watches reference them by uuid,
        which is why a tag uuid is meaningless when moved to another instance."""
        return self._req("/tags") or {}

    def tag_create(self, title):
        res = self._req("/tag", "POST", {"title": title})
        return res if isinstance(res, str) else (res or {}).get("uuid")


def resolve_api_key(api_key=None, container="changedetection"):
    if api_key:
        return api_key
    env = os.environ.get("CHANGEDETECTION_API_KEY")
    if env:
        return env
    try:
        out = subprocess.check_output(
            ["docker", "exec", container, "python3", "-c",
             "import json;print(json.load(open('/datastore/changedetection.json'))"
             "['settings']['application']['api_access_token'])"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        if out:
            return out
    except Exception:
        pass
    sys.exit("ERROR: no API key. Pass --api-key, set CHANGEDETECTION_API_KEY, "
             "or ensure the container is reachable via docker exec.")


# --------------------------------------------------------------------------- #
# Datastore
# --------------------------------------------------------------------------- #
def load_datastore(path):
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    return {"area": None, "records": {}}


def save_datastore(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
