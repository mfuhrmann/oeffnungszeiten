# FILTERS.md — how to work out what a page needs to be monitored properly

changedetection triggers on **any** text change on the page it fetches. A watch is only useful if
what it captures is (a) the opening-hours block, (b) all of it, and (c) nothing else. This document
is the accumulated method for getting there: the decision ladder, the thirteen page shapes we keep
hitting, the investigation procedure, and what actually counts as proof that a filter is right.

**Just want to watch one page you already know?** Start with
[ADD-A-WATCH.md](./ADD-A-WATCH.md) — a short standalone howto that needs no OSM and no
datastore. This document is the reference behind it.

Companion docs: [README.md](./README.md), [CONCEPT.md](./CONCEPT.md).

---

## 0. The three failure modes

Before anything else — know what you are hunting. A watch can be wrong in three different ways, and
only the first one is visible without looking:

| Failure | Symptom | How it is found |
|---|---|---|
| **Noisy** | fires on every recheck, diff is a captcha number / rotating teaser / today-widget | a recheck-all, count diffs |
| **Blind** | never fires, page has no hours on it at all | audit **snapshot content** for time patterns |
| **Wrong** | never fires, filter captures a news box / marketing text / boilerplate constant | read what the filter captured, by hand |

"Quiet" is not "working". A run over 360 watches that reported **0 diffs** hid **77 watches** pointed
at a page with no hours at all — silent precisely because nothing on the page could ever change.
Never treat silence as evidence.

---

## 1. Decision ladder

Work top-down. Stop at the first rung that applies. Higher rungs are cheaper to build, cheaper to
run (plain fetch, no browser), and survive site redesigns better.

**The published hours win.** What a business writes on its door and on its page is the fact we
monitor; JSON-LD is a fallback, not the first choice. It is tempting as rung 1 because it is the most
durable selector — one line, immune to layout. But durability is not truth: nobody maintains an
invisible block, because nobody complains about it. Customers complain about the sign. Measured on
this instance: of 16 pages carrying JSON-LD hours, **4 contradicted
the visible page** — Müllers Backshop published `00:00-23:59` for a bakery (imported from a Google
Business Profile, `image` still points at googleusercontent.com), tegut served a chain-wide
`Mo-Sa 07:00-22:00` while the branch closes at 19:00, Pizza Capri and Eiscafé Bonifatius were
simply stale. The same neglect that makes such a block wrong today makes a watch on it **silent
tomorrow**, when the business changes its hours and updates only what people can see.

Use JSON-LD when the visible page offers nothing better: no usable anchor, only a generated class
(`iJkyRv` from styled-components), a live open/closed widget inside the block, or a candidate that
changedetection's own browser cannot find (Vergölst). Note the reason in the entry.

1. **Visible hours block** — heading-anchored (case 2), stable class/id (case 3), or day-anchored
   text (case 10); if the week is split across siblings, their common ancestor (case 11)
2. **Hours only after JS** → `html_webdriver` + one of the above
3. **JSON-LD fallback** → `json:$..openingHoursSpecification`, when no visible anchor holds.
   Before choosing it, check that its hours agree with the visible ones; if they differ, the
   visible text is right and the block is unmaintained
4. **Chain / store locator** → per-branch deep link (case 5), or a keyed row when every branch
   lives on one page (case 12)
5. **Discovery landed on the wrong page** → repoint + `manual_url: true`
6. Then layer noise controls as needed: `sort_text_alphabetically`, `trigger_text`,
   `global_ignore_text`
7. Nothing works → **absence** (empty `watch_url`), record kept, retried each harvest

---

## 2. The thirteen cases

### Case 1 — JSON-LD structured data (fallback, see the ladder)
**Signature:** `<script type="application/ld+json">` containing `openingHours` or
`openingHoursSpecification` (LocalBusiness / Restaurant schema).
**Filter:** `json:$..openingHoursSpecification`, else `json:$..openingHours`.
**Why it is durable:** one stable line, immune to banners, cookie bars, live open/closed widgets and layout
changes; usually works on **plain fetch** (no browser). Measured yield: of 185 unfiltered pages, 15
carried it and **9 became clean filters, 0 reverted** — far better than any other automated pass.
Where it is used, the entry records **why** — so a later reader knows the visible page offered
nothing better, rather than assuming JSON-LD was the lazy first pick.

