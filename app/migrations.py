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


MIGRATIONS: list[tuple[str, MigrationFn]] = [
    ("20260306_legacy_schema_bootstrap", _run_legacy_schema_bootstrap),
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
