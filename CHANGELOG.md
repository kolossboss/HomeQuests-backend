# Changelog

## v2026.03.17-01 (2026-03-17)

- WebUI-Redesign: umfangreiche visuelle Ueberarbeitung mit neuem Premium-Theme (Sora + Manrope), modernisierten Panels, Tabs, Karten und Dashboard-Flows.
- UX-Feedback erweitert: globaler Status-Banner (`info/success/error`) und zentraler Loading-Indikator fuer laufende Requests/Refreshes.
- Formularvalidierung verbessert: feldspezifische Fehlermeldungen (`FIELD_ERROR_MESSAGES`) mit inline Fehleranzeige statt nur roter Markierung.
- Leere Listen vereinheitlicht: neue Empty-State-Komponenten fuer Karten- und Tabellenbereiche.
- Stabilitaet im API-Client: Netzwerk-/HTTP-Fehler zeigen jetzt sofort sichtbares UI-Feedback.
- WebUI-Assets: Cache-Buster fuer `styles.css` und `app.js` auf `20260317f` angehoben.

## v2026.03.16-05 (2026-03-16)

- System-Event-Ansicht in der WebUI vereinfacht: statt Tabelle + separatem Textfeld jetzt ein einheitlicher Log-Container (`system-events-view`) mit Scroll und Copy-Button.
- Copy-Funktion robust gemacht: Fallback kopiert jetzt direkt den sichtbaren Event-Container per Selection-Range, auch ohne Clipboard-API.
- System-Tab aufgeraeumt: Diagnose-Block steht wieder direkt vor dem Ereignis-Log; doppelte Export-UI entfernt.

## v2026.03.16-04 (2026-03-16)

- Daily-Realign abgesichert: frisch erzeugte Tages-Folgetasks werden am selben Tag nicht mehr automatisch auf heute zurueckgezogen.
- System-Tab erweitert: Ereignis-Log hat jetzt eine Kopieransicht (`textarea`) und einen Button „Alles kopieren“ fuer Export/Support-Zwecke.
- Event-Export verbessert: Eintraege werden als strukturierter Text mit Zeitstempel, Event-Typ und vollstaendiger Payload bereitgestellt.

## v2026.03.16-03 (2026-03-16)

- Aufgabenserien erweitert: `tasks.series_id` eingefuehrt (Migration + Indizes), damit wiederkehrende Aufgaben stabil ueber eine feste Serien-ID verfolgt werden.
- Task-Engine verbessert: taegliche Aufgaben werden im Maintenance-Loop bei klarer 1-Tages-Verschiebung automatisch auf den korrekten Tag ausgerichtet (`auto_daily_realign`).
- Sonderaufgaben-Claim gehaertet: Postgres-Advisory-Lock verhindert parallele Claim-Races pro Vorlage.
- Event-Payloads vereinheitlicht: `task.created`/`task.updated` enthalten jetzt umfassendere Task-Metadaten (inkl. `series_id`, Schedule, Zeitstempel, Reason).
- WebUI/System-Log verbessert: Payload wird vollstaendig als formatierter `pre`-Block angezeigt; Cache-Buster auf `styles.css?v=20260316b` und `app.js?v=20260316b` angehoben.
- Validierung erweitert: monatliche Aufgaben erfordern eine Faelligkeit; Erinnerungen ohne Faelligkeit werden bei `none`/`monthly` klar abgefangen.
- Tests: neuer Logik-Test `tests/test_task_logic.py` fuer zentrale Recurrence- und Validierungsfaelle.

## v2026.03.16-02 (2026-03-16)

- Wochenaufgaben-Serienlogik erweitert: zentrale Upsert-Helferfunktion fuer `task_generation_blocks` eingefuehrt.
- Serienwechsel bei flexiblen Wochenaufgaben abgesichert: alte Serien werden langfristig geblockt (`series_replaced`) und offene Aufgaben derselben Serie im aktuellen Zyklus automatisch deaktiviert.
- Wiederkehrende Aufgaben-Events angereichert: `task.created` enthaelt jetzt auch `source_task_id` und `source_recurrence_type` fuer bessere Nachvollziehbarkeit.

## v2026.03.16-01 (2026-03-16)

- Wiederkehrende Wochenaufgaben (flexibel): neue Generierungs-Sperre (`task_generation_blocks`), damit nach manuellem Loeschen im laufenden Zyklus keine sofortige Neuerzeugung erfolgt.
- Aufgabenlogik stabilisiert: Duplikat-Bereinigung bei offenen Wochenaufgaben im aktuellen Zyklus und robustere Nachfolger-Erkennung fuer flexible Wochenaufgaben.
- System-API erweitert: neue Endpunkte fuer Runtime-Infos (`/system/runtime`) und Event-Log (`/system/events`) inklusive Anzeige im System-Tab der WebUI.
- App-Metadaten: `app_version`/`app_build_ref` in den Settings nutzbar; `/health` liefert jetzt auch die Version.
- WebUI: Task-Formulare blenden „Immer erledigbar“ bei Wochenmodus „ganze Woche“ automatisch aus; Asset-Cache-Buster auf `styles.css?v=20260315a` und `app.js?v=20260316a`.

## v2026.03.14-03 (2026-03-14)

