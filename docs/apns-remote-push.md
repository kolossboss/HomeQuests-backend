# APNs Remote Push Setup (Optional)

APNs ist optional.

- Ohne Apple Developer Account kann HomeQuests normal genutzt werden.
- Wenn `APNS_ENABLED=false` gesetzt ist, laufen Backend, WebUI und iOS-App weiter.
- Es fehlen dann nur echte Apple Remote Push Benachrichtigungen.

## Voraussetzungen

Nur der Betreiber des Backends braucht:

- Apple Developer Account
- Team ID
- APNs Auth Key (`.p8`)
- Key ID des APNs Keys
- Bundle ID der iOS-App: `swapps.HomeQuests`

Normale Nutzer der iOS-App muessen nichts konfigurieren.

## Wo bekommt man Team ID, Key ID und `.p8`?

1. Apple Developer Account oeffnen:
   - [https://developer.apple.com/account](https://developer.apple.com/account)
2. Team ID:
   - Bereich `Membership`
3. APNs Key erstellen:
   - Bereich `Certificates, Identifiers & Profiles`
   - dann `Keys`
   - neuen Key anlegen
   - `Apple Push Notifications service (APNs)` aktivieren
4. Danach erhaelt man:
   - `Key ID`
   - Download einer `.p8` Datei

Wichtig:

- Die `.p8` Datei kann nur einmal heruntergeladen werden.
- Datei danach sicher aufbewahren.
- Key am besten per Dateipfad einbinden (`APNS_PRIVATE_KEY_PATH`).

## APNs Umgebungsvariablen

```bash
APNS_ENABLED=true
APNS_TEAM_ID=DEIN_APPLE_TEAM_ID
APNS_KEY_ID=DEINE_APNS_KEY_ID
APNS_BUNDLE_ID=swapps.HomeQuests
APNS_PRIVATE_KEY_PATH=/run/secrets/AuthKey_XXXXXXXXXX.p8
PUSH_WORKER_ENABLED=true
PUSH_WORKER_INTERVAL_SECONDS=60
```

Alternativ kann der Inhalt des `.p8`-Keys direkt ueber `APNS_PRIVATE_KEY` gesetzt werden.

## Welche Umgebung fuer APNs?

- Der Key sollte fuer APNs nutzbar sein.
- Wenn moeglich keine unnoetige Einschraenkung auf nur `Sandbox` oder nur `Production`.
- Die App nutzt:
  - Debug/Entwicklung: `development`
  - TestFlight/App Store: `production`

## Beispiel `.env` mit APNs

```bash
POSTGRES_PASSWORD=CHANGE_DB_PASSWORD
SECRET_KEY=CHANGE_THIS_WITH_OPENSSL_OUTPUT
API_PORT=8010

APNS_ENABLED=true
APNS_TEAM_ID=ABCDE12345
APNS_KEY_ID=1A2B3C4D5E
APNS_BUNDLE_ID=swapps.HomeQuests
APNS_PRIVATE_KEY_PATH=/opt/homequests/secrets/AuthKey_1A2B3C4D5E.p8
PUSH_WORKER_ENABLED=true
PUSH_WORKER_INTERVAL_SECONDS=60
```

## Beispiel `.env` ohne APNs

```bash
POSTGRES_PASSWORD=CHANGE_DB_PASSWORD
SECRET_KEY=CHANGE_THIS_WITH_OPENSSL_OUTPUT
API_PORT=8010

APNS_ENABLED=false
PUSH_WORKER_ENABLED=false
```
