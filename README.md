# HomeQuests Backend + Webapp

Dieses Verzeichnis ist als eigenstaendiges Repo nutzbar (API + Webapp unter `/`).

## 1) Nur Backend/Webapp in ein eigenes GitHub-Repo pushen

Diese Variante behaelt die Historie des `backend`-Ordners:

```bash
cd /Users/macminiserver/Documents/Xcode/HomeQuests
git subtree split --prefix=backend -b codex/backend-only
git remote add backend-origin git@github.com:DEIN_USER/homequests-backend.git
git push backend-origin codex/backend-only:main
```

Optional Branch lokal entfernen:

```bash
git branch -D codex/backend-only
```

## 2) Lokal mit Docker starten (im Backend-Repo)

```bash
cd backend
docker volume create homequests_postgres_data
docker compose up --build
```

Danach:

- Webapp: `http://localhost:8010/`
- API-Doku: `http://localhost:8010/docs`
- Health: `http://localhost:8010/health`

Optional anderer Port:

```bash
cd backend
API_PORT=8025 docker compose up --build
```

## Fehlerbehebung

- Wichtig: Für persistenten Betrieb niemals `docker compose down -v` verwenden.
  `-v` löscht Volumes absichtlich.
- Das Postgres-Volume ist als externes Volume konfiguriert (`homequests_postgres_data`).

- `Conflict. The container name "/homequests-db" is already in use`
  - Alte Container entfernen und neu starten:
    ```bash
    docker volume create homequests_postgres_data
    docker compose down --remove-orphans
    docker compose up --build -d
    ```
  - Hinweis: In `docker-compose.yml` sind feste `container_name` entfernt.

- `could not translate host name "db" to address`
  - API findet den DB-Host nicht.
  - Wenn du den Stack nutzt, muss der Service `db` mitlaufen.
  - Bei externer Datenbank `DB_HOST`/`DB_PORT` oder `DATABASE_URL` setzen, z. B.:
    ```bash
    DATABASE_URL=postgresql+psycopg2://user:pass@dein-db-host:5432/homequests
    ```

## Sicherer Update-Flow ohne DB-Verlust

```bash
docker volume create homequests_postgres_data
docker compose pull api
docker compose up -d --no-deps api
```

Wenn alte Daten noch im früheren Volume `familienplaner_postgres_data` liegen:

```bash
POSTGRES_VOLUME_NAME=familienplaner_postgres_data docker compose up -d
```

## 3) Docker-Image bauen und direkt nutzen

Image bauen:

```bash
cd backend
docker build -t ghcr.io/DEIN_USER/homequests-backend:latest .
```

Bei GHCR anmelden und pushen:

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u DEIN_USER --password-stdin
docker push ghcr.io/DEIN_USER/homequests-backend:latest
```

Image direkt starten:

```bash
docker run --rm -p 8010:8000 \
  -e DATABASE_URL='postgresql+psycopg2://homequests:homequests@HOST:5432/homequests' \
  -e SECRET_KEY='CHANGE_THIS_SECRET' \
  ghcr.io/DEIN_USER/homequests-backend:latest
```

Hinweis: Fuer den produktiven Betrieb ist ein separater PostgreSQL-Container oder Managed-Postgres noetig.

## 4) Codex mit Repo nutzen

Ja, das geht direkt:

1. Repo lokal klonen oder im bestehenden Ordner lassen.
2. Ordner in Codex oeffnen.
3. In Codex Aufgaben geben wie:
   - "Implementiere Feature X im Backend"
   - "Schreibe Tests"
   - "Erstelle Commit und Push auf Branch `codex/feature-x`"

Wenn `origin` gesetzt ist und du eingeloggt bist, kann Codex die Git-Schritte lokal ausfuehren.
