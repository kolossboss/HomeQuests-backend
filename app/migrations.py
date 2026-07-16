from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from threading import Lock

from sqlalchemy import Engine, text


MigrationFn = Callable[[Engine], None]
MIGRATION_LOCK_KEY = 930_000_002
_migration_process_lock = Lock()


def _run_legacy_schema_bootstrap(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS recurrence_type VARCHAR(16) NOT NULL DEFAULT 'none'"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS reminder_offsets_minutes JSON NOT NULL DEFAULT '[]'"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS active_weekdays JSON NOT NULL DEFAULT '[0,1,2,3,4,5,6]'"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS special_template_id INTEGER NULL"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS penalty_enabled BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS penalty_points INTEGER NOT NULL DEFAULT 0"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS penalty_last_applied_at TIMESTAMP NULL"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE special_task_templates "
                "ADD COLUMN IF NOT EXISTS active_weekdays JSON NOT NULL DEFAULT '[0,1,2,3,4,5,6]'"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE special_task_templates "
                "ADD COLUMN IF NOT EXISTS due_time_hhmm VARCHAR(5) NULL"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE rewards "
                "ADD COLUMN IF NOT EXISTS is_shareable BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )
        if engine.dialect.name == "postgresql":
            conn.execute(text("ALTER TABLE users ALTER COLUMN email DROP NOT NULL"))
            conn.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'specialtaskintervalenum') THEN
                            IF NOT EXISTS (
                                SELECT 1
                                FROM pg_enum e
                                JOIN pg_type t ON t.oid = e.enumtypid
                                WHERE t.typname = 'specialtaskintervalenum' AND e.enumlabel = 'monthly'
                            ) THEN
                                ALTER TYPE specialtaskintervalenum ADD VALUE 'monthly';
                            END IF;
                        END IF;
                    END $$;
                    """
                )
            )
            conn.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'taskstatusenum') THEN
                            IF NOT EXISTS (
                                SELECT 1
                                FROM pg_enum e
                                JOIN pg_type t ON t.oid = e.enumtypid
                                WHERE t.typname = 'taskstatusenum' AND e.enumlabel = 'missed_submitted'
                            ) THEN
                                ALTER TYPE taskstatusenum ADD VALUE 'missed_submitted';
                            END IF;
                        END IF;
                        IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'pointssourceenum') THEN
                            IF NOT EXISTS (
                                SELECT 1
                                FROM pg_enum e
                                JOIN pg_type t ON t.oid = e.enumtypid
                                WHERE t.typname = 'pointssourceenum' AND e.enumlabel = 'reward_contribution'
                            ) THEN
                                ALTER TYPE pointssourceenum ADD VALUE 'reward_contribution';
                            END IF;
                            IF NOT EXISTS (
                                SELECT 1
                                FROM pg_enum e
                                JOIN pg_type t ON t.oid = e.enumtypid
                                WHERE t.typname = 'pointssourceenum' AND e.enumlabel = 'task_penalty'
                            ) THEN
                                ALTER TYPE pointssourceenum ADD VALUE 'task_penalty';
                            END IF;
                        END IF;
                    END $$;
                    """
                )
            )


def _add_task_always_submittable_column(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS always_submittable BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )


def _add_user_ha_notify_service_column(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE users "
                "ADD COLUMN IF NOT EXISTS ha_notify_service VARCHAR(255) NULL"
            )
        )


def _create_home_assistant_settings_table(engine: Engine) -> None:
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS home_assistant_settings ("
                    "id SERIAL PRIMARY KEY, "
                    "family_id INTEGER NOT NULL UNIQUE REFERENCES families(id) ON DELETE CASCADE, "
                    "ha_enabled BOOLEAN NOT NULL DEFAULT FALSE, "
                    "ha_base_url VARCHAR(255) NULL, "
                    "ha_token TEXT NULL, "
                    "verify_ssl BOOLEAN NOT NULL DEFAULT TRUE, "
                    "updated_by_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL, "
                    "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                    "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
            )
        else:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS home_assistant_settings ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "family_id INTEGER NOT NULL UNIQUE, "
                    "ha_enabled BOOLEAN NOT NULL DEFAULT 0, "
                    "ha_base_url VARCHAR(255) NULL, "
                    "ha_token TEXT NULL, "
                    "verify_ssl BOOLEAN NOT NULL DEFAULT 1, "
                    "updated_by_id INTEGER NULL, "
                    "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                    "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
            )


