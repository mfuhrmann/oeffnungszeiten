#!/usr/bin/env python3
"""
josm_export.py — schreibt die Funde als .osm-Dateien, die JOSM direkt öffnen und hochladen kann.

Der Umweg über CSV und Abtippen entfällt damit, aber der Blick des Menschen nicht: die Dateien
tragen `action="modify"`, JOSM zeigt jede Änderung einzeln an, und hochgeladen wird erst nach
Durchsehen. Genau so soll es sein — 166 Objekte blind hochzuladen wäre ein mechanischer Edit
und bräuchte nach OSM-Regeln vorher eine Ankündigung in der Community.

Jedes Objekt wird unmittelbar vor dem Schreiben frisch von der API geholt. Das kostet eine
Sekunde pro Objekt, liefert aber die aktuelle `version` (ohne die lehnt der Upload ab) und
zeigt, ob inzwischen jemand anders den Tag schon gesetzt hat.

**Vorhandene Werte werden nie überschrieben.** Ein Tag wird nur gesetzt, wenn er fehlt.

`opening_hours` ist der Sonderfall: den tragen alle diese POIs bereits, das war die
Abfragebedingung. Die Zeiten von der Betriebsseite sind deshalb kein Nachtrag, sondern eine
Probe. Stimmen sie überein, wird `check_date:opening_hours` gesetzt — die Aussage "am
15.08.2026 an der Quelle geprüft", die OSM sonst nirgends hat. Weichen sie ab, fasst das Skript
nichts an und schreibt die Zeile nach `zeiten_abgleich.csv`; darüber entscheidet ein Mensch,
denn eine Seite kann genauso veraltet sein wie die Karte.

    python3 ../../scripts/josm_export.py                      # taggen-sicher.csv, 25 Objekte je Datei
    python3 ../../scripts/josm_export.py --liste pruefen.csv
    python3 ../../scripts/josm_export.py --stueck 40 --ohne-abgleich
Aufzurufen aus dem Arbeitsordner der Recherche: die CSV-Dateien liegen dort, nicht im
Repository.
"""
import csv
import datetime
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

API = 'https://api.openstreetmap.org/api/0.6'
UA = 'oeffnungszeiten-fulda/1.0 (website-tag research; kontakt via OSM-Nachricht)'
ZUSATZ = 'zusatzdaten.csv'
ZUORDNUNG = 'zuordnung.csv'
SPERRE = 'kontakt-sperre.csv'
ORDNER = 'josm'
HEUTE = datetime.date.today().isoformat()

ABTEILUNG = {'kundenservice', 'freshdesk', 'support', 'datenschutz', 'webmaster', 'presse',
             'karriere', 'bewerbung', 'marketing', 'service'}

ORDNUNG = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']
OSM_TAG = re.compile(r'\b(Mo|Tu|We|Th|Fr|Sa|Su|PH)\b')
OSM_SPANNE = re.compile(r'\b(\d{2}):(\d{2})-(\d{2}):(\d{2})\b')


