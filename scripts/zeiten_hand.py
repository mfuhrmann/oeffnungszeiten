#!/usr/bin/env python3
"""
zeiten_hand.py — traegt von Hand entschiedene Zeiten ein.

`zeiten_export.py` entscheidet maschinell und laesst deshalb nur durch, was ohne Ansehen der
Seite sicher ist. Der groessere Teil von `zeiten_abgleich.csv` ist so nicht zu entscheiden: dort
haengt es daran, welcher Block auf der Seite die Oeffnungszeit ist und ob er die ganze Woche
nennt. Diese Entscheidung faellt ein Mensch, und sie steht mit Begruendung in
`zeiten-entscheidung.csv` — eine Zeile je Objekt, Spalte `Wert` der neue `opening_hours`, oder
`=` fuer "die Karte stimmt, nur bestaetigen".

Die Maschine macht danach dasselbe wie sonst, und daran wird nicht gespart:

  * `opening_hours.js` prueft die Syntax jedes Wertes, im Wegwerfbrowser,
  * der Wert in OSM muss noch der sein, gegen den entschieden wurde,
  * ein juengeres `check_date:opening_hours` haelt die Zeile zurueck.

`check_date:opening_hours` wird immer mitgesetzt: auch ein geaenderter Wert ist an diesem Tag
an der Betreiberseite geprueft worden.

    docker run --rm -d --network host dgtlmoon/sockpuppetbrowser
    python3 ../../scripts/zeiten_hand.py [--schreiben]
Aufzurufen aus dem Arbeitsordner der Recherche: die CSV-Dateien liegen dort, nicht im
Repository.
"""
import csv
import datetime
import os
import re
import sys

import josm_export as J
import pruefe_syntax as P

import cdp_render as C

ENTSCHEIDUNG = 'zeiten-entscheidung.csv'
ABGLEICH = 'zeiten_abgleich.csv'
ORDNER = 'josm'
HEUTE = datetime.date.today().isoformat()


def main():
    schreiben = '--schreiben' in sys.argv
    karte = {r['OSM']: r['Karte'] for r in csv.DictReader(open(ABGLEICH, encoding='utf-8'))}
    zeilen = list(csv.DictReader(open(ENTSCHEIDUNG, encoding='utf-8')))

    # Nur die geaenderten Werte muessen durch die Syntaxpruefung; '=' laesst die Karte stehen,
    # und deren Gueltigkeit hat pruefe_osm_zeiten.py zu beantworten, nicht dieser Lauf.
    neu = [r for r in zeilen if r['Wert'] != '=']
    C.wait_for_browser(timeout=60)
    ws = C.WS(C.DEFAULT_WS)
    cdp = C.CDP(ws)
    try:
        sid = P.sitzung(cdp)
        urteil = P.pruefe(cdp, sid, [r['Wert'] for r in neu])
    finally:
        ws.close()
    ungueltig = {}
    for r, u in zip(neu, urteil):
        if not u['ok']:
            ungueltig[r['OSM']] = u['fehler'][:80]
        elif u.get('warnungen'):
            print(f"   Warnung  {r['Name'][:26]:<26} {'; '.join(u['warnungen'])[:80]}")

    fertig, zurueck = [], []
    for r in zeilen:
        if r['OSM'] in ungueltig:
            zurueck.append((r, f'ungueltige Syntax: {ungueltig[r["OSM"]]}')); continue
        typ, ident = re.search(r'/(node|way|relation)/(\d+)', r['OSM']).groups()
        try:
            wurzel = J.hole(typ, ident)
            el = wurzel.find(f'.//{typ}[@id="{ident}"]')
        except Exception as e:
            zurueck.append((r, f'API {type(e).__name__}')); continue
        if el is None:
            zurueck.append((r, 'geloescht?')); continue
        t = J.tags(el)
        if t.get('opening_hours') != karte.get(r['OSM']):
            zurueck.append((r, 'in OSM inzwischen geaendert')); continue
        if t.get('check_date:opening_hours', '') >= HEUTE:
            zurueck.append((r, f"schon geprueft: {t['check_date:opening_hours']}")); continue

        for tag in el.findall('tag'):
            if tag.get('k') in ('opening_hours', 'check_date:opening_hours'):
                el.remove(tag)
        wert = karte[r['OSM']] if r['Wert'] == '=' else r['Wert']
        J.ET.SubElement(el, 'tag', k='opening_hours', v=wert)
        J.ET.SubElement(el, 'tag', k='check_date:opening_hours', v=HEUTE)
        el.set('action', 'modify')
        fertig.append((el, wurzel, typ))
        pfeil = 'bestaetigt' if r['Wert'] == '=' else f"-> {wert[:44]}"
        print(f"{r['Name'][:26]:<26} {pfeil}")

    if schreiben and fertig:
        os.makedirs(ORDNER, exist_ok=True)
        elemente = []
        for el, wurzel, typ in fertig:
            if typ == 'way':
                elemente.extend(wurzel.findall('node'))
            elemente.append(el)
        pfad = os.path.join(ORDNER, 'zeiten-hand-01.osm')
        J.schreiben(pfad, elemente)
        print(f'  -> {pfad}  ({len(fertig)} Objekte)')

    print(f'\n{len(fertig)} Objekte, {len(zurueck)} zurueckgestellt')
    for r, grund in zurueck:
        print(f"   {r['Name'][:26]:<26} {grund}")


if __name__ == '__main__':
    main()
