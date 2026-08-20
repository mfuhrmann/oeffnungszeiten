# ADD-A-WATCH.md — watch one page's opening hours

For anyone running changedetection.io who knows a business and its website and wants a
reliable alert when its opening hours change.

**No OSM, no datastore, no API key needed.**

| To do this | You need |
|---|---|
| get a filter for a page | Python 3 + `lxml`, and `scripts/filter_wizard.py` + `scripts/hours_lang.py` + `scripts/osm_cd_common.py` |
| the browser retry (JS pages) | one container: `docker run --rm -p 3000:3000 dgtlmoon/sockpuppetbrowser` — found automatically on `localhost:3000` |
| `--apply`, or `watch_audit.py` | the changedetection API key (below) |

About 14 % of the pages in this project publish their hours only after JavaScript runs. The
wizard renders those through the browser above — `scripts/cdp_render.py` speaks CDP to it
directly, so **changedetection itself is not needed for rendering**. A browser elsewhere is named
with `--browser-ws`. Use a throwaway one: a shared instance serves every `html_webdriver` watch
while you render against it.

```bash
pip install lxml
# API key — only for --apply and watch_audit.py
export CHANGEDETECTION_API_KEY=$(docker exec changedetection python3 -c \
  "import json;print(json.load(open('/datastore/changedetection.json'))['settings']['application']['api_access_token'])")
# or read it in the UI: Settings → API
```

Without Docker the wizard still works — it just cannot do the browser retry, and says so
instead of failing. Both scripts default to `http://localhost:5000`; override with
`--base-url`.

---

## Why you need more than "add URL"

changedetection is a **text-diff tool, not a smart crawler**. It snapshots the visible text
of one URL and alerts when *anything* changes. It has no idea what an opening hour is. So a
naive watch fails in one of two ways:

- **Noisy** — you watch the whole page, and a rotating teaser, a cookie banner or a contact
  form's captcha number alerts you every few days. You stop reading the alerts.
- **Blind** — you watch a page that has no opening hours on it. It never alerts, looks
  perfectly healthy, and would never have told you when the hours changed. In this project
  **77 of 360 watches** were silently in that state.

The fix for both is the same: point the watch at the *hours block only*.

---

## The workflow

### 1. Find the page that actually carries the hours

**This part is yours — the wizard checks only the exact URL you hand it and never crawls the
site.** Usually the homepage, `/kontakt`, `/oeffnungszeiten` or `/impressum`. Try the
homepage first; plenty of businesses put the hours in the footer of every page.

For a chain, it is almost always a **per-branch deep link**, not the corporate site — a store
locator renders nothing useful. Find the branch's own page and use that.

Careful with a trap that has cost this project real time: many sites put a *site-wide footer
link* to `/kontakt` on every page, and it can lead to an accessibility statement, the
landlord's office hours, or a hotel's front desk rather than the restaurant you meant. Read
the text the wizard shows you and check the hours belong to **this** business.

### 2. Ask the wizard

```bash
python3 scripts/filter_wizard.py https://example.de/kontakt
```

It fetches the page, runs every strategy, and prints candidates as **the text each one
would capture**:

```
[1] common ancestor                    Di–Sa (5 of 7)            71 chars
    Dienstag – Freitag10:00 Uhr bis 18:00 UhrSamstag10:00 Uhr bis 14:00 Uhr
    → good pick — no warnings
    filter: xpath://div[contains(translate(...),"dienstag")][not(.//div[...])]

[2] day-anchored text                  Di–Fr (4 of 7)            41 chars
    Dienstag – Freitag10:00 Uhr bis 18:00 Uhr
    ! only 4 weekday(s) — rest may be in a sibling element
    → usable, but — see the warning above
```

**Pick by reading the text, not by judging the selector.** You know what your business's
hours look like; you do not need to know what an XPath is. Each candidate shows **which
weekdays it captured**, spelled out — a gap like `Mo–Mi, Fr (4 of 7)` is the signal that
the rest of the week sits in a sibling element. Lines marked `!` state what the tool
noticed; the `→` line says whether that disqualifies the candidate:

| Verdict | Meaning |
|---|---|
| `good pick` | nothing suspicious, or only a cosmetic caveat |
| `usable, but` | works, at a known cost (noise, or an incomplete week) |
| `avoid` | the named warning is fatal — brittle selector, no weekday, block page |
| `last resort` | the unfiltered whole page |

Warnings tell you what the tool cannot decide for you:

