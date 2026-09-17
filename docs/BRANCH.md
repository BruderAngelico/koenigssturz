# Branch-Änderungen (für PR / Push)

Kurzüberblick, was gegenüber dem Basis-Königssturz neu bzw. erweitert ist – damit
der Maintainer den PR annehmen und direkt mit Windows-Hüllen weitermachen kann.

## Zwei Programme

1. **Königssturz** – Beweissicherung (Supabase, IONOS-Mail, Vereinsarchiv-Download,
   Auswertung) plus **Paket für Reader**.
2. **VA Reader** – eigenständiges Leseprogramm: Zip-Pakete einspielen, Stammtische
   browsen, Audio/Folien/Transkript. **Kein cURL, kein Netz.**

Gemeinsame Module: `kingfall_va_store.py`, `kingfall_va_pack.py`.  
Einstieg Desktop-Hülle: `kingfall_macos.py` (`--web` / `--reader`).

## macOS-Apps

- Build: `./macos/build.sh` → `dist/Königssturz.app`, `dist/VA Reader.app`
- Icons: `icons/koenigssturz.png` / `icons/va-reader.png` (+ `.icns`)
- Daten: Königssturz → `Dokumente/Königssturz`; Reader → Application Support

## Vereinsarchiv / Reader (Funktionsstand)

- Abgleich vor Download, Folien/PDFs mitladen und öffnen
- Opus/OGG → M4A für den Player (ffmpeg/imageio-ffmpeg), Fortschritt inkl.
  Listen-Label „umwandeln x%“
- Konvertierung nach Import/Download, nicht beim bloßen Anklicken
- Pakete enthalten bereits gewandeltes Audio (`audio.play.m4a`)
- Mehrfach-Löschen per Checkbox („Auswahl löschen“)
- Spalten Audio + Docs; Spaltenbreiten auto + per Drag
- Listen-Layout ohne Überlappung mit dem Pack-Bereich

## Plattform-Hinweis

Python-Fachlogik ist OS-agnostisch (localhost FastAPI).  
`macos/` = aktuelle Host-UI.  
`desktop/README.md` = Skizze für Windows (WebView2).  
Texte sprechen von „diesem Rechner“, nicht nur Mac.

## Start zum Testen

```text
./macos/build.sh
open dist/Königssturz.app
open "dist/VA Reader.app"
```

Oder ohne App-Bundle:

```text
python3 kingfall_web.py
python3 kingfall_va_reader.py
```

## Push (wenn du bereit bist)

```text
git status
git add -A
git status   # dist/, pydeps/, vereinsarchiv/ bleiben ignoriert
git commit -m "VA Reader, macOS-Apps, Vereinsarchiv-Verbesserungen"
git push
```

`dist/` und `macos/pydeps*` sind in `.gitignore` – der Maintainer baut die Apps
lokal mit `./macos/build.sh`. Icons und Quellcode gehören ins Repo.
