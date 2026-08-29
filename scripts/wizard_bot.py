#!/usr/bin/env python3
"""
wizard_bot.py — run the filter wizard from a GitHub issue, so nobody has to fork.

Adding a watch means picking the filter that captures a page's opening hours, and that is the
one step of the contribution that needs code. This wraps `filter_wizard.py` for the workflows in
`.github/workflows/wizard*.yml`: the issue form carries URL, name and OSM id, the bot fetches the
page, posts the candidates as a comment, and turns `/pick N` into a pull request.

Three subcommands, all of them file-in/file-out so a workflow never has to pass untrusted text
through a shell:

  parse       read the issue body, print the fields as JSON (what the other two do first)
  candidates  fetch the page, write comment.md and candidates.json
  emit        fetch the page, take rank N, write the entry file, its PR body and meta.json

It writes no state anywhere else, holds no token, and talks to nothing but the page under
examination — the fetch runs in a job that has no write permission, and only its artifact
crosses into the job that can comment or push.

Examples:
  python3 scripts/wizard_bot.py candidates --body-file body.md --out out
  python3 scripts/wizard_bot.py emit --body-file body.md --pick 2 --out out
"""
import argparse
import collections
import ipaddress
import json
import os
import re
import socket
import sys
import urllib.parse

import hours_lang as L
import osm_cd_common as C

# filter_wizard pulls in lxml. Everything that reads the issue works without it, and CI checks
# that half in the job that installs nothing, so the import waits until a page is fetched.

# The issue form's headings. GitHub renders one "### <label>" per field, in this order, and
# writes "_No response_" where an optional field was left empty.
FIELDS = {
    "Seite mit den Öffnungszeiten": "url",
    "Name des Betriebs": "name",
    "OSM-Objekt": "osm_id",
    "Kategorie-Tag": "tags",
    "Sprache der Seite": "lang",
}
NO_RESPONSE = "_no response_"
OSM_ID = re.compile(r"^(node|way|relation)/[1-9][0-9]*$")
TAG = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")
PICK = re.compile(r"^\s*/pick\s+([0-9]{1,2})\s*$", re.M)
MAX_CANDIDATES = 6
MAX_TEXT = 600


class Refused(Exception):
    """Something in the issue cannot be worked with. The message goes back as a comment."""


def parse_body(body):
    r"""Issue-form markdown -> {url, name, osm_id, tags, lang}. Unknown headings are ignored.

    >>> b = "### Name des Betriebs\n\nCaf\u00e9 `X`\n\n### OSM-Objekt\n\n_No response_\n"
    >>> parse_body(b) == {"name": "Caf\u00e9 `X`", "osm_id": None}
    True
    >>> parse_body("### Sonst noch etwas\n\nzwei Betriebe auf der Seite\n")
    {}
    """
    out, key = {}, None
    for line in body.splitlines():
        head = line.strip()
        if head.startswith("###"):
            key = FIELDS.get(head.lstrip("#").strip())
            continue
        if key and head:
            out.setdefault(key, []).append(head)
    fields = {k: " ".join(v).strip() for k, v in out.items()}
    return {k: (None if not v or v.lower() == NO_RESPONSE else v) for k, v in fields.items()}


def check_url(raw):
    r"""A URL we are willing to fetch. Public http(s) only.

    The job doing the fetching has no token and no route to anything of ours, so this is not
    what keeps the cluster safe — that is the workflow's permissions. It keeps the bot from
    being pointed at a runner's own network as a matter of course, and it turns a typo into a
    sentence the reporter can act on instead of a stack trace.

    The scheme is judged before normalisation, because `normalize_url` prepends `https://` to
    anything that does not already carry a scheme it knows: `ftp://x.de` would otherwise become
    `https://ftp://x.de`, a fetch of the host `ftp`.

    >>> check_url("ftp://example.de")
    Traceback (most recent call last):
    wizard_bot.Refused: Nur http und https, nicht `ftp`.
    >>> check_url("")
    Traceback (most recent call last):
    wizard_bot.Refused: Es fehlt die URL der Seite.
    """
    if not raw:
        raise Refused("Es fehlt die URL der Seite.")
    raw = raw.split()[0]
    scheme = re.match(r"([a-zA-Z][a-zA-Z0-9+.-]*)://", raw)
    if scheme and scheme.group(1).lower() not in ("http", "https"):
        raise Refused(f"Nur http und https, nicht `{scheme.group(1).lower()}`.")
    url = C.normalize_url(raw)
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname
    if not host:
        raise Refused(f"Keine Adresse in `{raw}` erkennbar.")
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
    except OSError as e:
        raise Refused(f"`{host}` ist nicht auflösbar ({e.strerror or e}).")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise Refused(f"`{host}` zeigt auf eine nicht öffentliche Adresse ({ip}).")
    return url


