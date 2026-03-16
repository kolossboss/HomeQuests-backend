from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine, text


MigrationFn = Callable[[Engine], None]


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


MIGRATIONS: list[tuple[str, MigrationFn]] = [
    ("20260306_legacy_schema_bootstrap", _run_legacy_schema_bootstrap),
    ("20260306_task_always_submittable", _add_task_always_submittable_column),
    ("20260307_user_ha_notify_service", _add_user_ha_notify_service_column),
    ("20260307_home_assistant_settings", _create_home_assistant_settings_table),
    ("20260307_home_assistant_channel_and_user_prefs", _add_home_assistant_channel_and_user_prefs),
    ("20260307_home_assistant_delivery_logs", _create_home_assistant_delivery_logs_table),
    ("20260316_task_generation_blocks", _create_task_generation_blocks_table),
]


def run_migrations(engine: Engine) -> None:
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
