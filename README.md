# HomeQuests Backend + WebUI

<p align="left">
  <a href="https://apps.apple.com/de/app/homequests/id6759489304" target="_blank" rel="noopener noreferrer">
    <img src="app/web/static/favicon-homequests.png" alt="HomeQuests iOS App Icon" width="96" height="96" />
  </a>
</p>

**HomeQuests iOS App:** [HomeQuests im App Store](https://apps.apple.com/de/app/homequests/id6759489304)
**Home Assistant Integration:** [homequests-backend-ha](https://github.com/kolossboss/homequests-backend-ha)

## Was ist HomeQuests?

HomeQuests ist ein Belohnungssystem fuer Familien:

- Eltern erstellen Aufgaben (z. B. Zimmer aufraeumen, Hausaufgaben, Muell rausbringen)
- Kinder erledigen Aufgaben und sammeln Punkte
- Punkte koennen gegen Belohnungen eingeloest werden
- Rollen und Familienstrukturen sind im Backend abgebildet
- Eine WebUI und die iOS-App greifen auf dieselbe API zu

## Komponenten

- **Backend API**: Auth, Familien, Aufgaben, Punkte, Belohnungen
- **WebUI**: Browser-Oberflaeche fuer Verwaltung und Nutzung
- **iOS App**: Mobile Nutzung fuer Eltern und Kinder

## Inhaltsverzeichnis

Installation:
1. [Portainer (Empfohlen)](#portainer-empfohlen)
2. [Docker Compose](#docker-compose)
3. [Docker Run](#docker-run)

Weitere Punkte:
1. [Erreichbarkeit](#erreichbarkeit)
2. [Update auf neue Version](#update-auf-neue-version)
3. [Wichtiger Hinweis zu Daten](#wichtiger-hinweis-zu-daten)
4. [Benachrichtigungen](#benachrichtigungen)
5. [Datenbank-Tools (Backup/Cleanup)](#datenbank-tools-backupcleanup)

## Installation

### Portainer (Empfohlen)

1. In Portainer: **Stacks** -> **Add stack**
2. Stack-Name: `homequests`
3. Compose-Datei einfuegen (siehe Abschnitt [Docker Compose](#docker-compose))
4. Unter **Environment variables** setzen:
   - `POSTGRES_PASSWORD`
   - `SECRET_KEY`
   - optional `API_PORT` (Standard `8010`)
   - optional fuer APNs:
     - `APNS_ENABLED=true`
     - `APNS_TEAM_ID`
     - `APNS_KEY_ID`
     - `APNS_BUNDLE_ID=swapps.HomeQuests`
     - `APNS_PRIVATE_KEY_PATH`
     - `PUSH_WORKER_ENABLED=true`
   - Details: [Apple Push Notification (APNs) Setup](docs/apns-remote-push.md)
5. **Deploy the stack** klicken
6. Danach WebUI/API ueber `http://SERVER-IP:PORT` aufrufen

### Docker Compose

#### Voraussetzungen

- Docker + Docker Compose
- Freier Port `8010` (oder eigener Port)

#### 1) Projekt holen

```bash
git clone https://github.com/kolossboss/HomeQuests-backend.git
cd HomeQuests-backend
```

#### 2) Sicheren Secret Key erzeugen

```bash
openssl rand -base64 48
```

Den erzeugten Wert aufheben (wird gleich in `.env` verwendet).

#### 3) `.env` Datei erstellen

Im Projektordner ausfuehren:

```bash
cat > .env <<'ENV'
POSTGRES_PASSWORD=CHANGE_DB_PASSWORD
SECRET_KEY=CHANGE_THIS_WITH_OPENSSL_OUTPUT
SECRET_ENCRYPTION_KEY=
API_PORT=8010
APNS_ENABLED=false
SSE_ALLOW_QUERY_TOKEN=false
ENV
```

#### Optional: Apple Push Notification (APNs) aktivieren

APNs ist optional. HomeQuests laeuft auch ohne Apple Developer Account.

- Ohne APNs: `APNS_ENABLED=false`
- Mit APNs: Apple Developer Credentials + APNs Key erforderlich

Die vollstaendige Schritt-fuer-Schritt-Anleitung ist hier:

- [Apple Push Notification (APNs) Setup](docs/apns-remote-push.md)

#### 4) `docker-compose.yml` erstellen (oder vorhandene Datei nutzen)

Im Projektordner ausfuehren:

```bash
cat > docker-compose.yml <<'YAML'
name: homequests

services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: homequests
      POSTGRES_USER: homequests
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - homequests_postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U homequests -d homequests"]
      interval: 5s
      timeout: 5s
      retries: 10

  api:
    image: ghcr.io/kolossboss/homequests-api:latest
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql+psycopg2://homequests:${POSTGRES_PASSWORD}@db:5432/homequests
      SECRET_KEY: ${SECRET_KEY}
      ACCESS_TOKEN_EXPIRE_MINUTES: 525600
      APNS_ENABLED: ${APNS_ENABLED:-false}
      APNS_TEAM_ID: ${APNS_TEAM_ID:-}
      APNS_KEY_ID: ${APNS_KEY_ID:-}
      APNS_BUNDLE_ID: ${APNS_BUNDLE_ID:-swapps.HomeQuests}
      APNS_PRIVATE_KEY: ${APNS_PRIVATE_KEY:-}
      APNS_PRIVATE_KEY_PATH: ${APNS_PRIVATE_KEY_PATH:-}
      PUSH_WORKER_ENABLED: ${PUSH_WORKER_ENABLED:-false}
      PUSH_WORKER_INTERVAL_SECONDS: ${PUSH_WORKER_INTERVAL_SECONDS:-60}
      DB_BACKUP_ALLOWED_DIRS: ${DB_BACKUP_ALLOWED_DIRS:-/data/backups,/tmp/homequests-backups}
      DB_BACKUP_DEFAULT_DIR: ${DB_BACKUP_DEFAULT_DIR:-/data/backups}
      DB_BACKUP_TIMEOUT_SECONDS: ${DB_BACKUP_TIMEOUT_SECONDS:-180}
      DB_BACKUP_UPLOAD_MAX_BYTES: ${DB_BACKUP_UPLOAD_MAX_BYTES:-536870912}
      DB_CLEANUP_MAX_PASSES: ${DB_CLEANUP_MAX_PASSES:-8}
    ports:
      - "${API_PORT:-8010}:8000"
    volumes:
      # Standard: persistentes Docker-Volume fuer Backups.
      # Optional Host-Pfad setzen: BACKUP_MOUNT_SOURCE=/opt/homequests/backups
      - ${BACKUP_MOUNT_SOURCE:-homequests_backup_data}:/data/backups

volumes:
  homequests_postgres_data:
  homequests_backup_data:
YAML
```

#### 5) Starten

```bash
docker compose up -d
```

## Erreichbarkeit

Wenn `API_PORT=8010` gesetzt ist:

- WebUI: `http://SERVER-IP:8010/`
- API-Doku (Swagger): `http://SERVER-IP:8010/docs`
- Healthcheck: `http://SERVER-IP:8010/health`

## Docker Run

### 1) Postgres starten

```bash
docker network create homequests_net

docker run -d \
  --name homequests-db \
  --network homequests_net \
  -e POSTGRES_DB=homequests \
  -e POSTGRES_USER=homequests \
  -e POSTGRES_PASSWORD=CHANGE_DB_PASSWORD \
  -v homequests_postgres_data:/var/lib/postgresql/data \
  postgres:16-alpine
```

### 2) API + WebUI starten

```bash
docker run -d \
  --name homequests-api \
  --network homequests_net \
  -p 8010:8000 \
  -e DATABASE_URL='postgresql+psycopg2://homequests:CHANGE_DB_PASSWORD@homequests-db:5432/homequests' \
  -e SECRET_KEY='CHANGE_THIS_WITH_OPENSSL_OUTPUT' \
  -e ACCESS_TOKEN_EXPIRE_MINUTES='525600' \
  -e APNS_ENABLED='false' \
  -e PUSH_WORKER_ENABLED='false' \
  ghcr.io/kolossboss/homequests-api:latest
```

## Update auf neue Version

### Docker Compose

```bash
docker compose pull api
docker compose up -d --no-deps api
```

### Portainer

- Stack oeffnen -> **Pull and redeploy**

## Benachrichtigungen

### Apple Push Notification (APNs)

- APNs ist optional.
- Mit APNs bekommst du zuverlaessige iOS-Remote-Pushes ueber Apple.
- Die vollstaendige Einrichtung (Team ID, Key ID, `.p8`, Bundle ID) steht hier:
  - [Apple Push Notification (APNs) Setup](docs/apns-remote-push.md)

### Home Assistant Benachrichtigungen

HomeQuests kann Benachrichtigungen alternativ auch ueber Home Assistant senden.

1. Home Assistant URL finden
   - In Home Assistant: **Einstellungen -> System -> Netzwerk**
   - Nutze dort die **Lokale URL** oder **Externe URL** als `Base URL`
   - Beispiel: `http://192.168.1.20:8123` oder `https://ha.deinedomain.tld`
2. Long-Lived Access Token erstellen
   - In Home Assistant rechts unten auf dein **Profil** klicken
   - Abschnitt **Long-Lived Access Tokens** -> **Create Token**
   - Token einmalig kopieren und sicher ablegen
3. Notify-Service (Device) finden
   - In Home Assistant: **Entwicklerwerkzeuge -> Dienste**
   - Als Dienst z. B. `notify.mobile_app_iphone_von_simon` waehlen
   - Der Teil hinter `notify.` ist der Service-Name fuer HomeQuests:
     - Beispiel: `mobile_app_iphone_von_simon`
4. In HomeQuests konfigurieren
   - WebUI: **System -> Benachrichtigungskanäle -> Home Assistant -> Bearbeiten**
   - `Base URL`, `Token`, optional `SSL pruefen` setzen
   - Pro Nutzer das Geraet/den Notify-Service hinterlegen und speichern
   - Mit **Testen** pro Nutzer pruefen

Hinweise:
- Der Token wird im Backend verschluesselt gespeichert.
- Fuer getrennte Schluessel kannst du `SECRET_ENCRYPTION_KEY` setzen (falls leer, wird `SECRET_KEY` verwendet).
- `SSE_ALLOW_QUERY_TOKEN=false` ist empfohlen. Dann akzeptiert der Live-Stream keine `?access_token=` URLs, sondern nur Authorization Header/Cookie.
- Wenn beim Test `401 Unauthorized` kommt, sind URL oder Token in der Regel falsch.
- In HomeQuests ist immer nur ein Benachrichtigungskanal gleichzeitig aktiv (`SSE`, `APNs` oder `Home Assistant`).

## Datenbank-Tools (Backup/Cleanup/Restore)

Im **System-Tab** der WebUI gibt es jetzt DB-Tools:
- `Status laden`: Integritäts-/Duplikat-Diagnose für Aufgaben
- `Backup erstellen`: PostgreSQL-Dump (`pg_dump`, Format `custom`)
- `Backup erstellen und herunterladen`: erzeugt Backup serverseitig und lädt es direkt im Browser herunter (Zielordner lokal per Browser-Dialog)
- `Cleanup ausführen`: sicheres Wartungs-Cleanup über die bestehende Task-Maintenance-Logik
- `ANALYZE ausführen`: PostgreSQL `ANALYZE` für Statistik-Updates

Restore:
- Ein Restore ist über die **Erstinitialisierung** möglich (`Oder aus Backup wiederherstellen`).
- Zusätzlich können `.dump`-Dateien direkt in der Erstinitialisierung hochgeladen werden (`Backup hochladen`).
- API dazu:
  - `GET /auth/bootstrap-backups`
  - `POST /auth/bootstrap-backups/upload` (multipart/form-data)
  - `POST /auth/bootstrap-restore`
  - `GET /families/{family_id}/system/db-tools/backup/download?backup_file=...`
- Restore ist nur erlaubt, wenn noch kein Benutzer existiert (Bootstrap-Phase), damit aktive Produktivdaten nicht überschrieben werden.

Wichtige ENV-Variablen:
- `DB_BACKUP_ALLOWED_DIRS` (Komma-getrennte absolute Basispfade)
- `DB_BACKUP_DEFAULT_DIR` (muss unter `DB_BACKUP_ALLOWED_DIRS` liegen)
- `DB_BACKUP_TIMEOUT_SECONDS` (Default `180`)
- `DB_BACKUP_UPLOAD_MAX_BYTES` (Default `536870912`, also 512 MB)
- `DB_CLEANUP_MAX_PASSES` (Default `8`)

Hinweise:
- Backup-Ziele sind aus Sicherheitsgründen auf `DB_BACKUP_ALLOWED_DIRS` begrenzt.
- Wenn Backups auf dem Host persistent liegen sollen, den Zielpfad ins API-Container-Dateisystem mounten.
- Cleanup löscht nicht blind Daten, sondern nutzt dieselbe Wartungslogik wie der laufende Betrieb.
- Restore spielt den gesamten HomeQuests-Datenbankinhalt zurück (z. B. Nutzer, Passwort-Hashes, Rollen, Aufgaben, Historie, Belohnungen, Punkte, System-Events).
- Nicht im Backup enthalten sind Docker-/Host-Secrets und externe Dateien außerhalb der Datenbank (z. B. APNs `.p8`, `.env`, Reverse-Proxy/TLS-Konfiguration).

### Server-Neuaufsetzung mit Restore (empfohlen)

1. Neue Instanz starten (leere DB, gleiche App-Version oder neuer).
2. Sicherstellen, dass das Backup im erlaubten Pfad liegt (`DB_BACKUP_ALLOWED_DIRS`).
3. WebUI öffnen:
   - Erstinitialisierung sichtbar
   - Entweder:
     - `Backups laden` -> Backup wählen -> `Backup wiederherstellen`
   - Oder:
     - `.dump` via `Backup hochladen` laden -> anschließend `Backup wiederherstellen`
4. Danach mit bestehendem Benutzerkonto einloggen.

## Wichtiger Hinweis zu Daten

- Die Daten liegen in der Postgres-Datenbank (Volume `homequests_postgres_data`)
- Nicht `docker compose down -v` verwenden, wenn Daten erhalten bleiben sollen