def _add_home_assistant_channel_and_user_prefs(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE home_assistant_settings "
                "ADD COLUMN IF NOT EXISTS notification_channel VARCHAR(32) NOT NULL DEFAULT 'sse'"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE users "
                "ADD COLUMN IF NOT EXISTS ha_notifications_enabled BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE users "
                "ADD COLUMN IF NOT EXISTS ha_child_new_task BOOLEAN NOT NULL DEFAULT TRUE"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE users "
                "ADD COLUMN IF NOT EXISTS ha_manager_task_submitted BOOLEAN NOT NULL DEFAULT TRUE"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE users "
                "ADD COLUMN IF NOT EXISTS ha_manager_reward_requested BOOLEAN NOT NULL DEFAULT TRUE"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE users "
                "ADD COLUMN IF NOT EXISTS ha_task_due_reminder BOOLEAN NOT NULL DEFAULT TRUE"
            )
        )
        conn.execute(
            text(
                "UPDATE users SET ha_notifications_enabled = TRUE "
                "WHERE ha_notify_service IS NOT NULL AND TRIM(ha_notify_service) <> ''"
            )
        )


def _create_home_assistant_delivery_logs_table(engine: Engine) -> None:
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS home_assistant_delivery_logs ("
                    "id SERIAL PRIMARY KEY, "
                    "family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE, "
                    "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
                    "notify_service VARCHAR(255) NOT NULL, "
                    "dedupe_key VARCHAR(255) NOT NULL, "
                    "event_type VARCHAR(120) NOT NULL, "
                    "status VARCHAR(32) NOT NULL DEFAULT 'sent', "
                    "error_reason TEXT NULL, "
                    "sent_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_ha_delivery_dedupe "
                    "ON home_assistant_delivery_logs (family_id, user_id, notify_service, dedupe_key)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_ha_delivery_logs_family_user_sent_at "
                    "ON home_assistant_delivery_logs (family_id, user_id, sent_at)"
                )
            )
        else:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS home_assistant_delivery_logs ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "family_id INTEGER NOT NULL, "
                    "user_id INTEGER NOT NULL, "
                    "notify_service VARCHAR(255) NOT NULL, "
                    "dedupe_key VARCHAR(255) NOT NULL, "
                    "event_type VARCHAR(120) NOT NULL, "
                    "status VARCHAR(32) NOT NULL DEFAULT 'sent', "
                    "error_reason TEXT NULL, "
                    "sent_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_ha_delivery_dedupe "
                    "ON home_assistant_delivery_logs (family_id, user_id, notify_service, dedupe_key)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_ha_delivery_logs_family_user_sent_at "
                    "ON home_assistant_delivery_logs (family_id, user_id, sent_at)"
                )
            )


def _create_task_generation_blocks_table(engine: Engine) -> None:
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS task_generation_blocks ("
                    "id SERIAL PRIMARY KEY, "
                    "family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE, "
                    "key_hash VARCHAR(64) NOT NULL, "
                    "block_until TIMESTAMP NOT NULL, "
                    "reason VARCHAR(120) NULL, "
                    "created_by_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL, "
                    "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                    "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_task_generation_block_family_key "
                    "ON task_generation_blocks (family_id, key_hash)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_task_generation_blocks_family_until "
                    "ON task_generation_blocks (family_id, block_until)"
                )
            )
        else:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS task_generation_blocks ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "family_id INTEGER NOT NULL, "
                    "key_hash VARCHAR(64) NOT NULL, "
                    "block_until TIMESTAMP NOT NULL, "
                    "reason VARCHAR(120) NULL, "
                    "created_by_id INTEGER NULL, "
                    "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                    "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_task_generation_block_family_key "
                    "ON task_generation_blocks (family_id, key_hash)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_task_generation_blocks_family_until "
                    "ON task_generation_blocks (family_id, block_until)"
                )
            )


def _add_task_series_id_and_indexes(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS series_id VARCHAR(64) NULL"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_tasks_series_family_status "
                "ON tasks (family_id, series_id, is_active, status)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_tasks_family_active_recurrence_due "
                "ON tasks (family_id, is_active, recurrence_type, status, due_at)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_tasks_special_template_created_at "
                "ON tasks (special_template_id, created_at)"
            )
        )


