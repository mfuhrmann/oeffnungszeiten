# Concept — why this exists and how it is built

## Why watch opening hours

Opening hours are the detail in OpenStreetMap that goes stale fastest. A shop moves its closing day, a
practice changes its consultation times, a café opens later in winter — and OSM still says what
somebody surveyed three years ago. Unlike a wrong house number, nobody notices until they are standing
in front of a locked door.

It cannot be checked cartographically: there is no aerial image and no address register for opening
hours. They exist on the door and on the business's own website, and the website is the only source
you may query from a distance.

## What this project does

It watches the page where a business publishes its hours and reports when the text there changes. That
report is a **hint for a mapper**, not an automatic edit: the website can be wrong, the change may be a
holiday notice, and a sign on the door beats the website in case of doubt.

The boundary is deliberate. Nothing is imported; people are pointed at something they would otherwise
not see. An import would be a different exercise under different rules — see
[the OSM import guidelines](https://wiki.openstreetmap.org/wiki/Import/Guidelines).

## Where it works and where it does not

Success depends on the shape of the website, not on the trade.

**Well suited — owner-run businesses with their own small site.** Restaurants, cafés, doctors,
physiotherapists, hairdressers, workshops, small shops. The hours sit as text on `/kontakt` or
`/öffnungszeiten`, often in a table. A filter on that block holds for years.

**Poorly suited — chains.** One corporate page covers hundreds of branches and the hours live behind a
JavaScript store locator keyed by postcode. A diff on the corporate page says nothing about the branch
in town. Sometimes each branch has its own URL, and then it does work — that is how several bakery and
supermarket branches are covered here.

**Not suited at all:**

- **Bot protection.** Some sites refuse every automated fetch, browser included. Delivery platforms
  practically always.
- **Hours only in an image.** A photographed sign as a JPEG is text nobody can diff.
- **Live status instead of hours.** A page showing only "open now" changes several times a day and
  says nothing about the week.
- **No published hours.** More common than expected, especially where the web presence is a social
  media page.

## The alternative that gets overlooked

If you only need the *current* hours, read `opening_hours` straight from OSM — no monitoring, no
website. That is simpler and enough for many purposes. This project answers the other question:
**how do you notice that the value in OSM stopped being true.**

---

## Entries are the source of truth

One file per watch in `entries/`. The filename slug is the identity: stable, readable, and it makes
every change a legible diff.

```json
{
  "schema": 1,
  "name": "Robe's Bike House",
  "url": "https://your-bike-house.de/",
  "filter": "xpath://div[contains(translate(.,\"MONTAG…\"),\"dienstag\")][not(.//div[…])]",
  "tags": ["fulda-bicycle"],
  "lang": "de",
  "captured_sample": "Dienstag – Freitag10:00 Uhr bis 18:00 UhrSamstag10:00 Uhr bis 14:00 Uhr",
  "osm_id": "node/1579272617",
  "added": "2026-08-04"
}
```

`captured_sample` is what makes review possible: a reviewer reads the hours in the diff and can tell
whether the filter grabbed the hours block, a news box or a marketing paragraph — the judgement call
that keeps recurring ([FILTERS.md](./FILTERS.md) §4). It is documentation, not state; sync ignores it.
`osm_id` is optional and purely a reference, so a notification can carry an "edit this in OSM" link.

**The URL cannot be the identity.** Some pages back two businesses each — a restaurant and its beer
garden, a museum mapped twice, two outlets of one hotel — so a URL-keyed sync could not tell which
watch an entry owns. `entries/.lock.json` records slug → uuid and is committed: derived state, but it
makes the reconcile deterministic and lets a fresh clone adopt an existing instance instead of
duplicating it. **One lock per instance** — a lock from a different instance makes the sync create
everything twice.

## The pull request is the API

| Action | PR |
|---|---|
| add a watch | add a file |
| remove a watch | `git rm` the file |
| fix a filter | edit the file |

## Git is authoritative

`entries_sync.py` **enforces** the entry files: create what is missing, update what differs, delete
watches whose file is gone. One authority is worth more than the convenience of editing in the UI.

Consequence: **a filter tweaked in the UI is reverted on the next sync.** To keep a UI experiment, run
`cd_export.py --split entries` and open a PR. That script is a round-trip helper, not a backup.

## Topology

```
  contributor                  this repository                    cluster
  filter_wizard.py ──PR──▶  entries/   ◀────────pull──────────  sync CronJob
                            charts/ apps/ clusters/  ──▶ Flux ──▶ changedetection + browser
```

The cluster **pulls**. Nothing is exposed inbound, so CI cannot reach the app and the reconcile has to
run from inside. There is no Ingress either: changedetection has one shared password and no user
model, and an authenticated user can point a watch at any URL — an exposed instance is an SSRF pivot.
Access is `kubectl port-forward`.

Deployment — chart, HelmRelease, cluster wiring — lives in `charts/`, `apps/` and `clusters/`, and is
documented in [docs/changedetection.md](./docs/changedetection.md). Those paths reconcile into a live
cluster on merge, so they are covered by CODEOWNERS.

## No backup of the volume

Snapshot history is not worth preserving. A re-created watch stores its first fetch as a baseline and
stays quiet until the hours actually change, so nothing false is raised. What must be in git, because
it exists nowhere else: the entry files and `deploy/global-settings.json` — the noise-suppression
patterns and the recheck interval.

## Boundaries

- **No submission web form.** The pull request flow needs no service, no moderation tooling and no
  attack surface. Such a form would need OSM OAuth2 for identity and SSRF protection, because it
  fetches user-supplied URLs server-side.
- **No multi-tenant changedetection.** One shared password, no user model. Contributors never get
  access; the repository is the interface.
- **No discovery.** Coverage is human work. Nothing queries Overpass to find new businesses, so a shop
  that opens is only watched once somebody adds a file.

## Another city

Nothing here is tied to Fulda. An entry is a URL, a filter and a language; weekday and time-format
detection lives in `scripts/hours_lang.py` and takes new languages. Seeding many businesses at once is
a separate exercise — query an OSM category in an area, keep the objects carrying a `website` tag, and
find the page on each site that holds the hours.