**Three traps:**
- **WordPress theme boilerplate.** Four unrelated businesses emitted the identical
  `Monday,…,Sunday 09:00-17:00`. The resulting watch looks healthy and monitors a constant.
  Sanity-check that the hours differ per day and per business before accepting.
- **Boilerplate JSON-LD can hide real hours elsewhere on the same page.** `baecker-happ.de`
  serves that exact constant on every URL — while `/fachgeschaefte/` also carries **eleven Fulda
  branches with genuine per-branch hours**, behind a city selector. Two watches were retired as
  "publishes no hours" on the strength of the JSON-LD alone. If the JSON-LD is uniform
  `09:00-17:00`, treat the page as *unexamined*, not as answered.
- CD **strips `<script>` text**, so hours that exist only inside a framework payload (Next.js flight
  data — e-motion) are not reachable this way even though you can see them in the HTML.

**When probing by hand, print more than the top candidate.** The same Happ page ranked the
boilerplate JSON-LD first and the real branch-hours widget second; a probe script that printed
only `candidates[0]` reported "no hours" for a page covered in them.

### Case 2 — heading-anchored block in plain HTML
**Signature:** an "Öffnungszeiten" heading with the times in or next to it, present in the raw
HTML (no JS needed).
**Filter:** `xpath:(//h3[contains(normalize-space(.),"Öffnungszeiten")])/parent::*`
**Automated by** `scripts/hours_filter.py` Phase A (`propose_from_html`, `hours_filter.py:76`). The
heuristic it encodes, worth knowing because you will apply it by hand too:

> for each hours keyword × each of `h1…h6, strong, b, p, span, div, li, td`
> → consider the heading's **parent**, then the heading itself
> → keep only if it contains a time, is 10–1400 chars, and is **< 70 % of the page's total text**
> → the combined match across all XPath hits must be < 1700 chars
> → among survivors, **prefer the shortest**

That is: *the smallest element under the hours heading that still contains a time.* Typical yield
11–14 filters per harvest tier.

### Case 3 — stable class or id container
**Signature:** the page ships a purpose-built hours element with a human-authored (not generated)
class or id.
**Filter:** plain CSS. In use: `.detail-dealer-open-hours` (Pappert), `#home__times`,
`.seitencontent` (gruemel), `.penci-working-hours` (A7 Bikestore), `.location-detail-openday` (Davis),
`.et_pb_text_1` (Os Sabores), `.close__meta` (RED Sports).
Prefer this over Case 2 when it exists — it is shorter and reads better in the UI.

### Case 4 — hours render only under JS
**Signature:** plain fetch shows a shell / spinner / no times; the browser shows hours.
**Fix:** selector + `fetch_backend: html_webdriver`.
**Caveats:**
- CD's browser renders some SPAs in **English** (Davis), so German-keyword XPaths silently match
  nothing. Anchor on class, not text.
- `hours_filter.py --render` (Phase B) is the *same* finder with JS rendered first, so it fails on
  exactly the markup Phase A fails on: **3 hits out of 188 candidates (1.6 %)**, one of which
  auto-reverted. Do not run it expecting to clear a whole-page backlog.

### Case 5 — chain / store locator
**Signature:** many businesses share one corporate URL; the page is a locator, not a branch page.
**Fix:** find the per-branch deep link, split into separate `manual` watches.
In use: Subway `restaurants.subway.com/de/deutschland/he/fulda/<street>` (+ `table.c-hours-details`
and `sort_text_alphabetically`, rotation-proof), Pappert `/dealer/<slug>/`, meliva
`/standort/<slug>/`, Maritim per-outlet sections, tredy via `data-store-id` on `/storefinder`.
**Matching a record to its branch page** is the real work — two approaches that worked:
- **coordinates via Overpass** (Pappert: all 7 matched ≤ 30 m)
- **the practitioners' names** (meliva: Orthopädie Fulda → `dalberg-klinik-fulda`, identified via
  Schiffhauer/Kegel/Weghenkel)

