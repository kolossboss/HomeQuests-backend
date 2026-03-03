# HomeQuests Backend + WebUI

<p align="left">
  <a href="https://apps.apple.com/de/app/homequests/id6759489304" target="_blank" rel="noopener noreferrer">
    <img src="app/web/static/favicon-homequests.png" alt="HomeQuests iOS App Icon" width="96" height="96" />
  </a>
</p>

**HomeQuests iOS App:** [HomeQuests im App Store](https://apps.apple.com/de/app/homequests/id6759489304)

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

## Schnellstart mit Docker Compose

### Voraussetzungen

- Docker + Docker Compose
- Freier Port `8010` (oder eigener Port)

### 1) Projekt holen

```bash
git clone https://github.com/kolossboss/HomeQuests-backend.git
cd HomeQuests-backend
```

### 2) Sicheren Secret Key erzeugen

```bash
openssl rand -base64 48
```

Den erzeugten Wert aufheben (wird gleich in `.env` verwendet).

### 3) `.env` Datei erstellen

```bash
cat > .env <<'ENV'
POSTGRES_PASSWORD=CHANGE_DB_PASSWORD
SECRET_KEY=CHANGE_THIS_WITH_OPENSSL_OUTPUT
API_PORT=8010
ENV
```

### 4) `docker-compose.yml` erstellen (oder vorhandene Datei nutzen)

```yaml
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
    ports:
      - "${API_PORT:-8010}:8000"

volumes:
  homequests_postgres_data:
```

### 5) Starten

```bash
docker compose up -d
```

## Erreichbarkeit

Wenn `API_PORT=8010` gesetzt ist:

- WebUI: `http://SERVER-IP:8010/`
- API-Doku (Swagger): `http://SERVER-IP:8010/docs`
- Healthcheck: `http://SERVER-IP:8010/health`

## Portainer Anleitung (Stack)

1. In Portainer: **Stacks** -> **Add stack**
2. Stack-Name: `homequests`
3. Obige Compose-Datei einfuergen
4. Unter **Environment variables** setzen:
   - `POSTGRES_PASSWORD`
   - `SECRET_KEY`
   - optional `API_PORT` (Standard `8010`)
5. **Deploy the stack** klicken
6. Danach WebUI/API ueber `http://SERVER-IP:PORT` aufrufen

## Nur mit `docker run` starten

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

## Wichtiger Hinweis zu Daten

- Die Daten liegen in der Postgres-Datenbank (Volume `homequests_postgres_data`)
- Nicht `docker compose down -v` verwenden, wenn Daten erhalten bleiben sollen
