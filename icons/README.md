# App-Icons

| Datei | Verwendung |
|-------|------------|
| `koenigssturz.png` | Master Königssturz (fallende Krone) |
| `va-reader.png` | Master VA Reader (dieselbe Krone + offenes Buch) |
| `Koenigssturz.icns` | macOS-Bundle Königssturz → `AppIcon.icns` |
| `VAReader.icns` | macOS-Bundle VA Reader → `AppIcon.icns` |

PNG-Master sind plattformneutral (auch für späteres Windows-`.ico`).  
`./macos/build.sh` kopiert die `.icns` als `Resources/AppIcon.icns` und setzt `CFBundleIconFile`.