### Case 6 — discovery landed on the wrong page
**Signature:** watch URL is plausible but the hours belong to someone else, or there are none.
`discover_subpage()` scores the first `Kontakt`-ish href, which on many sites is a **site-wide
footer link** present on every page.
**Fix:** repoint `watch_url` in the datastore and set `manual_url: true` so harvest stops
re-discovering it.
**Seen:** gruemel ×2 → accessibility statement (and, sharing a URL, they shared one **watch**);
`fulda.de/kontakt` → **Bürgerbüro** hours for both Vonderau Museum records; `re-gruppe.de/service/
kontakt` → the **operator's office** hours (Mo–Fr 9–16) for two swimming pools; `hotel-esperanto.de/
kontakt/` → three restaurants onto a page with no hours; Karlchen vom Dach → an Elementor **popup
trigger** (`#elementor-action%3A…`).
**Grep for the bug shape:** two records with the same `cd_uuid`.

### Case 7 — content reorders every day
**Signature:** diff shows the same lines in a different order, or today's line duplicated.
**Fix:** `sort_text_alphabetically`. In use: Subway ×2, Matratzen Concord, meliva ×2.
**Limit:** it hides re-ordering, **not duplication**. Davis had a weekly panel plus a sibling
`.location-detail-opentime.highlight` "today" widget inside the same wrapper — today's line appeared
twice and moved daily. Sorting masked the order but not the duplicate; the wrapper had to change.

### Case 8 — server swaps content between concurrent requests
**Signature:** two watches on the same host false-diff forever, each showing the other's page.
gruemel.de (IIS) returns the *same* page to two simultaneous requests for different URLs. Reproduced
with **separate cookie jars**, so it is server-side global state, not a session problem, and no
fetch setting avoids it.
**Fix:** give each watch a **`trigger_text`** page-identity marker — a string only that page's
filtered text contains. `trigger_text` blocks the change **and** skips the `previous_md5` update
when the marker is absent (`processors/text_json_diff/processor.py`), so a swapped fetch is
discarded instead of poisoning the baseline.
**Cost:** if the marker ever disappears the watch goes quiet. Pick a durable string.

### Case 10 — day-anchored text (no heading, no useful class)
**Signature:** the page prints hours in a bare `<p>` or `<div>` with no "Öffnungszeiten"
heading anywhere and no meaningful class — Reinholz Kaffeerösterei's footer reads
`Kaffeeladen im Steinweg: Mo-Sa: 10 - 18 Uhr`. Cases 2 and 3 are both structurally blind to
this, and it is the single largest group in the remaining backlog.
**How the wizard solves it:** find the *innermost* element whose own text looks like hours,
then anchor on the nearest durable ancestor class — skipping grid/utility classes (`col-12`,
`row`, `d-flex`) and page-builder GUIDs. If no such class exists, anchor on the weekday word
itself, restricted to the innermost match:
```
//p[contains(translate(.,"MONTAG…","montag…"),"montag")][not(.//p[contains(…,"montag")])]
```
The `not(...)` clause is essential: without it every ancestor matches too and the filter
swallows the page.
**Every anchor is validated by what it captures.** An ancestor class like `fl-module` matches
dozens of unrelated blocks; taking it unchecked turned a 71-character filter into the whole
page.

### Case 11 — common ancestor (the week is split across siblings)
**Signature:** no single element holds the whole week. Robe's Bike House has
`Dienstag – Freitag …` in one `<p>` and `Samstag …` in the next, so every individual candidate
is an incomplete filter and would silently miss half the changes.
**Fix:** anchor the **lowest common ancestor** of the hour-bearing elements.
**Why it matters here:** the only element that did cover both was a Beaver Builder div whose id
was `fl-icon-text-cjg0i7ku1qhr` — complete but brittle. The durable answer is the LCA reached by
a day-word anchor. Watch for this whenever a candidate reports fewer weekdays than the page
visibly shows.

