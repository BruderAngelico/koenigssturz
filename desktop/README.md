# Desktop-Hüllen (macOS / später Windows)

Die Python-Programme (`kingfall_web.py`, `kingfall_va_reader.py`, …) sind
**plattformneutral**: FastAPI auf `127.0.0.1`, Daten lokal, UI im Browser bzw.
eingebettetem WebView.

## macOS (fertig in diesem Branch)

| App | Bundle | Einstieg |
|-----|--------|----------|
| Königssturz | `dist/Königssturz.app` | `KINGFALLProgram=web` → `kingfall_macos.py --web` |
| VA Reader | `dist/VA Reader.app` | `KINGFALLProgram=reader` → `kingfall_macos.py --reader` |

Bauen: `./macos/build.sh` (Swift + Cocoa/WebKit, Icons aus `icons/`).

## Windows (nächster Schritt für den Maintainer)

Vorschlag, analog zur macOS-Hülle:

1. Kleine Host-App (WinUI / WPF / WinForms + **WebView2**) startet Python mit
   `kingfall_macos.py --web` bzw. `--reader` (oder Umgebungsvariable
   `KINGFALL_PROGRAM`).
2. Wartet auf die Zeile `KINGFALL_READY url=http://127.0.0.1:…/` und lädt die URL.
3. Datenordner z. B. unter `%LOCALAPPDATA%\Königssturz` bzw.
   `%LOCALAPPDATA%\KoenigssturzVAReader` (über `KINGFALL_CWD` setzen).
4. Icons: Master-PNGs in `icons/koenigssturz.png` und `icons/va-reader.png`
   zu `.ico` konvertieren und als App-Icon setzen.

Keine Fachlogik in die Hülle legen – Download, Pack, Import, Audio-Umwandlung
bleiben in den gemeinsamen Python-Modulen.