def check_meta(f):
    """Everything about the entry that needs no network, checked on its own so CI can too.

    >>> check_meta({"name": "Café X", "osm_id": "https://www.openstreetmap.org/node/42"})["osm_id"]
    'node/42'
    >>> check_meta({"name": "X", "tags": "fulda-cafe, fulda"})["tags"]
    ['fulda-cafe', 'fulda']
    >>> check_meta({"name": "X"})["lang"]
    'de'
    >>> check_meta({"name": ""})
    Traceback (most recent call last):
    wizard_bot.Refused: Es fehlt der Name des Betriebs.
    >>> check_meta({"name": "X", "osm_id": "12345"})
    Traceback (most recent call last):
    wizard_bot.Refused: `12345` ist keine OSM-Id in der Form `node/123456`.
    >>> check_meta({"name": "X", "tags": "Fulda Bäckerei"})
    Traceback (most recent call last):
    wizard_bot.Refused: `Fulda` ist kein brauchbarer Tag (klein, ohne Leerzeichen, z. B. `fulda-bakery`).
    >>> check_meta({"name": "X", "lang": "fr"})
    Traceback (most recent call last):
    wizard_bot.Refused: Sprache `fr` kennt der Wizard nicht, nur `de` und `en`.
    """
    name = (f.get("name") or "").strip()
    if not name:
        raise Refused("Es fehlt der Name des Betriebs.")
    if len(name) > 80:
        raise Refused("Der Name ist länger als 80 Zeichen.")
    osm_id = (f.get("osm_id") or "").strip() or None
    if osm_id:
        # accept the whole openstreetmap.org address, since that is what the browser offers
        osm_id = "/".join(osm_id.rstrip("/").split("/")[-2:]) if "/" in osm_id else osm_id
        if not OSM_ID.match(osm_id):
            raise Refused(f"`{osm_id}` ist keine OSM-Id in der Form `node/123456`.")
    tags = [t.strip() for t in re.split(r"[,\s]+", f.get("tags") or "") if t.strip()]
    for t in tags:
        if not TAG.match(t):
            raise Refused(f"`{t}` ist kein brauchbarer Tag (klein, ohne Leerzeichen, "
                          f"z. B. `fulda-bakery`).")
    lang = (f.get("lang") or "de").strip().lower()[:2]
    if lang not in ("de", "en"):
        raise Refused(f"Sprache `{lang}` kennt der Wizard nicht, nur `de` und `en`.")
    return {"name": name, "osm_id": osm_id, "tags": tags, "lang": lang}


def check_fields(f):
    """The URL (needs DNS) and everything else (does not)."""
    return {"url": check_url(f.get("url")), **check_meta(f)}


def known_tags(entries="entries"):
    """{tag: how many watches carry it}. The vocabulary is the file tree, not a list to maintain."""
    seen = collections.Counter()
    for name in sorted(os.listdir(entries)) if os.path.isdir(entries) else []:
        if not name.endswith(".json") or name.startswith("."):
            continue
        try:
            e = json.load(open(os.path.join(entries, name)))
        except Exception:
            continue
        seen.update(e.get("tags") or [])
    return seen


