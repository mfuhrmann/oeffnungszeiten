#!/usr/bin/env python3
"""
zeiten_osm.py — schlägt aus dem Rohtext von `zusatzdaten.csv` einen `opening_hours`-Wert vor.

Getrennt von `seiten_daten.py`, weil Holen und Deuten zwei verschiedene Fehlerquellen sind:
Ein falsch geparster Wert ist nur dann noch zu erkennen, wenn das Original danebensteht. Die
Spalte `Zeiten_roh` bleibt deshalb unangetastet, `Zeiten_osm` kommt daneben, und `Zeiten_pruefen`
sagt, warum eine Zeile trotzdem von Hand angesehen werden muss.

Bewusst konservativ: was nicht sauber in Tage und Spannen zerfällt, bleibt leer. Ein leeres Feld
kostet eine Handbewegung, ein falsches `opening_hours` steht jahrelang falsch in der Karte.

    python3 ../../scripts/zeiten_osm.py          # schreibt zusatzdaten.csv an Ort und Stelle fort
Aufzurufen aus dem Arbeitsordner der Recherche: die CSV-Dateien liegen dort, nicht im
Repository.
"""
import csv
import re
import sys

DATEI = 'zusatzdaten.csv'

TAGE = [
    (r'mo(?:ntag)?s?', 'Mo'), (r'di(?:enstag)?s?', 'Tu'), (r'mi(?:ttwoch)?s?', 'We'),
    (r'do(?:nnerstag)?s?', 'Th'), (r'fr(?:eitag)?s?', 'Fr'), (r'sa(?:mstag)?s?', 'Sa'),
    (r'so(?:nntag)?s?', 'Su'), (r'feiertags?', 'PH'),
]
ORDNUNG = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']
TAG_RE = re.compile('|'.join(f'(?P<{k}>\\b{m}\\b)' for m, k in
                             [(m, k) for m, k in TAGE]), re.I)
# Zwischen Zahl und "pm" steht auf manchen Seiten kein Leerzeichen, sondern ein kaputt
# kodiertes (bei 9gg.de ein U+FFFD). Nur \s zuzulassen liess das pm fallen — aus 4:30–10 pm
# wurde 04:30-10:00, ein Wert, der jede Syntaxpruefung besteht und die Kneipe morgens oeffnet.
_MUELL = r'[\s �​]*'
SPANNE = re.compile(rf'(\d{{1,2}})(?:[:.](\d{{2}}))?{_MUELL}(am|pm)?{_MUELL}(?:-|–|—|bis)'
                    rf'{_MUELL}(\d{{1,2}})(?:[:.](\d{{2}}))?{_MUELL}(am|pm)?', re.I)
BIS = re.compile(r'\s*(?:-|–|—|bis)\s*$')

# Wörter, die verraten, dass die Zeile mehr sagt als Tag und Spanne — Termin nur nach
# Vereinbarung, Ferienregelung, Mittagspause mit Bedingung. Der Vorschlag bleibt, bekommt aber
# einen Vermerk.
# `geschlossen` stand hier, solange der Bauer damit nichts anfangen konnte. Jetzt wird daraus
# ein ausdrueckliches `off`, also ist es keine Prosa mehr, sondern eine Angabe.
PROSA = re.compile(r'(vereinbar|termin|nach absprache|ausser|außer|ferien|urlaub|'
                   r'sowie|nur |ggf|sprechstunde|notdienst|feiertag)', re.I)
GESCHLOSSEN = re.compile(r'geschlossen|ruhetag|closed', re.I)


def tage(text):
    """Alle Tage eines Abschnitts, Bereiche wie 'Montag - Donnerstag' aufgelöst."""
    treffer = []
    for m in TAG_RE.finditer(text):
        kuerzel = m.lastgroup
        # 'Mo - Do' heisst Bereich, 'Mo, Do' oder 'Mo + Do' heisst Aufzählung.
        if treffer and BIS.search(text[:m.start()]):
            a, b = ORDNUNG.index(treffer[-1]), ORDNUNG.index(kuerzel) if kuerzel in ORDNUNG else -1
            if b > a:
                treffer.extend(ORDNUNG[a + 1:b + 1])
                continue
        if kuerzel not in treffer:
            treffer.append(kuerzel)
    return treffer


def zwoelfstunden(stunde, marke, marke_ende):
    """5 + 'pm' -> 17. Fehlt die Marke vorn, gilt die hintere ('4:30–10 pm' ist Nachmittag).

    Die Plattform 9gg.de liefert deutsche Wochentage mit englischen Zeiten, und ohne diese
    Umrechnung wurde aus dem Kneipenabend "Montag 5–9:30 pm" ein "05:00-09:30" — ein Wert, der
    sauber aussieht, durch jede Syntaxpruefung kommt und trotzdem falsch ist.
    """
    marke = (marke or marke_ende or '').lower()
    if marke == 'pm' and stunde < 12:
        return stunde + 12
    if marke == 'am' and stunde == 12:
        return 0
    return stunde


def spannen(text):
    raus = []
    for m in SPANNE.finditer(text):
        h1, m1, ende1, h2, m2, ende2 = (m.group(1), m.group(2) or '00', m.group(3),
                                        m.group(4), m.group(5) or '00', m.group(6))
        if int(h1) > 24 or int(h2) > 24 or int(m1) > 59 or int(m2) > 59:
            continue
        a = zwoelfstunden(int(h1), ende1, ende2)
        b = zwoelfstunden(int(h2), ende2, ende2)
        raus.append(f'{a:02d}:{m1}-{b:02d}:{m2}')
    return raus


