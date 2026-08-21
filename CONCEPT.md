# Concept — why this exists and how it is built

## Why watch opening hours

Opening hours are the detail in OpenStreetMap that goes stale fastest, and usually nobody notices
until they are standing in front of a locked door. There are only two places to get them: the sign
on that door, and the operator's own website, if there is one.

## What this project does

It watches the website where a business publishes its hours and reports when the text there
changes. That report is a **hint for a mapper**, not an automatic edit: the website can be wrong, the change may be a
holiday notice, and a sign on the door beats the website in case of doubt.

The boundary is deliberate. Nothing is imported; people are pointed at something they would otherwise
miss.

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

`captured_sample` is the text the filter actually caught when the entry was written. CI never
fetches a page, so this line is all a reviewer has: an XPath says nothing about whether it caught
opening hours, a news box or the phone hotline — the captured text says it at a glance. It is
evidence from the day of submission, not a live value: the page moves on, the line stays, and
nothing rechecks it. What each watch captures *today* is what `watch_audit.py` reads.

`osm_id` is optional and purely a reference, so a notification can carry an "edit this in OSM" link.

**The slug is the filename.** `robes-bike-house.json` is the slug `robes-bike-house`. That name is
the identity in git; an entry file carries no id of its own. changedetection gives every watch a
uuid instead, and nothing stores which uuid belongs to which slug.

The sync works it out each run: it matches by URL, and where one URL carries two businesses (a
restaurant and its beer garden, two outlets of one hotel) by name against the watch title.
`entries/.lock.json` remembers the answer but is **not committed**: it fits only one instance, and
the cluster syncs from a throwaway checkout. Measured twice against the live instance: with no
cache at all, every watch was adopted and none created. A *stale* cache is the dangerous one,
because it claims a mapping that has moved on.

## The pull request is the API

| Action | PR |
|---|---|
| add a watch | add a file |
| remove a watch | `git rm` the file, and say so — the watch is taken away by hand |
| fix a filter | edit the file |

## Git is authoritative

The entry files decide, not the app. Every hour `entries_sync.py` compares the two and writes the
files through: it creates a watch that is missing and corrects one that differs.

Two things follow.

**Edit in the UI and you lose it.** Change a filter in changedetection and it works until the next
sync, then the file wins. To keep such an experiment, pull it back into a file with
`cd_export.py --split entries` and open a pull request. That script is a round-trip helper, not a
backup.

**Deleting is the exception.** The sync never removes a watch on its own. It can only tell that a
watch is unclaimed, and saying "no file claims this" is not the same as "this must go": a renamed
file or a half-finished checkout would look identical. So a removed entry leaves its watch running
until a person takes it away.

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
- **No discovery.** Nothing here queries Overpass, and nothing writes to the map. A shop that opens
  is watched once somebody adds a file. Finding candidates in OSM, proving which page belongs to
  which shop and carrying values back into the map is a separate project: a wrong watch is noise,
  a wrong tag is somebody else's data, and the two deserve different rules.

## Another city

Nothing here is tied to Fulda. An entry is a URL, a filter and a language; weekday and time-format
detection lives in `scripts/hours_lang.py` and takes new languages. Seeding many businesses at once
is the separate exercise named above: query an OSM category in an area, keep the objects carrying a
`website` tag, and find the page on each site that holds the hours. What arrives here is the
result — a URL worth watching.
