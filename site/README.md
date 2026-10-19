# Rätegrün

Platzhalter-Auftritt für eine linke Cannabis-Gemeinschaft im Rätesystem. Die Seite informiert. Sie verkauft nichts und wirbt nicht für Konsum.

Im selben Repository liegt **Königssturz**, ein lokales Python-Werkzeug. Das bleibt unangetastet. Diese Website lebt in `site/`.

Der Name steht in [`lib/siteConfig.ts`](lib/siteConfig.ts). Längere Texte stehen in [`content/`](content/).

## Start

```bash
cd site
npm install
npm run dev
```

Danach im Browser [http://localhost:3000](http://localhost:3000).

```bash
npm run lint
npm run build
```

## Was schon da ist

- Startseite und Unterseiten (Wer wir sind, Rätesystem, Regeln, Mitmachen, Support)
- Login und Registrierung nur als Oberfläche, mit Prüfung der Eingaben
- Altersdialog, gespeichert in `localStorage`, ohne Cookie
- Heller und dunkler Modus
- Impressum, Datenschutz, Barrierefreiheit als markierte Platzhalter
- `sitemap.xml` und `robots.txt`

Auth-Funktionen liegen in [`lib/auth`](lib/auth). Sie rufen kein Netz auf und enthalten keine Schlüssel.

## Offene Punkte

- Auth-Backend anbinden, zum Beispiel Supabase Auth, in `lib/auth`. Keine Schlüssel ins Repo.
- Rechtstexte von einer qualifizierten Person prüfen und die `[TODO]`-Felder ersetzen. Keine erfundenen Namen oder Anschriften einsetzen.
- Echte Texte, echter Name, finales Logo. Der jetzige Name „Rätegrün“ ist ein Platzhalter.
- Kontakt, Meldungen und Newsletter an einen echten, sicheren Kanal hängen. Bis dahin senden die Formulare nichts. Sie sind keine Meldestelle.
- Domain in `siteConfig.url` setzen.
- Altersdialog ist ein Hinweis, kein Identitätsnachweis.
- Beschlussverfahren, Awareness-Erreichbarkeit und Beitragsregeln mit der echten Gruppe schreiben.
