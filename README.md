# HomeQuests Backend + WebUI

HomeQuests ist ein Familienplaner fuer Aufgaben, Punkte und Belohnungen.
Dieses Repository enthaelt das Backend (FastAPI) und die WebUI.

## Was die App kann

- Rollenmodell mit `admin`, `parent`, `child`
- Login mit JWT
- Bootstrap fuer die Ersteinrichtung
- Aufgaben mit Wiederholung (`einmalig`, `taeglich`, `woechentlich`, `monatlich`)
- Aufgaben-Freigaben (Kind reicht ein, Eltern pruefen)
- Sonderaufgaben mit Intervall-Limits
- Belohnungen mit Einloesung und Freigabe
- Punktekonten und Verlauf (Ledger)
- Familienkalender
- Live-Updates per SSE
- WebUI unter `/`

## Tech Stack

- Python 3.12
- FastAPI
- PostgreSQL
- Docker / Docker Compose
- GitHub Actions + GHCR (Container Registry)

## Schnellstart (lokal mit Docker)

Voraussetzung: Docker + Docker Compose sind installiert.

1. Persistentes Docker-Volume anlegen:

```bash
docker volume create homequests_postgres_data
```

2. Sicheren Secret Key erzeugen:

```bash
openssl rand -hex 64
```

3. Starten (mindestens zwei ENV setzen):

```bash
POSTGRES_PASSWORD='DEIN_STARKES_DB_PASSWORT' \
SECRET_KEY='DEIN_SECRET_KEY' \
docker compose up --build -d
```

4. Aufrufen:

- WebUI: `http://localhost:8010/`
- API Doku (Swagger): `http://localhost:8010/docs`
- Health: `http://localhost:8010/health`

## Erster Login / Ersteinrichtung

Beim ersten Start ist noch kein Benutzer vorhanden.
In der WebUI fuehrst du die Bootstrap-Erstellung durch:

- Name
- E-Mail (optional)
- Passwort + Passwort-Bestaetigung

Danach kannst du dich normal anmelden und weitere Mitglieder anlegen.

## Wichtige Endpunkte

- `POST /auth/bootstrap`
- `GET /auth/bootstrap-status`
- `POST /auth/login`
- `GET /auth/me`
- `GET /docs` (Swagger UI)
- `GET /health`

## Deployment mit Portainer / Proxmox

Fuer Portainer ist eine image-basierte Compose-Datei vorhanden:

- `docker-compose.portainer.yml`

Diese nutzt das GHCR-Image:

- `ghcr.io/kolossboss/homequests-api:latest`

### Minimale ENV-Variablen in Portainer

- `POSTGRES_PASSWORD`
- `SECRET_KEY`

Weitere Werte sind in der Compose-Datei bereits sinnvoll vorbelegt.

## GitHub + Container Image (GHCR)

Beim Push auf `main` baut GitHub Actions automatisch ein neues Docker-Image und pusht nach GHCR.

Workflow-Datei:

- `.github/workflows/docker-ghcr.yml`

Image-Tags:

- `ghcr.io/kolossboss/homequests-api:latest`
- `ghcr.io/kolossboss/homequests-api:sha-<commit>`

### Wenn Repository/Package privat ist

Portainer braucht dann GHCR-Registry-Credentials:

- Registry: `ghcr.io`
- Username: `kolossboss`
- Passwort: GitHub PAT (classic) mit mindestens `read:packages`

## Update-Ablauf (empfohlen)

1. Aenderungen per PR nach `main` mergen.
2. GitHub Action baut/pusht neues Image.
3. In Portainer Stack `Update/Re-deploy` ausfuehren.

Hinweis: Nicht mit `down -v` arbeiten, wenn Daten erhalten bleiben sollen.

## Troubleshooting

- Fehler `could not translate host name "db"`:
  - Stack nicht komplett gestartet oder falscher DB-Host in `DATABASE_URL`.
- Fehler `Datenbankverbindung fehlgeschlagen`:
  - `POSTGRES_PASSWORD`/`DATABASE_URL` pruefen.
- Leere DB nach Neustart:
  - Sicherstellen, dass das persistente Volume verwendet wird.
