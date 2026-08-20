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
no-watch.json       objects deliberately not watched, with the reason and when to look again
scripts/            wizard, sync, audit, renderer, OSM export  (stdlib only, except lxml)
deploy/             managed global settings — noise-suppression patterns, recheck interval
charts/             Helm chart for changedetection and its browser
apps/               Flux HelmRelease and the values for this cluster
clusters/           Flux entry point — what the cluster reconciles
docker-compose.yml  optional local trial; not needed to contribute
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
| `no_watch.py` | the absence list: what is deliberately not watched, and what is due for another look |
| `coverage.py` | the denominator: every OSM object that could have hours, and which of them are covered |
| `audit_report.py` | the monthly report: what the audit found, posted into the notification room |

Each has `--help`. Nothing writes to changedetection without `--apply`.

`hours_lang.py` and `osm_cd_common.py` are libraries, not commands: hours detection that survives
German pages, and the Overpass query plus the changedetection API client.

### Writing back to OSM

Watching a page answers *what changed*; the map still has to be edited. That path lives here too,
and deliberately so, because it is the half that can write to somebody else's data:

| | |
|---|---|
| `zeiten_export.py` | compare a page's hours against the map, and hold back everything that is not proven |
| `zeiten_durchsehen.py` | sort what is left by the question a human has to answer |
| `zeiten_bestaetigen.py` | page and map agree to the character: stamp `check_date:opening_hours`, change nothing else |
| `zeiten_hand.py` | apply a human's decision from `zeiten-entscheidung.csv`, with the same checks |
| `zeiten_osm.py` | read `opening_hours` out of a page's raw text |
| `pruefe_syntax.py` | check an `opening_hours` value before it is ever uploaded |
| `josm_export.py` | write the accepted changes as a JOSM file, to be reviewed and uploaded by hand |

`opening_hours` is never invented from a page: agreement produces a `check_date`, disagreement
produces a question for a person. Nothing here uploads by itself.

### What is *not* watched

`entries/` answers "what do we watch". On its own that number means little — 502 of how many? The
counterpart is [`no-watch.json`](./no-watch.json): every object that deliberately has no watch,
with the reason, the date it was established, and when to look again. An object belongs in exactly
one of the two lists, and CI fails if it appears in both.

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

### How much is left

`entries/` and `no-watch.json` are both numerators. `scripts/coverage.py` asks OSM for the
denominator: every object in the area that could plausibly carry opening hours, sorted into four
states — watched, a recorded absence, no website tag in OSM, or open.

Selection is by exclusion rather than by a curated list of shop types: everything under
`shop`/`craft`/`office`/`healthcare`/`amenity`/`leisure`/`tourism` with a `name`, `operator` or
`brand`, minus an explicit list of infrastructure that has no hours. A curated list can only find
what someone thought of, and this project has twice been surprised by what it did not think of.

It also cross-tabulates against `opening_hours`, because the two halves are different jobs: an
object with a website and **no** hours in OSM is the cheapest work here — nothing to overwrite,
the tag is simply added — while one with both needs a page-against-map comparison.

```bash
python3 scripts/coverage.py                  # summary
python3 scripts/coverage.py --csv offen.csv  # the open ones, to work through
```

The run also names watches and absences whose OSM object no longer turns up, which is how a
deleted or retagged object gets noticed at all.

### Writing back to OpenStreetMap

Watching a page is half the job; the other half is carrying what changed into the map. These
build JOSM files — they never upload anything themselves, a mapper opens the file and looks at
every object before it goes up.

| | |
|---|---|
| `zeiten_osm.py` | raw hours text from a page → an `opening_hours` proposal, without touching the original |
| `pruefe_syntax.py` | check a value against `opening_hours.js`, the reference library, in a throwaway browser |
| `josm_export.py` | write `.osm` files with `action="modify"` — `website`, `phone`, `email`, `check_date:opening_hours` |
| `zeiten_bestaetigen.py` | where a block of the page matches the map exactly, set only `check_date:opening_hours` |
| `zeiten_durchsehen.py` | sort the disagreements by the question each one asks, for a human to answer |
| `zeiten_hand.py` | apply those answers, still checking syntax, a changed OSM value and a newer `check_date` |
| `zeiten_export.py` | the fully automatic path: replace `opening_hours` where four conditions hold |

`opening_hours` is only ever **widened or replaced after a human decision** — never narrowed
because a page stayed silent about a day. A website can be as stale as the map.

They read their input from CSV files in the working directory, so they are called from wherever
that research lives, not from the repository root:

```bash
cd ../my-research && python3 ../oeffnungszeiten/scripts/zeiten_durchsehen.py
```

**The working data stays out of this repository on purpose.** It holds phone numbers and mail
addresses gathered from business pages, it changes with every run, and none of it is needed to
review a watch. What belongs here is the tooling and the rules it encodes.

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