### Case 12 — keyed row (one branch inside a page listing many)
**Signature:** a chain publishes **all** branches on one page, often behind a city selector, so
Case 5's per-branch URL does not exist. `baecker-happ.de/fachgeschaefte/` lists eleven Fulda
shops, each with its own hours.
**Fix:** filter to one row by a key unique to that branch — the street address works best,
combined with a time so the address block alone is not matched:
```
xpath://div[contains(.,"Kanalstraße 54") and contains(.,"Uhr")]
          [not(.//div[contains(.,"Kanalstraße 54") and contains(.,"Uhr")])]
```
Get the address from OSM (`addr:street` + `addr:housenumber`) so the key matches the right
business. Prefer this to filtering the whole branch-list widget: one watch per branch means an
alert names the shop that actually changed.

### Case 9 — site-wide noise classes
Handled globally, not per watch, via `settings.application.global_ignore_text` (25 patterns):
- German **math captchas** ("bitte addieren", "was ist die summe", "summe aus N und M",
  "wieviel ist N +/- M", "SPAM-Schutz die Zahl N", "3 + 5 = ?") — Divi/Contao contact forms
- **live open/closed widgets** ("we're open", "closes at N", "öffnet um/bald", "schließt um",
  "derzeit geschlossen", "jetzt geöffnet/geschlossen", "wir öffnen heute um 16:00")
- the page **echoing our own IP** back (PROSOL `Client-IP:…`)

Patterns are kept narrow on purpose so a legitimate weekly `Montag: geschlossen` still triggers.

Editing global settings has two hard rules — see §6.

### Case 13 — the snapshot is raw HTML, not text
Symptom: the stored snapshot shows markup (`<div class="row g-0">`), the capture is ten times
longer than the text it contains, and no `ignore_text` pattern ever matches. The filter is fine —
changedetection decided the page was **plaintext** and skipped its HTML-to-text step.

The decision lives in `processors/magic.py`: a page counts as HTML if one of
`<!doctype html`, `<html`, `<head`, `<body`, `<script`, `<iframe`, `<div` appears in the **first
200 bytes**, or if the Content-Type header is exactly `text/html`. A header of
`text/html; charset=UTF-8` is *not* an exact match, so everything rests on those 200 bytes —
and Shopware opens its pages with Twig include comments:

```
<!-- INCLUDE BEGIN @Storefront/storefront/page/content/index.html.twig (vendor/…) -->
```

No pattern matches, the header check fails, and the fallback `http_content_header.startswith('text/')`
marks it plaintext. Fix: set `fetch_backend: html_webdriver`. The browser returns a normal
serialised DOM starting with `<html>`, the same filter then yields clean text
(elektrozigge.de: 3333 chars of markup → 248 chars of hours).

Check for it with a per-watch snapshot read, not the UI diff:

```bash
curl -s -H "x-api-key: $KEY" "$CD/api/v1/watch/<uuid>/history/<ts>" | head -c 200
```

### Not a case — absence
No hours published anywhere, anti-bot 403 in all modes (lieferando/DataDome class), dead domain:
leave `watch_url` empty. cd_sync skips it, no watch, no flag. **Do not set `manual_url`** unless the
domain is actively hostile (hijacked, e.g. baeckereistorch.de) — without it, harvest retries
discovery every run and the record auto-revives when the site comes back. Add a `note` saying why,
and word it for `unmonitorable_report.py`'s bucketing regex
(`BLOCK_RE = anti-bot|403|DataDome|lieferando`) — a note reading "…, *not* anti-bot" lands in the
anti-bot bucket.

---

## 3. Investigation procedure — one page

### The fast path: two tools that do this for you

Most of §3 is only needed when the tools come up short. Try them first — neither needs
browser devtools, and neither requires the OSM datastore.

**`filter_wizard.py` — choose a filter by reading text, not selectors.** It runs every
strategy in §2 and prints the *text each candidate would capture* as a numbered menu, with
warnings attached. An admin who can read "Mo-Sa: 10 - 18 Uhr" can pick correctly without
knowing what an XPath is.

```bash
python3 scripts/filter_wizard.py https://example.de/kontakt      # explore a URL
python3 scripts/filter_wizard.py --uuid <uuid>                   # existing watch
python3 scripts/filter_wizard.py --uuid <uuid> --pick 2 --apply  # write the choice
python3 scripts/filter_wizard.py <url> --render                  # JS-rendered page
python3 scripts/filter_wizard.py <url> --lang en                 # English page
```