- WebUI-Design: Kartenhintergruende von Verlauf auf klare iOS-nahe Farbflächen umgestellt (Dashboard, Fokus-/Metrik-Karten, Detailbereiche).
- Dashboard-Modal: einheitlicher Card-Stil fuer Aufgaben-/Anfragekarten inklusive abgestufter Farbtoene je Status.
- WebUI-Assets: Cache-Buster fuer `styles.css`/`app.js` auf `20260314b` angehoben.

## v2026.03.14-02 (2026-03-14)

- Dashboard-Modal (Manager): offene/überfällige Kinderaufgaben können direkt aus der Detailansicht gelöscht werden.
- UI-Flow: zusätzliche Delete-Aktion mit Bestätigungsdialog im Modal-Context.
- WebUI-Assets: Cache-Buster für `styles.css`/`app.js` auf `20260314a` angehoben.

## v2026.03.14-01 (2026-03-14)

- Task-Submit-Logik für tägliche Aufgaben präzisiert: ohne `always_submittable` ist nur der aktuell fällige Kalendertag zulässig.
- Validierung verbessert: klare Fehler für „noch nicht fällig“, „nicht mehr für heute einreichbar“ und ungültige tägliche Aufgaben ohne `due_at`.
- Wochentag-Check für tägliche Aufgaben gehärtet (Fälligkeitstag + aktueller Tag müssen zu `active_weekdays` passen).

## v2026.03.11-03 (2026-03-11)

- Frontend-Refresh: `refreshFamilyData` gegen parallele Läufe abgesichert (`dataRefreshInFlight`), um doppelte Updates zu vermeiden.
- Live-Updates: ausstehende Live-Refreshes werden während offener Editor/Modal-Interaktionen zurückgestellt und danach sauber nachgezogen.
- WebUI-Assets: Cache-Buster für `styles.css`/`app.js` auf `20260311c` angehoben.

## v2026.03.11-02 (2026-03-11)

- Aufgaben/Sonderaufgaben: neue Duplizieren-Aktion im Manager-UI fuer schnellere Erstellung aehnlicher Eintraege.
- Sonderaufgaben-Limit: Verbrauchszaehler zaehlt jetzt template-weit pro Intervall (nicht mehr pro Kind), damit Limits konsistent greifen.
- WebUI: weitere visuelle Feinanpassungen bei Task-Karten und Sonderaufgaben-Bereich sowie Asset-Cache-Buster-Update.

## v2026.03.11-01 (2026-03-11)

- Aufgabenlogik: flexible Wochenaufgaben ohne festes Fälligkeitsdatum werden nun automatisch pro Woche weitergeführt.
- Wartung/Worker: Wochen-Rollover läuft zusätzlich im Maintenance-Flow, inklusive automatischem Verpasst-Markieren offener Altaufgaben.
- Aufgaben-API: beim Laden der Aufgabenliste werden fällige Wochenfortschritte direkt nachgezogen, damit die Ansicht aktuell bleibt.

## v2026.03.10-04 (2026-03-10)

- Dashboard/WebUI: neue Modal-Ansichten fuer Kinder- und Eltern-Dashboard weiter ausgebaut.
- Frontend-Logik: Aufgaben-, Belohnungs- und Detailkarten im Dashboard differenzierter nach Status/Farbton gerendert.
- UX: bessere Trennung von offenen, ueberfaelligen, verpassten und eingereichten Aufgaben direkt in der UI.

## v2026.03.10-03 (2026-03-10)

- WebUI-Template: Modal-Container (`HA Nutzer bearbeiten`, `Dashboard-Details`) aus der verschachtelten Sektion auf Root-Ebene verschoben.
- Frontend-Assets: Cache-Buster fuer `styles.css` und `app.js` auf `20260310c` angehoben.
- Fokus: stabilere Modal-Darstellung und konsistente Layer-/Z-Index-Struktur im Dashboard.

## v2026.03.10-02 (2026-03-10)

- WebUI: weiteres Rework von Aufgabenansicht, Filter-/Aktionsbereichen und Kartenstruktur.
- Styling: zusaetzliche Responsive- und Layout-Anpassungen in `styles.css` fuer bessere Nutzbarkeit.
- Frontend-Logik: Interaktionen in `app.js` und Markup in `index.html` fuer den neuen Flow nachgezogen.

## v2026.03.10-01 (2026-03-10)

- WebUI: umfangreiches Styling-Update fuer Listen, Karten und Filterbereiche.
- Dashboard/UI: Layout in `index.html` und Logik in `app.js` weiter aufgeraeumt.
- Fokus: bessere Lesbarkeit und klarere visuelle Trennung in der Aufgabenansicht.

## v2026.03.09-02 (2026-03-09)

- Tasks: Verpasste wiederkehrende Aufgaben werden sauber als `missed_submitted` behandelt; Eltern-Review unterstützt `approve`.
- Push/APNs: Dedupe für Geräte/Events verbessert, weniger Mehrfachzustellungen.
- Dashboard/WebUI: Fokus auf `Verpasst` und `Heute fällig`, direktere Aktionen und bessere Übersicht.
- Stabilität: Reminder-/Push-Worker und zugehörige API-Flows weiter gehärtet.

## v2026.03.09-01 (2026-03-09)

- Initialer kurzer Changelog im Backend-Repo eingeführt.
- Grundlage für versionierte, knappe Release-Notizen geschaffen.