def area_prefix(seen):
    """The area every tag starts with, read off the existing ones.

    Not a constant: the prefix is what separates one district's watches from another's, so a
    second Landkreis in the same instance brings its own. Deriving it means the bot says
    `fulda-` here and the right thing elsewhere, with nothing to keep in sync.

    >>> import collections
    >>> area_prefix(collections.Counter({"fulda-bakery": 22, "fulda-cafe": 15}))
    'fulda'
    >>> area_prefix(collections.Counter()) is None
    True
    """
    parts = collections.Counter(t.split("-")[0] for t in seen.elements() if "-" in t)
    return parts.most_common(1)[0][0] if parts else None


def tag_note(tags, entries="entries"):
    """A warning for a category tag nothing else uses, or [].

    Not a refusal: a genuinely new category is normal, a slipped one is not, and only a person
    can tell them apart. But an unnoticed one-off splits the grouping in two, and the list has
    56 tags with a single watch to show for it — including one that was the OSM *key*, and
    therefore fitted every shop in town.
    """
    seen = known_tags(entries)
    unknown = [t for t in tags if t not in seen]
    if not unknown:
        return []
    area = area_prefix(seen)
    common = ", ".join(f"`{t}` ({n})" for t, n in seen.most_common(8))
    rule = (f"Gemeint ist das Gebiet und der OSM-Wert der Kategorie, hier also `{area}-` und "
            f"der Wert: `shop=florist` wird zu `{area}-florist`. Nimm den Wert, nicht den "
            f"Schlüssel, denn `{area}-shop` passt auf jeden Laden."
            if area else
            "Gemeint ist das Gebiet und der OSM-Wert der Kategorie, etwa `fulda-florist` für "
            "`shop=florist` in Fulda.")
    lead = ("Diesen Tag trägt bisher kein Watch" if len(unknown) == 1
            else "Diese Tags trägt bisher kein Watch")
    return [f"⚠ {lead}: {', '.join('`' + t + '`' for t in unknown)}. "
            f"{rule} Gebräuchlich sind: {common}. Eine neue Kategorie oder ein neues Gebiet "
            f"ist in Ordnung, sag es dann kurz im Pull Request."]


def already_watched(url, entries="entries"):
    """The entry that already watches this page, if there is one.

    Cheaper to say so before fetching than to let a reviewer find the duplicate in the diff —
    and a second watch on one URL is a real failure mode here: two businesses sharing a page
    once shared a single watch, and each new file makes that harder to see.
    """
    if not os.path.isdir(entries):
        return None
    want = C.normalize_url(url).rstrip("/").lower()
    for name in sorted(os.listdir(entries)):
        if not name.endswith(".json") or name.startswith("."):
            continue
        try:
            e = json.load(open(os.path.join(entries, name)))
        except Exception:
            continue
        if (e.get("url") or "").rstrip("/").lower() == want:
            return name
    return None


def fetch(url, lang):
    """(html, ranked candidates). Raises Refused with a readable reason."""
    import filter_wizard as W
    try:
        html = W.fetch_plain(url)
    except Exception as e:
        raise Refused(f"Die Seite war nicht abrufbar: `{e}`. Antwortet sie im Browser, "
                      f"blockt der Anbieter vermutlich Rechenzentren — dann trägt der Fall "
                      f"in `blocked-hosts.txt` mehr als ein Watch.")
    ranked = W.collect(html, lang)
    real = [c for c in ranked if c["strategy"] != "whole page"]
    if not real:
        raise Refused(
            "Im ausgelieferten HTML stehen keine Öffnungszeiten.\n\n"
            "Zwei Ursachen, beide häufig: die Zeiten stehen auf einer anderen Unterseite "
            "(`/kontakt`, `/oeffnungszeiten`, bei Ketten die Filialseite), oder sie erscheinen "
            "erst, wenn JavaScript gelaufen ist. Der erste Fall ist der häufigere — gemessen "
            "11 von 12. Probier die Unterseite und öffne ein neues Issue damit. Für den "
            "zweiten Fall braucht es einen Browser, den dieser Bot nicht hat.")
    return html, ranked


def fence(text):
    """A fenced block that survives backticks in the captured text."""
    body = re.sub(r"\s{2,}", " ", text).strip()[:MAX_TEXT]
    ticks = "`" * max(3, max((len(m) for m in re.findall(r"`+", body)), default=0) + 1)
    return f"{ticks}\n{body}\n{ticks}"