def _add_achievement_points_source(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'pointssourceenum') THEN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM pg_enum e
                            JOIN pg_type t ON t.oid = e.enumtypid
                            WHERE t.typname = 'pointssourceenum' AND e.enumlabel = 'achievement_unlock'
                        ) THEN
                            ALTER TYPE pointssourceenum ADD VALUE 'achievement_unlock';
                        END IF;
                    END IF;
                END $$;
                """
            )
        )


def _add_achievement_claim_columns(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE achievement_progress "
                "ADD COLUMN IF NOT EXISTS profile_claimed_at TIMESTAMP NULL"
            )
        )


def _add_achievement_diamond_difficulty(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'achievementdifficultyenum') THEN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM pg_enum e
                            JOIN pg_type t ON t.oid = e.enumtypid
                            WHERE t.typname = 'achievementdifficultyenum' AND e.enumlabel = 'diamond'
                        ) THEN
                            ALTER TYPE achievementdifficultyenum ADD VALUE 'diamond';
                        END IF;
                    END IF;
                END $$;
                """
            )
        )


def _create_achievement_family_calibrations_table(engine: Engine) -> None:
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS achievement_family_calibrations ("
                    "id SERIAL PRIMARY KEY, "
                    "family_id INTEGER NOT NULL UNIQUE REFERENCES families(id) ON DELETE CASCADE, "
                    "status VARCHAR(32) NOT NULL DEFAULT 'pending', "
                    "started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                    "calibrated_at TIMESTAMP NULL, "
                    "baseline_weekly_points INTEGER NOT NULL DEFAULT 250, "
                    "observed_weekly_points INTEGER NOT NULL DEFAULT 0, "
                    "configured_weekly_points INTEGER NOT NULL DEFAULT 0, "
                    "effective_weekly_points INTEGER NOT NULL DEFAULT 0, "
                    "point_scale INTEGER NOT NULL DEFAULT 100, "
                    "sample_days INTEGER NOT NULL DEFAULT 0, "
                    "tasks_configured_count INTEGER NOT NULL DEFAULT 0, "
                    "rewards_configured_count INTEGER NOT NULL DEFAULT 0, "
                    "approved_tasks_sample_count INTEGER NOT NULL DEFAULT 0, "
                    "approved_points_sample INTEGER NOT NULL DEFAULT 0, "
                    "min_days_required INTEGER NOT NULL DEFAULT 14, "
                    "min_tasks_required INTEGER NOT NULL DEFAULT 10, "
                    "min_rewards_required INTEGER NOT NULL DEFAULT 5, "
                    "preview_payload JSON NOT NULL DEFAULT '{}', "
                    "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
            )
        else:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS achievement_family_calibrations ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "family_id INTEGER NOT NULL UNIQUE, "
                    "status VARCHAR(32) NOT NULL DEFAULT 'pending', "
                    "started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                    "calibrated_at TIMESTAMP NULL, "
                    "baseline_weekly_points INTEGER NOT NULL DEFAULT 250, "
                    "observed_weekly_points INTEGER NOT NULL DEFAULT 0, "
                    "configured_weekly_points INTEGER NOT NULL DEFAULT 0, "
                    "effective_weekly_points INTEGER NOT NULL DEFAULT 0, "
                    "point_scale INTEGER NOT NULL DEFAULT 100, "
                    "sample_days INTEGER NOT NULL DEFAULT 0, "
                    "tasks_configured_count INTEGER NOT NULL DEFAULT 0, "
                    "rewards_configured_count INTEGER NOT NULL DEFAULT 0, "
                    "approved_tasks_sample_count INTEGER NOT NULL DEFAULT 0, "
                    "approved_points_sample INTEGER NOT NULL DEFAULT 0, "
                    "min_days_required INTEGER NOT NULL DEFAULT 14, "
                    "min_tasks_required INTEGER NOT NULL DEFAULT 10, "
                    "min_rewards_required INTEGER NOT NULL DEFAULT 5, "
                    "preview_payload JSON NOT NULL DEFAULT '{}', "
                    "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
            )


