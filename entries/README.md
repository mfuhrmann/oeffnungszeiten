# entries/ — data licence and provenance

The code in this repository is GPL-3.0 (see [../LICENSE](../LICENSE)). **These entry files
are not covered by it** — a code licence cannot license a database.

## Licence: ODbL 1.0

276 of the 280 entries here were derived from **OpenStreetMap** — the business names, their
`website` tags and the `osm_id` references all came from Overpass queries against OSM. That
makes this a derivative database, so it is published under the
[Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/), the same terms
as the source.

In practice:

- **Attribution** — "© OpenStreetMap contributors" wherever this data is shown.
- **Share-alike** — if you publish a modified version of this database, publish it under ODbL too.
- GPL-3.0 still covers `scripts/`, the documentation, and everything that is not data.

## What is in an entry

Facts about a public business: its name, its public website, the page that lists its opening
hours, and a selector pointing at the hours block. `captured_sample` is a short excerpt of
publicly published opening hours, kept so a reviewer can judge an entry from the diff.

No personal data. Practice entries name the practice, not the practitioners — where a
doctor's name was needed to identify the right branch page (meliva), it went in the commit
message, not the entry.

## Corrections

If an entry has the wrong website, the wrong branch, or a business that has closed, open a
pull request — see [../CONTRIBUTING.md](../CONTRIBUTING.md). Where the underlying OSM object
is wrong, fixing it in OSM helps everyone; several entries carry a `note` recording exactly
that (a stale O2 node, a `website` tag pointing at a manufacturer rather than the shop).
