# Contributing

This repository watches business websites for **opening-hours changes**. One file per watch lives
in [`entries/`](./entries); adding, changing or removing one is a pull request. You need no running
service, no API key and no knowledge of XPath.

**Structure and documentation are English, evidence about a Fulda business is German.** Field names,
code and docs read for another city; a `note` quotes what the page says ("Termine nur nach
Vereinbarung", "Praxisurlaub"), and translating a quote weakens it as evidence. The Matrix messages
are German too: they are read by whoever maps Fulda.

---

## 1. Find the page that carries the hours

Usually the homepage, `/kontakt` or `/oeffnungszeiten`. For a chain it is almost always the branch's
own page, not the corporate site. Check that the hours belong to *this* business: a site-wide footer
link often leads to a landlord's office hours or an accessibility statement.

A page that publishes hours nowhere is not worth a watch. Such a watch stays silent forever and
looks perfectly healthy; [FILTERS.md](./FILTERS.md) §0 has the measurement.

## 2. Get the OSM id

Search the business on [openstreetmap.org](https://www.openstreetmap.org), open the object, and take
the id out of the URL: `node/1579272617`, sometimes `way/…` or `relation/…`.

An alert is supposed to end in an OSM edit, so the message carries a link to the object, built from
`osm_id`. Without one the alert still arrives, with the page URL and the diff but no link, and
whoever reads it has to find the business in the map by hand: most of the work the alert exists to
save. Nearly every entry here has an id. Leave it out only when the business is genuinely not in OSM
yet, and say so in the pull request. Nothing in the code follows the id; this repository never
queries OSM itself.

## 3. Let the wizard write the entry

```bash
pip install lxml
python3 scripts/filter_wizard.py https://example.de/kontakt --emit entries \
    --name "Example GmbH" --osm-id node/1579272617 --tags fulda-restaurants
```

`--tags` groups the watch by category: pick the tag its neighbours use, e.g. `fulda-restaurants`,
`fulda-bakery`, `fulda-doctors`. Repeat the flag or comma-separate for several. The wizard says so
if you leave `--tags` or `--osm-id` off.

It prints candidates as **the text each one would capture**. Pick by reading the hours: you know
what your business's opening times look like, and you do not need to judge a selector. Heed the `!`
warnings, especially `only N weekday(s)` (half the week is elsewhere) and `brittle selector` (it
will break at the next site edit).

**If the page needs JavaScript**, the wizard says so. Start a browser and run it again; no
changedetection involved:

```bash
docker run --rm -p 3000:3000 dgtlmoon/sockpuppetbrowser
```

A browser on `localhost:3000` is found by itself and the wizard prints which one it used;
`--browser-ws ws://host:port` is only for a browser somewhere else. The wizard then writes
`fetch_backend: html_webdriver` into the entry. Without Docker you can still open the pull request:
say in the description that the page needs rendering, and a maintainer will finish it.

## 4. Check the entry

```bash
python3 scripts/validate_entries.py --only entries/example-gmbh.json           # structure, as CI does
python3 scripts/validate_entries.py --live --only entries/example-gmbh.json    # and against the page
```

The first call is what CI will run: **structure only**, and on purpose, because it needs no network
and so never fails for a reason you cannot fix. The second one fetches the page and is worth the
minute before anything leaves your machine. Add `--browser-ws ws://localhost:3000` for a page that
needs JavaScript. Only `xpath:` filters are evaluated live; CSS and JSON-LD ones are not.

Whether the filter really captures the hours is judged by a human, from the `captured_sample` in
your diff. That is why it has to be there and has to show actual opening hours.

## 5. Open the pull request

```bash
git checkout -b add-example-gmbh
git add entries/example-gmbh.json
git commit -m "add Example GmbH"
git push origin add-example-gmbh
gh pr create --fill
```

Never commit onto `main`. Without the `gh` CLI, push the branch and open the pull request on
github.com.

---

## The entry file

The wizard writes all of it. Written by hand it looks like this:

```json
{
  "schema": 1,
  "name": "Example GmbH",
  "url": "https://example.de/kontakt",
  "filter": "xpath://div[contains(@class,\"opening-hours\")]",
  "captured_sample": "Mo–Fr 09:00–18:00 · Sa 09:00–13:00",
  "osm_id": "node/1579272617",
  "tags": ["fulda-restaurants"],
  "lang": "de",
  "added": "2026-08-05"
}
```

| Field | |
|---|---|
| `schema`, `name`, `url` | required |
| `filter` | CSS, `xpath:…` or `json:…`. Omit only if the whole page is genuinely the target |
| `captured_sample` | required: it is how a reviewer judges the entry without fetching anything |
| `osm_id` | `node/…`, `way/…` or `relation/…`. Not enforced by CI, but see step 2 |
| `tags` | category names, never tag uuids: a uuid means nothing in another instance |
| `lang` | `de` (default) or `en`, affects weekday detection |
| `fetch_backend` | `html_webdriver` if the hours need JavaScript, set by the wizard |
| `sort_text_alphabetically` | `true` if the block re-orders daily, set by the wizard |

The filename comes from the name and the URL, so two branches of one chain do not collide.

## Change or remove a watch

- **Change**: edit the file. The next sync writes it through.
- **Remove**: `git rm` the file. The watch is gone within the hour. Removing many at once is the one
  thing to announce in the pull request: the sync refuses to delete more than a handful in one run,
  because a checkout that arrives empty looks exactly like a request to delete everything.

## What gets rejected

CI fails a pull request for:

- invalid JSON, a missing `url`/`name`, an unsupported `schema`
- a duplicate slug (the filename is the identity)
- an **absolute XPath** such as `/html/body/div[2]/div/main/…`
- a missing `captured_sample`: nothing in the diff would show what the filter captures

A reviewer will send back, without CI failing:

- a filter anchored on a generated class (`elementor-element-224ed87`)
- several entries pointing at one store locator. Split them into per-branch pages instead
- a page that publishes no hours (step 1), and the wizard's `!` warnings if they were ignored

What makes a selector brittle, and what to anchor on instead: [FILTERS.md](./FILTERS.md).

## Background

- [FILTERS.md](./FILTERS.md): the page shapes that keep coming back, the four criteria that prove a
  filter is right, and the traps that cost real debugging time
- [CONCEPT.md](./CONCEPT.md): why entries are the source of truth
- [docs/changedetection.md](./docs/changedetection.md): how the service is deployed

## Licence of contributions

By opening a pull request you agree that your **code** is contributed under
[GPL-3.0](./LICENSE) and your **entry data** under
[ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/), matching OpenStreetMap, from which
most of this dataset derives.