def _add_operational_query_indexes(engine: Engine) -> None:
    # Ausschließlich additive, nicht-eindeutige Indizes. Bestehende Daten werden
    # weder validiert noch verändert, sodass das Upgrade auch mit Altbeständen läuft.
    statements = [
        "CREATE INDEX IF NOT EXISTS ix_live_update_events_family_id_id ON live_update_events (family_id, id)",
        "CREATE INDEX IF NOT EXISTS ix_points_ledger_family_user_created ON points_ledger (family_id, user_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_task_submissions_task_submitted ON task_submissions (task_id, submitted_at)",
        "CREATE INDEX IF NOT EXISTS ix_task_approvals_submission_reviewed ON task_approvals (submission_id, reviewed_at)",
        "CREATE INDEX IF NOT EXISTS ix_reward_redemptions_reward_status_requested ON reward_redemptions (reward_id, status, requested_at)",
        "CREATE INDEX IF NOT EXISTS ix_reward_contributions_family_reward_status ON reward_contributions (family_id, reward_id, status, redemption_id)",
        "CREATE INDEX IF NOT EXISTS ix_achievement_task_records_family_user_due ON achievement_task_records (family_id, user_id, due_at, outcome)",
        "CREATE INDEX IF NOT EXISTS ix_achievement_progress_family_user_status ON achievement_progress (family_id, user_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_push_delivery_logs_device_status_dedupe ON push_delivery_logs (device_id, status, dedupe_key)",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _add_api_query_indexes(engine: Engine) -> None:
    # Häufige Listen-, Dashboard- und Login-Filter. Getrennte Migration, damit
    # bereits laufende 20260715-Installationen die Ergänzungen sicher erhalten.
    statements = [
        "CREATE INDEX IF NOT EXISTS ix_users_display_name_lower ON users (lower(display_name))",
        "CREATE INDEX IF NOT EXISTS ix_tasks_family_created ON tasks (family_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_tasks_family_assignee_state_due "
        "ON tasks (family_id, assignee_id, status, is_active, due_at)",
        "CREATE INDEX IF NOT EXISTS ix_calendar_events_family_start ON calendar_events (family_id, start_at)",
        "CREATE INDEX IF NOT EXISTS ix_reward_redemptions_requester_status_requested "
        "ON reward_redemptions (requested_by_id, status, requested_at)",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


MIGRATIONS: list[tuple[str, MigrationFn]] = [
    ("20260306_legacy_schema_bootstrap", _run_legacy_schema_bootstrap),
    ("20260306_task_always_submittable", _add_task_always_submittable_column),
    ("20260307_user_ha_notify_service", _add_user_ha_notify_service_column),
    ("20260307_home_assistant_settings", _create_home_assistant_settings_table),
    ("20260307_home_assistant_channel_and_user_prefs", _add_home_assistant_channel_and_user_prefs),
    ("20260307_home_assistant_delivery_logs", _create_home_assistant_delivery_logs_table),
    ("20260316_task_generation_blocks", _create_task_generation_blocks_table),
    ("20260316_task_series_id_and_indexes", _add_task_series_id_and_indexes),
    ("20260422_achievement_points_source", _add_achievement_points_source),
    ("20260423_achievement_claim_columns", _add_achievement_claim_columns),
    ("20260424_achievement_diamond_difficulty", _add_achievement_diamond_difficulty),
    ("20260428_achievement_family_calibrations", _create_achievement_family_calibrations_table),
    ("20260715_operational_query_indexes", _add_operational_query_indexes),
    ("20260715_api_query_indexes", _add_api_query_indexes),
]


def _run_migrations_unlocked(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version VARCHAR(128) PRIMARY KEY, "
                "applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        applied_versions = {
            row[0]
            for row in conn.execute(text("SELECT version FROM schema_migrations")).all()
        }

    for version, migration in MIGRATIONS:
        if version in applied_versions:
            continue
        migration(engine)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO schema_migrations (version) VALUES (:version) "
                    "ON CONFLICT (version) DO NOTHING"
                ),
                {"version": version},
            )


@contextmanager
def _migration_guard(engine: Engine):
    with _migration_process_lock:
        if engine.dialect.name != "postgresql":
            yield
            return

        # Sessionweiter Lock: Migrationen öffnen selbst Transaktionen auf weiteren
        # Connections. Der Lock bleibt deshalb über deren gesamte Laufzeit bestehen.
        with engine.connect() as lock_connection:
            lock_connection.execute(
                text("SELECT pg_advisory_lock(:key)"),
                {"key": MIGRATION_LOCK_KEY},
            )
            try:
                yield
            finally:
                lock_connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": MIGRATION_LOCK_KEY},
                )


def run_migrations(engine: Engine) -> None:
    with _migration_guard(engine):
        _run_migrations_unlocked(engine)
