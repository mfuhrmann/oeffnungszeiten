# Öffnungszeiten

Which websites of businesses in Fulda get checked for **changed opening hours**, so a mapper hears
about it and can update OpenStreetMap. One file per watch, changed through pull requests.

The watching itself is done by a [changedetection.io](https://changedetection.io) instance, deployed
from this repository by Flux — see [docs/changedetection.md](./docs/changedetection.md).

Every watch is rechecked every three days.

## Add or change a watch

```bash
pip install lxml
python3 scripts/filter_wizard.py https://example.de/kontakt \
    --emit entries --name "Example GmbH" --tags fulda-restaurants
```

The wizard reads the page and prints candidates as **the text each filter would capture** — you
pick by reading German opening hours, not by judging an XPath. It writes the entry file; you commit
it and open a pull request. Full walkthrough: **[CONTRIBUTING.md](./CONTRIBUTING.md)**.

For a page whose hours only appear after JavaScript, start one container and the wizard finds it:

```bash
docker run --rm -p 3000:3000 dgtlmoon/sockpuppetbrowser
```

## Why a filter is needed at all

changedetection is a **text-diff tool, not a smart crawler**. It snapshots the visible text of one
URL and alerts when *anything* changes — it has no concept of an opening hour. Two failure modes
follow: watch the whole page and every banner, captcha or "open now" widget alerts; watch the wrong
page and nothing ever alerts, which looks exactly like a healthy watch.

Both are solved by pointing each watch at the hours block. How to find one, the page shapes that
occur in practice, and what counts as proof that a filter is right:
**[FILTERS.md](./FILTERS.md)**.

## Layout

```
entries/            one file per watch — the source of truth
  .lock.json        slug → watch uuid, per changedetection instance
no-watch.json       the block list: pages looked at and not worth watching, with the reason
scripts/            wizard, prescreen, sync, audit, renderer  (stdlib only, except lxml)
deploy/             managed global settings: noise-suppression patterns, recheck interval
charts/             Helm chart for changedetection and its browser
apps/               Flux HelmRelease and the values for this cluster
clusters/           Flux entry point — what the cluster reconciles
```

## The scripts

| | |
|---|---|
| `filter_wizard.py` | propose a filter for a page, pick it by reading the captured text |
| `entries_sync.py` | reconcile a changedetection instance against `entries/` |
| `validate_entries.py` | what CI runs on every pull request — structure, and the filter against the live page |
| `watch_audit.py` | what each watch actually captured: RED / AMBER / green with reasons |
| `cdp_render.py` | render a page through a headless browser, no changedetection needed |
| `cd_export.py` | turn a UI experiment back into entry files |
| `apply_global_settings.py` | merge `deploy/global-settings.json` into an instance |
| `matrix_relay_seed.py` | mint the Matrix session the notification relay runs on |
| `no_watch.py` | the block list: which pages are deliberately not watched, and what is due for another look |
| `prescreen.py` | does this page publish hours at all — the question before a filter is worth building |
| `audit_report.py` | the weekly report: what the audit found, posted into the notification room |

Each has `--help`. Nothing writes to changedetection without `--apply`.

`hours_lang.py` and `osm_cd_common.py` are libraries, not commands: hours detection that survives
German pages, and the changedetection API client.

### What is *not* watched

`entries/` answers "which pages do we watch". Its counterpart is
[`no-watch.json`](./no-watch.json): the pages that were looked at and found to have nothing worth
watching, with the reason, the date it was established, and when to look again. A page belongs in
exactly one of the two lists, and CI fails if it appears in both.

Both lists are keyed by the **page**, not by the map object. One address can carry several
businesses — a branch list, a practice with two doctors, a shared building — and whether OSM knows
them is a different question from whether the page publishes hours. Keying by object hid that: the
same page could be recorded as "publishes nothing" for one object while a watch on it was
capturing hours for another, which is exactly what the first run of the page-keyed check found.

Two kinds of reason, and `recheck` carries the difference:

- a property of the **business** — the page states no hours, appointment only, only a social
  profile, only a delivery microsite, site gone — gets a date. The question at that date is not
  "can we fetch it now" but *has this business got its own page yet*. That distinction matters
  for the platform cases: a Lieferando microsite publishes **delivery** windows that flip when
  the shop toggles offline, and a social profile hides its hours behind a login wall among
  rotating follower counts. Neither becomes usable by fetching from somewhere else.
- a property of **this instance** — `anti-bot`, `datacenter-block` — gets `on-relocation`.
  Time changes nothing there: the block is the same tomorrow. What changes it is the instance
  moving to a residential address, or the pinned user agent being bumped. Measured on one host:
  200 from a home connection, 403 from the VPS, same user agent, same second.

Every reason names the **cause**, not the symptom, and the note has to say what the page *does*
show and how that was checked — CI rejects a record without one. "No hours published" was the old
wording, and it swept three different things into one bucket: a page that really states nothing,
a chain whose branch link nobody found, and our own discovery landing on a site-wide footer link.

What does **not** belong in this list is work nobody has done yet — a filter that needs a browser,
a chain page whose branch link has not been found, a `website` tag pointing at the wrong company.
That is backlog, and putting it under a heading like "unmonitorable" is how it disappears.

```bash
python3 scripts/no_watch.py                    # summary per reason
python3 scripts/no_watch.py --faellig          # due for another look today
python3 scripts/no_watch.py --standortwechsel  # what a move would put back in play
```

One record, in full:

```json
{ "osm_id": "node/12842624670",
  "name": "Kopfarbeit",
  "reason": "social-only",
  "established": "2026-08-01",
  "source": "https://www.facebook.com/…",
  "recheck": "2027-02-01",
  "note": "Einziger Auftritt ist eine Facebook-Seite: Login-Wand davor, dahinter rotierende
           Follower-Zahlen und kein stabiler Anker fuer die Zeiten." }
```

### Which pages become watches

A watch is worth having only if its page publishes hours at all. `prescreen.py` answers that
before anyone builds a filter: it fetches each candidate once and sorts it into blocked (a host
this instance cannot reach), platform (a delivery microsite, whose times are DELIVERY windows),
unreachable, throttled, no-times, and worth-it. Only the last group needs a person; the first
three are already the note that belongs in the block list.

```bash
python3 scripts/prescreen.py --csv kandidaten.csv --anzahl 10
```

Where the candidates come from is deliberately outside this repository. Finding objects in
OpenStreetMap, proving which page belongs to which shop and writing tags back into the map is a
different job with a different failure cost — a wrong watch is noise, a wrong tag is somebody
else's data. It lives in its own project; this one takes a URL and watches it.

## Documentation

| | |
|---|---|
| [CONTRIBUTING.md](./CONTRIBUTING.md) | add, change or remove a watch |
| [ADD-A-WATCH.md](./ADD-A-WATCH.md) | the same for your own changedetection instance, without this repo |
| [FILTERS.md](./FILTERS.md) | how to find an hours block, and how to know the filter is right |
| [CONCEPT.md](./CONCEPT.md) | why this exists, and why watches are files in git |
| [docs/changedetection.md](./docs/changedetection.md) | how it is deployed, and the three decisions behind it |
| [docs/notifications.md](./docs/notifications.md) | how a change reaches the Matrix room, and how to seed the session |

## Licence

AGPL-3.0-or-later, see [LICENSE](./LICENSE). Opening hours read from business websites are facts,
not creative works; when you carry them into OSM, record the source and the date
(`source:opening_hours`, `check_date:opening_hours`) and never copy from a map service whose licence
forbids it.