def candidate_block(i, cand, lang):
    import filter_wizard as W

    days = L.days_phrase(cand.get("days"), lang)
    head = f"### [{i}] {cand['strategy']}"
    meta = f"{days} · {len(cand['text'])} Zeichen · `{cand['filter'] or 'kein Filter'}`"
    lines = [head, meta, "", fence(cand["text"]), ""]
    lines += [f"- ! {f}" for f in cand.get("flags", [])]
    lines.append(f"- → {W.verdict(cand)}")
    return "\n".join(lines)


def candidates_comment(f, ranked, html_len, notes=()):
    shown = ranked[:MAX_CANDIDATES]
    parts = [
        f"Abgerufen: {f['url']} ({html_len} Bytes, einfacher Abruf, kein Browser).",
        "",
        *([*notes, ""] if notes else []),
        "**Nicht der Selektor entscheidet, sondern der Text.** Lies die Blöcke und sag, welcher "
        "genau die Öffnungszeiten dieses Betriebs enthält — nicht die Zeiten eines "
        "Terminformulars, nicht die einer Nachbarfiliale, nicht eine Uhr, die jede Minute "
        "weiterläuft. Die Reihenfolge ist ein Vorschlag, kein Urteil.",
        "",
    ]
    parts += [candidate_block(i, c, f["lang"]) + "\n" for i, c in enumerate(shown, 1)]
    parts += [
        "",
        "---",
        "",
        f"Antworte mit `/pick N`, etwa `/pick 1`, dann baue ich den Eintrag und öffne den "
        f"Pull Request. Passt keiner, ist meist die Seite falsch: viele Betriebe führen ihre "
        f"Zeiten auf `/kontakt` oder auf der Filialseite.",
    ]
    return "\n".join(parts)


def pr_body(f, cand, path, issue, notes=()):
    import filter_wizard as W
    return "\n".join([
        f"Watch für **{f['name']}**, vorgeschlagen in #{issue}.",
        "",
        f"- Seite: {f['url']}",
        f"- OSM: {'https://www.openstreetmap.org/' + f['osm_id'] if f['osm_id'] else '—'}",
        f"- Filter: `{cand['filter'] or 'kein Filter (ganze Seite)'}`",
        "",
        "Das fängt der Filter heute:",
        "",
        fence(cand["text"]),
        "",
        *[f"- ! {x}" for x in cand.get("flags", [])],
        f"- → {W.verdict(cand)}",
        "",
        f"Datei: `{path}`. Die Prüfung in CI holt die Seite noch einmal und hält den Filter "
        f"dagegen; was hier steht, ist der Stand des Abrufs von eben.",
        "",
        *([*notes, ""] if notes else []),
        f"Closes #{issue}",
    ])


def compare_url(repo, branch, title, body, base="main"):
    """A link that opens GitHub's pull-request form with everything filled in.

    The fallback for the day Actions is not allowed to open the pull request itself — that is a
    repository setting, off by default. One click by a human replaces it, and it buys something
    the automatic path does not have: a pull request opened by a person runs CI, while one
    opened with GITHUB_TOKEN starts no further workflow.

    >>> compare_url("o/r", "watch/x", "watch: X & Y", "b")
    'https://github.com/o/r/compare/main...watch/x?quick_pull=1&title=watch%3A+X+%26+Y&body=b'
    """
    q = urllib.parse.urlencode({"quick_pull": 1, "title": title, "body": body[:4000]})
    return f"https://github.com/{repo}/compare/{base}...{branch}?{q}"


def write(out, name, text):
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, name)
    with open(path, "w") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")
    return path


