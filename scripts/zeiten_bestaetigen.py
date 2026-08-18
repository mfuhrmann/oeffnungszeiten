#!/usr/bin/env python3
"""
zeiten_bestaetigen.py — setzt `check_date:opening_hours`, wo die Seite die Karte bestaetigt.

`zeiten_export.py` ist der Weg fuer den Fall "Seite widerspricht der Karte, Betreiber gewinnt".
Von den 74 Zeilen in `zeiten_abgleich.csv` kommt dort fast nichts durch, und der Grund ist
meistens keine Abweichung, sondern der Parser: `zeiten_osm.py` bricht ab, sobald ein Wochentag
sich wiederholt, und nimmt nur den ersten Block. Der erste Block ist auf vielen Seiten aber die
Telefonzeit, das Menue "ohne Mittagessen" oder der Vormittag — waehrend ein spaeterer Block
genau das sagt, was in der Karte steht.

Hier wird der Rohtext deshalb in Bloecke geschnitten und jeder Block einzeln gegen den
Kartenwert gehalten. Deckt sich einer davon exakt, ist das keine Abweichung, sondern eine
Pruefung: `opening_hours` bleibt unangetastet, nur `check_date:opening_hours` kommt dazu.
Damit ist kein Syntaxcheck noetig — der bestehende Wert wird nicht angefasst.

Zwei Regeln halten den Rest draussen:

  * Wiederholt sich ein Tag mit einer Spanne, die die bisherigen **ueberschneidet**, ist das ein
    zweiter Fahrplan und beginnt einen neuen Block. Eine spaetere, freie Spanne ist dagegen der
    Nachmittagsblock desselben Tages (Urologische Gemeinschaftspraxis: 08:00-11:00 und
    13:30-16:00 stehen in zwei Zeilen).
  * Traegt eine Seite **mehrere verschiedene** Wochenfahrplaene (keiner Teilmenge des anderen),
    ist nicht belegt, welcher zu diesem Objekt gehoert. Holiday Land fuehrt auf einer Seite drei
    Filialen mit drei Zeiten, die Kita Maberzell drei Betreuungsmodelle.

    python3 ../../scripts/zeiten_bestaetigen.py            # Bericht
    python3 ../../scripts/zeiten_bestaetigen.py --schreiben # dazu josm/zeiten-bestaetigt-NN.osm
Aufzurufen aus dem Arbeitsordner der Recherche: die CSV-Dateien liegen dort, nicht im
Repository.
"""
import csv
import datetime
import os
import re
import sys

import josm_export as J
import zeiten_osm as Z

ABGLEICH = 'zeiten_abgleich.csv'
ORDNER = 'josm'
BERICHT = 'zeiten-bestaetigt.csv'
HEUTE = datetime.date.today().isoformat()


def ueberlappt(vorhanden, neu):
    return any(not (n.split('-')[0] >= a.split('-')[1] or n.split('-')[1] <= a.split('-')[0])
               for a in vorhanden for n in neu)


def bloecke(roh):
    """Rohtext in Bloecke. Neuer Block erst, wenn ein wiederholter Tag ueberlappt."""
    raus, akt, plan = [], [], {}
    for teil in roh.split('|'):
        d, s = Z.tage(teil), Z.spannen(teil)
        if d and s and any(t in plan for t in d) and any(ueberlappt(plan.get(t, []), s) for t in d):
            raus.append(akt)
            akt, plan = [], {}
        if d and s:
            for t in d:
                plan.setdefault(t, []).extend(s)
        akt.append(teil)
    if akt:
        raus.append(akt)
    return ['|'.join(b) for b in raus]


def plan_aus_block(text):
    """wie zeiten_osm.bauen, aber ohne Abbruch beim ueberschneidungsfreien zweiten Eintrag."""
    plan, letzte = {}, []
    for teil in text.split('|'):
        t = teil.strip()
        d, s = Z.tage(t), Z.spannen(t)
        if d and not s and Z.GESCHLOSSEN.search(t):
            for tag in d:
                plan.setdefault(tag, [])
            letzte = d
            continue
        if not s:
            continue
        ziel = d or letzte
        if not ziel:
            continue
        for tag in ziel:
            plan.setdefault(tag, [])
            for sp in s:
                if sp not in plan[tag]:
                    plan[tag].append(sp)
        letzte = ziel
    return {k: sorted(v) for k, v in plan.items() if k != 'PH'}


