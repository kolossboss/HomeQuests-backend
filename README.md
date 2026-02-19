# HomeQuests Backend + Webapp

FastAPI-Backend mit Webapp (`/`) und PostgreSQL.

## Lokal starten

```bash
docker compose up --build
```

Danach:
- Webapp: `http://localhost:8010/`
- API-Doku: `http://localhost:8010/docs`
- Health: `http://localhost:8010/health`

Optional anderer Port:

```bash
API_PORT=8025 docker compose up --build
```

## Automatisches Docker-Image in GHCR

Dieses Repo hat eine GitHub Action unter:
- `.github/workflows/docker-ghcr.yml`

Die Action baut und pusht bei jedem Push auf `main` nach:
- `ghcr.io/kolossboss/homequests-api:latest`
- `ghcr.io/kolossboss/homequests-api:sha-<commit>`

Wichtig in GitHub:
1. Repo `Settings -> Actions -> General`: Workflows erlaubt.
2. Repo `Settings -> Actions -> General -> Workflow permissions`:
   - `Read and write permissions` aktivieren.
3. Optional: Package-Sichtbarkeit in GHCR auf `Public` setzen,
   damit Portainer ohne Registry-Login pullen kann.

## Portainer / Proxmox Nutzung

Fuer Portainer liegt eine image-basierte Compose-Datei bereit:
- `docker-compose.portainer.yml`

Sie nutzt:
- `image: ghcr.io/kolossboss/homequests-api:latest`
- `pull_policy: always`

Start:

```bash
docker compose -f docker-compose.portainer.yml up -d
```

Hinweis:
- Wenn GHCR-Package privat ist, musst du in Portainer eine Registry
  fuer `ghcr.io` mit GitHub-Token hinterlegen.

## Codex Workflow (sicher)

Helfer-Skript:

```bash
./tools/codex_git_flow.sh
```

Standardablauf:

```bash
./tools/codex_git_flow.sh start feature-name
# Aenderungen machen lassen
./tools/codex_git_flow.sh save "Feature: kurze Beschreibung"
```

`save` ist absichtlich nur auf `codex/*`-Branches erlaubt.
