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

## What is deliberately not watched

`entries/` says which pages are watched. [`no-watch.json`](./no-watch.json) says which pages were
looked at and found to have nothing worth watching. A page belongs in exactly one of the two, and
CI fails if it appears in both.

Both are keyed by the **page**, not by the map object. One address can carry several businesses: a
branch list, a practice with two doctors, a shared building. Whether OSM knows them is a different
question from whether the page publishes hours, and keying by object hid that. The first run of the
page-keyed check found a page recorded as "publishes nothing" for one object while a watch on it
was capturing hours for another.

The reason names the cause, not the symptom, and `recheck` follows from it:

- **A property of the business** (states no hours, appointment only, only a social profile, only a
  delivery microsite, site gone) gets a date. The question at that date is not "can we fetch it
  now" but *has this business got its own page yet*. A delivery microsite publishes delivery
  windows that flip when the shop goes offline; a social profile hides hours behind a login wall.
  Neither improves by fetching from somewhere else.
- **A property of this instance** (`anti-bot`, `datacenter-block`) gets `on-relocation`. Time
  changes nothing there. What changes it is the instance moving to a residential connection, or the
  pinned user agent being bumped. Measured on one host: 200 from a home line, 403 from the VPS,
  same user agent, same second.

Every record carries a note saying what the page *does* show and how that was checked; CI rejects
one without it. What does **not** belong in this list is work nobody has done yet: a filter that
needs a browser, a chain page whose branch link has not been found, a `website` tag pointing at the
wrong company. That is backlog, and filing it under "unmonitorable" is how it disappears.

```json
{ "url": "https://www.facebook.com/…",
  "name": "Kopfarbeit",
  "reason": "social-only",
  "established": "2026-08-01",
  "recheck": "2027-02-01",
  "note": "Einziger Auftritt ist eine Facebook-Seite: Login-Wand davor, dahinter rotierende
           Follower-Zahlen und kein stabiler Anker fuer die Zeiten.",
  "osm_id": "node/12842624670" }
```

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

**A fix made in the UI does not last.** Repairing a filter in changedetection is often the quickest
way to get it right, because you see immediately what it captures. It holds until the next sync, and
then the entry file wins. So when the filter finally works, write it into the file: read the watch
back out with `cd_export.py --split entries`, or copy the selector across by hand, and open a pull
request. Otherwise the work is gone at the next full hour.

**Deleting is guarded.** Remove a file and the sync removes the watch. It cannot be that simple
on its own, though: "no file claims this watch" and "the checkout arrived empty" look exactly the
same from the cluster, and the second must never delete anything. So the job counts. A handful of
unclaimed watches is a merged pull request and gets deleted; more than that, or no entries loaded
at all, and it deletes nothing and says so in the Matrix room. The limit is a chart value.

## Topology

```
  a contributor            this repository                 the server
  filter_wizard.py ─PR─▶  entries/          ◀── reads ──   sync job, once an hour
                          charts/ apps/     ◀── reads ──   Flux, on every commit
                                                           changedetection + a browser
```

Two programs on the server read this repository, and nothing here reaches into the server.

**Flux** keeps the server's setup equal to `charts/`, `apps/` and `clusters/`. Merge a change there
and the running software is adjusted a few minutes later. Those directories therefore describe a
live machine, not a plan, which is why they are covered by CODEOWNERS.
[docs/changedetection.md](./docs/changedetection.md) explains what they contain.

**The sync job** does the same for the watches: once an hour it clones this repository and makes
changedetection match `entries/`.

Both **pull**, and that is the point: the server has no address the outside world can call, so
nothing in this repository and no build in CI can reach it. changedetection has no public URL
either, for reasons of its own that
[docs/changedetection.md](./docs/changedetection.md) sets out. Looking at its interface means
forwarding a port from your own machine, which needs access to the server first.

## No backup of the volume

Snapshot history is not worth preserving. A re-created watch stores its first fetch as a baseline and
stays quiet until the hours actually change, so nothing false is raised. What must be in git, because
it exists nowhere else: the entry files and `deploy/global-settings.json` — the noise-suppression
patterns and the recheck interval.

## Boundaries

- **No submission web form of our own.** A form with its own login and its own server would need
  OSM OAuth2 for identity and SSRF protection, because it fetches user-supplied URLs server-side.
  Both objections are answered by borrowing GitHub instead: an issue form carries the URL, the
  identity is the GitHub account a pull request needs anyway, and the fetch runs in a throwaway
  runner that holds no token and can reach nothing of ours (`.github/workflows/wizard.yml`).
  What stays out is a service we operate and moderate.
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