def bauen(roh):
    """Rohtext -> (opening_hours, Vermerk)."""
    if not roh.strip():
        return '', ''
    plan, letzte, zweiter = {}, [], False
    for teil in roh.split('|'):
        t = teil.strip()
        d, s = tage(t), spannen(t)
        # "Sonntag Geschlossen" ist eine Angabe, kein Fehlen. Sie wegzulassen macht aus
        # "geprueft und zu" ein "unbekannt" — und sie kostete den Vorschlag fuer Locke und
        # Oez Urfa den Tag, an dem die Sperre "laesst Tage weg" zuschlug.
        if d and not s and GESCHLOSSEN.search(t):
            for tag in d:
                plan.setdefault(tag, [])
            letzte = d
            continue
        if not s:
            continue
        # Taucht ein Wochentag ein zweites Mal auf, führt die Seite zwei Zeitblöcke — zwei
        # Behandler, zwei Standorte, Sommer und Winter. Zusammengelegt ergäbe das Unsinn
        # (eine Praxis bekam 'Tu 08:00-16:30,10:00-18:30'), also endet die Auswertung hier.
        if d and all(tag in plan for tag in d):
            zweiter = True
            break
        # Eine Zeile ohne Tag, aber mit Spanne, gehört zur vorherigen Zeile: so stehen
        # Mittagspausen auf vielen Seiten ('Mo + Mi + Fr 08:00-13:00' / '14:30-18:00').
        ziel = d or letzte
        if not ziel:
            continue
        for tag in ziel:
            plan.setdefault(tag, [])
            for spanne in s:
                if spanne not in plan[tag]:
                    plan[tag].append(spanne)
        letzte = ziel

    if not plan:
        return '', 'keine Tag-Zeit-Paare erkannt' if roh.strip() else ''

    # Tage mit identischen Spannen zusammenfassen, in Wochenreihenfolge.
    reihenfolge = ORDNUNG + ['PH']
    gruppen, lauf = [], []
    for tag in reihenfolge:
        if tag not in plan:
            if lauf:
                gruppen.append(lauf)
                lauf = []
            continue
        if lauf and plan[lauf[-1]] == plan[tag] and tag != 'PH':
            lauf.append(tag)
        else:
            if lauf:
                gruppen.append(lauf)
            lauf = [tag]
    if lauf:
        gruppen.append(lauf)

    teile = []
    for g in gruppen:
        kopf = g[0] if len(g) == 1 else (f'{g[0]}-{g[-1]}' if len(g) > 2 else ','.join(g))
        spannen_text = ','.join(sorted(plan[g[0]])) if plan[g[0]] else 'off'
        teile.append(f'{kopf} {spannen_text}')

    # Ueber Mitternacht laufende Zeiten brauchen additive Regeln. Mit Semikolon getrennt gilt
    # jede Regel fuer den *ganzen* Tag und loescht den Uebertrag der vorigen: bei
    # "Mo-Th 11:00-01:00; Fr 11:00-03:00" faellt die Donnerstagnacht weg, sobald der Freitag
    # eine eigene Regel hat. opening_hours.js warnt an drei Stellen; mit Komma sind es null.
    ueber_nacht = any(s.split('-')[1] <= s.split('-')[0]
                      for spannen_liste in plan.values() for s in spannen_liste)
    wert = (', ' if ueber_nacht else '; ').join(teile)

    vermerke = []
    if zweiter:
        vermerke.append('zweiter Zeitenblock auf der Seite, nur der erste verwendet')
    if PROSA.search(roh):
        vermerke.append('Prosa im Original')
    if len(plan) < 5:
        vermerke.append(f'nur {len(plan)} Tage erkannt')
    return wert, '; '.join(vermerke)


def main():
    if '--test' in sys.argv:
        for probe in ('Mo – Do 8 – 12 Uhr und 13 – 18 Uhr | Fr 8 – 14 Uhr',
                      'Mo + Mi +Fr 08:00 - 13:00 Uhr | 14:30 - 18:00 Uhr | Di + Do 08:00 - 13:00',
                      'Montag - Donnerstag 08:00 bis 12:00 Uhr | Freitag 08:00 bis 13:00 Uhr',
                      'Dienstag-Freitag 10:00-18:00 | Samstag 10:00-14:00 | Montag geschlossen'):
            print(f'{probe}\n  -> {bauen(probe)}\n')
        return

    zeilen = list(csv.DictReader(open(DATEI, encoding='utf-8')))
    felder = list(zeilen[0]) if zeilen else []
    for neu in ('Zeiten_osm', 'Zeiten_pruefen'):
        if neu not in felder:
            felder.append(neu)

    gebaut = 0
    for r in zeilen:
        r['Zeiten_osm'], r['Zeiten_pruefen'] = bauen(r.get('Zeiten_roh', ''))
        gebaut += bool(r['Zeiten_osm'])

    with open(DATEI, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=felder)
        w.writeheader()
        w.writerows(zeilen)
    print(f'{gebaut} von {len(zeilen)} Zeilen mit Vorschlag')


if __name__ == '__main__':
    main()
