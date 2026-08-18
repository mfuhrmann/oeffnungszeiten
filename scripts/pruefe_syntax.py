#!/usr/bin/env python3
"""
pruefe_syntax.py — prueft `opening_hours`-Werte gegen opening_hours.js, die Referenz.

Bis hierher war die Syntax der erzeugten Vorschlaege nur durch Konstruktion gesichert: der
Bauer setzt Tageskuerzel und `HH:MM-HH:MM` zusammen, also *sollte* dabei Gueltiges herauskommen.
Das ist keine Pruefung, sondern eine Hoffnung. Die Grammatik von `opening_hours` kennt Faelle,
die kein selbstgebautes Regex trifft, und die Referenz ist die Bibliothek, die auch OSM-Werkzeuge
benutzen.

Die Bibliothek ist Javascript, also laeuft sie im Browser: das Evaluation Tool laedt sie global
(`typeof opening_hours === "function"`), und ueber CDP werden die Werte dort ausgewertet. Der
Browser ist ein Wegwerfcontainer, nie der geteilte im Cluster:

    docker run --rm -d --network host dgtlmoon/sockpuppetbrowser
    python3 ../../scripts/pruefe_syntax.py

Geprueft werden beide Seiten des Zeitenvergleichs: unsere Vorschlaege aus `zusatzdaten.csv` und
die Werte, die in OSM stehen (`zeiten_abgleich.csv`). Letzteres ist ein Nebenprodukt, aber ein
nuetzliches — ein ungueltiger Wert in der Karte ist ein Mapping-Fehler, den sonst niemand meldet.
Aufzurufen aus dem Arbeitsordner der Recherche: die CSV-Dateien liegen dort, nicht im
Repository.
"""
import csv
import json
import os
import time

import cdp_render as C

TOOL = 'https://openingh.openstreetmap.de/evaluation_tool/'
# Ohne Ortsangabe kann die Bibliothek Feiertage (PH) nicht aufloesen und wirft.
NOMINATIM = {'address': {'country_code': 'de', 'state': 'Hessen'},
             'lat': 50.5558, 'lon': 9.6808}


def sitzung(cdp):
    tid = cdp.call('Target.createTarget', {'url': 'about:blank'})['targetId']
    sid = cdp.call('Target.attachToTarget', {'targetId': tid, 'flatten': True})['sessionId']
    cdp.call('Page.enable', session=sid)
    cdp.call('Page.navigate', {'url': TOOL}, session=sid, timeout=45)
    try:
        cdp.wait_event('Page.loadEventFired', timeout=30)
    except TimeoutError:
        pass
    time.sleep(3)
    return sid


def pruefe(cdp, sid, werte):
    """[wert] -> [{ok, fehler, warnungen}] in einem Rutsch."""
    js = ('(function(werte, nom) { return werte.map(function(w) {'
          '  try {'
          '    var oh = new opening_hours(w, nom);'
          '    var warn = [];'
          '    try { warn = oh.getWarnings() || []; } catch (e) { warn = ["Warnung: " + e]; }'
          '    return {ok: true, fehler: "", warnungen: warn};'
          '  } catch (e) { return {ok: false, fehler: String(e), warnungen: []}; }'
          '}); })(' + json.dumps(werte, ensure_ascii=False) + ', '
          + json.dumps(NOMINATIM) + ')')
    res = cdp.call('Runtime.evaluate',
                   {'expression': js, 'returnByValue': True, 'awaitPromise': False},
                   session=sid, timeout=120)
    ergebnis = (res.get('result') or {}).get('value')
    if ergebnis is None:
        raise RuntimeError(f'keine Auswertung: {res}')
    return ergebnis


def bericht(titel, zeilen, cdp, sid):
    werte = [z['wert'] for z in zeilen]
    if not werte:
        return []
    ergebnis = pruefe(cdp, sid, werte)
    schlecht = 0
    print(f'\n== {titel}: {len(werte)} Werte')
    for z, e in zip(zeilen, ergebnis):
        if not e['ok']:
            schlecht += 1
            print(f"   UNGUELTIG  {z['name'][:26]:<26} {z['wert'][:52]}")
            print(f"              {e['fehler'][:110]}")
        elif e['warnungen']:
            print(f"   Warnung    {z['name'][:26]:<26} {z['wert'][:46]}")
            for w in e['warnungen'][:2]:
                print(f"              {str(w)[:110]}")
    print(f'   {len(werte) - schlecht} gueltig, {schlecht} ungueltig')
    return ergebnis


def main():
    C.wait_for_browser(timeout=60)
    ws = C.WS(C.DEFAULT_WS)
    cdp = C.CDP(ws)
    try:
        sid = sitzung(cdp)
        # Die Probe braucht etwas wirklich Kaputtes: ein Komma am Ende ist der Bibliothek nur
        # eine Warnung wert, taugt also nicht als Gegenbeispiel.
        probe = pruefe(cdp, sid, ['Mo-Fr 08:00-12:00', 'Mo-Fr 44:99-77:88'])
        if probe[0]['ok'] and not probe[1]['ok']:
            print('Referenz antwortet plausibel (gueltig ok, kaputt abgelehnt)')
        else:
            print('WARNUNG: Referenz verhaelt sich unerwartet', probe)

        vorschlaege = [{'name': r['Name'], 'wert': r['Zeiten_osm']}
                       for r in csv.DictReader(open('zusatzdaten.csv', encoding='utf-8'))
                       if r.get('Zeiten_osm')]
        bericht('Unsere Vorschlaege (zusatzdaten.csv)', vorschlaege, cdp, sid)

        if os.path.exists('zeiten_abgleich.csv'):
            karte = [{'name': r['Name'], 'wert': r['Karte']}
                     for r in csv.DictReader(open('zeiten_abgleich.csv', encoding='utf-8'))
                     if r.get('Karte')]
            bericht('Was in OSM steht (zeiten_abgleich.csv)', karte, cdp, sid)
    finally:
        ws.close()


if __name__ == '__main__':
    main()
