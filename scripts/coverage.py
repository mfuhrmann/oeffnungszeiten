#!/usr/bin/env python3
"""
coverage.py — how much of the city is covered, and what is left.

`entries/` says what is watched, `no-watch.json` says what deliberately is not. Both are
numerators. Without the denominator neither means anything: 502 of how many? This asks OSM for
every object in the area that could plausibly have opening hours and sorts it into four states.

The two lists this project grew out of were both partial, in opposite directions, and neither
knew it: the harvest kept only objects that already had a `website` tag, the later research took
only objects that had `opening_hours` and no website. A pharmacy with both was invisible to both
— 8 of 22 in Fulda, found by accident.

Selection is by exclusion, not by a curated list of shop types: everything under
shop / craft / office / healthcare / amenity / leisure / tourism that carries a `name`, an
`operator` or a `brand` counts, minus infrastructure that has no staff and no hours (benches,
parking, post boxes). A curated list can only find what someone thought of; the point of a
denominator is to surface what nobody did. `operator` and `brand` are in there because a real
shop can be mapped without a name — a Fulda copyshop is tagged with `operator` only, and asking
for `name` alone dropped a watch we actually run.

Four states per object, and each is a different piece of work:

    watched            a watch exists
    absent             no watch, and no-watch.json records why
    no-website         OSM has no website tag — someone has to find one (or there is none)
    open               has a website, no watch, no recorded reason: this is the backlog

Cross-tabulated against `opening_hours`, because "has a website but no hours in OSM" is the
cheapest work in the project: nothing to overwrite, the tag is simply added.

    python3 scripts/coverage.py                       # summary
    python3 scripts/coverage.py --csv offen.csv       # the open ones, to work through
    python3 scripts/coverage.py --area-rel 454863     # a different city
"""
import argparse
import collections
import csv
import glob
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import osm_cd_common as C  # noqa: E402

SCHLUESSEL = ["shop", "craft", "office", "healthcare", "amenity", "leisure", "tourism"]
# Infrastructure: named, but nothing opens or closes. Kept as an explicit list so a reader can
# argue with it — every entry here is an object the count deliberately ignores.
OHNE_ZEITEN = {
    "bench", "waste_basket", "waste_disposal", "parking", "parking_space",
    "parking_entrance", "bicycle_parking", "motorcycle_parking", "atm", "post_box",
    "telephone", "clock", "drinking_water", "fountain", "shelter", "toilets", "bbq",
    "hunting_stand", "vending_machine", "charging_station", "car_sharing", "bicycle_rental",
    "taxi", "bus_station", "ferry_terminal", "fuel_station", "grit_bin", "letter_box",
    "photo_booth", "public_bookcase", "water_point", "watering_place", "fire_hydrant",
    "street_lamp", "picnic_table", "shower", "lounger", "firepit", "smoking_area",
    "artwork", "viewpoint", "picnic_site", "camp_pitch", "wilderness_hut", "attraction",
    "pitch", "playground", "park", "garden", "swimming_area", "slipway",
    "track", "outdoor_seating", "bleachers", "common", "dog_park", "fitness_station",
}


# One query of this size several times in an afternoon is enough to be rate-limited, and the
# 429 arrives as a plain HTTPError. Fall back to a mirror rather than fail the run.
SPIEGEL = ["https://overpass-api.de/api/interpreter",
           "https://overpass.kumi.systems/api/interpreter",
           "https://overpass.private.coffee/api/interpreter"]


def overpass(rel_id, url=None, versuche=3):
    q = f"""
    [out:json][timeout:180];
    rel({rel_id}); map_to_area -> .a;
    (
    """ + "\n".join(f'      nwr["{k}"][~"^(name|operator|brand)$"~"."](area.a);'
                     for k in SCHLUESSEL) + """
    );
    out tags center;
    """
    ziele = [url] if url else SPIEGEL
    for ziel in ziele:
        for n in range(versuche):
            try:
                req = urllib.request.Request(
                    ziel, data=urllib.parse.urlencode({"data": q}).encode(),
                    headers=C.OVERPASS_UA)
                with urllib.request.urlopen(req, timeout=300) as r:
                    return json.load(r)["elements"]
            except Exception as e:
                print(f"{ziel.split('/')[2]}: {type(e).__name__} ({n + 1}/{versuche})",
                      file=sys.stderr)
                time.sleep(15 * (n + 1))
    sys.exit("no Overpass mirror answered")


# Two categories split down the middle, and OSM has a subkey for exactly that. A staffed tourist
# office and a recycling yard keep hours; an information board on a road and a glass container do
# not — and Fulda has 38 Hessen-Mobil boards and a row of containers that would otherwise pad the
# denominator. Dropping the pair wholesale is just as wrong: this project watches a DB information
# desk and a recycling yard.
BEMANNT = {"information": {"office", "visitor_centre"},
           "recycling": {"centre"}}


def unbemannt(t):
    if t.get("tourism") == "information":
        return t.get("information") not in BEMANNT["information"]
    if t.get("amenity") == "recycling":
        return t.get("recycling_type") not in BEMANNT["recycling"]
    return False


