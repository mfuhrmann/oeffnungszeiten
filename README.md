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
    --emit entries --name "Example GmbH" --osm-id node/1579272617 --tags fulda-restaurants
```

The wizard reads the page and prints candidates as **the text each filter would capture** — you
pick by reading German opening hours, not by judging an XPath. `--emit` writes the finished entry
file; you commit it and open a pull request.

Everything else about that path, including pages that only show their hours after JavaScript:
**[CONTRIBUTING.md](./CONTRIBUTING.md)**.

## Why a filter is needed at all

changedetection alerts on *any* text change. Unfiltered, a watch fires on a rotating teaser, a
cookie banner or a visitor counter, and the one alert that matters drowns in them. So every entry
carries a selector that narrows the page down to its opening-hours block. Finding one, and telling
a good one from a plausible one, is the craft of this project: **[FILTERS.md](./FILTERS.md)**.

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
| `rotation_check.py` | did the hours change, or did the page reorder itself? triages an alarm from the stored snapshots |
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

[`no-watch.json`](./no-watch.json) is the counterpart of `entries/`: pages that were looked at and
found to have nothing worth watching, each with a reason and a date to look again. Both lists are
keyed by the page, and CI fails if a page appears in both. Why the reasons are shaped the way they
are: [CONCEPT.md](./CONCEPT.md).

```bash
python3 scripts/no_watch.py                    # summary per reason
python3 scripts/no_watch.py --faellig          # due for another look today
python3 scripts/no_watch.py --standortwechsel  # what a move would put back in play
```

### Which pages become watches

A watch is only worth having if its page publishes hours at all. `prescreen.py` answers that before
anyone builds a filter, sorting candidates into blocked, delivery platform, unreachable, throttled,
no-times and worth-it. Only the last group needs a person.

```bash
python3 scripts/prescreen.py --csv kandidaten.csv --anzahl 10
```

The CSV needs four columns: `osm_id`, `name`, `kategorie`, `website`. Where that list comes from is
outside this repository, and deliberately so: finding objects in OpenStreetMap and writing tags back
into the map is a different job with a different failure cost. This one takes a URL and watches it.

## Documentation

| | |
|---|---|
| [CONTRIBUTING.md](./CONTRIBUTING.md) | add, change or remove a watch |
| [FILTERS.md](./FILTERS.md) | how to find an hours block, and how to know the filter is right |
| [CONCEPT.md](./CONCEPT.md) | why this exists, and why watches are files in git |
| [docs/changedetection.md](./docs/changedetection.md) | how it is deployed, and the three decisions behind it |
| [docs/notifications.md](./docs/notifications.md) | how a change reaches the Matrix room, and how to seed the session |
| [entries/README.md](./entries/README.md) | what an entry contains, and under which licence |

Six documents, and each answers one question. This page is the map; nothing here is explained
twice.

## Licence

GPL-3.0-or-later, see [LICENSE](./LICENSE). Opening hours read from business websites are facts,
not creative works; when you carry them into OSM, record the source and the date
(`source:opening_hours`, `check_date:opening_hours`) and never copy from a map service whose licence
forbids it.
