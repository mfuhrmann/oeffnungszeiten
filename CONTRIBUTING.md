# Contributing

This repository watches business websites for **opening-hours changes**. One file per watch
lives in [`entries/`](./entries); adding one is a pull request.

You do not need the running service, an API key, or any knowledge of XPath.

---


## Which language

**Structure and documentation are English. Evidence about one Fulda business is German.**

Field names, reason values, code, comments, README, FILTERS.md — English, so that someone setting
this up for another city can read it. The `note` on an entry or on an absence is German: it quotes
what the page actually says ("Liefer zeiten", "Termine nur nach Vereinbarung", "Praxisurlaub"), and
translating a quote weakens it as evidence. The Matrix messages are German too — they are read by
whoever maps Fulda.

## Add a business

**1. Find the page that actually carries the hours.** Usually the homepage, `/kontakt` or
`/oeffnungszeiten`. For a chain it is almost always the branch's own page, not the corporate
site. Check the hours belong to *this* business — a site-wide footer link often leads to a
landlord's office hours or an accessibility statement.

**2. Let the wizard propose the entry:**

```bash
pip install lxml
python3 scripts/filter_wizard.py https://example.de/kontakt --emit entries \
    --name "Example GmbH" --tags fulda-restaurants
```

`--tags` is what groups the watch by category (and decides which sibling its notification
setup is copied from) — pick the tag its neighbours use, e.g. `fulda-restaurants`,
`fulda-bakery`, `fulda-doctors`. Repeat the flag or comma-separate for several. The wizard
says so if you forget.

It prints candidates as **the text each one would capture**. Pick by reading the hours — you
know what your business's opening times look like; you do not need to judge a selector. Heed
the `!` warnings, especially `only N weekday(s)` (half the week is elsewhere) and
`brittle selector` (it will break at the next site edit).

**If the page needs JavaScript** — the wizard says so — start a browser and point the wizard at
it. One container, and no changedetection involved:

```bash
docker run --rm -p 3000:3000 dgtlmoon/sockpuppetbrowser
python3 scripts/filter_wizard.py https://example.de/kontakt --emit entries \
    --name "Example GmbH" --tags fulda-restaurants
```

A browser on `localhost:3000` is found by itself — the wizard prints which one it used.
`--browser-ws ws://host:port` is only needed for a browser somewhere else.

The wizard then writes `fetch_backend: html_webdriver` into the entry by itself. Without Docker
you can still open the pull request — say in the description that the page needs rendering and a
maintainer will finish it.

**3. Commit the file and open a pull request.**

```bash
git add entries/example-gmbh.json
git commit -m "add Example GmbH"
```

CI checks the **structure** of every entry — that is all, and on purpose: it needs no network, so
it never fails for a reason you cannot fix.

Whether the filter really captures the hours is judged from the `captured_sample` in your diff. That
is why it has to be there, and why it has to show the actual opening hours. If you can, check it
against the live page before opening the pull request:

```bash
python3 scripts/validate_entries.py --live --only entries/example-gmbh.json
```

Add `--browser-ws ws://localhost:3000` for a page that needs JavaScript. Note that CSS and JSON-LD
filters are not evaluated even then — only `xpath:` ones are.

### Writing an entry by hand

```json
{
  "schema": 1,
  "name": "Example GmbH",
  "url": "https://example.de/kontakt",
  "filter": "xpath://div[contains(@class,\"opening-hours\")]",
  "captured_sample": "Mo–Fr 09:00–18:00 · Sa 09:00–13:00",
  "lang": "de",
  "added": "2026-08-05"
}
```

| Field | |
|---|---|
| `schema`, `name`, `url` | required |
| `filter` | CSS, `xpath:…` or `json:…`. Omit only if the whole page is genuinely the target |
| `captured_sample` | **please include it** — it is how a reviewer judges the entry without fetching anything |
| `fetch_backend` | `html_webdriver` if the hours need JavaScript |
| `sort_text_alphabetically` | `true` if the block re-orders daily |
| `lang` | `de` (default) or `en` — affects weekday detection |
| `osm_id` | optional, e.g. `node/1579272617`; used to link alerts back to OpenStreetMap |
| `tags` | category names, never tag uuids — a uuid means nothing in another instance |

## Change or remove a business

- **Change** — edit the file. The next sync writes it through.
- **Remove** — `git rm` the file. The watch is gone within the hour. Removing many at once is the
  one thing to announce in the pull request: the sync refuses to delete more than a handful in one
  run, because a checkout that arrives empty looks exactly like a request to delete everything.

## What gets rejected

CI fails a pull request for:

- invalid JSON, a missing `url`/`name`, an unsupported `schema`
- a duplicate slug (the filename is the identity)
- an **absolute XPath** such as `/html/body/div[2]/div/main/…`
- a missing `captured_sample` — nothing in the diff would show what the filter captures

Warnings do not block a merge, but expect a reviewer to ask about them. What makes a selector
brittle, and what to anchor on instead: [FILTERS.md](./FILTERS.md).

## Please do not

- add a business whose site publishes **no hours anywhere**. Such a watch is silent forever and
  looks perfectly healthy; [FILTERS.md](./FILTERS.md) §0 has the measurement. If the wizard finds
  nothing even with `--render`, that is the answer.
- anchor a filter on a generated class (`elementor-element-224ed87`).
- point several entries at a store locator. Split them into per-branch pages instead.

## Background

- [FILTERS.md](./FILTERS.md) — the full method: twelve page shapes, the four criteria that
  prove a filter is right, and the traps that cost real debugging time
- [CONCEPT.md](./CONCEPT.md) — why entries are the source of truth and how the
  service is deployed


## Licence of contributions

By opening a pull request you agree that your **code** is contributed under
[GPL-3.0](./LICENSE) and your **entry data** under
[ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/), matching OpenStreetMap, from which
most of this dataset derives.
