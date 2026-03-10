# Changelog

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