| Warning | What it means |
|---|---|
| `only N weekday(s)` | the rest of the week is probably in a sibling element — prefer a candidate with more days |
| `brittle selector` | the class/id is page-builder output and will change at the next site edit |
| `wide (>70% of the page)` | barely a filter; expect noise |
| `same hours every day` | might be generated theme boilerplate — a filter that monitors a constant |
| `captures the same hours N×` | the page ships desktop + mobile copies; can produce a diff every day |
| `also captures contact details` | harmless, slightly noisier |

If the plain HTML has nothing, the wizard **retries through a browser by itself** and tells
you so — that answer is what decides the Fetch method in the next step. With no browser
reachable it says what to start instead of failing silently, so an empty result is never
ambiguous between "no hours here" and "no browser here".

### 3. Paste the answer into changedetection

Picking a candidate prints exactly what to enter:

```
Add this in changedetection  (Edit watch → these fields)
  URL                        https://example.de/kontakt
  Fetch method               Basic fast text  (plain fetch — no browser needed)
  Filters & Triggers →       xpath://div[...]
    CSS/JSONPath/XPath Filter

  It should then capture exactly this:
    Dienstag – Freitag10:00 Uhr bis 18:00 Uhr Samstag10:00 Uhr bis 14:00 Uhr
```

Create the watch in the UI, paste those three fields, save. **"Fetch method" matters**: if
the wizard had to render the page, a plain fetch will capture nothing at all.

> **Contributing to *this* repo instead of your own instance?** Then skip the UI: watches here
> are one file per watch in [`entries/`](./entries), changed through pull requests. Add
> `--emit entries --name "Example GmbH" --tags fulda-hairdresser` to the same wizard command and
> it writes the entry file for you — filter, fetch method, and a `captured_sample` so a reviewer
> can judge it from the diff without fetching anything. Full walkthrough:
> [CONTRIBUTING.md](./CONTRIBUTING.md). A watch created by hand in the UI is *not* picked up by
> git and gets reverted or pruned by the next sync, so the entry file is the real deliverable.

### 4. Check it after the first fetch

Open the watch's snapshot in the UI and confirm it contains the hours and nothing else. With
the API key set, the audit script does this for every watch at once:

```bash
python3 scripts/watch_audit.py                  # RED / AMBER / green + plain-language reasons
python3 scripts/watch_audit.py --only red
python3 scripts/watch_audit.py --html audit.html
```

RED = broken, blocked, or watching a page with no hours (can never fire).
AMBER = works but incomplete, duplicated, boilerplate, unfiltered or currently noisy.

**Do not judge a watch by whether it stays quiet.** Silence means the captured text has not
changed — which is equally what a watch pointed at a page with no hours looks like, forever.
Judge it by what the snapshot *contains*. That is the one thing this whole workflow exists to
get right.

---

## When it still misbehaves

| Symptom | Fix |
|---|---|
| alerts every day, diff shows the same lines reordered | tick **sort text alphabetically** |
| diff is a captcha number, "Wir öffnen heute um 16:00", or your own IP | add the phrase to **Settings → global ignore text** |
| two watches on the same host keep showing each other's page | give each a **trigger text** marker unique to its page |
| watch went silent after a site redesign | the selector was brittle — re-run the wizard |
| nothing found even after rendering | the page genuinely has no hours; try another page or accept it is not monitorable |

---

## Not every business can be watched

Some are genuinely impossible, and knowing when to stop saves hours:

- **anti-bot walls** (lieferando/DataDome class) return 403 in every mode, plain and browser
- **hours only in an image** or a PDF — nothing to diff
- **social-media-only presence** (a Facebook embed) — usually only a live "currently closed"
  widget, no published hours
- **unstaffed 24/7 locations** — nothing ever changes

Record these somewhere and move on. Re-check occasionally; sites get rebuilt.

---

## Going deeper

[FILTERS.md](./FILTERS.md) is the full method behind the wizard: the decision ladder, the
nine page shapes and how to recognise each, hand-building selectors, the four criteria that
prove a filter is right, and the traps that cost this project real debugging time (why you
must not verify an ignore rule by diffing stored snapshots, why a "recheck all" is not proof
every watch ran, why a German time regex needs more than `\d{1,2}:\d{2}`).

Language support lives in `scripts/hours_lang.py` — `LANGS` currently has `de` and `en`.
Add a dictionary there and both scripts pick it up (`--lang xx`).
