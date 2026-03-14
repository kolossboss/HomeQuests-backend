# Changelog

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