def kategorie(t):
    for k in SCHLUESSEL:
        if t.get(k) and t[k] not in ("no", "yes"):
            return f"{k}={t[k]}"
    for k in SCHLUESSEL:
        if t.get(k):
            return k
    return "?"


def main():
    ap = argparse.ArgumentParser(description="coverage against OSM")
    ap.add_argument("--area-rel", default="454863", help="OSM relation id of the area")
    ap.add_argument("--entries", default="entries")
    ap.add_argument("--absences", default="no-watch.json")
    ap.add_argument("--csv", metavar="PATH", help="write the open ones here")
    ap.add_argument("--overpass-url", default=None)
    args = ap.parse_args()

    watched, namen = {}, {}
    for f in glob.glob(os.path.join(args.entries, "*.json")):
        if f.endswith(".lock.json"):
            continue
        e = json.load(open(f, encoding="utf-8"))
        if e.get("osm_id"):
            watched[e["osm_id"]] = e
            namen[e["osm_id"]] = e.get("name", "")
    absent = {}
    if os.path.exists(args.absences):
        for r in json.load(open(args.absences, encoding="utf-8"))["records"]:
            absent[r["osm_id"]] = r

    roh = overpass(args.area_rel, args.overpass_url)
    objekte = []
    for e in roh:
        t = e.get("tags", {})
        # Drop it only when EVERY category it carries is infrastructure. An object can hold two:
        # the Bürgerzentrum bike station is `amenity=bicycle_rental` and `shop=rental` at once,
        # and testing with `any` threw it out on the first key.
        kategorien = [t[k] for k in SCHLUESSEL if t.get(k)]
        if kategorien and all(v in OHNE_ZEITEN for v in kategorien):
            continue
        if unbemannt(t):
            continue
        oid = f"{e['type']}/{e['id']}"
        website = t.get("website") or t.get("contact:website") or ""
        objekte.append({
            "osm_id": oid,
            "name": t.get("name") or t.get("operator") or t.get("brand", ""),
            "kategorie": kategorie(t),
            "website": website, "opening_hours": t.get("opening_hours", ""),
            "lat": e.get("lat", (e.get("center") or {}).get("lat")),
            "lon": e.get("lon", (e.get("center") or {}).get("lon")),
            "status": ("watched" if oid in watched else
                       "absent" if oid in absent else
                       "no-website" if not website else "open"),
        })

    # The same business mapped twice — a node sitting inside its own building way — counts twice.
    # Same name and category is NOT enough to call that: Pappert has seven branches under one
    # name. A pair only counts as one place when the types differ and they are within 50 m.
    nach_name = collections.defaultdict(list)
    for o in objekte:
        if o["name"]:
            nach_name[(o["name"].lower(), o["kategorie"])].append(o)
    doppelt = 0
    for gruppe in nach_name.values():
        for i, a in enumerate(gruppe):
            for b in gruppe[i + 1:]:
                if a["osm_id"].split("/")[0] == b["osm_id"].split("/")[0]:
                    continue
                if None in (a["lat"], b["lat"]):
                    continue
                if (abs(a["lat"] - b["lat"]) < 0.00045
                        and abs(a["lon"] - b["lon"]) < 0.0007):
                    doppelt += 1

    zaehler = collections.Counter(o["status"] for o in objekte)
    print(f"{len(objekte)} objects in the area that could carry opening hours "
          f"({doppelt} of them a node and a way for the same place)\n")
    for s in ("watched", "absent", "no-website", "open"):
        print(f"{zaehler[s]:6}  {s}")

    print("\n                       has opening_hours   no opening_hours")
    for s in ("watched", "absent", "no-website", "open"):
        mit = sum(1 for o in objekte if o["status"] == s and o["opening_hours"])
        ohne = sum(1 for o in objekte if o["status"] == s and not o["opening_hours"])
        print(f"  {s:<18} {mit:>10}        {ohne:>10}")

    offen = [o for o in objekte if o["status"] == "open"]
    print(f"\nthe open ones, by category:")
    for k, n in collections.Counter(o["kategorie"] for o in offen).most_common(12):
        print(f"  {n:4}  {k}")

    # Watches whose object is gone from OSM: they still run, but against a record nobody keeps.
    gesehen = {o["osm_id"] for o in objekte}
    verwaist = [i for i in watched if i not in gesehen]
    if verwaist:
        print(f"\n{len(verwaist)} watched objects are not in this query's result "
              f"(deleted, retagged or outside the area):")
        for i in verwaist[:10]:
            print(f"  {i:<20} {namen.get(i, '')[:40]}")

    fehlend = [i for i in absent if i not in gesehen]
    if fehlend:
        print(f"\n{len(fehlend)} recorded absences are not in this query's result — check "
              f"whether the object was deleted or retagged:")
        for i in fehlend[:10]:
            print(f"  {i:<20} {absent[i].get('name', '')[:40]}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            felder = ["osm_id", "name", "kategorie", "website", "opening_hours", "status"]
            w = csv.DictWriter(fh, fieldnames=felder, extrasaction="ignore")
            w.writeheader()
            w.writerows(sorted(offen, key=lambda o: (o["kategorie"], o["name"])))
        print(f"\n-> {args.csv} ({len(offen)} rows)")


if __name__ == "__main__":
    main()
