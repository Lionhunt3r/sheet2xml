# sheet2xml

Wandelt Noten in MusicXML um, das sich direkt in Sibelius, MuseScore, Dorico oder Finale öffnen lässt.
Als Eingabe gehen PDFs, Scans und Handyfotos.

## So funktioniert es

| Eingabe | Weg | Genauigkeit |
|---|---|---|
| PDF aus einem Notenprogramm (Sibelius, Dorico, Finale, MuseScore) | Die Noten werden direkt aus dem PDF gelesen: Notenköpfe, Hälse, Balken, Vorzeichen, Bögen und Stimmen stehen dort als Zeichen und Linien. | sehr hoch, meist fehlerfrei |
| Scan oder Foto | Das Bild wird entzerrt, begradigt und aufgehellt. Danach erkennt [Audiveris](https://github.com/Audiveris/audiveris) die Noten (optische Notenerkennung). | ordentlich bei sauberen Vorlagen, bitte gegenlesen |

Ob ein PDF Notensatz-Daten enthält, erkennt das Programm selbst.

Takte, deren Rhythmus nicht aufgeht, werden markiert. Mit der Option **Mit Claude prüfen** schickt der Server diese Takte als Bildausschnitt
zusammen mit der eigenen Lesung an Claude und übernimmt die Korrektur nur, wenn sie rhythmisch stimmt. Das gilt für PDFs aus
Notenprogrammen ebenso wie für Scans und Fotos; dort lohnt es sich besonders, weil die Notenerkennung öfter Fähnchen,
Punkte oder Noten übersieht. Bei Fotos bleiben die erkannten Liedtexte erhalten.
Dafür braucht der Server einen `ANTHROPIC_API_KEY`.

## Server starten (Docker, z. B. auf TrueNAS)

```bash
git clone https://github.com/Lionhunt3r/sheet2xml.git
cd sheet2xml
docker compose up -d --build
```

Danach ist die Upload-Seite unter `http://<server>:8000` erreichbar. Auf dem Handy lässt sie sich über
„Zum Home-Bildschirm“ wie eine App ablegen; „Foto aufnehmen“ öffnet direkt die Kamera.

Einstellungen über Umgebungsvariablen (z. B. in einer `.env`-Datei neben `docker-compose.yml`):

| Variable | Bedeutung |
|---|---|
| `ANTHROPIC_API_KEY` | aktiviert die Prüfung durch Claude |
| `SHEET2XML_TOKEN` | schützt den Server mit einem Zugangsschlüssel |
| `SHEET2XML_WORKERS` | gleichzeitige Umwandlungen (Standard 2) |
| `SHEET2XML_MAX_UPLOAD_MB` | maximale Uploadgröße (Standard 80) |

**TrueNAS SCALE:** Jeder Push auf `main` baut das Image automatisch als `ghcr.io/lionhunt3r/sheet2xml:latest`.
Unter *Apps → Discover Apps → Custom App* dieses Image eintragen, Container-Port 8000 auf einen freien Port legen und
die Umgebungsvariablen oben nach Bedarf setzen. Ist das Paket auf GitHub privat, vorher unter
*Package settings → Change visibility* öffentlich machen oder in TrueNAS Zugangsdaten für `ghcr.io` hinterlegen.
Alternativ per Shell im geklonten Ordner `docker compose up -d --build`.

**Mac mit Apple Silicon:** Docker Desktop installieren, dann wie oben. Das Image ist für x86_64 gebaut und läuft über Rosetta.

## App (Flutter)

Im Ordner `app/` liegt eine Flutter-App für Android, iOS, macOS und Web. Sie spricht mit dem Server:
Server-Adresse (z. B. `http://truenas.local:8000`) in den Einstellungen eintragen, dann PDF wählen oder
Seiten fotografieren, umwandeln und die MusicXML-Datei speichern oder per „Teilen“ direkt in MuseScore,
Sibelius & Co. öffnen.

```bash
cd app
flutter run                    # auf angeschlossenem Gerät
flutter build apk              # Android
flutter build ios              # iOS (auf dem Mac mit Xcode)
flutter build macos            # Mac-App
```

GitHub baut bei jedem Push auf `main` eine Android-APK und die Web-Version (unter *Actions → App → Artifacts*).

## Kommandozeile

```bash
pip install -e ".[review]"
sheet2xml "Partitur.pdf"                 # schreibt Partitur.musicxml
sheet2xml seite1.jpg seite2.jpg -o lied.musicxml
sheet2xml "Partitur.pdf" --review        # auffällige Takte von Claude prüfen lassen
```

Für Scans und Fotos muss [Audiveris](https://github.com/Audiveris/audiveris/releases) installiert sein
(Pfad bei Bedarf über `AUDIVERIS_BIN`), für Liedtexte zusätzlich die Tesseract-Modelle `deu` und `eng`
aus [tessdata](https://github.com/tesseract-ocr/tessdata) (Ordner über `TESSDATA_PREFIX`).

## API

| Aufruf | Zweck |
|---|---|
| `POST /api/jobs` | Formular-Upload: `files` (ein PDF oder mehrere Bilder), `review=true/false` |
| `GET /api/jobs/{id}` | Status, Fortschrittsmeldungen, Stimmen, Taktanzahl, Hinweise |
| `GET /api/jobs/{id}/result` | die MusicXML-Datei |
| `GET /api/health` | Version und verfügbare Funktionen |

Ist `SHEET2XML_TOKEN` gesetzt, erwartet jede Anfrage `Authorization: Bearer <token>`.

## Entwicklung

```bash
pip install -e ".[server,dev]"
pytest
python tools/evaluate.py ergebnis.musicxml referenz.mxl   # Vergleich mit einer Referenz
```

Aufbau des Codes:

- `src/sheet2xml/vector/`: PDF aus Notenprogrammen lesen (Schriften, Notenzeilen und Systeme, Symbole, Stimmen zuordnen)
- `src/sheet2xml/omr/`: Bildaufbereitung und Audiveris
- `src/sheet2xml/review.py`: Prüfung auffälliger Takte durch Claude
- `src/sheet2xml/musicxml.py`: MusicXML schreiben
- `src/sheet2xml/web/`: API und Upload-Seite
