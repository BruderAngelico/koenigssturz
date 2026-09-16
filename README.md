# Königssturz

Lokale Beweissicherung für eine Supabase-Datenbank und ein IONOS-Postfach, plus Auswertung der gesicherten Dateien im Browser. Zusätzlich lädt der Karteireiter **Vereinsarchiv** Stammtische (öffentlich und intern) samt Audio lokal herunter. Es läuft nur auf deinem Rechner (`127.0.0.1`). Nichts wird in die Cloud geschickt, außer du pushst selbst nach GitHub.

Die Sicherungen liegen als Ordner neben dem Programm, Namen wie `beweissicherung_2026-…`. Git ignoriert diese Ordner absichtlich: darin stehen Personen-, Rechnungs- und Maildaten. Dasselbe gilt für `vereinsarchiv/`.

## Start

Im Projektordner, einmalig Abhängigkeiten:

```text
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

Dann die Oberfläche:

```text
python kingfall_web.py
```

Der Browser öffnet sich von selbst, typischerweise [http://127.0.0.1:18765/](http://127.0.0.1:18765/). Das Terminalfenster offen lassen. Port festlegen: `python kingfall_web.py --port 18765`.

Vier Karteireiter: **Supabase**, **E-Mail (IONOS)**, **Vereinsarchiv**, **Auswertung**.

## Supabase sichern

1. In den DevTools der laufenden Web-App einen cURL-Request kopieren (irgendein API-Aufruf mit gültigem Token) und ins große Feld einfügen.
2. Optional E-Mail, Passwort und TOTP eintragen. Dann erneuert Königssturz den Token, wenn er abläuft.
3. **Hole Tables** – die Schemas erscheinen. **Empfohlen** kreuzt `public`, `auth` und `storage` an.
4. **Tabellen laden**, dann **Der König ist tot, lang lebe der König**.

Jede Sicherung schreibt in einen **neuen** Ordner. Ein Test-Run (Knopf oben rechts) holt nur wenige Zeilen, zum Ausprobieren.

**Fortfahren** setzt eine unterbrochene Sicherung fort, ohne schon fertige Tabellen noch einmal zu holen.

Pro Tabelle entstehen typischerweise:

- `{schema}_{tabelle}.jsonl` – die Zeilen
- `schema_{schema}_{tabelle}.json` – Spalten, Primary Key, Foreign Keys
- `beweissicherung_{schema}_{tabelle}.json` – Kopie als JSON-Array

## E-Mail (IONOS) sichern

IMAP, Standard `imap.ionos.de` Port 993. Große Postfächer werden in 25er-Paketen geholt. Nach einem Timeout: Ordnerliste neu holen, fertige Ordner abwählen, nur den Rest sichern. Auch hier schreibt jeder Lauf in einen neuen Ordner. Die Mail-Übersicht unten ist nur eine Liste, keine Leseansicht.

## Vereinsarchiv

Stammtische vom Vorstandsbereich (`vereinsarchiv-vorstand.pages.dev`). Login dort geht nur per Magic-Link, deshalb keinen Benutzer/Passwort-Login in Königssturz, sondern einen cURL aus den DevTools:

1. Im Browser am Vereinsarchiv anmelden, DevTools → Network, einen API-Request als cURL kopieren.
2. Im Karteireiter **Vereinsarchiv** einfügen, **Token übernehmen**. Am besten einen Request nach `rest/v1` oder `storage` nehmen (Host `*.supabase.co`), nicht nur einen Aufruf der pages.dev-Seite.
3. Öffentlich und/oder intern ankreuzen, **Download**.

Schon vollständige Stammtische (JSON plus Audio, falls `audio_pfad` gesetzt ist) werden übersprungen. Es gibt keinen Zeitstempel-Ordner: alles landet in `vereinsarchiv/stammtische/` und `vereinsarchiv/stammtische_intern/`. Ein zweiter Lauf holt nur fehlende oder unvollständige Einträge.

Unten die lokale Liste: Titel, Kurzfassung, Fragen, Aufgaben, Sachstand, Transkript. Audio spielt aus der lokalen Datei. Klick auf eine Transkriptzeile springt in der Aufnahme an diese Stelle.

## Auswertung

Oben den Backup-Ordner wählen (Standard: der neueste). Links die Tabellen, rechts die Daten. Die Trennlinie dazwischen lässt sich ziehen.

### Suchen und filtern

- **Suche** durchsucht alle Spalten der gewählten Tabelle.
- **Nur Zeilen wo** setzt Bedingungen (ist, enthält, ist einer von, leer, größer gleich, zwischen, …). Nach Wahl der Spalte schlägt das Wertfeld häufige Einträge vor. **ist einer von** nimmt mehrere Werte mit Komma. Klick auf eine Zelle öffnet die Vorschau mit **Als Filter**; Umschalt+Klick setzt den Filter direkt. Ein zweiter Klick auf dieselbe Spalte sammelt die Werte in „ist einer von“.
- **Zeitraum von / bis** filtert eine wählbare Datumsspalte (Tag oder Monat). **Summe** gruppiert nach Monat und summiert `total_amount` (sonst Anzahl). In **Gruppiere nach** gibt es dafür auch `Spalte (Monat)`.
- **Zur Akte** merkt die aktuelle Ansicht. **Befund** schreibt sie als Markdown; liegen Einträge in der Akte, stehen sie alle in derselben Datei.
- **Gruppiere nach** und **Berechne** (Anzahl, Summe, …) erzeugen Verdichtungen. Darunter erscheint ein Balkendiagramm für Anzahl oder Summe.
- **SQL anzeigen/bearbeiten** zeigt das erzeugte `SELECT`. Wer den Text ändert, führt genau dieses SELECT aus. Mehrere Anweisungen, Kommentare und Dateizugriffe sind gesperrt.
- Spaltenköpfe sortieren aufsteigend/absteigend.
- Lange Zellen, JSON, HTML und XML: Klick öffnet eine Vorschau (Kopieren, Escape schließt, HTML optional in einem isolierten Rahmen).

### Überall suchen

Feld **Überall** plus **In allen Tabellen** sucht den Text in jeder Tabelle des Ordners (mindestens zwei Zeichen). Die Trefferliste darunter öffnet die Tabelle und übernimmt den Suchtext.

### Zwei Sicherungen vergleichen

Unter **Vergleich mit** den zweiten Ordner wählen.

- **Tabelle vergleichen** braucht eine Tabelle links. Es zeigt Zeilen, die nur links, nur rechts oder in beiden mit anderem Inhalt vorkommen. Grundlage ist der Primary Key aus dem Schema, sonst `id`.
- **Ordner vergleichen** braucht keine Tabelle: welche Tabellen neu, verschwunden oder in der Zeilenzahl anders sind. Ein Klick auf den Tabellennamen öffnet sie und startet den Zeilenvergleich.

Neu / gelöscht / geändert / gleich lassen sich ankreuzen. Farben: grün neu, rot gelöscht, beige geändert. Bei geänderten Zeilen sind die abweichenden Felder fett unterstrichen; der Tooltip zeigt alt → neu.

### Verwandte Zeilen

Zellen mit IDs, `mitgliedsnummer`, `invoice_id` und echten Foreign Keys aus dem Schema sind unterstrichen. Im Vorschaufenster gibt es **Öffnen: …** – die Zieltabelle wird mit Filter auf genau diesen Wert geladen.

### Spalten, Abfragen, Notiz

- **Spalten** blendet Felder aus (bleibt im Browser gespeichert, gilt auch für PNG).
- **Abfrage** speichert die aktuelle Kombination aus Suche, Filter, Datum, Gruppe und SQL unter einem Namen. Zu Tabellen mit `status` oder `mitgliedsnummer` gibt es fertige Vorschläge.
- **Notiz zum Befund** ist Freitext. Sie steht oben in der PNG und im Befund-Export. **Zur Akte** legt die aktuelle Ansicht ab; **Befund** schreibt die ganze Akte plus die aktuelle Ansicht.

### Export

| Knopf  | Inhalt |
|--------|--------|
| JSON   | Alle Treffer der aktuellen Abfrage oder des Vergleichs (bis 100 000 Zeilen) |
| CSV    | Dasselbe, UTF-8 mit BOM, öffnet sich in Excel |
| PNG    | Bild mit Ordner, Tabelle, Filtern, Notiz und der sichtbaren Tabelle. Nur wenn alles auf **eine Seite** passt (max. 40 Zeilen, 16 Spalten) |
| Befund | Markdown mit Notiz, Abfrage, sichtbarer Tabelle. Liegt etwas in der Akte, stehen alle Einträge in einer Datei. |

PNG und Befund nutzen die **sichtbaren** Spalten.

## Dateien und Git

Code: `kingfall.py` (Sicherung), `kingfall_web.py` (Browser-UI), `kingfall_analyze.py` (DuckDB-Auswertung), `kingfall_vereinsarchiv.py` (Stammtische + Audio), `requirements.txt`.

Repo: [github.com/bonzei123/koenigssturz](https://github.com/bonzei123/koenigssturz). Die `.gitignore` hält `.venv`, IDE-Kram, alle `beweissicherung_*`-Ordner und `vereinsarchiv/` raus. Nach Code-Änderungen:

```text
git add -A
git status
git commit -m "Kurze Begründung"
git push
```

`git add -A` nimmt die Sicherungsordner trotzdem nicht mit, solange die Ignore-Regeln stehen.

## Hinweise

- Die UI spricht Deutsch, die SQL-Spaltennamen bleiben wie in der Datenbank.
- DuckDB lädt jeweils eine Tabelle in den Speicher. Sehr große Tabellen beim Zeilenvergleich: Grenze 50 000 Zeilen, sonst zuerst filtern.
- Token, Passwörter und TOTP bleiben in der laufenden Sitzung, nicht in Git.