It flags what it cannot judge for you: `brittle selector`, `wide (>70% of the page)`,
`only N weekday(s)`, `same hours every day — check this is not theme boilerplate`,
`captures the same hours N×`. Nothing is written without `--apply`. "Whole page" is always
offered last, so *no filter* stays a visible, deliberate choice.

**`watch_audit.py` — find the watches that only look healthy.** Reads each watch's stored
snapshot and judges it against the four criteria in §4, in plain language.

```bash
python3 scripts/watch_audit.py                                # every watch, worst first
python3 scripts/watch_audit.py --datastore <area>.json    # nicer names
python3 scripts/watch_audit.py --only red                     # just the broken ones
python3 scripts/watch_audit.py --html audit.html              # report for a browser
```

RED = broken, blocked, or watching a page with no hours (can never fire).
AMBER = works but incomplete, duplicated, boilerplate, unfiltered or currently noisy.
Read-only; it never writes. A full pass takes about two seconds.

Both share `scripts/hours_lang.py`, which holds the German/English keyword lists and the
time/weekday regexes. Add a language there and both tools gain it.

### Step 0 — get the watch and see it as CD sees it

```bash
KEY=$(docker exec changedetection python3 -c \
  "import json;print(json.load(open('/datastore/changedetection.json'))['settings']['application']['api_access_token'])")

# ALWAYS per-watch GET. The list endpoint omits include_filters, notification_urls,
# notification_body and extract_text, and misreports fetch_backend.
curl -s -H "x-api-key: $KEY" http://localhost:5000/api/v1/watch/<uuid> | python3 -m json.tool

# what the watch is currently capturing (last stored snapshot)
curl -s -H "x-api-key: $KEY" http://localhost:5000/api/v1/watch/<uuid>/history      # -> {ts: file}
curl -s -H "x-api-key: $KEY" http://localhost:5000/api/v1/watch/<uuid>/history/<ts> # -> the text
```

Read the snapshot **before** touching anything. Most "mystery" watches are explained by it.

### Step 1 — does the page have hours at all?

Use `hours_lang.py` — do not hand-roll this regex, it is wrong in more ways than it looks.
`\d{1,2}[:.]\d{2}` alone under-counts badly, which once claimed 92 hours-free watches where the
true count was 77. Every one of these formats appears on real Fulda pages and each broke a
naive pattern:

| Format | Seen on | What a naive regex does |
|---|---|---|
| `10 - 18 Uhr` | most German sites | misses it — no minutes at all |
| `Mo-Di 11-24h` | Heimat | misses it — `h` suffix, no minutes |
| `11 : 00 - 22 : 00` | Aiko Sushibar | misses it — spaces around the colon |
| `donnerstags8:00` | Jordan's Mensa | misses the **weekday** — `\b` fails between `s` and `8` |
| `212.110.223.68` | anti-bot block pages | **false hit** inside the IP address |
| `27.03.2025` | closure notices | false hit unless dates are excluded |

```python
import hours_lang as L
L.time_matches(text)      # [(token, surrounding context), …] — context so you can CHECK it
L.weekdays(text, "de")    # ['Mon','Thu','Fri'] — distinct days, abbreviations only if a time exists
L.hours_score(text)       # 0 = not an hours block
L.looks_blocked(text)     # anti-bot interstitial, not content
```

**Always print the matched context before trusting a hit** — that is why `time_matches`
returns it. A bare `captcha` or `forbidden` in the text is *not* proof of a block page either:
those words appear in ordinary contact forms, which marked a working shop page as blocked
until the phrase list was tightened and a length guard added.

Two more measurement traps, both of which produced confident wrong answers:

- **Weekday detection must be bilingual even on German pages.** A `json:` filter renders as
  `https://schema.org/Monday`, so a German-only day list reported *every* JSON-LD watch as
  "times found but no weekday named". Acting on that would have replaced working JSON-LD
  filters with a nav menu and a contact card. `hours_lang` now always counts English full
  names as well.
- **Uniform hours are not evidence of boilerplate.** Aldi really does open 08:00–21:00 seven
  days a week. Flagging uniformity alone produced 22 false alarms against 2 real findings; it
  only means something when the range is the schema.org default `09:00-17:00`, or when the
  identical text also appears on an unrelated watch.

