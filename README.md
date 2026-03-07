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
API_PORT=8010
APNS_ENABLED=false
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
    ports:
      - "${API_PORT:-8010}:8000"

volumes:
  homequests_postgres_data:
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
- Wenn beim Test `401 Unauthorized` kommt, sind URL oder Token in der Regel falsch.
- In HomeQuests ist immer nur ein Benachrichtigungskanal gleichzeitig aktiv (`SSE`, `APNs` oder `Home Assistant`).

## Wichtiger Hinweis zu Daten

- Die Daten liegen in der Postgres-Datenbank (Volume `homequests_postgres_data`)
- Nicht `docker compose down -v` verwenden, wenn Daten erhalten bleiben sollen
