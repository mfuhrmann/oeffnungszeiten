#!/usr/bin/env python3
"""
zeiten_durchsehen.py — sortiert die offenen Zeilen aus `zeiten_abgleich.csv` nach Entscheidung.

Was `zeiten_bestaetigen.py` bestaetigt und `zeiten_export.py` uebernehmen darf, ist beides
maschinell entscheidbar. Der Rest ist es nicht — aber er ist auch kein Haufen: die Frage, die
der Mapper zu beantworten hat, ist je nach Fall eine andere, und sie steht schon in der
Gegenueberstellung. Danach wird hier gruppiert:

  mehr        Die Seite kennt alles, was die Karte kennt, und mehr. Additiv, also der einzige
              Fall, in dem ein Upload nichts verlieren kann.
  weniger     Die Seite ist eine Teilmenge der Karte. Fast immer nennt sie nur den Vormittag
              oder laesst den Samstag weg — Schweigen ist kein Beleg fuer geschlossen, hier
              wird nichts hochgeladen.
  verschoben  Beide nennen dieselben Tage mit anderen Zeiten. Nur hier entscheidet wirklich,
              wer frischer ist, und das kann nur ein Mensch.
  unklar      Kein Block der Seite laesst sich zerlegen, oder die Zeiten stehen in Prosa.

Verglichen wird gegen den Block der Seite, der der Karte am naechsten kommt — nicht gegen den
ersten, denn der ist oft die Telefonzeit (siehe zeiten_bestaetigen.py).

    python3 ../../scripts/zeiten_durchsehen.py [--markdown zeiten-durchsehen.md]
Aufzurufen aus dem Arbeitsordner der Recherche: die CSV-Dateien liegen dort, nicht im
Repository.
"""
import csv
import sys
from collections import Counter

import josm_export as J
import zeiten_osm as Z
from zeiten_bestaetigen import bloecke, plan_aus_block, paare


def zeigen(plan):
    """Plan wieder als lesbaren Ausdruck. Nur zum Ansehen, nichts davon wird getaggt."""
    if not plan:
        return ''
    return '; '.join(f"{t} {','.join(plan[t]) or 'off'}"
                     for t in Z.ORDNUNG if t in plan)

ABGLEICH = 'zeiten_abgleich.csv'


def klasse(karte, block):
    if not block:
        return 'unklar'
    k, b = paare(karte), paare(block)
    if k == b:
        return 'gleich'
    if k < b:
        return 'mehr'
    if b < k:
        return 'weniger'
    return 'verschoben'


def main():
    ziel = sys.argv[sys.argv.index('--markdown') + 1] if '--markdown' in sys.argv else None
    belege = {r['OSM']: r['Beleg'] for r in csv.DictReader(open('funde.csv', encoding='utf-8'))}
    # `zeiten_abgleich.csv` kappt den Rohtext bei 200 Zeichen; `zusatzdaten.csv` haelt ihn ganz.
    # Genau am Ende steht oft der Block, um den es geht — der Kuechenschluss, der Ruhetag.
    roh_voll = {r['OSM']: r['Zeiten_roh']
                for r in csv.DictReader(open('zusatzdaten.csv', encoding='utf-8'))}
    zeilen = list(csv.DictReader(open(ABGLEICH, encoding='utf-8')))

    sortiert = {'mehr': [], 'verschoben': [], 'weniger': [], 'unklar': [], 'lieferando': []}
    for r in zeilen:
        if 'LIEFERANDO' in belege.get(r['OSM'], ''):
            sortiert['lieferando'].append((r, None)); continue
        karte = J.plan(r['Karte'])
        if not karte:
            sortiert['unklar'].append((r, None)); continue
        karte = {k: sorted(v) for k, v in karte.items() if k != 'PH'}
        plaene = [p for p in (plan_aus_block(b) for b in bloecke(r['Roh'])) if p]
        if not plaene:
            sortiert['unklar'].append((r, None)); continue
        # der Block, der der Karte am naechsten kommt — ein exakt gleicher zuerst, sonst
        # entschiede bei Gleichstand die Reihenfolge und eine Bestaetigung saehe wie ein
        # Konflikt aus.
        best = next((p for p in plaene if p == karte),
                    max(plaene, key=lambda p: len(paare(karte) & paare(p))))
        k = klasse(karte, best)
        if k == 'gleich':
            continue          # gehoert zu zeiten_bestaetigen.py
        sortiert[k].append((r, best))

    for k, v in sortiert.items():
        print(f'{len(v):3}  {k}')

    if not ziel:
        return
    with open(ziel, 'w', encoding='utf-8') as fh:
        fh.write('# Zeitenabgleich — was von Hand zu entscheiden ist\n')
        for k in ('mehr', 'verschoben', 'weniger', 'unklar', 'lieferando'):
            if not sortiert[k]:
                continue
            fh.write(f'\n## {k} ({len(sortiert[k])})\n\n')
            for r, best in sortiert[k]:
                fh.write(f"### {r['Name']}\n\n")
                fh.write(f"- OSM: {r['OSM']}\n- Quelle: {r['Website']}\n")
                fh.write(f"- Karte: `{r['Karte']}`\n- Seite (erster Block): `{r['Seite']}`\n")
                if best:
                    fh.write(f"- Seite (passendster Block): `{zeigen(best)}`\n")
                fh.write(f"- Rohtext: {roh_voll.get(r['OSM']) or r['Roh']}\n\n")
    print(f'-> {ziel}')


if __name__ == '__main__':
    main()