def paare(plan):
    return {(t, s) for t, sp in plan.items() for s in sp}


def mehrere_fahrplaene(plaene):
    """True, wenn zwei Wochenplaene (>=5 Tage) nebeneinander stehen und keiner im anderen steckt."""
    voll = [p for p in plaene if len(p) >= 5]
    for i, a in enumerate(voll):
        for b in voll[i + 1:]:
            if not (paare(a) <= paare(b) or paare(b) <= paare(a)):
                return True
    return False


def main():
    schreiben = '--schreiben' in sys.argv
    belege = {r['OSM']: r['Beleg'] for r in csv.DictReader(open('funde.csv', encoding='utf-8'))}
    zeilen = list(csv.DictReader(open(ABGLEICH, encoding='utf-8')))

    treffer, verworfen = [], []
    for r in zeilen:
        beleg = belege.get(r['OSM'], '')
        if 'KETTENSEITE' in beleg:
            verworfen.append((r, 'Kettenseite')); continue
        if 'LIEFERANDO' in beleg:
            verworfen.append((r, 'Lieferando-Seite')); continue
        karte = J.plan(r['Karte'])
        if not karte:
            verworfen.append((r, 'Kartenwert nicht zerlegbar')); continue
        karte = {k: sorted(v) for k, v in karte.items() if k != 'PH'}
        plaene = [plan_aus_block(b) for b in bloecke(r['Roh'])]
        if mehrere_fahrplaene(plaene):
            verworfen.append((r, 'mehrere verschiedene Fahrplaene auf der Seite')); continue
        block = next((i for i, p in enumerate(plaene) if p == karte), None)
        if block is None:
            verworfen.append((r, 'kein Block deckt sich mit der Karte')); continue
        treffer.append((r, block + 1))

    fertig, bericht = [], []
    for r, block in treffer:
        typ, ident = re.search(r'/(node|way|relation)/(\d+)', r['OSM']).groups()
        try:
            wurzel = J.hole(typ, ident)
            el = wurzel.find(f'.//{typ}[@id="{ident}"]')
        except Exception as e:
            verworfen.append((r, f'API {type(e).__name__}')); continue
        if el is None:
            verworfen.append((r, 'geloescht?')); continue
        t = J.tags(el)
        # Der Wert muss noch der sein, den wir geprueft haben — sonst gilt die Pruefung nicht ihm.
        if t.get('opening_hours') != r['Karte']:
            verworfen.append((r, 'in OSM inzwischen geaendert')); continue
        if t.get('check_date:opening_hours', '') >= HEUTE:
            verworfen.append((r, f"schon geprueft: {t['check_date:opening_hours']}")); continue
        for tag in el.findall('tag'):
            if tag.get('k') == 'check_date:opening_hours':
                el.remove(tag)
        J.ET.SubElement(el, 'tag', k='check_date:opening_hours', v=HEUTE)
        el.set('action', 'modify')
        fertig.append((el, wurzel, typ))
        bericht.append({'Name': r['Name'], 'OSM': r['OSM'], 'opening_hours': r['Karte'],
                        'Block': block, 'Quelle': r['Website'], 'Rohtext': r['Roh'][:200]})
        print(f"{r['Name'][:30]:<30} Block {block}  {r['Karte'][:46]}")

    if schreiben and fertig:
        os.makedirs(ORDNER, exist_ok=True)
        elemente = []
        for el, wurzel, typ in fertig:
            if typ == 'way':
                elemente.extend(wurzel.findall('node'))
            elemente.append(el)
        pfad = os.path.join(ORDNER, 'zeiten-bestaetigt-01.osm')
        J.schreiben(pfad, elemente)
        print(f'  -> {pfad}  ({len(fertig)} Objekte)')
        with open(BERICHT, 'w', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=list(bericht[0]))
            w.writeheader()
            w.writerows(bericht)

    print(f'\n{len(fertig)} bestaetigt, {len(verworfen)} bleiben zur Hand')
    from collections import Counter
    for grund, n in Counter(g for _, g in verworfen).most_common():
        print(f'  {n:3}  {grund}')


if __name__ == '__main__':
    main()
