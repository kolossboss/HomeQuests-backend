# Achievement-System

## Überblick

Das Achievement-System ist als zentrales Backend-Modul aufgebaut, damit WebUI, iOS, Android und Home Assistant später dieselben Regeln, Fortschritte und Unlock-Events verwenden können.

Bausteine:

- `achievement_definitions`
  Seedbarer Katalog mit stabilem Key, Name, Schwierigkeit, Icon, Regeltyp, Regel-JSON und Belohnungs-JSON.
- `achievement_progress`
  Persistierter Fortschritt pro Kind und Achievement, inklusive `profile_claimed_at` und `reward_granted_at` für den zweistufigen Claim-Flow.
- `achievement_unlock_events`
  Historie für Freischaltungen inkl. `presentation_payload` als Hook für animierte Mobile-Overlays.
- `achievement_freeze_windows`
  Freeze-/Urlaubsfenster für streak-basierte Achievements.
- `achievement_task_records`
  Dauerhafte, achievement-relevante Aufgabenfakten. Diese Tabelle ist bewusst getrennt von `tasks`, damit spätere Löschungen oder Serienwechsel historische Streaks nicht zerstören.

## Regeltypen

Aktuell implementiert:

- `aggregate_count`
  Für Zähler wie verdiente Punkte, bestätigte Aufgaben, Wochenaufgaben oder Sonderaufgaben.
- `streak`
  Für Wochen-/Monatsserien mit Freeze-Unterstützung.

Aktuell genutzte Metriken:

- `earned_points_total`
- `approved_tasks_total`
- `approved_weekly_tasks_total`
- `approved_special_tasks_total`
- `all_due_tasks_completed`
- `all_due_tasks_completed_early`
- `all_active_special_tasks_completed`

## Belohnungen

Belohnungen werden über `reward_kind` und `reward_config` modelliert.

Aktuell:

- `points_grant`

Vorgabe:

- Bronze: `10`
- Silber: `25`
- Gold: `50`
- Platin: `150`

## API

Wichtige Endpunkte:

- `GET /families/{family_id}/achievements/me`
- `GET /families/{family_id}/achievements/users/{user_id}`
- `POST /families/{family_id}/achievements/users/{user_id}/evaluate`
- `POST /families/{family_id}/achievements/{achievement_id}/claim-profile`
- `POST /families/{family_id}/achievements/{achievement_id}/claim-reward`
- `GET /families/{family_id}/achievements/users/{user_id}/freeze-windows`
- `POST /families/{family_id}/achievements/users/{user_id}/freeze-windows`

## Live-Events / iOS-Hook

Bei Freischaltung wird ein Live-Event `achievement.unlocked` erzeugt. Die Punkte werden dabei noch nicht gebucht.

Wichtige Payload-Teile:

- `achievement_key`
- `user_id`
- `difficulty`
- `reward`
- `presentation`

`presentation` ist bewusst UI-orientiert gehalten und kann in iOS später direkt in ein animiertes Banner, Sheet oder Confetti-Overlay übersetzt werden.

## Claim-Flow

Der Ablauf ist bewusst in echte Zustände getrennt:

1. Regel erfüllt: `unlocked_at` wird gesetzt und `achievement.unlocked` informiert die Clients.
2. Kind klickt die Auszeichnung: `POST /families/{family_id}/achievements/{achievement_id}/claim-profile` setzt `profile_claimed_at`.
3. Falls Punkte vorhanden sind, öffnet das Kind das Geschenk: `POST /families/{family_id}/achievements/{achievement_id}/claim-reward` bucht die Punkte im `points_ledger` und setzt `reward_granted_at`.

Das verhindert doppelte Gutschriften und macht Animationen nicht nur dekorativ, sondern fachlich korrekt.

## WebUI / Kinder-Dashboard

Das Kinder-Dashboard zeigt eine eigene Achievement-Kachel, sobald neue Auszeichnungen oder offene Geschenke vorhanden sind. Die Detailseite trennt die Ansicht bewusst in vier Bereiche:

- Neue Auszeichnungen, die per Dreh-Animation ins Profil übernommen werden.
- Offene Geschenke, die per Loot-Animation Punkte auszahlen.
- Bereits im Profil gespeicherte Auszeichnungen.
- Der restliche Katalog mit gesperrten oder laufenden Achievements.

Die Punkteanimation zählt den sichtbaren Kontostand von alt nach neu hoch. Die Backend-API bleibt dabei maßgeblich: Punkte werden erst durch `claim-reward` gebucht, nicht durch die reine Animation.

## Benachrichtigungen

`achievement.unlocked` wird zusätzlich durch den Push-/Home-Assistant-Dispatcher ausgewertet. Wenn ein Kind ein Achievement freischaltet, kann es dadurch browserseitig per Live-Event und extern über konfigurierte Kanäle benachrichtigt werden.

## Erweiterung

Neue Achievements:

1. Seed in `backend/app/achievement_catalog.py` ergänzen.
2. Falls nötig neue Metrik in `backend/app/achievement_engine.py` implementieren.
3. Optional neues `icon_key` im WebUI-Icon-Mapping ergänzen.

## TODOs

- Template-Historie für Sonderaufgaben ergänzen, damit monatliche Coverage rückwirkend exakt gegen das damalige aktive Template-Set ausgewertet wird.
- Eigene Mobile-Clients sollen `achievement.unlocked` zusätzlich lokal quittieren können, damit `displayed_at` gesetzt werden kann.
- Falls später sehr viele Achievements oder Familien entstehen, kann die Evaluation auf Job-/Queue-Basis ausgelagert werden.
