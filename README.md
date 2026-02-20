# HomeQuests Backend

HomeQuests ist ein Familien-Aufgabenplaner mit Rollen, Punkten und Belohnungen.
Dieses Repo enthaelt das FastAPI-Backend plus Weboberflaeche.

## Was ist enthalten?

- REST API mit JWT-Login
- Rollenmodell: `admin`, `parent`, `child`
- Bootstrap-Setup fuer erste Familie + Admin
- Aufgabenverwaltung inkl. Wiederholungen
- Aufgaben-Freigaben (Child -> Parent)
- Sonderaufgaben mit Intervall-Limits
- Belohnungen inkl. Einloesung/Freigabe
- Punktekonto + Historie
- Familienkalender
- Live-Updates per SSE
- Webapp unter `/`

## Projektstruktur

- `app/` - FastAPI Anwendung
- `Dockerfile` - API Image Build
- `docker-compose.yml` - lokales Build-Setup
- `docker-compose.portainer.yml` - Deployment per GHCR-Image
- `.github/workflows/docker-ghcr.yml` - Build + Push nach GHCR bei Push auf `main`

## Lokal starten (Developer)

```bash
docker volume create homequests_postgres_data
docker compose up --build
```

Danach:
- Webapp: `http://localhost:8010/`
- API Doku: `http://localhost:8010/docs`
- Health: `http://localhost:8010/health`

Optional anderer Port:

```bash
API_PORT=8025 docker compose up --build
```

## GitHub Setup (wichtig)

### 1) Workflow-Rechte aktivieren

Im Repo unter `Settings -> Actions -> General`:

- Actions erlauben
- `Workflow permissions` auf `Read and write permissions`

Nur dann kann die Action Images nach GHCR pushen.

### 2) Automatischer Docker Build

Workflow-Datei:
- `.github/workflows/docker-ghcr.yml`

Trigger:
- bei jedem Push auf `main`
- manuell via `workflow_dispatch`

Resultierende Images:
- `ghcr.io/kolossboss/homequests-api:latest`
- `ghcr.io/kolossboss/homequests-api:sha-<commit>`

## Deployment mit Portainer (Proxmox)

Verwende als Stack-Datei:
- `docker-compose.portainer.yml`

Diese Datei zieht ein fertiges GHCR-Image (`pull_policy: always`).

### ENV-Variablen in Portainer setzen

Setze im Stack mindestens:

- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_VOLUME_NAME` (z. B. `homequests_postgres_data`)
- `SECRET_KEY`

Optional:

- `DB_HOST` und `DB_PORT` (wenn DB nicht im selben Stack läuft)
- `DATABASE_URL` (überschreibt alle Einzelwerte)

Beispiel fuer starken `SECRET_KEY`:

```bash
openssl rand -hex 64
```

Alternative:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

### Wichtige Fehlerbilder

- Niemals `down -v` im Produktivbetrieb nutzen, da Volumes sonst gelöscht werden.
- Das DB-Volume ist als externes Volume konfiguriert und muss existieren:
  ```bash
  docker volume create homequests_postgres_data
  ```
- Falls deine Altdaten noch in `familienplaner_postgres_data` liegen, setze:
  ```bash
  POSTGRES_VOLUME_NAME=familienplaner_postgres_data
  ```

- `could not translate host name "db" to address`
  - Stack wurde nicht als kompletter Compose-Stack gestartet oder `DATABASE_URL` zeigt auf falschen Host.
  - In Portainer immer `db` + `api` zusammen deployen oder `DATABASE_URL` auf externe DB setzen.

- `Conflict. The container name "/homequests-db" is already in use`
  - Altcontainer entfernen und Stack neu deployen:
    ```bash
    docker rm -f homequests-db homequests-api 2>/dev/null || true
    ```
  - Hinweis: Feste `container_name` sind jetzt aus den Compose-Dateien entfernt.

### GHCR privat vs. public

Wenn `ghcr.io/kolossboss/homequests-api` privat ist:
- In Portainer Registry fuer `ghcr.io` anlegen
- Username: `kolossboss`
- Passwort: GitHub PAT (classic) mit mindestens `read:packages`

Wenn das Package public ist:
- kein Registry-Login fuer Pull noetig

## Update-Flow (empfohlen)

1. Feature-Branch erstellen (`codex/...`)
2. PR auf `main`
3. Merge auf `main`
4. GitHub Action baut/pusht neues Image
5. In Portainer Stack `Update/Re-deploy` ausfuehren (Volume bleibt erhalten)

## Git-Workflow fuer dieses Repo

Helfer-Skript:

```bash
./tools/codex_git_flow.sh
```

Typischer Ablauf:

```bash
./tools/codex_git_flow.sh start feature-name
# Aenderungen umsetzen
./tools/codex_git_flow.sh save "Feature: kurze Beschreibung"
```

`save` ist absichtlich nur auf `codex/*` erlaubt, damit `main` stabil bleibt.