Rapid probing also trips anti-bot on some hosts, so if block-page shapes appear, back off and
re-probe gently.

No hours in plain fetch → try rendered (Step 3) before concluding anything.

### Step 2 — probe for JSON-LD first (Case 1)

```python
import json, re, urllib.request
html = urllib.request.urlopen(urllib.request.Request(url, headers=C.UA), timeout=18).read().decode('utf-8','replace')
for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S|re.I):
    blob = m.group(1)
    if 'openingHours' in blob:
        print(json.dumps(json.loads(blob), ensure_ascii=False, indent=2)[:1500])
```

Found → filter is `json:$..openingHoursSpecification` (or `$..openingHours`). Then check the values
differ per day / are not the `Monday,…,Sunday 09:00-17:00` boilerplate.

### Step 3 — render it if plain fetch is empty

```bash
WS=$(docker exec changedetection printenv PLAYWRIGHT_DRIVER_URL)   # ws://playwright-chrome:3000
```

`hours_filter.py --render` does this in bulk (`render_in_container`, `hours_filter.py:120`): copies a
Playwright snippet into the container, opens each URL with `locale="de-DE"`, waits 3.5 s after
`domcontentloaded`, dumps `page.content()`.

Note the locale — CD's own browser does **not** always honour it (Davis renders English), so verify
against the *rendered* DOM, not your assumption.

### Step 4 — build the selector

Run the batch finder against the datastore first; it may already solve it:

```bash
python3 scripts/hours_filter.py --datastore <area>.json --dry-run     # find + print, changes nothing
python3 scripts/hours_filter.py --datastore <area>.json               # plain only, applies
python3 scripts/hours_filter.py --datastore <area>.json --render      # + JS SPAs
```

It only ever *adds* a filter (and switches backend for rendered ones), rechecks, verifies, and
reverts anything that would blind the watch. It never mutates the datastore.

By hand, anchor on **text or an authored class**, never on:
- **GUID-ish class names** — `.text-46bf3150-186a-…`, `.heading-module-3e50…`,
  `elementor-element-224ed87`. Page-builder output; changes on the next site edit, and may not even
  be the hours element (Fuldas Tauchertreff pointed at a notice paragraph).
- **absolute XPath** — `/html/body/div[2]/div[6]` blinded Eye Eye Optik, whose page had the hours in
  plain HTML all along. The same class of selector was removed once before in `7caa701`.

Useful shapes when the markup is awkward:
- text-anchored `<p>`: `xpath://p[contains(.,"Montag") and contains(.,"Freitag") and contains(.,"Uhr")]`
- space-padded class test when a page ships desktop **and** mobile copies of the same block:
  `xpath://div[contains(concat(" ",normalize-space(@class)," ")," randspalte ")]//div[…]`
  (Mensa Hochschule — picks exactly one of the two)

### Step 5 — apply

```bash
curl -s -X PUT -H "x-api-key: $KEY" -H 'Content-Type: application/json' \
  -d '{"include_filters":["xpath://div[@id=\"home__times\"]"]}' \
  http://localhost:5000/api/v1/watch/<uuid>

curl -s -H "x-api-key: $KEY" "http://localhost:5000/api/v1/watch/<uuid>?recheck=1"
```

Or in Python via the shared helper — `C.CDIO(base, key).update(uuid, include_filters=[...])`,
`.get(uuid)`, `.recheck(uuid)`, `.delete(uuid)` (`scripts/osm_cd_common.py:227`).

### Step 6 — verify (see §4). Then update the datastore only if you changed `watch_url`
(add `manual_url: true`). Filters live in CD only — never write them back.

---

## 4. What counts as proof

`hours_filter.py`'s automatic check is *"does the filtered snapshot still contain a time?"*. That
catches **blinding** and nothing else. It has passed, in production:
- a practice **news box** (job ad + "Wir machen Urlaub" + "keine Neupatienten" — all contain times)
- a **marketing paragraph**
- WordPress **boilerplate JSON-LD** monitoring a constant
- Davis's wrapper that **duplicated** today's line and moved it daily

So after applying, read the captured text and confirm all four:

1. **It is the hours block** — not news, not a job ad, not a service list.
2. **It is complete** — a filter grabbing Mo–Fr while Sa/So sit in a sibling element looks perfectly
   healthy and silently misses half the changes. Count distinct weekdays.
