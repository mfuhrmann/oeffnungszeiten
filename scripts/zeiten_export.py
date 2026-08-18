#!/usr/bin/env python3
"""
zeiten_export.py — schlägt `opening_hours` aus der Betreiberseite als JOSM-Änderung vor.

Der erste Schritt dieses Projekts hat `opening_hours` grundsätzlich nie geschrieben, weil eine
Website so veraltet sein kann wie die Karte. Hier wird das bewusst umgedreht: wo Seite und Karte
sich widersprechen, bekommt der Wert des Betreibers den Vorzug — aber nur unter Bedingungen, und
jede Zeile geht durch die Sichtprüfung eines Menschen, bevor sie hochgeht.

Bedingungen, alle vier müssen gelten:

  1. Der Vorschlag trägt **keinen** Vermerk aus `zeiten_osm.py` (kein zweiter Zeitenblock, keine
     Prosa, nicht nur ein oder zwei erkannte Tage).
  2. `opening_hours.js` hält ihn für gültig — geprüft im Browser, nicht geraten.
  3. Der Wert in OSM ist **unverändert** gegenüber dem Zeitpunkt des Abgleichs. Hat inzwischen
     jemand anders daran gearbeitet, bleibt die Zeile liegen: dessen Arbeit ist frischer als
     unsere Messung.
  4. Das Objekt trägt kein jüngeres `check_date:opening_hours` als unsere Erhebung. Wer vor Ort
     war, weiß es besser als eine Website.

Gesetzt werden `opening_hours` (überschrieben) und `check_date:opening_hours` (heute). Die
Ausgabe liegt in `josm/zeiten-NN.osm`, die Begründung Zeile für Zeile in `zeiten-vorschlag.csv`.

    docker run --rm -d --network host dgtlmoon/sockpuppetbrowser   # fuer die Syntaxpruefung
    python3 ../../scripts/zeiten_export.py [--stueck 25]
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

ABGLEICH = 'zeiten_abgleich.csv'
ZUSATZ = 'zusatzdaten.csv'
ORDNER = 'josm'
BERICHT = 'zeiten-vorschlag.csv'
HEUTE = datetime.date.today().isoformat()


def offen_tage(wert):
    """Tage, die in einem Wert ausdruecklich als geschlossen stehen ("Su, PH off")."""
    # Nur an Semikolons trennen: "Su, Mo, PH off" ist EINE Regel, und ein Komma-Split haette
    # das `off` nur beim letzten Tag gefunden — Su und Mo waeren unbemerkt durchgefallen.
    raus = set()
    for teil in (wert or '').split(';'):
        if re.search(r'\boff\b', teil):
            raus.update(re.findall(r'\b(Mo|Tu|We|Th|Fr|Sa|Su|PH)\b', teil))
    return raus


def main():
    stueck = int(sys.argv[sys.argv.index('--stueck') + 1]) if '--stueck' in sys.argv else 25
    zusatz = {r['OSM']: r for r in csv.DictReader(open(ZUSATZ, encoding='utf-8'))}
    belege = {r['OSM']: r['Beleg'] for r in csv.DictReader(open('funde.csv', encoding='utf-8'))}
    zeilen = list(csv.DictReader(open(ABGLEICH, encoding='utf-8')))

    kandidaten, verworfen = [], []
    for r in zeilen:
        z = zusatz.get(r['OSM'], {})
        neu, alt = J.plan(r.get('Seite', '')), J.plan(r.get('Karte', ''))
        # Kettenstartseiten tragen die Zeiten der Zentrale oder der Service-Hotline, nie die
        # der Filiale: Cecil schlug so "Mo-Sa 08:00-21:00" aus der Fusszeile vor.
        if 'KETTENSEITE' in belege.get(r['OSM'], ''):
            verworfen.append((r, 'Kettenseite — Zeiten gehoeren nicht zur Filiale'))
        # Lieferando-Storefronts zeigen einen Block mit class="openingtimes", beschriftet ist
        # er aber "Liefer zeiten". Oez Urfa bekam so das Lieferfenster als Oeffnungszeit in
        # die Karte geschrieben; der Changeset musste zurueckgenommen werden.
        elif 'LIEFERANDO' in belege.get(r['OSM'], ''):
            verworfen.append((r, 'Lieferando-Seite — das sind Lieferzeiten, keine Oeffnungszeiten'))
        elif z.get('Zeiten_pruefen'):
            verworfen.append((r, f"Vermerk: {z['Zeiten_pruefen']}"))
        elif not r.get('Seite'):
            verworfen.append((r, 'kein Vorschlag'))
        # Ein Satz wie "Mo-Fr: 9-17, Sa: 9-16, So: 10-12" zerfaellt nicht in Gruppen, wenn er
        # in einem Stueck dasteht: dann bekommt jeder Tag jede Spanne. Das Ergebnis ist
        # syntaktisch gueltig und trotzdem Unsinn — Wunderblume bekam so an sieben Tagen drei
        # Spannen.
        elif neu and len({tuple(v) for v in neu.values()}) == 1 and \
                len(next(iter(neu.values()))) >= 3:
            verworfen.append((r, 'alle Tage identisch mit drei Spannen — Satz nicht zerlegt'))
        # Nie Tage wegnehmen: deckt der Vorschlag weniger Wochentage ab als die Karte, waere der
        # Upload ein Informationsverlust. Meist fehlt der Samstag, weil die Quelle ihn nicht
        # nennt — das heisst nicht, dass geschlossen ist.
        elif neu and alt and set(alt) - set(neu):
            fehlend = ', '.join(sorted(set(alt) - set(neu)))
            verworfen.append((r, f'Vorschlag laesst Tage weg, die die Karte kennt: {fehlend}'))
        # Auch keine Spanne verlieren. Kischporski und Fischer & Groß haetten ihre
        # Nachmittagssprechstunde eingebuesst, weil die Seite nur den Vormittag nennt — die
        # Tage waren vollstaendig, die Oeffnungszeiten nicht.
        elif neu and alt and [t for t in alt if t in neu and len(neu[t]) < len(alt[t])]:
            weniger = ', '.join(t for t in alt if t in neu and len(neu[t]) < len(alt[t]))
            verworfen.append((r, f'Vorschlag hat weniger Zeitspannen an: {weniger}'))
        # `PH off` und andere off-Regeln stehen in der Karte, weil jemand sie erhoben hat.
        # Websites erwaehnen Feiertage fast nie; ihr Schweigen ist kein Beleg fuer geoeffnet.
        # Ein Betrieb oeffnet nicht zweimal zur selben Uhrzeit. Zwei Spannen mit gleichem
        # Beginn heissen, dass die Seite zwei Werte je Zeile fuehrt — Hans im Glueck hat ein
        # rollendes Tagesfenster mit Klammerwert und kam so auf "12:00-20:30,12:00-21:00".
        elif neu and any(len({s.split('-')[0] for s in v}) < len(v) for v in neu.values()):
            verworfen.append((r, 'zwei Zeitspannen mit gleichem Beginn — Seite fuehrt zwei Werte'))
        # off-Tage einzeln vergleichen, nicht nur das Wort. Gleis 10 brachte ein "Mo off" mit
        # und haette dabei "Su off" und "PH off" verloren.
        elif offen_tage(r.get('Karte', '')) - offen_tage(r.get('Seite', '')):
            fehlend = ', '.join(sorted(offen_tage(r.get('Karte', ''))
                                       - offen_tage(r.get('Seite', ''))))
            verworfen.append((r, f'Vorschlag verliert die off-Regel fuer: {fehlend}'))
        else:
            kandidaten.append(r)

    # Syntax gegen die Referenz, bevor irgendetwas geschrieben wird.
    C.wait_for_browser(timeout=60)
    ws = C.WS(C.DEFAULT_WS)
    cdp = C.CDP(ws)
    try:
        sid = P.sitzung(cdp)
        urteil = P.pruefe(cdp, sid, [r['Seite'] for r in kandidaten])
    finally:
        ws.close()
    gueltig = []
    for r, u in zip(kandidaten, urteil):
        if u['ok']:
            gueltig.append(r)
        else:
            verworfen.append((r, f"ungueltige Syntax: {u['fehler'][:60]}"))

    os.makedirs(ORDNER, exist_ok=True)
    fertig, bericht = [], []
    for r in gueltig:
        m = re.search(r'/(node|way|relation)/(\d+)', r['OSM'])
        typ, ident = m.groups()
        try:
            wurzel = J.hole(typ, ident)
            el = wurzel.find(f'.//{typ}[@id="{ident}"]')
        except Exception as e:
            verworfen.append((r, f'API {type(e).__name__}'))
            continue
        if el is None:
            verworfen.append((r, 'geloescht?'))
            continue
        t = J.tags(el)

        if t.get('opening_hours') != r['Karte']:
            verworfen.append((r, 'in OSM inzwischen geaendert'))
            continue
        geprueft = t.get('check_date:opening_hours', '')
        if geprueft and geprueft > HEUTE:
            verworfen.append((r, f'jüngeres check_date {geprueft}'))
            continue

        for tag in el.findall('tag'):
            if tag.get('k') in ('opening_hours', 'check_date:opening_hours'):
                el.remove(tag)
        J.ET.SubElement(el, 'tag', k='opening_hours', v=r['Seite'])
        J.ET.SubElement(el, 'tag', k='check_date:opening_hours', v=HEUTE)
        el.set('action', 'modify')
        fertig.append((el, wurzel, typ))
        bericht.append({'Name': r['Name'], 'OSM': r['OSM'], 'alt': r['Karte'],
                        'neu': r['Seite'], 'Quelle': r['Website'],
                        'Rohtext': r.get('Roh', '')[:160]})
        print(f"{r['Name'][:26]:<26} {r['Karte'][:34]:<34} -> {r['Seite'][:34]}")

    for n in range(0, len(fertig), stueck):
        teil = fertig[n:n + stueck]
        elemente = []
        for el, wurzel, typ in teil:
            if typ == 'way':
                elemente.extend(wurzel.findall('node'))
            elemente.append(el)
        pfad = os.path.join(ORDNER, f'zeiten-{n // stueck + 1:02d}.osm')
        J.schreiben(pfad, elemente)
        print(f'  -> {pfad}  ({len(teil)} Objekte)')

    if bericht:
        with open(BERICHT, 'w', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=list(bericht[0]))
            w.writeheader()
            w.writerows(bericht)

    print(f'\n{len(fertig)} Vorschlaege, {len(verworfen)} zurueckgestellt')
    for r, grund in verworfen:
        print(f"   {r['Name'][:26]:<26} {grund[:70]}")


if __name__ == '__main__':
    main()