def hole(typ, ident):
    pfad = f'{API}/{typ}/{ident}' + ('/full' if typ == 'way' else '')
    req = urllib.request.Request(pfad, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return ET.fromstring(r.read())


def tags(el):
    return {t.get('k'): t.get('v') for t in el.findall('tag')}


def telefon_osm(roh):
    """0661/76209 -> +49 661 76209. Nur was sicher zuzuordnen ist, sonst leer."""
    z = re.sub(r'\D', '', roh)
    if z.startswith('0049'):
        z = '0' + z[4:]
    elif z.startswith('49') and not z.startswith('490'):
        z = '0' + z[2:]
    if not z.startswith('0') or not 8 <= len(z) <= 14:
        return ''
    for vorwahl in ('0661',):                      # Fulda selbst
        if z.startswith(vorwahl):
            return f'+49 {vorwahl[1:]} {z[len(vorwahl):]}'
    if re.match(r'^0(15|16|17)\d', z):             # mobil, dreistellige Netzkennzahl
        return f'+49 {z[1:4]} {z[4:]}'
    if re.match(r'^066\d\d', z):                   # Landkreis
        return f'+49 {z[1:5]} {z[5:]}'
    return ''


def plan(wert):
    """opening_hours -> {Tag: [Spannen]}. Gibt None zurück, wenn etwas nicht verstanden wird."""
    if not wert or re.search(r'(sunrise|sunset|week|Jan|Feb|Mar|Apr|May|Jun|Jul|'
                             r'Aug|Sep|Oct|Nov|Dec|\d{4}|"|24/7)', wert):
        return None
    raus = {}
    # Etliche Werte trennen ihre Regeln mit Komma statt Semikolon ('Mo-Fr 08:00-13:00,
    # Mo 13:30-18:00'). Ohne diese Trennung landen Tage und Spannen zweier Regeln in einem
    # Topf, und der Abgleich meldet eine Abweichung, die keine ist.
    roh_teile = []
    for stueck in wert.split(';'):
        roh_teile.extend(re.split(r',\s*(?=(?:Mo|Tu|We|Th|Fr|Sa|Su|PH)\b)', stueck))
    # Aufzählungen wie 'Su, PH off' teilen sich einen Zusatz; ein Stück ohne eigene Uhrzeit
    # gehört noch zum folgenden und wird wieder angehängt.
    teile, offen = [], ''
    for stueck in roh_teile:
        kandidat = f'{offen}, {stueck}' if offen else stueck
        if OSM_SPANNE.search(stueck) or re.search(r'\b(off|closed)\b', stueck):
            teile.append(kandidat)
            offen = ''
        else:
            offen = kandidat
    if offen:
        teile.append(offen)
    for teil in teile:
        teil = teil.strip()
        # 'Su off', 'PH off' — geschlossene Tage stehen in der Karte ausdrücklich, auf der
        # Betriebsseite meist gar nicht. Beides heisst dasselbe, also fallen sie hier weg.
        if re.search(r'\b(off|closed)\b', teil):
            continue
        spannen = [f'{a}:{b}-{c}:{d}' for a, b, c, d in OSM_SPANNE.findall(teil)]
        if not spannen:
            return None
        tage, marken = [], OSM_TAG.findall(teil.split(str(spannen[0])[:2])[0] if spannen else teil)
        marken = OSM_TAG.findall(re.sub(r'\d{2}:\d{2}-\d{2}:\d{2}', '', teil))
        i = 0
        while i < len(marken):
            tag = marken[i]
            tage.append(tag)
            # 'Mo-Fr' steht als zwei Marken mit einem Bindestrich dazwischen
            if i + 1 < len(marken) and f'{tag}-{marken[i + 1]}' in teil:
                a, b = ORDNUNG.index(tag), ORDNUNG.index(marken[i + 1])
                tage.extend(ORDNUNG[a + 1:b + 1])
                i += 1
            i += 1
        for tag in tage:
            raus.setdefault(tag, [])
            for s in spannen:
                if s not in raus[tag]:
                    raus[tag].append(s)
    return {k: sorted(v) for k, v in raus.items()} or None


def gleich(a, b):
    pa, pb = plan(a), plan(b)
    if pa is None or pb is None:
        return None
    return pa == pb


def schreiben(pfad, elemente):
    osm = ET.Element('osm', version='0.6', generator='josm_export.py', upload='true')
    for el in elemente:
        osm.append(el)
    ET.ElementTree(osm).write(pfad, encoding='utf-8', xml_declaration=True)


def main():
    liste = 'taggen-sicher.csv'
    stueck = 25
    if '--liste' in sys.argv:
        liste = sys.argv[sys.argv.index('--liste') + 1]
    if '--stueck' in sys.argv:
        stueck = int(sys.argv[sys.argv.index('--stueck') + 1])
    abgleich = '--ohne-abgleich' not in sys.argv

    zusatz = {}
    if os.path.exists(ZUSATZ):
        zusatz = {r['OSM']: r for r in csv.DictReader(open(ZUSATZ, encoding='utf-8'))}
    zuordnung = {}
    if os.path.exists(ZUORDNUNG) and '--ohne-zuordnung' not in sys.argv:
        zuordnung = {r['OSM']: r for r in csv.DictReader(open(ZUORDNUNG, encoding='utf-8'))}
    # Objekte, deren Telefon oder Mail nicht am Ort der Adresse steht. Eine Filialliste
    # traegt zwanzig Nummern; welche unsere ist, entscheidet die Naehe zur Adresse, und wo
    # die fehlt, nimmt der Crawler die erste der Seite. Happ Pacelliallee 4 bekam so die
    # Nummer der Kanalstrasse. Diese Objekte behalten die Website und verlieren den Rest.
    sperre = set()
    if os.path.exists(SPERRE):
        # Spalte `Feld` grenzt ein, was gesperrt ist: `phone`, `email`, oder leer fuer beides.
        # Noetig, seit die Kettenfaelle auftauchten, wo die Filialseite die richtige Nummer
        # nennt und nur die Mailadresse aus dem Konzern-Impressum stammt (Targobank, BDH).
        for r in csv.DictReader(open(SPERRE, encoding='utf-8')):
            f = (r.get('Feld') or '').strip().lower()
            sperre.update({(r['OSM'], x) for x in ([f] if f in ('phone', 'email')
                                                   else ['phone', 'email'])})

    os.makedirs(ORDNER, exist_ok=True)
    zeilen = list(csv.DictReader(open(liste, encoding='utf-8')))
    fertig, uebersprungen, abweichungen, gesetzt, zurueck = [], [], [], [], []
    zweifel = []

    for i, r in enumerate(zeilen, 1):
        m = re.search(r'/(node|way|relation)/(\d+)', r['OSM'])
        if not m:
            continue
        typ, ident = m.groups()
        try:
            wurzel = hole(typ, ident)
        except Exception as e:
            uebersprungen.append((r['Name'], f'API: {type(e).__name__}'))
            continue
        el = wurzel.find(f'.//{typ}[@id="{ident}"]')
        if el is None:
            uebersprungen.append((r['Name'], 'geloescht?'))
            continue

        vorhanden = tags(el)
        z = zusatz.get(r['OSM'], {})
        neu = {}

        # Zwei Sperren, beide aus Schaden gelernt. Von den ersten 25 exportierten Objekten
        # waren vier falsch: zwei tote Domains, eine 404, und eine Seite, die zu einem
        # gleichnamigen Betrieb 300 km entfernt gehoerte.
        #
        # (1) Was der Crawler nicht erreicht hat, wird nicht getaggt. Der Status stand die
        #     ganze Zeit in zusatzdaten.csv, der Export hat ihn nur nie angesehen.
        # (2) Eine erreichbare Seite muss eine Fuldaer Adresse oder Telefonnummer tragen,
        #     sonst ist nicht belegt, dass sie zu diesem POI gehoert.
        # Ausdrueckliche Entscheidung eines Menschen: Kettenstartseiten werden getaggt,
        # obwohl sie keine Fuldaer Adresse nennen koennen (siehe ketten_freigeben.py).
        # Die Erreichbarkeitspruefung gilt weiter — eine tote Kettenseite bleibt draußen.
        kette = 'KETTENSEITE' in r.get('Beleg', '')
        # `GEPRUEFT` ist dasselbe Zugestaendnis fuer den umgekehrten Fall: die Seite *nennt*
        # die Adresse, nur nicht in einer Form, die die Erkennung sieht — "Karl str 5" ohne
        # Punkt, "Friedrichstr. 1" ohne PLZ daneben, oder ein OSM-Objekt ganz ohne Adresse,
        # dessen Lage nur ueber die Entfernung zu belegen ist. Wer das Wort setzt, hat die
        # Seite gelesen und schreibt den Grund in den Beleg. Sperre 1 gilt weiter.
        geprueft = 'GEPRUEFT' in r.get('Beleg', '')
        frei = kette or geprueft
        status = z.get('Status', '')
        # `nachpruefen_render.py` schreibt "ok (gerendert)" bzw. "ohne Zeiten (gerendert)".
        # Ein Vergleich auf Gleichheit hielt genau die Zeilen zurueck, die der Browser gerade
        # gerettet hatte — Andrea Funke, Satinee und brillen.de lagen so unbemerkt fest.
        # "nichts gefunden" heisst: die Seite kam an, es stand nur nichts Fuldaer Konkretes
        # darauf. Genau das ist bei einer Kettenstartseite der Normalfall und der Grund, warum
        # es `KETTENSEITE` ueberhaupt gibt — als Erreichbarkeitsfehler gewertet hielt es
        # Jack & Jones, ONLY und Snipes fest, die alle drei mit 200 antworten. Ein echter
        # Fehler (HTTP 4xx/5xx, URLError) sperrt weiter, auch die Kettenseite.
        erreicht = status.startswith(('ok', 'ohne Zeiten')) or (frei and status == 'nichts gefunden')
        if z and not erreicht:
            zurueck.append({'OSM': r['OSM'], 'Name': r.get('Name', ''),
                            'Website': r.get('Website', ''), 'Grund': f'Seite: {status}'})
            continue
        if z and not frei and r.get('Website') and not (z.get('Adresse') or z.get('Telefon')):
            zurueck.append({'OSM': r['OSM'], 'Name': r.get('Name', ''),
                            'Website': r.get('Website', ''),
                            'Grund': 'Seite nennt weder Fuldaer Adresse noch Telefon'})
            continue
        # (3) Das Urteil von pruefe_zuordnung.py: gehoert die Seite ueberhaupt zu diesem
        #     Betrieb? Faengt den Fall gleicher Name, andere Stadt.
        zu = zuordnung.get(r['OSM'], {})
        if zu and not frei and zu.get('Urteil') != 'ok':
            zurueck.append({'OSM': r['OSM'], 'Name': r.get('Name', ''),
                            'Website': r.get('Website', ''),
                            'Grund': f"Zuordnung: {zu.get('Grund') or zu.get('Urteil')}"})
            continue

        if r.get('Website') and 'website' not in vorhanden and 'contact:website' not in vorhanden:
            neu['website'] = r['Website']
        # Social-Auftritte werden bewusst NICHT getaggt: fuer den Monitor sind sie unbrauchbar
        # (keine Zeiten, kein stabiler Text), und als Kontaktangabe bringen sie der Karte zu
        # wenig, um den Pflegeaufwand zu rechtfertigen. `social.csv` bleibt als Rechercheergebnis
        # bestehen — dort steht, welcher Betrieb ueberhaupt nur dort auftritt.

        # Auf einer Kettenstartseite steht die Zentrale, nicht die Filiale: liberty nennt
        # Bad Hersfeld, Vero Moda customerservice@bestseller.com, Leguano die Bueroezeiten der
        # Firma. Fuer diese Zeilen ist ausschliesslich der website-Tag belegt — alles andere
        # waere die Adresse eines fremden Ortes am Fuldaer Laden.
        if kette:
            if neu:
                for tag_name, wert in neu.items():
                    ET.SubElement(el, 'tag', k=tag_name, v=wert)
                    gesetzt.append({'OSM': r['OSM'], 'Name': r.get('Name', ''),
                                    'Tag': tag_name, 'Wert': wert})
                el.set('action', 'modify')
                fertig.append((el, wurzel, typ))
                print(f'{i:>3}/{len(zeilen)}  {r.get("Name", "")[:32]:<32} '
                      f'{", ".join(neu)} (Kettenseite)')
            else:
                uebersprungen.append((r['Name'], 'nichts zu setzen'))
            time.sleep(1)
            continue

        # `Kontaktanker` sagt, worauf Nummer und Mailadresse beruhen (seiten_daten.py):
        # `adresse` = im Umfeld der OSM-Adresse gefunden, `einzig` = die Seite nennt nur diesen
        # einen Kontakt, `unsicher` = mehrere zur Auswahl und keiner bei unserer Adresse. Der
        # letzte Fall ist geraten und geht nicht in die Karte — so bekam der Fuldaer POI der
        # Ergotherapie Dörr die Nummer der Zweitpraxis in Tann-Günthers.
        # `unsicher` heisst nicht falsch: eine Filialliste nennt viele Anschriften, und
        # `fc-fulda@lbs-ht.de` oder `horas@sparkasse-fulda.de` sind trotzdem filialgenau. Der
        # Wert geht mit, die Zeile aber zusaetzlich in `kontakt-pruefen.csv` — ein Mensch sieht
        # sich an, was auf einer Seite mit mehreren Standorten aufgesammelt wurde.
        if z.get('Kontaktanker', '') == 'unsicher' and (z.get('Telefon') or z.get('EMail')):
            zweifel.append({'OSM': r['OSM'], 'Name': r.get('Name', ''),
                            'Website': r.get('Website', ''), 'Telefon': z.get('Telefon', ''),
                            'EMail': z.get('EMail', '')})
        tel = '' if (r['OSM'], 'phone') in sperre else telefon_osm(z.get('Telefon', ''))
        if tel and not (vorhanden.keys() & {'phone', 'contact:phone'}):
            neu['phone'] = tel
        # Abteilungspostfaecher gehoeren nicht an einen Laden: Fielmann liefert
        # kundenservice@, eyes+more freshdesk@ — richtige Adressen des Konzerns, aber niemand
        # erreicht darueber die Fuldaer Filiale. Dieselbe Klasse wie giessen@koehler24.de.
        mail = '' if (r['OSM'], 'email') in sperre else z.get('EMail', '')
        # Manche Seiten setzen die Adresse in Versalien (MAIL@CASA-ESPANA.INFO). Das ist
        # Typografie, nicht Schreibweise — durchgaengig grosse Adressen werden kleingeschrieben,
        # gemischte bleiben unangetastet.
        if mail.isupper():
            mail = mail.lower()
        lokal = mail.split('@')[0].lower()
        if mail and lokal in ABTEILUNG:
            mail = ''
        if mail and not (vorhanden.keys() & {'email', 'contact:email'}):
            neu['email'] = mail

        # Zeiten: nie überschreiben. Übereinstimmung bestätigt, Abweichung wird berichtet.
        seite, karte = z.get('Zeiten_osm', ''), vorhanden.get('opening_hours', '')
        if abgleich and seite and karte:
            urteil = gleich(seite, karte)
            if urteil is True and 'check_date:opening_hours' not in vorhanden:
                neu['check_date:opening_hours'] = HEUTE
            elif urteil is False:
                abweichungen.append({'OSM': r['OSM'], 'Name': r.get('Name', ''),
                                     'Karte': karte, 'Seite': seite,
                                     'Roh': z.get('Zeiten_roh', '')[:200],
                                     'Website': r.get('Website', '')})

        if not neu:
            uebersprungen.append((r['Name'], 'nichts zu setzen'))
            continue

        for k, v in neu.items():
            ET.SubElement(el, 'tag', k=k, v=v)
            gesetzt.append({'OSM': r['OSM'], 'Name': r.get('Name', ''), 'Tag': k, 'Wert': v})
        el.set('action', 'modify')
        fertig.append((el, wurzel, typ))
        print(f'{i:>3}/{len(zeilen)}  {r.get("Name", "")[:32]:<32} {", ".join(neu)}')
        time.sleep(1)

    grund = os.path.splitext(os.path.basename(liste))[0]
    for n in range(0, len(fertig), stueck):
        teil = fertig[n:n + stueck]
        elemente = []
        for el, wurzel, typ in teil:
            if typ == 'way':          # Stützpunkte mitgeben, sonst zeigt JOSM die Linie nicht
                elemente.extend(wurzel.findall('node'))
            elemente.append(el)
        pfad = os.path.join(ORDNER, f'{grund}-{n // stueck + 1:02d}.osm')
        schreiben(pfad, elemente)
        print(f'  -> {pfad}  ({len(teil)} Objekte)')

    if zurueck:
        with open('nachpruefen.csv', 'w', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=list(zurueck[0]))
            w.writeheader()
            w.writerows(zurueck)
        print(f'{len(zurueck)} zurueckgehalten -> nachpruefen.csv')
    if zweifel:
        with open('kontakt-pruefen.csv', 'w', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=list(zweifel[0]))
            w.writeheader()
            w.writerows(zweifel)
        print(f'{len(zweifel)} Kontakte von Seiten mit mehreren Standorten -> kontakt-pruefen.csv')

    if gesetzt:
        with open(os.path.join(ORDNER, 'aenderungen.csv'), 'w', newline='',
                  encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=list(gesetzt[0]))
            w.writeheader()
            w.writerows(gesetzt)

    if abweichungen:
        with open('zeiten_abgleich.csv', 'w', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=list(abweichungen[0]))
            w.writeheader()
            w.writerows(abweichungen)
        print(f'\n{len(abweichungen)} Zeiten weichen ab -> zeiten_abgleich.csv')
    print(f'{len(fertig)} Objekte exportiert, {len(uebersprungen)} uebersprungen')


if __name__ == '__main__':
    main()
