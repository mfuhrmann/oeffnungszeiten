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
scripts/            wizard, sync, audit, renderer  (stdlib only, except lxml)
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

Each has `--help`. Nothing writes to changedetection without `--apply`.

## Documentation

| | |
|---|---|
| [CONTRIBUTING.md](./CONTRIBUTING.md) | add, change or remove a watch |
| [ADD-A-WATCH.md](./ADD-A-WATCH.md) | the same for your own changedetection instance, without this repo |
| [FILTERS.md](./FILTERS.md) | how to find an hours block, and how to know the filter is right |
| [CONCEPT.md](./CONCEPT.md) | why this exists, and why watches are files in git |
| [docs/changedetection.md](./docs/changedetection.md) | how it is deployed, and the three decisions behind it |

## Licence

AGPL-3.0-or-later, see [LICENSE](./LICENSE). Opening hours read from business websites are facts,
not creative works; when you carry them into OSM, record the source and the date
(`source:opening_hours`, `check_date:opening_hours`) and never copy from a map service whose licence
forbids it.