3. **It is not a constant** — values differ per day and are not shared with unrelated businesses.
4. **It is stable across two rechecks** — no rotation, no today-widget, no captcha number.

### Do NOT verify an ignore rule by diffing stored snapshots

`ignore_text` is applied only to `text_for_checksuming`
(`processors/text_json_diff/processor.py:568`); the **stored snapshot keeps the ignored lines**
unless the global `strip_ignored_lines` is enabled (it is not). A diff of two snapshots therefore
still *shows* an ignored captcha number changing while changedetection has already discounted it.
This cost a debugging cycle. The valid test is whether **`last_changed` advances** on a later check.

### A "recheck all" is not proof every watch ran

One recheck-all **silently skipped 7 watches** — they still carried timestamps from days earlier.
Always reconcile `last_checked` against the batch before drawing conclusions from a diff count.

---

## 5. Batch passes, with their real yields

Ranked by measured value, so nobody re-runs the weak ones expecting more:

| Pass | Yield | Verdict |
|---|---|---|
| `watch_audit.py` | found **77 blind watches out of 360**; ~2 s for a full pass | run after every batch |
| `filter_wizard.py` | covers all five strategies, incl. pages with no hours keyword | first thing to try on one page |
| JSON-LD sweep | **9 clean filters / 185 pages** (15 carried markup), 0 reverted | now inside the wizard |
| `hours_filter.py` Phase A | ~11–14 per harvest tier | good, run after every harvest |
| `hours_filter.py --render` Phase B | **3 / 188 candidates (1.6 %)**, 1 auto-reverted | not a backlog fix |
| Playwright rescue of blind watches | **1 / 77** (RED Sports) | do it, but expect ~nothing |

Sequence after any harvest: `hours_filter.py --dry-run` → apply → `watch_audit.py` →
`filter_wizard.py` on each RED/AMBER → recheck-all → triage diffs → `unmonitorable_report.py`.

**Why the wizard finds things `hours_filter.py` cannot.** Phase A only anchors on an
"Öffnungszeiten"-style heading. Many pages publish hours with no such heading and no useful
class — Reinholz Kaffeerösterei prints `Kaffeeladen im Steinweg: Mo-Sa: 10 - 18 Uhr` in a bare
`<p>` in the footer. The wizard's *day-anchored text* strategy finds the innermost element that looks
like hours and anchors on the nearest durable ancestor class, skipping Bootstrap-style layout
classes (`col-12`, `row`, `d-flex`) and page-builder GUIDs. That class of page is most of the
remaining backlog.

---

## 6. Two operational rules that bite

**Editing global settings: STOP the app first.** Watches themselves need no care — since 0.55.8
each one is its own `/datastore/<uuid>/watch.json`, written immediately and atomically, so a UI save
survives even a hard kill. Settings are different: `changedetection.json` is read **only at startup**
and overwritten from memory on the next commit, so an edit made while the app runs is invisible and
then lost. Prefer `scripts/apply_global_settings.py` (it is what the initContainer runs) over hand
edits, and either way stop the app:

```bash
kubectl -n changedetection scale deploy/changedetection --replicas=0    # k8s
#   … edit the PVC, then scale back to 1
docker compose stop changedetection                                     # compose
docker run --rm -v changedetection_changedetection-data:/datastore python:3.12-alpine …   # edit here
docker compose start changedetection
# then verify the value actually persisted
```

**Expect exactly one noisy pass after any global-settings edit.** Changing globals changes
`filter_config_hash`, which deliberately bypasses the skip-check
(`processors/text_json_diff/processor.py:436`), so every affected watch re-baselines once. That
first noisy pass is not a regression.

---

## 7. Where the remaining work is

An unfiltered watch sits on the whole page. If that page does contain hours, the watch works but is
noisy — that is the honest risk, and narrowing it is the standing body of work. Order of attack:
group by site platform first (several share WordPress, Elementor or Jimdo markup, so one pattern
covers a group), hand-build the rest.

`watch_audit.py` reports the current split. A green verdict there means the snapshot contains hours,
not that the four criteria in §4 were checked by a human — that judgement stays manual.