def main():
    ap = argparse.ArgumentParser(description="Run the filter wizard from a GitHub issue")
    ap.add_argument("command", choices=["parse", "candidates", "emit", "compare"])
    ap.add_argument("--body-file", required=True,
                    help="file holding the issue body, or the pull-request body for `compare`")
    ap.add_argument("--repo", help="owner/name, for `compare`")
    ap.add_argument("--branch", help="branch the entry sits on, for `compare`")
    ap.add_argument("--title", help="pull-request title, for `compare`")
    ap.add_argument("--comment-file", help="file holding the /pick comment (emit)")
    ap.add_argument("--pick", type=int, help="rank to take (emit, overrides --comment-file)")
    ap.add_argument("--issue", type=int, default=0, help="issue number, for the PR body")
    ap.add_argument("--out", default="out", help="directory for the generated files")
    ap.add_argument("--entries", default="entries",
                    help="checked for a watch on the same page (default: %(default)s)")
    args = ap.parse_args()

    body = open(args.body_file, encoding="utf-8", errors="replace").read()
    if args.command == "compare":
        url = compare_url(args.repo, args.branch, args.title, body)
        write(args.out, "compare.md",
              f"Der Branch steht: [`{args.branch}`](https://github.com/{args.repo}/tree/"
              f"{args.branch}). Den Pull Request musst du selbst öffnen, ein Klick:\n\n"
              f"**[Pull Request anlegen]({url})**\n\n"
              f"Titel und Text sind vorausgefüllt. Actions darf hier keine Pull Requests "
              f"anlegen — das ist eine Repo-Einstellung (Settings → Actions → General → "
              f"Workflow permissions). Der Klick hat einen eigenen Vorteil: ein von einem "
              f"Menschen geöffneter Pull Request löst CI aus, ein von `GITHUB_TOKEN` "
              f"geöffneter nicht.")
        return 0
    try:
        f = check_fields(parse_body(body))
        if args.command == "parse":
            print(json.dumps(f, ensure_ascii=False, indent=1))
            return 0

        pick = args.pick
        if args.command == "emit" and not pick:
            m = PICK.search(open(args.comment_file, encoding="utf-8", errors="replace").read())
            if not m:
                raise Refused("Ich lese in dem Kommentar kein `/pick N`.")
            pick = int(m.group(1))

        dup = already_watched(f["url"], args.entries)
        if dup:
            raise Refused(
                f"Diese Seite wird schon beobachtet: `entries/{dup}`.\n\n"
                f"Teilen sich zwei Betriebe die Seite, braucht der zweite einen eigenen "
                f"Schlüssel im Filter, meist seine Adresse (FILTERS.md Fall 12) — das ist eine "
                f"Änderung an der bestehenden Datei, kein neuer Watch.")
        html, ranked = fetch(f["url"], f["lang"])
        if args.command == "candidates":
            write(args.out, "comment.md", candidates_comment(
                f, ranked, len(html), tag_note(f["tags"], args.entries)))
            write(args.out, "candidates.json", json.dumps(ranked, ensure_ascii=False, indent=1))
            return 0

        if not 1 <= pick <= len(ranked):
            raise Refused(f"`{pick}` gibt es nicht, die Seite hatte {len(ranked)} Kandidaten. "
                          f"Der Abruf von eben kann anders ausgefallen sein als der erste — "
                          f"dann setz das Label neu und such aus der neuen Liste.")
        cand = ranked[pick - 1]
        import filter_wizard as W
        path = W.emit_entry(os.path.join(args.out, "entry"), f["name"], f["url"], cand,
                            False, f["lang"], f["osm_id"], f["tags"])
        slug = os.path.basename(path)[:-len(".json")]
        write(args.out, "pr-body.md", pr_body(f, cand, f"entries/{slug}.json", args.issue,
                                              tag_note(f["tags"], args.entries)))
        write(args.out, "meta.json", json.dumps(
            {"slug": slug, "name": f["name"], "url": f["url"], "pick": pick,
             "branch": f"watch/{slug}", "entry": path,
             "title": f"watch: {f['name']}"}, ensure_ascii=False, indent=1))
        return 0
    except Refused as e:
        write(args.out, "comment.md",
              f"Das kann ich so nicht bauen.\n\n{e}\n\n"
              f"Ändere das Issue und setz das Label `wizard` neu, dann probiere ich es "
              f"noch einmal.")
        print(f"refused: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
