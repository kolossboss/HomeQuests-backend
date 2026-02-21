# HomeQuests Backend + WebUI

HomeQuests ist ein Backend mit WebUI fuer Familienorganisation:
Aufgaben planen, Punkte vergeben, Belohnungen einloesen und den Verlauf verfolgen.

## Funktionen

- Rollen: `admin`, `parent`, `child`
- Login mit JWT
- Ersteinrichtung (Bootstrap) beim ersten Start
- Aufgaben mit Wiederholung (einmalig, taeglich, woechentlich, monatlich)
- Aufgaben-Einreichung und Eltern-Freigabe
- Sonderaufgaben mit Limits pro Intervall
- Belohnungen und Einloesungen
- Punktekonten + Ledger-Historie
- Familienkalender
- Live-Updates per SSE
- WebUI unter `/`

## Schnellstart (lokal)

Voraussetzung: Docker + Docker Compose

1. Sicheren Key erzeugen:

```bash
openssl rand -hex 64
```

2. Starten (nur 2 Variablen noetig):

```bash
POSTGRES_PASSWORD='DEIN_STARKES_DB_PASSWORT' \
SECRET_KEY='DEIN_SECRET_KEY' \
docker compose up --build -d
```

3. Aufrufen:

- WebUI: `http://localhost:8010/`
- API Doku (Swagger): `http://localhost:8010/docs`
- Healthcheck: `http://localhost:8010/health`

## Erster Start

Beim ersten Aufruf ist noch kein Benutzer vorhanden.
In der WebUI die Ersteinrichtung ausfuellen:

- Name
- E-Mail (optional)
- Passwort + Passwort-Wiederholung

Danach ist der erste Admin angelegt.

## Deployment mit Portainer / Proxmox

Nutze diese Datei im Stack:

- `docker-compose.portainer.yml`

Noetige Stack-Variablen:

- `POSTGRES_PASSWORD`
- `SECRET_KEY`

Dann Stack deployen und bei Updates einfach `Update/Re-deploy` ausfuehren.

## API

Wichtige Endpunkte:

- `POST /auth/bootstrap`
- `GET /auth/bootstrap-status`
- `POST /auth/login`
- `GET /auth/me`
- `GET /docs`
- `GET /health`

## Docker-Image via GitHub

Bei jedem Merge auf `main` baut GitHub Actions automatisch ein neues Image:

- `ghcr.io/kolossboss/homequests-api:latest`
- `ghcr.io/kolossboss/homequests-api:sha-<commit>`

Workflow-Datei:

- `.github/workflows/docker-ghcr.yml`

Hinweis:
- Wenn das Image spaeter `public` ist, koennen Nutzer es ohne GHCR-Login pullen.
- Solange es `private` ist, braucht Portainer Registry-Credentials fuer `ghcr.io`.

## Troubleshooting

- Fehler `could not translate host name "db"`:
  - Stack unvollstaendig gestartet oder falsche Compose-Datei genutzt.
- Fehler `Datenbankverbindung fehlgeschlagen`:
  - `POSTGRES_PASSWORD` und `SECRET_KEY` gesetzt?
- Daten nach Neustart weg:
  - Nicht mit `down -v` arbeiten.
