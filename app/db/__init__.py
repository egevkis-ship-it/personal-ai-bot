import json
from datetime import date, datetime
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text

from app.config import settings


engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def to_date(value):
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    return value



async def init_db() -> None:
    async with engine.begin() as conn:
        # Core logs
        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS raw_messages (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT now(),
            telegram_user_id TEXT,
            message_type TEXT,
            original_text TEXT,
            transcript TEXT,
            intent TEXT,
            parsed_json JSONB,
            status TEXT DEFAULT 'received',
            error TEXT
        );
        """))

        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS ops_actions (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT now(),
            requested_by TEXT,
            action_type TEXT,
            status TEXT,
            risk_level TEXT,
            plan_json JSONB,
            commands_json JSONB,
            approval_required BOOLEAN DEFAULT true,
            approved_at TIMESTAMPTZ,
            executed_at TIMESTAMPTZ,
            result_log TEXT,
            error_log TEXT
        );
        """))

        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value JSONB,
            updated_at TIMESTAMPTZ DEFAULT now()
        );
        """))

        # Fitness: training plans
        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS training_plans (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT now(),
            telegram_user_id TEXT,
            plan_name TEXT,
            period_type TEXT, -- week / month / custom / program
            start_date DATE,
            end_date DATE,
            status TEXT DEFAULT 'active', -- active / archived / cancelled
            source_text TEXT,
            notes TEXT
        );
        """))

        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS planned_workouts (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT now(),
            plan_id BIGINT REFERENCES training_plans(id) ON DELETE CASCADE,
            telegram_user_id TEXT,
            planned_date DATE,
            weekday TEXT,
            sequence_number INTEGER,
            is_floating BOOLEAN DEFAULT false,
            title TEXT,
            focus TEXT,
            focus_label TEXT,
            workout_type TEXT DEFAULT 'planned', -- planned / custom / replacement
            status TEXT DEFAULT 'planned', -- planned / completed / completed_modified / skipped / moved / replaced / cancelled
            replaced_by_id BIGINT,
            source_text TEXT,
            notes TEXT
        );
        """))

        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS planned_exercises (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT now(),
            planned_workout_id BIGINT REFERENCES planned_workouts(id) ON DELETE CASCADE,
            exercise_order INTEGER,
            exercise_name TEXT,
            target_sets INTEGER,
            target_reps_min INTEGER,
            target_reps_max INTEGER,
            target_reps_text TEXT,
            target_weight_kg NUMERIC,
            notes TEXT
        );
        """))

        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS planned_workout_events (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT now(),
            planned_workout_id BIGINT REFERENCES planned_workouts(id) ON DELETE SET NULL,
            event_type TEXT, -- created / moved / swapped / replaced / skipped / completed / shortened / custom_added
            old_value_json JSONB,
            new_value_json JSONB,
            source_text TEXT,
            notes TEXT
        );
        """))

        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS fitness_workouts (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT now(),
            telegram_user_id TEXT,
            planned_workout_id BIGINT REFERENCES planned_workouts(id) ON DELETE SET NULL,
            workout_date DATE,
            workout_type TEXT,
            focus TEXT,
            focus_label TEXT,
            bodyweight_kg NUMERIC,
            completion_type TEXT DEFAULT 'custom', -- as_planned / modified / shortened / custom / replacement
            source_text TEXT,
            notes TEXT
        );
        """))

        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS fitness_exercise_sets (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT now(),
            workout_id BIGINT REFERENCES fitness_workouts(id) ON DELETE CASCADE,
            exercise_name TEXT,
            set_number INTEGER,
            weight_kg NUMERIC,
            reps INTEGER,
            rpe NUMERIC,
            notes TEXT
        );
        """))

        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS fitness_workout_logs (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            telegram_user_id TEXT NOT NULL,
            planned_workout_id BIGINT REFERENCES planned_workouts(id) ON DELETE SET NULL,
            workout_date DATE,
            title TEXT,
            status TEXT DEFAULT 'active',
            started_at TIMESTAMPTZ DEFAULT now(),
            finished_at TIMESTAMPTZ,
            source_text TEXT,
            notes TEXT
        );
        """))

        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS fitness_workout_log_sets (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT now(),
            workout_log_id BIGINT REFERENCES fitness_workout_logs(id) ON DELETE CASCADE,
            exercise_name TEXT,
            exercise_key TEXT,
            set_number INTEGER,
            weight_kg NUMERIC,
            reps INTEGER,
            rpe NUMERIC,
            notes TEXT
        );
        """))

        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS body_measurements (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT now(),
            telegram_user_id TEXT,
            measurement_date DATE,
            weight_kg NUMERIC,
            waist_cm NUMERIC,
            chest_cm NUMERIC,
            hips_cm NUMERIC,
            arm_cm NUMERIC,
            thigh_cm NUMERIC,
            neck_cm NUMERIC,
            notes TEXT,
            source_text TEXT
        );
        """))

        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS training_constraints (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT now(),
            telegram_user_id TEXT,
            constraint_date DATE,
            body_part TEXT, -- knee / shoulder / back / elbow / general etc.
            severity TEXT,
            note TEXT,
            source_text TEXT,
            status TEXT DEFAULT 'active'
        );
        """))

        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS fitness_pending_decisions (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT now(),
            telegram_user_id TEXT,
            decision_type TEXT,
            status TEXT DEFAULT 'pending', -- pending / resolved / cancelled
            context_json JSONB,
            source_text TEXT,
            resolved_at TIMESTAMPTZ
        );
        """))

        # ═══ Самообучение ════════════════════════════════════════════
        # Каждое сообщение бота вместе с входом — лог последнего взаимодействия.
        # Если пользователь пишет фидбек ("неправильно понял", "не так", "запомни"),
        # мы создаём корректировку, привязанную к последнему взаимодействию.
        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS last_interaction (
            telegram_user_id TEXT PRIMARY KEY,
            updated_at TIMESTAMPTZ DEFAULT now(),
            input_text TEXT,
            bot_response TEXT,
            action TEXT,
            parsed_json JSONB,
            current_workout_date DATE,
            current_planned_workout_id BIGINT,
            current_focus TEXT
        );
        """))
        # Forward-compat migrations for existing installs
        await conn.execute(text("ALTER TABLE last_interaction ADD COLUMN IF NOT EXISTS current_workout_date DATE;"))
        await conn.execute(text("ALTER TABLE last_interaction ADD COLUMN IF NOT EXISTS current_planned_workout_id BIGINT;"))
        await conn.execute(text("ALTER TABLE last_interaction ADD COLUMN IF NOT EXISTS current_focus TEXT;"))

        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS learning_corrections (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT now(),
            telegram_user_id TEXT,
            original_input TEXT,
            bot_response TEXT,
            user_feedback TEXT,
            correction_type TEXT, -- parser_error / format / naming / behavior / code_fix
            rule_pattern TEXT,    -- когда применять (low-level паттерн от Claude)
            rule_action TEXT,     -- что делать (правильный ответ/действие)
            scope TEXT DEFAULT 'fitness', -- fitness / general / ops
            status TEXT DEFAULT 'active', -- active / disabled / superseded
            applied_count INTEGER DEFAULT 0,
            notes TEXT
        );
        """))

        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            id BIGSERIAL PRIMARY KEY,
            telegram_user_id TEXT,
            key TEXT,
            value TEXT,
            created_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE (telegram_user_id, key)
        );
        """))

        await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_corrections_user_status
        ON learning_corrections (telegram_user_id, status, created_at DESC);
        """))

        # Helpful indexes
        await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_training_plans_user_status
        ON training_plans (telegram_user_id, status);
        """))

        await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_planned_workouts_user_date_status
        ON planned_workouts (telegram_user_id, planned_date, status);
        """))

        await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_planned_workouts_user_sequence_status
        ON planned_workouts (telegram_user_id, sequence_number, status);
        """))

        await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_fitness_workout_logs_user_status
        ON fitness_workout_logs (telegram_user_id, status);
        """))

        await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_fitness_workout_log_sets_log
        ON fitness_workout_log_sets (workout_log_id);
        """))

        await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_fitness_workouts_user_date
        ON fitness_workouts (telegram_user_id, workout_date);
        """))

        await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_body_measurements_user_date
        ON body_measurements (telegram_user_id, measurement_date);
        """))

        # Forward-compat: % жира + длительность упражнения (для кардио/планки)
        await conn.execute(text("""
        ALTER TABLE body_measurements ADD COLUMN IF NOT EXISTS bodyfat_pct NUMERIC;
        """))
        await conn.execute(text("""
        ALTER TABLE fitness_exercise_sets ADD COLUMN IF NOT EXISTS duration_seconds INTEGER;
        """))
        await conn.execute(text("""
        ALTER TABLE fitness_exercise_sets ADD COLUMN IF NOT EXISTS distance_m NUMERIC;
        """))
        await conn.execute(text("""
        ALTER TABLE fitness_exercise_sets ADD COLUMN IF NOT EXISTS is_warmup BOOLEAN DEFAULT false;
        """))
        await conn.execute(text("""
        ALTER TABLE planned_exercises ADD COLUMN IF NOT EXISTS superset_group TEXT;
        """))
        await conn.execute(text("""
        ALTER TABLE planned_exercises ADD COLUMN IF NOT EXISTS tempo TEXT;
        """))

        # Compatibility migrations if older tables already existed
        await conn.execute(text("""
        ALTER TABLE fitness_workouts
        ADD COLUMN IF NOT EXISTS planned_workout_id BIGINT REFERENCES planned_workouts(id) ON DELETE SET NULL;
        """))

        await conn.execute(text("""
        ALTER TABLE fitness_workouts
        ADD COLUMN IF NOT EXISTS focus TEXT;
        """))

        await conn.execute(text("""
        ALTER TABLE fitness_workouts
        ADD COLUMN IF NOT EXISTS focus_label TEXT;
        """))

        await conn.execute(text("""
        ALTER TABLE fitness_workouts
        ADD COLUMN IF NOT EXISTS completion_type TEXT DEFAULT 'custom';
        """))


async def save_raw_message(
    telegram_user_id: str | None,
    message_type: str,
    original_text: str | None,
    transcript: str | None,
    intent: str | None,
    parsed_json: str | None,
    status: str,
    error: str | None = None,
) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
            INSERT INTO raw_messages
            (telegram_user_id, message_type, original_text, transcript, intent, parsed_json, status, error)
            VALUES
            (:telegram_user_id, :message_type, :original_text, :transcript, :intent, CAST(:parsed_json AS JSONB), :status, :error)
            """),
            {
                "telegram_user_id": telegram_user_id,
                "message_type": message_type,
                "original_text": original_text,
                "transcript": transcript,
                "intent": intent,
                "parsed_json": parsed_json,
                "status": status,
                "error": error,
            },
        )
        await session.commit()


async def db_healthcheck() -> bool:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def save_training_plan(
    telegram_user_id: str | None,
    plan_name: str | None,
    period_type: str | None,
    start_date: str | None,
    end_date: str | None,
    source_text: str,
    notes: str | None,
    planned_workouts: list[dict],
) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            INSERT INTO training_plans
            (telegram_user_id, plan_name, period_type, start_date, end_date, source_text, notes)
            VALUES
            (:telegram_user_id, :plan_name, :period_type, :start_date, :end_date, :source_text, :notes)
            RETURNING id
            """),
            {
                "telegram_user_id": telegram_user_id,
                "plan_name": plan_name,
                "period_type": period_type,
                "start_date": to_date(start_date),
                "end_date": to_date(end_date),
                "source_text": source_text,
                "notes": notes,
            },
        )
        plan_id = result.scalar_one()

        for workout in planned_workouts:
            workout_result = await session.execute(
                text("""
                INSERT INTO planned_workouts
                (plan_id, telegram_user_id, planned_date, weekday, sequence_number, is_floating,
                 title, focus, focus_label, workout_type, status, source_text, notes)
                VALUES
                (:plan_id, :telegram_user_id, :planned_date, :weekday, :sequence_number, :is_floating,
                 :title, :focus, :focus_label, :workout_type, :status, :source_text, :notes)
                RETURNING id
                """),
                {
                    "plan_id": plan_id,
                    "telegram_user_id": telegram_user_id,
                    "planned_date": to_date(workout.get("planned_date")),
                    "weekday": workout.get("weekday"),
                    "sequence_number": workout.get("sequence_number"),
                    "is_floating": workout.get("is_floating", False),
                    "title": workout.get("title"),
                    "focus": workout.get("focus"),
                    "focus_label": workout.get("focus_label"),
                    "workout_type": workout.get("workout_type", "planned"),
                    "status": workout.get("status", "planned"),
                    "source_text": source_text,
                    "notes": workout.get("notes"),
                },
            )
            planned_workout_id = workout_result.scalar_one()

            await session.execute(
                text("""
                INSERT INTO planned_workout_events
                (planned_workout_id, event_type, old_value_json, new_value_json, source_text, notes)
                VALUES
                (:planned_workout_id, 'created', NULL, CAST(:new_value_json AS JSONB), :source_text, NULL)
                """),
                {
                    "planned_workout_id": planned_workout_id,
                    "new_value_json": json.dumps(workout, ensure_ascii=False),
                    "source_text": source_text,
                },
            )

            for exercise in workout.get("exercises", []):
                await session.execute(
                    text("""
                    INSERT INTO planned_exercises
                    (planned_workout_id, exercise_order, exercise_name, target_sets,
                     target_reps_min, target_reps_max, target_reps_text, target_weight_kg, notes)
                    VALUES
                    (:planned_workout_id, :exercise_order, :exercise_name, :target_sets,
                     :target_reps_min, :target_reps_max, :target_reps_text, :target_weight_kg, :notes)
                    """),
                    {
                        "planned_workout_id": planned_workout_id,
                        "exercise_order": exercise.get("exercise_order"),
                        "exercise_name": exercise.get("exercise_name"),
                        "target_sets": exercise.get("target_sets"),
                        "target_reps_min": exercise.get("target_reps_min"),
                        "target_reps_max": exercise.get("target_reps_max"),
                        "target_reps_text": exercise.get("target_reps_text"),
                        "target_weight_kg": exercise.get("target_weight_kg"),
                        "notes": exercise.get("notes"),
                    },
                )

        await session.commit()
        return plan_id


async def get_today_planned_workout(telegram_user_id: str | None, today: str) -> dict | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT pw.*
            FROM planned_workouts pw
            WHERE pw.telegram_user_id = :telegram_user_id
              AND pw.planned_date = :today
              AND pw.status = 'planned'
            ORDER BY
              CASE WHEN EXISTS (
                SELECT 1
                FROM planned_exercises ex
                WHERE ex.planned_workout_id = pw.id
              ) THEN 0 ELSE 1 END,
              pw.sequence_number NULLS LAST,
              pw.id DESC
            LIMIT 1
            """),
            {"telegram_user_id": telegram_user_id, "today": to_date(today)},
        )
        workout = result.mappings().first()
        if not workout:
            return None

        exercises = await _get_planned_exercises(session, workout["id"])
        return {"workout": dict(workout), "exercises": exercises}


async def get_next_planned_workout(telegram_user_id: str | None) -> dict | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT *
            FROM planned_workouts
            WHERE telegram_user_id = :telegram_user_id
              AND status = 'planned'
            ORDER BY
              CASE WHEN planned_date IS NULL THEN 1 ELSE 0 END,
              planned_date ASC NULLS LAST,
              sequence_number ASC NULLS LAST,
              id ASC
            LIMIT 1
            """),
            {"telegram_user_id": telegram_user_id},
        )
        workout = result.mappings().first()
        if not workout:
            return None

        exercises = await _get_planned_exercises(session, workout["id"])
        return {"workout": dict(workout), "exercises": exercises}


async def get_week_plan(telegram_user_id: str | None, start_date: str, end_date: str) -> list[dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT *
            FROM planned_workouts
            WHERE telegram_user_id = :telegram_user_id
              AND (
                planned_date BETWEEN :start_date AND :end_date
                OR (planned_date IS NULL AND status = 'planned')
              )
            ORDER BY
              planned_date ASC NULLS LAST,
              sequence_number ASC NULLS LAST,
              id ASC
            """),
            {
                "telegram_user_id": telegram_user_id,
                "start_date": to_date(start_date),
                "end_date": to_date(end_date),
            },
        )

        rows = []
        for workout in result.mappings().all():
            exercises = await _get_planned_exercises(session, workout["id"])
            rows.append({"workout": dict(workout), "exercises": exercises})
        return rows


async def _get_planned_exercises(session, planned_workout_id: int) -> list[dict]:
    result = await session.execute(
        text("""
        SELECT *
        FROM planned_exercises
        WHERE planned_workout_id = :planned_workout_id
        ORDER BY exercise_order NULLS LAST, id
        """),
        {"planned_workout_id": planned_workout_id},
    )
    return [dict(row) for row in result.mappings().all()]


async def save_fitness_workout(
    telegram_user_id: str | None,
    workout_date: str,
    workout_type: str | None,
    focus: str | None,
    focus_label: str | None,
    bodyweight_kg,
    source_text: str,
    notes: str | None,
    exercises: list[dict],
    planned_workout_id: int | None = None,
    completion_type: str = "custom",
) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            INSERT INTO fitness_workouts
            (telegram_user_id, planned_workout_id, workout_date, workout_type, focus, focus_label,
             bodyweight_kg, completion_type, source_text, notes)
            VALUES
            (:telegram_user_id, :planned_workout_id, :workout_date, :workout_type, :focus, :focus_label,
             :bodyweight_kg, :completion_type, :source_text, :notes)
            RETURNING id
            """),
            {
                "telegram_user_id": telegram_user_id,
                "planned_workout_id": planned_workout_id,
                "workout_date": to_date(workout_date),
                "workout_type": workout_type,
                "focus": focus,
                "focus_label": focus_label,
                "bodyweight_kg": bodyweight_kg,
                "completion_type": completion_type,
                "source_text": source_text,
                "notes": notes,
            },
        )
        workout_id = result.scalar_one()

        for exercise in exercises:
            exercise_name = exercise.get("name") or exercise.get("exercise_name")
            for set_data in exercise.get("sets", []):
                await session.execute(
                    text("""
                    INSERT INTO fitness_exercise_sets
                    (workout_id, exercise_name, set_number, weight_kg, reps, rpe, notes)
                    VALUES
                    (:workout_id, :exercise_name, :set_number, :weight_kg, :reps, :rpe, :notes)
                    """),
                    {
                        "workout_id": workout_id,
                        "exercise_name": exercise_name,
                        "set_number": set_data.get("set_number"),
                        "weight_kg": set_data.get("weight_kg"),
                        "reps": set_data.get("reps"),
                        "rpe": set_data.get("rpe"),
                        "notes": set_data.get("notes"),
                    },
                )

        if planned_workout_id:
            status = "completed_modified" if completion_type in ("modified", "shortened", "replacement") else "completed"
            await session.execute(
                text("""
                UPDATE planned_workouts
                SET status = :status
                WHERE id = :planned_workout_id
                """),
                {"status": status, "planned_workout_id": planned_workout_id},
            )

            await session.execute(
                text("""
                INSERT INTO planned_workout_events
                (planned_workout_id, event_type, old_value_json, new_value_json, source_text, notes)
                VALUES
                (:planned_workout_id, 'completed', NULL, CAST(:new_value_json AS JSONB), :source_text, NULL)
                """),
                {
                    "planned_workout_id": planned_workout_id,
                    "new_value_json": json.dumps(
                        {"fitness_workout_id": workout_id, "completion_type": completion_type},
                        ensure_ascii=False,
                    ),
                    "source_text": source_text,
                },
            )

        await session.commit()
        return workout_id


async def save_body_measurement(
    telegram_user_id: str | None,
    measurement_date: str,
    source_text: str,
    data: dict,
) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            INSERT INTO body_measurements
            (telegram_user_id, measurement_date, weight_kg, waist_cm, chest_cm, hips_cm,
             arm_cm, thigh_cm, neck_cm, notes, source_text)
            VALUES
            (:telegram_user_id, :measurement_date, :weight_kg, :waist_cm, :chest_cm, :hips_cm,
             :arm_cm, :thigh_cm, :neck_cm, :notes, :source_text)
            RETURNING id
            """),
            {
                "telegram_user_id": telegram_user_id,
                "measurement_date": to_date(measurement_date),
                "weight_kg": data.get("weight_kg"),
                "waist_cm": data.get("waist_cm"),
                "chest_cm": data.get("chest_cm"),
                "hips_cm": data.get("hips_cm"),
                "arm_cm": data.get("arm_cm"),
                "thigh_cm": data.get("thigh_cm"),
                "neck_cm": data.get("neck_cm"),
                "notes": data.get("notes"),
                "source_text": source_text,
            },
        )
        measurement_id = result.scalar_one()
        await session.commit()
        return measurement_id


async def get_last_workout(telegram_user_id: str | None) -> dict | None:
    async with AsyncSessionLocal() as session:
        if telegram_user_id:
            workout_result = await session.execute(
                text("""
                SELECT id, workout_date, workout_type, focus, focus_label, bodyweight_kg, completion_type, notes
                FROM fitness_workouts
                WHERE telegram_user_id = :telegram_user_id
                ORDER BY workout_date DESC, id DESC
                LIMIT 1
                """),
                {"telegram_user_id": telegram_user_id},
            )
        else:
            workout_result = await session.execute(
                text("""
                SELECT id, workout_date, workout_type, focus, focus_label, bodyweight_kg, completion_type, notes
                FROM fitness_workouts
                ORDER BY workout_date DESC, id DESC
                LIMIT 1
                """)
            )

        workout = workout_result.mappings().first()

        if not workout:
            return None

        sets_result = await session.execute(
            text("""
            SELECT exercise_name, set_number, weight_kg, reps, rpe, notes
            FROM fitness_exercise_sets
            WHERE workout_id = :workout_id
            ORDER BY id ASC
            """),
            {"workout_id": workout["id"]},
        )

        return {
            "workout": dict(workout),
            "sets": [dict(row) for row in sets_result.mappings().all()],
        }


async def add_training_constraint(
    telegram_user_id: str | None,
    body_part: str,
    severity: str | None = None,
    note: str | None = None,
    constraint_date: str | None = None,
    source_text: str | None = None,
) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            INSERT INTO training_constraints
              (telegram_user_id, constraint_date, body_part, severity, note, source_text, status)
            VALUES (:uid, :d, :bp, :sev, :n, :src, 'active')
            RETURNING id
            """),
            {
                "uid": telegram_user_id,
                "d": constraint_date or None,
                "bp": body_part,
                "sev": severity,
                "n": note,
                "src": source_text,
            },
        )
        cid = result.scalar()
        await session.commit()
        return cid


async def list_active_constraints(telegram_user_id: str | None) -> list[dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT id, body_part, severity, note, constraint_date, created_at
            FROM training_constraints
            WHERE telegram_user_id = :uid AND status = 'active'
            ORDER BY created_at DESC
            """),
            {"uid": telegram_user_id},
        )
        return [dict(r) for r in result.mappings().all()]


async def resolve_constraint(constraint_id: int, status: str = "resolved") -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("UPDATE training_constraints SET status = :s WHERE id = :id"),
            {"s": status, "id": constraint_id},
        )
        await session.commit()


async def skip_planned_workout(
    telegram_user_id: str | None,
    planned_date: str,
    reason: str | None = None,
) -> int:
    """Mark a planned workout on a specific date as skipped."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            UPDATE planned_workouts
            SET status = 'skipped',
                notes = COALESCE(notes, '') || CASE WHEN :reason IS NULL THEN '' ELSE E'\nSkip reason: ' || :reason END
            WHERE telegram_user_id = :uid AND planned_date = :d AND status = 'planned'
            RETURNING id
            """),
            {"uid": telegram_user_id, "d": planned_date, "reason": reason},
        )
        ids = [r[0] for r in result.fetchall()]
        await session.commit()
        return len(ids)


async def shift_planned_workouts(
    telegram_user_id: str | None,
    from_date: str,
    days: int,
) -> int:
    """Shift all planned workouts from `from_date` onwards by `days` days."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            UPDATE planned_workouts
            SET planned_date = planned_date + (:days || ' days')::interval
            WHERE telegram_user_id = :uid
              AND planned_date >= :from_d
              AND status = 'planned'
            RETURNING id
            """),
            {"uid": telegram_user_id, "from_d": from_date, "days": days},
        )
        n = len(result.fetchall())
        await session.commit()
        return n


async def cancel_plan_period(
    telegram_user_id: str | None,
    start_date: str,
    end_date: str,
) -> int:
    """Mark planned workouts in [start, end] as cancelled."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            UPDATE planned_workouts
            SET status = 'cancelled'
            WHERE telegram_user_id = :uid
              AND planned_date BETWEEN :s AND :e
              AND status = 'planned'
            RETURNING id
            """),
            {"uid": telegram_user_id, "s": start_date, "e": end_date},
        )
        n = len(result.fetchall())
        await session.commit()
        return n


async def delete_last_n_sets(workout_id: int, n: int = 1) -> int:
    """Delete the last N sets from a workout. Returns count deleted."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            DELETE FROM fitness_exercise_sets
            WHERE id IN (
                SELECT id FROM fitness_exercise_sets
                WHERE workout_id = :wid
                ORDER BY id DESC LIMIT :n
            )
            RETURNING id
            """),
            {"wid": workout_id, "n": n},
        )
        deleted = len(result.fetchall())
        await session.commit()
        return deleted


async def delete_last_exercise_from_workout(workout_id: int) -> tuple[str | None, int]:
    """Delete all sets of the most recently logged exercise. Returns (exercise_name, count)."""
    async with AsyncSessionLocal() as session:
        last = await session.execute(
            text("""
            SELECT exercise_name
            FROM fitness_exercise_sets
            WHERE workout_id = :wid
            ORDER BY id DESC LIMIT 1
            """),
            {"wid": workout_id},
        )
        row = last.first()
        if not row:
            return None, 0
        name = row[0]
        result = await session.execute(
            text("""
            DELETE FROM fitness_exercise_sets
            WHERE workout_id = :wid AND exercise_name = :name
            RETURNING id
            """),
            {"wid": workout_id, "name": name},
        )
        n = len(result.fetchall())
        await session.commit()
        return name, n


async def delete_workout(workout_id: int) -> int:
    """Delete a workout and all its sets (cascade). Returns 1 if deleted, 0 if not found."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("DELETE FROM fitness_workouts WHERE id = :wid RETURNING id"),
            {"wid": workout_id},
        )
        n = len(result.fetchall())
        await session.commit()
        return n


async def rename_exercise_in_workout(
    workout_id: int,
    old_name: str,
    new_name: str,
) -> int:
    """Rename all sets matching old_name (fuzzy) within a workout."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            UPDATE fitness_exercise_sets
            SET exercise_name = :new_name
            WHERE workout_id = :wid AND lower(exercise_name) LIKE lower(:old_pat)
            RETURNING id
            """),
            {"wid": workout_id, "new_name": new_name, "old_pat": f"%{old_name}%"},
        )
        n = len(result.fetchall())
        await session.commit()
        return n


async def save_last_interaction(
    telegram_user_id: str | None,
    input_text: str,
    bot_response: str,
    action: str | None = None,
    parsed: dict | None = None,
    current_workout_date: str | None = None,
    current_planned_workout_id: int | None = None,
    current_focus: str | None = None,
) -> None:
    """Upsert the most recent user↔bot interaction for self-learning + context carry-over."""
    import json as _json
    if not telegram_user_id:
        return
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
            INSERT INTO last_interaction
              (telegram_user_id, updated_at, input_text, bot_response, action, parsed_json,
               current_workout_date, current_planned_workout_id, current_focus)
            VALUES (:uid, now(), :inp, :resp, :act, CAST(:parsed AS JSONB),
                    :cwd, :cpwid, :cfocus)
            ON CONFLICT (telegram_user_id) DO UPDATE
            SET updated_at = now(),
                input_text = COALESCE(NULLIF(EXCLUDED.input_text, ''), last_interaction.input_text),
                bot_response = COALESCE(NULLIF(EXCLUDED.bot_response, ''), last_interaction.bot_response),
                action = COALESCE(EXCLUDED.action, last_interaction.action),
                parsed_json = COALESCE(EXCLUDED.parsed_json, last_interaction.parsed_json),
                current_workout_date = COALESCE(EXCLUDED.current_workout_date, last_interaction.current_workout_date),
                current_planned_workout_id = COALESCE(EXCLUDED.current_planned_workout_id, last_interaction.current_planned_workout_id),
                current_focus = COALESCE(EXCLUDED.current_focus, last_interaction.current_focus)
            """),
            {
                "uid": telegram_user_id,
                "inp": input_text[:4000] if input_text else None,
                "resp": bot_response[:4000] if bot_response else None,
                "act": action,
                "parsed": _json.dumps(parsed or {}, ensure_ascii=False),
                "cwd": current_workout_date,
                "cpwid": current_planned_workout_id,
                "cfocus": current_focus,
            },
        )
        await session.commit()


async def get_last_interaction(telegram_user_id: str | None) -> dict | None:
    if not telegram_user_id:
        return None
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT * FROM last_interaction WHERE telegram_user_id = :uid"),
            {"uid": telegram_user_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None


async def save_learning_correction(
    telegram_user_id: str | None,
    original_input: str | None,
    bot_response: str | None,
    user_feedback: str,
    rule_pattern: str,
    rule_action: str,
    correction_type: str = "parser_error",
    scope: str = "fitness",
    notes: str | None = None,
) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            INSERT INTO learning_corrections
              (telegram_user_id, original_input, bot_response, user_feedback,
               correction_type, rule_pattern, rule_action, scope, notes)
            VALUES (:uid, :orig, :resp, :fb, :ct, :rp, :ra, :sc, :n)
            RETURNING id
            """),
            {
                "uid": telegram_user_id, "orig": original_input, "resp": bot_response,
                "fb": user_feedback, "ct": correction_type, "rp": rule_pattern,
                "ra": rule_action, "sc": scope, "n": notes,
            },
        )
        cid = result.scalar()
        await session.commit()
        return cid


async def get_active_corrections(
    telegram_user_id: str | None,
    scope: str = "fitness",
    limit: int = 30,
) -> list[dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT id, original_input, user_feedback, rule_pattern, rule_action, correction_type
            FROM learning_corrections
            WHERE telegram_user_id = :uid AND scope = :sc AND status = 'active'
            ORDER BY created_at DESC
            LIMIT :lim
            """),
            {"uid": telegram_user_id, "sc": scope, "lim": limit},
        )
        return [dict(r) for r in result.mappings().all()]


async def disable_correction(correction_id: int) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("UPDATE learning_corrections SET status = 'disabled' WHERE id = :id"),
            {"id": correction_id},
        )
        await session.commit()


async def increment_correction_applied(correction_id: int) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("UPDATE learning_corrections SET applied_count = applied_count + 1 WHERE id = :id"),
            {"id": correction_id},
        )
        await session.commit()


async def set_user_preference(telegram_user_id: str | None, key: str, value: str) -> None:
    if not telegram_user_id:
        return
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
            INSERT INTO user_preferences (telegram_user_id, key, value)
            VALUES (:uid, :k, :v)
            ON CONFLICT (telegram_user_id, key) DO UPDATE SET value = :v, created_at = now()
            """),
            {"uid": telegram_user_id, "k": key, "v": value},
        )
        await session.commit()


async def get_user_preferences(telegram_user_id: str | None) -> dict[str, str]:
    if not telegram_user_id:
        return {}
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT key, value FROM user_preferences WHERE telegram_user_id = :uid"),
            {"uid": telegram_user_id},
        )
        return {r["key"]: r["value"] for r in result.mappings().all()}


async def get_completed_workouts_in_period(
    telegram_user_id: str | None,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Return all completed workouts in [start_date, end_date] with their sets."""
    async with AsyncSessionLocal() as session:
        workouts_result = await session.execute(
            text("""
            SELECT id, workout_date, workout_type, focus, focus_label,
                   bodyweight_kg, completion_type, notes, source_text
            FROM fitness_workouts
            WHERE telegram_user_id = :uid
              AND workout_date BETWEEN :start_date AND :end_date
            ORDER BY workout_date ASC, id ASC
            """),
            {"uid": telegram_user_id, "start_date": start_date, "end_date": end_date},
        )
        workouts = [dict(row) for row in workouts_result.mappings().all()]
        if not workouts:
            return []

        ids = [w["id"] for w in workouts]
        sets_result = await session.execute(
            text("""
            SELECT workout_id, exercise_name, set_number, weight_kg, reps, rpe, notes
            FROM fitness_exercise_sets
            WHERE workout_id = ANY(:ids)
            ORDER BY workout_id ASC, id ASC
            """),
            {"ids": ids},
        )
        sets_by_workout: dict[int, list[dict]] = {}
        for row in sets_result.mappings().all():
            sets_by_workout.setdefault(row["workout_id"], []).append(dict(row))

        for w in workouts:
            w["sets"] = sets_by_workout.get(w["id"], [])
        return workouts


async def get_personal_records(
    telegram_user_id: str | None,
    limit: int = 20,
) -> list[dict]:
    """Return max weight per exercise across all workouts."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT s.exercise_name,
                   MAX(s.weight_kg) AS max_weight,
                   MAX(s.reps) FILTER (WHERE s.weight_kg = sub.max_w) AS best_reps,
                   COUNT(DISTINCT w.id) AS sessions
            FROM fitness_exercise_sets s
            JOIN fitness_workouts w ON w.id = s.workout_id
            JOIN (
                SELECT s2.exercise_name, MAX(s2.weight_kg) AS max_w
                FROM fitness_exercise_sets s2
                JOIN fitness_workouts w2 ON w2.id = s2.workout_id
                WHERE w2.telegram_user_id = :uid
                  AND s2.weight_kg IS NOT NULL
                GROUP BY s2.exercise_name
            ) sub ON sub.exercise_name = s.exercise_name
            WHERE w.telegram_user_id = :uid
              AND s.weight_kg IS NOT NULL
            GROUP BY s.exercise_name, sub.max_w
            ORDER BY max_weight DESC NULLS LAST
            LIMIT :lim
            """),
            {"uid": telegram_user_id, "lim": limit},
        )
        return [dict(row) for row in result.mappings().all()]


async def append_exercise_to_existing_workout(
    workout_id: int,
    exercise_name: str,
    sets: list[dict],
    source_text: str | None = None,
) -> int:
    """Append a new exercise (with sets) to an existing workout. Returns count inserted."""
    async with AsyncSessionLocal() as session:
        max_result = await session.execute(
            text("""
            SELECT COALESCE(MAX(set_number), 0) AS max_n
            FROM fitness_exercise_sets
            WHERE workout_id = :wid AND lower(exercise_name) = lower(:ename)
            """),
            {"wid": workout_id, "ename": exercise_name},
        )
        start_n = max_result.scalar() or 0

        inserted = 0
        for i, s in enumerate(sets, start=1):
            if s.get("weight_kg") is None and s.get("reps") is None:
                continue
            await session.execute(
                text("""
                INSERT INTO fitness_exercise_sets
                  (workout_id, exercise_name, set_number, weight_kg, reps, rpe, notes)
                VALUES
                  (:wid, :ename, :sn, :w, :r, :rpe, :notes)
                """),
                {
                    "wid": workout_id,
                    "ename": exercise_name,
                    "sn": start_n + i,
                    "w": s.get("weight_kg"),
                    "r": s.get("reps"),
                    "rpe": s.get("rpe"),
                    "notes": s.get("notes") or (source_text[:200] if source_text else None),
                },
            )
            inserted += 1
        await session.commit()
        return inserted


async def get_last_measurement(telegram_user_id: str | None) -> dict | None:
    async with AsyncSessionLocal() as session:
        if telegram_user_id:
            result = await session.execute(
                text("""
                SELECT *
                FROM body_measurements
                WHERE telegram_user_id = :telegram_user_id
                ORDER BY measurement_date DESC, id DESC
                LIMIT 1
                """),
                {"telegram_user_id": telegram_user_id},
            )
        else:
            result = await session.execute(
                text("""
                SELECT *
                FROM body_measurements
                ORDER BY measurement_date DESC, id DESC
                LIMIT 1
                """)
            )

        row = result.mappings().first()
        return dict(row) if row else None


async def get_planned_workout_by_focus(telegram_user_id: str | None, focus: str | None, focus_label: str | None = None) -> dict | None:
    async with AsyncSessionLocal() as session:
        if focus:
            result = await session.execute(
                text("""
                SELECT *
                FROM planned_workouts
                WHERE telegram_user_id = :telegram_user_id
                  AND status = 'planned'
                  AND focus = :focus
                ORDER BY
                  CASE WHEN planned_date IS NULL THEN 1 ELSE 0 END,
                  planned_date ASC NULLS LAST,
                  sequence_number ASC NULLS LAST,
                  id ASC
                LIMIT 1
                """),
                {
                    "telegram_user_id": telegram_user_id,
                    "focus": focus,
                },
            )
        elif focus_label:
            result = await session.execute(
                text("""
                SELECT *
                FROM planned_workouts
                WHERE telegram_user_id = :telegram_user_id
                  AND status = 'planned'
                  AND lower(focus_label) = lower(:focus_label)
                ORDER BY
                  CASE WHEN planned_date IS NULL THEN 1 ELSE 0 END,
                  planned_date ASC NULLS LAST,
                  sequence_number ASC NULLS LAST,
                  id ASC
                LIMIT 1
                """),
                {
                    "telegram_user_id": telegram_user_id,
                    "focus_label": focus_label,
                },
            )
        else:
            return None

        workout = result.mappings().first()
        if not workout:
            return None

        exercises = await _get_planned_exercises(session, workout["id"])
        return {"workout": dict(workout), "exercises": exercises}


async def get_planned_workout_by_id(planned_workout_id: int) -> dict | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT *
            FROM planned_workouts
            WHERE id = :planned_workout_id
            LIMIT 1
            """),
            {"planned_workout_id": planned_workout_id},
        )
        workout = result.mappings().first()
        if not workout:
            return None

        exercises = await _get_planned_exercises(session, workout["id"])
        return {"workout": dict(workout), "exercises": exercises}


async def mark_planned_workout_skipped(
    planned_workout_id: int,
    source_text: str,
    reason: str | None = None,
) -> None:
    async with AsyncSessionLocal() as session:
        old_result = await session.execute(
            text("SELECT row_to_json(planned_workouts.*) AS data FROM planned_workouts WHERE id = :id"),
            {"id": planned_workout_id},
        )
        old_value = old_result.scalar_one_or_none()

        reason_note = "" if not reason else "\nПричина пропуска: " + reason

        await session.execute(
            text("""
            UPDATE planned_workouts
            SET status = 'skipped',
                notes = COALESCE(notes, '') || :reason_note
            WHERE id = :id
            """),
            {"id": planned_workout_id, "reason_note": reason_note},
        )

        new_result = await session.execute(
            text("SELECT row_to_json(planned_workouts.*) AS data FROM planned_workouts WHERE id = :id"),
            {"id": planned_workout_id},
        )
        new_value = new_result.scalar_one_or_none()

        await session.execute(
            text("""
            INSERT INTO planned_workout_events
            (planned_workout_id, event_type, old_value_json, new_value_json, source_text, notes)
            VALUES
            (:id, 'skipped', CAST(:old_value AS JSONB), CAST(:new_value AS JSONB), :source_text, :reason)
            """),
            {
                "id": planned_workout_id,
                "old_value": json.dumps(old_value, ensure_ascii=False) if old_value else None,
                "new_value": json.dumps(new_value, ensure_ascii=False) if new_value else None,
                "source_text": source_text,
                "reason": reason,
            },
        )
        await session.commit()


async def move_planned_workout(
    planned_workout_id: int,
    new_date: str | None,
    new_weekday: str | None,
    source_text: str,
) -> None:
    async with AsyncSessionLocal() as session:
        old_result = await session.execute(
            text("SELECT row_to_json(planned_workouts.*) AS data FROM planned_workouts WHERE id = :id"),
            {"id": planned_workout_id},
        )
        old_value = old_result.scalar_one_or_none()

        parsed_new_date = to_date(new_date)

        await session.execute(
            text("""
            UPDATE planned_workouts
            SET planned_date = :new_date,
                weekday = :new_weekday,
                is_floating = :is_floating,
                status = 'planned'
            WHERE id = :id
            """),
            {
                "id": planned_workout_id,
                "new_date": parsed_new_date,
                "new_weekday": new_weekday,
                "is_floating": parsed_new_date is None,
            },
        )

        new_result = await session.execute(
            text("SELECT row_to_json(planned_workouts.*) AS data FROM planned_workouts WHERE id = :id"),
            {"id": planned_workout_id},
        )
        new_value = new_result.scalar_one_or_none()

        await session.execute(
            text("""
            INSERT INTO planned_workout_events
            (planned_workout_id, event_type, old_value_json, new_value_json, source_text, notes)
            VALUES
            (:id, 'moved', CAST(:old_value AS JSONB), CAST(:new_value AS JSONB), :source_text, NULL)
            """),
            {
                "id": planned_workout_id,
                "old_value": json.dumps(old_value, ensure_ascii=False) if old_value else None,
                "new_value": json.dumps(new_value, ensure_ascii=False) if new_value else None,
                "source_text": source_text,
            },
        )
        await session.commit()


async def swap_planned_workouts(
    first_workout_id: int,
    second_workout_id: int,
    source_text: str,
) -> None:
    async with AsyncSessionLocal() as session:
        old_result = await session.execute(
            text("""
            SELECT json_agg(row_to_json(pw.*)) AS data
            FROM planned_workouts pw
            WHERE id IN (:first_id, :second_id)
            """),
            {"first_id": first_workout_id, "second_id": second_workout_id},
        )
        old_value = old_result.scalar_one_or_none()

        first = await session.execute(
            text("SELECT planned_date, weekday, sequence_number, is_floating FROM planned_workouts WHERE id = :id"),
            {"id": first_workout_id},
        )
        second = await session.execute(
            text("SELECT planned_date, weekday, sequence_number, is_floating FROM planned_workouts WHERE id = :id"),
            {"id": second_workout_id},
        )

        first_row = first.mappings().first()
        second_row = second.mappings().first()

        if not first_row or not second_row:
            raise ValueError("Не нашёл одну из тренировок для обмена местами")

        await session.execute(
            text("""
            UPDATE planned_workouts
            SET planned_date = :planned_date,
                weekday = :weekday,
                sequence_number = :sequence_number,
                is_floating = :is_floating
            WHERE id = :id
            """),
            {
                "id": first_workout_id,
                "planned_date": second_row["planned_date"],
                "weekday": second_row["weekday"],
                "sequence_number": second_row["sequence_number"],
                "is_floating": second_row["is_floating"],
            },
        )

        await session.execute(
            text("""
            UPDATE planned_workouts
            SET planned_date = :planned_date,
                weekday = :weekday,
                sequence_number = :sequence_number,
                is_floating = :is_floating
            WHERE id = :id
            """),
            {
                "id": second_workout_id,
                "planned_date": first_row["planned_date"],
                "weekday": first_row["weekday"],
                "sequence_number": first_row["sequence_number"],
                "is_floating": first_row["is_floating"],
            },
        )

        new_result = await session.execute(
            text("""
            SELECT json_agg(row_to_json(pw.*)) AS data
            FROM planned_workouts pw
            WHERE id IN (:first_id, :second_id)
            """),
            {"first_id": first_workout_id, "second_id": second_workout_id},
        )
        new_value = new_result.scalar_one_or_none()

        for workout_id in (first_workout_id, second_workout_id):
            await session.execute(
                text("""
                INSERT INTO planned_workout_events
                (planned_workout_id, event_type, old_value_json, new_value_json, source_text, notes)
                VALUES
                (:id, 'swapped', CAST(:old_value AS JSONB), CAST(:new_value AS JSONB), :source_text, NULL)
                """),
                {
                    "id": workout_id,
                    "old_value": json.dumps(old_value, ensure_ascii=False) if old_value else None,
                    "new_value": json.dumps(new_value, ensure_ascii=False) if new_value else None,
                    "source_text": source_text,
                },
            )

        await session.commit()


async def replace_planned_workout(
    target_workout_id: int,
    replacement: dict,
    source_text: str,
) -> int:
    async with AsyncSessionLocal() as session:
        old_result = await session.execute(
            text("SELECT row_to_json(planned_workouts.*) AS data FROM planned_workouts WHERE id = :id"),
            {"id": target_workout_id},
        )
        old_value = old_result.scalar_one_or_none()

        target_result = await session.execute(
            text("""
            SELECT plan_id, telegram_user_id, planned_date, weekday, sequence_number
            FROM planned_workouts
            WHERE id = :id
            """),
            {"id": target_workout_id},
        )
        target = target_result.mappings().first()
        if not target:
            raise ValueError("Не нашёл тренировку для замены")

        await session.execute(
            text("""
            UPDATE planned_workouts
            SET status = 'replaced'
            WHERE id = :id
            """),
            {"id": target_workout_id},
        )

        replacement_result = await session.execute(
            text("""
            INSERT INTO planned_workouts
            (plan_id, telegram_user_id, planned_date, weekday, sequence_number, is_floating,
             title, focus, focus_label, workout_type, status, source_text, notes)
            VALUES
            (:plan_id, :telegram_user_id, :planned_date, :weekday, :sequence_number, false,
             :title, :focus, :focus_label, 'replacement', 'planned', :source_text, :notes)
            RETURNING id
            """),
            {
                "plan_id": target["plan_id"],
                "telegram_user_id": target["telegram_user_id"],
                "planned_date": target["planned_date"],
                "weekday": target["weekday"],
                "sequence_number": target["sequence_number"],
                "title": replacement.get("title"),
                "focus": replacement.get("focus"),
                "focus_label": replacement.get("focus_label"),
                "source_text": source_text,
                "notes": replacement.get("notes"),
            },
        )
        replacement_id = replacement_result.scalar_one()

        await session.execute(
            text("""
            UPDATE planned_workouts
            SET replaced_by_id = :replacement_id
            WHERE id = :target_id
            """),
            {"replacement_id": replacement_id, "target_id": target_workout_id},
        )

        for exercise in replacement.get("exercises", []):
            exercise_name = exercise.get("exercise_name")
            if not exercise_name:
                continue

            await session.execute(
                text("""
                INSERT INTO planned_exercises
                (planned_workout_id, exercise_order, exercise_name, target_sets,
                 target_reps_min, target_reps_max, target_reps_text, target_weight_kg, notes)
                VALUES
                (:planned_workout_id, :exercise_order, :exercise_name, :target_sets,
                 :target_reps_min, :target_reps_max, :target_reps_text, :target_weight_kg, :notes)
                """),
                {
                    "planned_workout_id": replacement_id,
                    "exercise_order": exercise.get("exercise_order"),
                    "exercise_name": exercise_name,
                    "target_sets": exercise.get("target_sets"),
                    "target_reps_min": exercise.get("target_reps_min"),
                    "target_reps_max": exercise.get("target_reps_max"),
                    "target_reps_text": exercise.get("target_reps_text"),
                    "target_weight_kg": exercise.get("target_weight_kg"),
                    "notes": exercise.get("notes"),
                },
            )

        await session.execute(
            text("""
            INSERT INTO planned_workout_events
            (planned_workout_id, event_type, old_value_json, new_value_json, source_text, notes)
            VALUES
            (:id, 'replaced', CAST(:old_value AS JSONB), CAST(:new_value AS JSONB), :source_text, NULL)
            """),
            {
                "id": target_workout_id,
                "old_value": json.dumps(old_value, ensure_ascii=False) if old_value else None,
                "new_value": json.dumps({"replacement_id": replacement_id, "replacement": replacement}, ensure_ascii=False),
                "source_text": source_text,
            },
        )

        await session.commit()
        return replacement_id


async def get_latest_planned_workout_template_by_focus(
    telegram_user_id: str | None,
    focus: str | None,
    exclude_workout_id: int | None = None,
) -> dict | None:
    if not focus:
        return None

    async with AsyncSessionLocal() as session:
        if exclude_workout_id is not None:
            result = await session.execute(
                text("""
                SELECT *
                FROM planned_workouts
                WHERE telegram_user_id = :telegram_user_id
                  AND focus = :focus
                  AND id != :exclude_workout_id
                ORDER BY
                  CASE
                    WHEN status = 'planned' THEN 0
                    WHEN status = 'skipped' THEN 1
                    WHEN status = 'completed' THEN 2
                    WHEN status = 'completed_modified' THEN 3
                    ELSE 4
                  END,
                  planned_date DESC NULLS LAST,
                  id DESC
                LIMIT 1
                """),
                {
                    "telegram_user_id": telegram_user_id,
                    "focus": focus,
                    "exclude_workout_id": exclude_workout_id,
                },
            )
        else:
            result = await session.execute(
                text("""
                SELECT *
                FROM planned_workouts
                WHERE telegram_user_id = :telegram_user_id
                  AND focus = :focus
                ORDER BY
                  CASE
                    WHEN status = 'planned' THEN 0
                    WHEN status = 'skipped' THEN 1
                    WHEN status = 'completed' THEN 2
                    WHEN status = 'completed_modified' THEN 3
                    ELSE 4
                  END,
                  planned_date DESC NULLS LAST,
                  id DESC
                LIMIT 1
                """),
                {
                    "telegram_user_id": telegram_user_id,
                    "focus": focus,
                },
            )

        workout = result.mappings().first()
        if not workout:
            return None

        exercises = await _get_planned_exercises(session, workout["id"])
        return {"workout": dict(workout), "exercises": exercises}


async def get_planned_workouts_on_date(
    telegram_user_id: str | None,
    planned_date: str | None,
) -> list[dict]:
    if not planned_date:
        return []

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT *
            FROM planned_workouts
            WHERE telegram_user_id = :telegram_user_id
              AND planned_date = :planned_date
              AND status = 'planned'
            ORDER BY sequence_number NULLS LAST, id
            """),
            {
                "telegram_user_id": telegram_user_id,
                "planned_date": to_date(planned_date),
            },
        )

        rows = []
        for workout in result.mappings().all():
            exercises = await _get_planned_exercises(session, workout["id"])
            rows.append({"workout": dict(workout), "exercises": exercises})
        return rows


async def get_fitness_debug_week(telegram_user_id: str | None, start_date: str, end_date: str) -> list[dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT
                id,
                plan_id,
                planned_date,
                weekday,
                sequence_number,
                is_floating,
                title,
                focus,
                focus_label,
                workout_type,
                status,
                replaced_by_id
            FROM planned_workouts
            WHERE telegram_user_id = :telegram_user_id
              AND (
                planned_date BETWEEN :start_date AND :end_date
                OR planned_date IS NULL
              )
            ORDER BY planned_date ASC NULLS LAST, sequence_number ASC NULLS LAST, id ASC
            """),
            {
                "telegram_user_id": telegram_user_id,
                "start_date": to_date(start_date),
                "end_date": to_date(end_date),
            },
        )
        return [dict(row) for row in result.mappings().all()]


async def create_fitness_pending_decision(
    telegram_user_id: str | None,
    decision_type: str,
    context: dict,
    source_text: str,
) -> int:
    async with AsyncSessionLocal() as session:
        # Close previous pending decision of same type for this user
        await session.execute(
            text("""
            UPDATE fitness_pending_decisions
            SET status = 'cancelled',
                resolved_at = now()
            WHERE telegram_user_id = :telegram_user_id
              AND decision_type = :decision_type
              AND status = 'pending'
            """),
            {
                "telegram_user_id": telegram_user_id,
                "decision_type": decision_type,
            },
        )

        result = await session.execute(
            text("""
            INSERT INTO fitness_pending_decisions
            (telegram_user_id, decision_type, status, context_json, source_text)
            VALUES
            (:telegram_user_id, :decision_type, 'pending', CAST(:context_json AS JSONB), :source_text)
            RETURNING id
            """),
            {
                "telegram_user_id": telegram_user_id,
                "decision_type": decision_type,
                "context_json": json.dumps(context, ensure_ascii=False),
                "source_text": source_text,
            },
        )
        decision_id = result.scalar_one()
        await session.commit()
        return decision_id


async def get_latest_fitness_pending_decision(
    telegram_user_id: str | None,
) -> dict | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT id, decision_type, context_json, source_text, created_at
            FROM fitness_pending_decisions
            WHERE telegram_user_id = :telegram_user_id
              AND status = 'pending'
            ORDER BY id DESC
            LIMIT 1
            """),
            {"telegram_user_id": telegram_user_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None


async def resolve_fitness_pending_decision(
    decision_id: int,
    status: str = "resolved",
) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
            UPDATE fitness_pending_decisions
            SET status = :status,
                resolved_at = now()
            WHERE id = :decision_id
            """),
            {
                "decision_id": decision_id,
                "status": status,
            },
        )
        await session.commit()


async def find_nearest_free_training_date(
    telegram_user_id: str | None,
    start_date: str,
    max_days: int = 14,
) -> str | None:
    """
    Finds the nearest date after start_date with no planned active workouts.
    """
    from datetime import timedelta

    base = to_date(start_date)
    if base is None:
        return None

    async with AsyncSessionLocal() as session:
        for offset in range(1, max_days + 1):
            candidate = base + timedelta(days=offset)
            result = await session.execute(
                text("""
                SELECT COUNT(*) AS cnt
                FROM planned_workouts
                WHERE telegram_user_id = :telegram_user_id
                  AND planned_date = :candidate
                  AND status = 'planned'
                """),
                {
                    "telegram_user_id": telegram_user_id,
                    "candidate": candidate,
                },
            )
            count = result.scalar_one()
            if count == 0:
                return candidate.isoformat()

    return None


async def reset_fitness_week_plan(
    telegram_user_id: str | None,
    start_date: str,
    end_date: str,
    source_text: str = "/fitness_reset_week",
) -> dict:
    """
    Cancels all planned workouts for the selected week and archives active plans
    that overlap this week. Does not delete completed workout history.
    """
    async with AsyncSessionLocal() as session:
        # Count affected planned workouts first
        count_result = await session.execute(
            text("""
            SELECT COUNT(*) AS cnt
            FROM planned_workouts
            WHERE telegram_user_id = :telegram_user_id
              AND planned_date BETWEEN :start_date AND :end_date
              AND status IN ('planned', 'skipped', 'moved', 'replaced')
            """),
            {
                "telegram_user_id": telegram_user_id,
                "start_date": to_date(start_date),
                "end_date": to_date(end_date),
            },
        )
        affected_workouts = count_result.scalar_one()

        # Mark workouts as cancelled
        await session.execute(
            text("""
            UPDATE planned_workouts
            SET status = 'cancelled',
                notes = COALESCE(notes, '') || '\nCancelled by fitness week reset.'
            WHERE telegram_user_id = :telegram_user_id
              AND planned_date BETWEEN :start_date AND :end_date
              AND status IN ('planned', 'skipped', 'moved', 'replaced')
            """),
            {
                "telegram_user_id": telegram_user_id,
                "start_date": to_date(start_date),
                "end_date": to_date(end_date),
            },
        )

        # Archive active plans overlapping this week
        plan_count_result = await session.execute(
            text("""
            SELECT COUNT(*) AS cnt
            FROM training_plans
            WHERE telegram_user_id = :telegram_user_id
              AND status = 'active'
              AND (
                (start_date <= :end_date AND end_date >= :start_date)
                OR start_date IS NULL
                OR end_date IS NULL
              )
            """),
            {
                "telegram_user_id": telegram_user_id,
                "start_date": to_date(start_date),
                "end_date": to_date(end_date),
            },
        )
        affected_plans = plan_count_result.scalar_one()

        await session.execute(
            text("""
            UPDATE training_plans
            SET status = 'archived',
                notes = COALESCE(notes, '') || '\nArchived by fitness week reset.'
            WHERE telegram_user_id = :telegram_user_id
              AND status = 'active'
              AND (
                (start_date <= :end_date AND end_date >= :start_date)
                OR start_date IS NULL
                OR end_date IS NULL
              )
            """),
            {
                "telegram_user_id": telegram_user_id,
                "start_date": to_date(start_date),
                "end_date": to_date(end_date),
            },
        )

        # Close pending fitness decisions
        await session.execute(
            text("""
            UPDATE fitness_pending_decisions
            SET status = 'cancelled',
                resolved_at = now()
            WHERE telegram_user_id = :telegram_user_id
              AND status = 'pending'
            """),
            {"telegram_user_id": telegram_user_id},
        )

        await session.commit()

        return {
            "affected_workouts": affected_workouts,
            "affected_plans": affected_plans,
            "start_date": start_date,
            "end_date": end_date,
        }


async def get_active_planned_workouts_in_period(
    telegram_user_id: str | None,
    start_date: str,
    end_date: str,
) -> list[dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT *
            FROM planned_workouts
            WHERE telegram_user_id = :telegram_user_id
              AND planned_date BETWEEN :start_date AND :end_date
              AND status = 'planned'
            ORDER BY planned_date ASC, sequence_number ASC NULLS LAST, id ASC
            """),
            {
                "telegram_user_id": telegram_user_id,
                "start_date": to_date(start_date),
                "end_date": to_date(end_date),
            },
        )
        return [dict(row) for row in result.mappings().all()]


async def cancel_active_planned_workouts_in_period(
    telegram_user_id: str | None,
    start_date: str,
    end_date: str,
    source_text: str,
) -> int:
    async with AsyncSessionLocal() as session:
        count_result = await session.execute(
            text("""
            SELECT COUNT(*) AS cnt
            FROM planned_workouts
            WHERE telegram_user_id = :telegram_user_id
              AND planned_date BETWEEN :start_date AND :end_date
              AND status = 'planned'
            """),
            {
                "telegram_user_id": telegram_user_id,
                "start_date": to_date(start_date),
                "end_date": to_date(end_date),
            },
        )
        affected = count_result.scalar_one()

        await session.execute(
            text("""
            UPDATE planned_workouts
            SET status = 'cancelled',
                notes = COALESCE(notes, '') || '\nCancelled by plan replacement.'
            WHERE telegram_user_id = :telegram_user_id
              AND planned_date BETWEEN :start_date AND :end_date
              AND status = 'planned'
            """),
            {
                "telegram_user_id": telegram_user_id,
                "start_date": to_date(start_date),
                "end_date": to_date(end_date),
            },
        )

        await session.execute(
            text("""
            UPDATE training_plans
            SET status = 'archived',
                notes = COALESCE(notes, '') || '\nArchived by plan replacement.'
            WHERE telegram_user_id = :telegram_user_id
              AND status = 'active'
              AND start_date <= :end_date
              AND end_date >= :start_date
            """),
            {
                "telegram_user_id": telegram_user_id,
                "start_date": to_date(start_date),
                "end_date": to_date(end_date),
            },
        )

        await session.commit()
        return affected


async def get_planned_workouts_in_period(
    telegram_user_id: str | None,
    start_date: str,
    end_date: str,
    include_cancelled: bool = False,
) -> list[dict]:
    async with AsyncSessionLocal() as session:
        if include_cancelled:
            result = await session.execute(
                text("""
                SELECT *
                FROM planned_workouts
                WHERE telegram_user_id = :telegram_user_id
                  AND planned_date BETWEEN :start_date AND :end_date
                ORDER BY planned_date ASC, sequence_number ASC NULLS LAST, id ASC
                """),
                {
                    "telegram_user_id": telegram_user_id,
                    "start_date": to_date(start_date),
                    "end_date": to_date(end_date),
                },
            )
        else:
            result = await session.execute(
                text("""
                SELECT *
                FROM planned_workouts
                WHERE telegram_user_id = :telegram_user_id
                  AND planned_date BETWEEN :start_date AND :end_date
                  AND status != 'cancelled'
                ORDER BY planned_date ASC, sequence_number ASC NULLS LAST, id ASC
                """),
                {
                    "telegram_user_id": telegram_user_id,
                    "start_date": to_date(start_date),
                    "end_date": to_date(end_date),
                },
            )

        rows = []
        for workout in result.mappings().all():
            exercises = await _get_planned_exercises(session, workout["id"])
            rows.append({"workout": dict(workout), "exercises": exercises})
        return rows


async def save_fitness_workout_session_v2(
    telegram_user_id: str | None,
    workout_date: str,
    workout_type: str | None,
    focus: str | None,
    focus_label: str | None,
    source_text: str,
    notes: str | None,
    exercises: list[dict],
) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            INSERT INTO fitness_workouts
            (telegram_user_id, workout_date, workout_type, focus, focus_label, completion_type, source_text, notes)
            VALUES
            (:telegram_user_id, :workout_date, :workout_type, :focus, :focus_label, 'custom', :source_text, :notes)
            RETURNING id
            """),
            {
                "telegram_user_id": telegram_user_id,
                "workout_date": to_date(workout_date),
                "workout_type": workout_type,
                "focus": focus,
                "focus_label": focus_label,
                "source_text": source_text,
                "notes": notes,
            },
        )
        workout_id = result.scalar_one()

        for exercise in exercises:
            exercise_name = exercise.get("exercise_name")
            if not exercise_name:
                continue
            for i, item in enumerate(exercise.get("sets") or [], start=1):
                await session.execute(
                    text("""
                    INSERT INTO fitness_exercise_sets
                    (workout_id, exercise_name, set_number, weight_kg, reps, rpe, notes)
                    VALUES
                    (:workout_id, :exercise_name, :set_number, :weight_kg, :reps, :rpe, :notes)
                    """),
                    {
                        "workout_id": workout_id,
                        "exercise_name": exercise_name,
                        "set_number": item.get("set_number") or i,
                        "weight_kg": item.get("weight_kg"),
                        "reps": item.get("reps"),
                        "rpe": item.get("rpe"),
                        "notes": item.get("notes"),
                    },
                )

        await session.commit()
        return workout_id


async def append_fitness_workout_sets_v2(
    workout_id: int,
    exercise_name: str,
    sets: list[dict],
    source_text: str,
) -> int:
    async with AsyncSessionLocal() as session:
        max_result = await session.execute(
            text("""
            SELECT COALESCE(MAX(set_number), 0)
            FROM fitness_exercise_sets
            WHERE workout_id = :workout_id
              AND exercise_name = :exercise_name
            """),
            {"workout_id": workout_id, "exercise_name": exercise_name},
        )
        current_max = max_result.scalar_one() or 0

        inserted = 0
        for i, item in enumerate(sets or [], start=1):
            if item.get("weight_kg") is None and item.get("reps") is None:
                continue

            await session.execute(
                text("""
                INSERT INTO fitness_exercise_sets
                (workout_id, exercise_name, set_number, weight_kg, reps, rpe, notes)
                VALUES
                (:workout_id, :exercise_name, :set_number, :weight_kg, :reps, :rpe, :notes)
                """),
                {
                    "workout_id": workout_id,
                    "exercise_name": exercise_name,
                    "set_number": item.get("set_number") or (current_max + i),
                    "weight_kg": item.get("weight_kg"),
                    "reps": item.get("reps"),
                    "rpe": item.get("rpe"),
                    "notes": item.get("notes") or source_text,
                },
            )
            inserted += 1

        await session.commit()
        return inserted


async def update_fitness_pending_decision_context(decision_id: int, context: dict) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
            UPDATE fitness_pending_decisions
            SET context_json = CAST(:context_json AS JSONB)
            WHERE id = :decision_id
            """),
            {
                "decision_id": decision_id,
                "context_json": json.dumps(context, ensure_ascii=False),
            },
        )
        await session.commit()


async def delete_last_fitness_set_v2(workout_id: int) -> dict | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT id, exercise_name, weight_kg, reps
            FROM fitness_exercise_sets
            WHERE workout_id = :workout_id
            ORDER BY id DESC
            LIMIT 1
            """),
            {"workout_id": workout_id},
        )
        row = result.mappings().first()
        if not row:
            return None

        await session.execute(
            text("DELETE FROM fitness_exercise_sets WHERE id = :id"),
            {"id": row["id"]},
        )
        await session.commit()
        return dict(row)


async def update_last_fitness_set_v2(
    workout_id: int,
    field: str,
    new_value,
) -> dict | None:
    if field not in ("weight_kg", "reps"):
        return None

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT id
            FROM fitness_exercise_sets
            WHERE workout_id = :workout_id
            ORDER BY id DESC
            LIMIT 1
            """),
            {"workout_id": workout_id},
        )
        row = result.mappings().first()
        if not row:
            return None

        if field == "weight_kg":
            await session.execute(
                text("UPDATE fitness_exercise_sets SET weight_kg = :new_value WHERE id = :id"),
                {"new_value": new_value, "id": row["id"]},
            )
        else:
            await session.execute(
                text("UPDATE fitness_exercise_sets SET reps = :new_value WHERE id = :id"),
                {"new_value": new_value, "id": row["id"]},
            )

        updated_result = await session.execute(
            text("""
            SELECT exercise_name, weight_kg, reps
            FROM fitness_exercise_sets
            WHERE id = :id
            """),
            {"id": row["id"]},
        )

        await session.commit()
        updated = updated_result.mappings().first()
        return dict(updated) if updated else None


async def auto_close_stale_active_workout_sessions(timeout_minutes: int = 60) -> int:
    """
    Close active workout sessions after timeout_minutes since the last
    training-related activity. Does not send Telegram messages.
    """
    from datetime import datetime, timezone, timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT id, context_json
            FROM fitness_pending_decisions
            WHERE decision_type = 'active_workout_session'
              AND status = 'pending'
            """)
        )

        rows = result.mappings().all()
        closed_count = 0

        for row in rows:
            context = row.get("context_json") or {}
            session_status = context.get("session_status") or "active"

            if session_status != "active":
                continue

            last_activity_raw = (
                context.get("last_training_activity_at")
                or context.get("last_activity_at")
                or context.get("started_at")
            )

            if not last_activity_raw:
                continue

            try:
                if isinstance(last_activity_raw, str):
                    last_activity = datetime.fromisoformat(
                        last_activity_raw.replace("Z", "+00:00")
                    )
                else:
                    last_activity = last_activity_raw

                if last_activity.tzinfo is None:
                    last_activity = last_activity.replace(tzinfo=timezone.utc)
            except Exception:
                continue

            if last_activity > cutoff:
                continue

            context["session_status"] = "closed_auto"
            context["closed_at"] = datetime.now(timezone.utc).isoformat()
            context["close_reason"] = (
                f"auto_closed_after_{timeout_minutes}_minutes_training_inactivity"
            )

            await session.execute(
                text("""
                UPDATE fitness_pending_decisions
                SET status = 'resolved',
                    resolved_at = now(),
                    context_json = CAST(:context_json AS JSONB)
                WHERE id = :decision_id
                """),
                {
                    "decision_id": row["id"],
                    "context_json": json.dumps(context, ensure_ascii=False),
                },
            )

            closed_count += 1

        await session.commit()
        return closed_count


async def get_recent_exercise_history(
    telegram_user_id: str | None,
    exercise_key: str,
    limit_workouts: int = 3,
) -> list[dict]:
    """
    Returns recent workouts where normalized exercise key matches.

    v0.2.0 note:
    This does not require an exercise_key column yet.
    It normalizes exercise_name in Python, so it works with existing data.
    """
    from app.modules.fitness.exercise_normalizer import normalize_exercise_name

    async with AsyncSessionLocal() as session:
        if telegram_user_id:
            result = await session.execute(
                text("""
                SELECT
                    w.id AS workout_id,
                    w.workout_date AS workout_date,
                    s.exercise_name,
                    s.set_number,
                    s.weight_kg,
                    s.reps,
                    s.rpe,
                    s.notes
                FROM fitness_workouts w
                JOIN fitness_exercise_sets s ON s.workout_id = w.id
                WHERE w.telegram_user_id = :telegram_user_id
                ORDER BY w.workout_date DESC, w.id DESC, s.id ASC
                LIMIT 1000
                """),
                {"telegram_user_id": telegram_user_id},
            )
        else:
            result = await session.execute(
                text("""
                SELECT
                    w.id AS workout_id,
                    w.workout_date AS workout_date,
                    s.exercise_name,
                    s.set_number,
                    s.weight_kg,
                    s.reps,
                    s.rpe,
                    s.notes
                FROM fitness_workouts w
                JOIN fitness_exercise_sets s ON s.workout_id = w.id
                ORDER BY w.workout_date DESC, w.id DESC, s.id ASC
                LIMIT 1000
                """)
            )

        rows = [dict(row) for row in result.mappings().all()]

    grouped = {}

    for row in rows:
        normalized = normalize_exercise_name(row.get("exercise_name"))

        if normalized.get("exercise_key") != exercise_key:
            continue

        workout_id = row.get("workout_id")

        if workout_id not in grouped:
            grouped[workout_id] = {
                "workout_id": workout_id,
                "workout_date": str(row.get("workout_date")),
                "exercise_name": row.get("exercise_name"),
                "sets": [],
            }

        grouped[workout_id]["sets"].append(
            {
                "exercise_name": row.get("exercise_name"),
                "set_number": row.get("set_number"),
                "weight_kg": row.get("weight_kg"),
                "reps": row.get("reps"),
                "rpe": row.get("rpe"),
                "notes": row.get("notes"),
            }
        )

    return list(grouped.values())[:limit_workouts]


async def get_next_planned_workouts(
    telegram_user_id: str | None,
    limit: int = 3,
) -> list[dict]:
    """
    Return nearest active planned workouts from today onward.
    """
    from datetime import date

    today = date.today()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT pw.id
            FROM planned_workouts pw
            WHERE pw.telegram_user_id = :telegram_user_id
              AND pw.planned_date >= :today
              AND pw.status = 'planned'
              AND EXISTS (
                SELECT 1 FROM planned_exercises ex WHERE ex.planned_workout_id = pw.id
              )
            ORDER BY pw.planned_date ASC, pw.sequence_number ASC, pw.id DESC
            LIMIT :limit
            """),
            {
                "telegram_user_id": telegram_user_id,
                "today": today,
                "limit": limit,
            },
        )

        rows = result.mappings().all()
        ids = [row["id"] for row in rows]

    result = []
    for workout_id in ids:
        item = await get_planned_workout_by_id(workout_id)
        if item:
            result.append(item)

    return result






async def copy_planned_workouts_period(
    telegram_user_id: str | None,
    source_start_date: str,
    source_end_date: str,
    target_start_date: str,
    target_end_date: str,
    collision_policy: str = "skip_existing",
    source_text: str | None = None,
) -> dict:
    """
    Copy all active planned workouts from source period to target period,
    preserving day offsets. Duplicate-safe by default.
    """
    from datetime import date as date_type, timedelta

    if not telegram_user_id:
        return {"ok": False, "created": [], "skipped": [], "reason": "telegram_user_id is missing"}

    source_start = date_type.fromisoformat(source_start_date) if isinstance(source_start_date, str) else source_start_date
    source_end = date_type.fromisoformat(source_end_date) if isinstance(source_end_date, str) else source_end_date
    target_start = date_type.fromisoformat(target_start_date) if isinstance(target_start_date, str) else target_start_date
    target_end = date_type.fromisoformat(target_end_date) if isinstance(target_end_date, str) else target_end_date

    created = []
    skipped = []

    async with AsyncSessionLocal() as session:
        source_result = await session.execute(
            text(
                """
                SELECT id, planned_date, title, focus, focus_label, workout_type, notes
                FROM planned_workouts
                WHERE telegram_user_id = :telegram_user_id
                  AND planned_date >= :source_start
                  AND planned_date <= :source_end
                  AND status = 'planned'
                ORDER BY planned_date, id
                """
            ),
            {
                "telegram_user_id": str(telegram_user_id),
                "source_start": source_start,
                "source_end": source_end,
            },
        )
        source_workouts = list(source_result.mappings())

        if not source_workouts:
            return {
                "ok": True,
                "created": [],
                "skipped": [],
                "source_count": 0,
            }

        source_period_days = (source_end - source_start).days + 1
        target_period_days = (target_end - target_start).days + 1

        for period_offset in range(0, target_period_days, source_period_days):
            for workout in source_workouts:
                source_date = workout["planned_date"]
                offset_days = (source_date - source_start).days
                target_date = target_start + timedelta(days=period_offset + offset_days)

                if target_date > target_end:
                    continue

                existing_result = await session.execute(
                    text(
                        """
                        SELECT id, title
                        FROM planned_workouts
                        WHERE telegram_user_id = :telegram_user_id
                          AND planned_date = :target_date
                          AND status = 'planned'
                        LIMIT 1
                        """
                    ),
                    {
                        "telegram_user_id": str(telegram_user_id),
                        "target_date": target_date,
                    },
                )
                existing = existing_result.mappings().first()

                if existing and collision_policy != "replace_existing":
                    skipped.append({
                        "target_date": target_date.isoformat(),
                        "reason": f"уже есть активная тренировка — {existing['title'] or 'Плановая тренировка'}",
                    })
                    continue

                if existing and collision_policy == "replace_existing":
                    await session.execute(
                        text(
                            """
                            UPDATE planned_workouts
                            SET status = 'cancelled'
                            WHERE telegram_user_id = :telegram_user_id
                              AND planned_date = :target_date
                              AND status = 'planned'
                            """
                        ),
                        {
                            "telegram_user_id": str(telegram_user_id),
                            "target_date": target_date,
                        },
                    )

                insert_result = await session.execute(
                    text(
                        """
                        INSERT INTO planned_workouts (
                            telegram_user_id,
                            planned_date,
                            weekday,
                            sequence_number,
                            is_floating,
                            title,
                            focus,
                            focus_label,
                            workout_type,
                            status,
                            notes
                        )
                        VALUES (
                            :telegram_user_id,
                            :planned_date,
                            :weekday,
                            1,
                            false,
                            :title,
                            :focus,
                            :focus_label,
                            :workout_type,
                            'planned',
                            :notes
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "telegram_user_id": str(telegram_user_id),
                        "planned_date": target_date,
                        "weekday": target_date.strftime("%A"),
                        "title": workout["title"],
                        "focus": workout["focus"],
                        "focus_label": workout["focus_label"],
                        "workout_type": workout["workout_type"] or "planned",
                        "notes": workout["notes"],
                    },
                )
                new_workout_id = insert_result.scalar_one()

                exercises_result = await session.execute(
                    text(
                        """
                        SELECT
                            exercise_order,
                            exercise_name,
                            target_sets,
                            target_reps_min,
                            target_reps_max,
                            target_reps_text,
                            target_weight_kg,
                            notes
                        FROM planned_exercises
                        WHERE planned_workout_id = :planned_workout_id
                        ORDER BY exercise_order, id
                        """
                    ),
                    {"planned_workout_id": int(workout["id"])},
                )
                exercises = list(exercises_result.mappings())

                for exercise in exercises:
                    await session.execute(
                        text(
                            """
                            INSERT INTO planned_exercises (
                                planned_workout_id,
                                exercise_order,
                                exercise_name,
                                target_sets,
                                target_reps_min,
                                target_reps_max,
                                target_reps_text,
                                target_weight_kg,
                                notes
                            )
                            VALUES (
                                :planned_workout_id,
                                :exercise_order,
                                :exercise_name,
                                :target_sets,
                                :target_reps_min,
                                :target_reps_max,
                                :target_reps_text,
                                :target_weight_kg,
                                :notes
                            )
                            """
                        ),
                        {
                            "planned_workout_id": int(new_workout_id),
                            "exercise_order": exercise["exercise_order"],
                            "exercise_name": exercise["exercise_name"],
                            "target_sets": exercise["target_sets"],
                            "target_reps_min": exercise["target_reps_min"],
                            "target_reps_max": exercise["target_reps_max"],
                            "target_reps_text": exercise["target_reps_text"],
                            "target_weight_kg": exercise["target_weight_kg"],
                            "notes": exercise["notes"],
                        },
                    )

                created.append({
                    "target_date": target_date.isoformat(),
                    "title": workout["title"] or "Плановая тренировка",
                    "source_date": source_date.isoformat(),
                })

        await session.commit()

    return {
        "ok": True,
        "created": created,
        "skipped": skipped,
        "source_count": len(source_workouts),
    }


async def has_active_planned_workout_on_date(
    telegram_user_id: str | None,
    target_date: str,
) -> bool:
    """
    Return True if user already has an active planned workout on target_date.
    Used by copy flow to avoid duplicate planned workouts.
    """
    if not telegram_user_id or not target_date:
        return False

    from datetime import date as date_type

    if isinstance(target_date, str):
        target_date_value = date_type.fromisoformat(target_date)
    else:
        target_date_value = target_date

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(
                """
                SELECT id
                FROM planned_workouts
                WHERE telegram_user_id = :telegram_user_id
                  AND planned_date = :target_date
                  AND status = 'planned'
                LIMIT 1
                """
            ),
            {
                "telegram_user_id": str(telegram_user_id),
                "target_date": target_date_value,
            },
        )

        return result.first() is not None


async def move_planned_workouts_between_dates(
    telegram_user_id: str | None,
    source_date: str,
    target_date: str,
    source_text: str | None = None,
    mode: str = "move",
) -> int:
    """
    Move or copy active planned workouts from source_date to target_date.

    mode='move' updates original workouts.
    mode='copy' creates a new day plan with copied workouts and keeps originals.
    """
    source_items = await get_planned_workouts_in_period(
        telegram_user_id=telegram_user_id,
        start_date=source_date,
        end_date=source_date,
        include_cancelled=False,
    )

    active_items = [
        item for item in source_items
        if (item.get("workout") or {}).get("status") == "planned"
    ]

    if not active_items:
        return 0

    if mode == "copy":
        planned_workouts = []
        for i, item in enumerate(active_items, start=1):
            workout = item.get("workout") or {}
            exercises = item.get("exercises") or []

            copied_exercises = []
            for ex_index, ex in enumerate(exercises, start=1):
                copied_exercises.append(
                    {
                        "exercise_order": ex.get("exercise_order") or ex_index,
                        "exercise_name": ex.get("exercise_name"),
                        "target_sets": ex.get("target_sets"),
                        "target_reps_min": ex.get("target_reps_min"),
                        "target_reps_max": ex.get("target_reps_max"),
                        "target_reps_text": ex.get("target_reps_text"),
                        "target_weight_kg": ex.get("target_weight_kg"),
                        "notes": ex.get("notes"),
                    }
                )

            planned_workouts.append(
                {
                    "planned_date": target_date,
                    "weekday": None,
                    "sequence_number": i,
                    "is_floating": False,
                    "title": workout.get("title"),
                    "focus": workout.get("focus"),
                    "focus_label": workout.get("focus_label"),
                    "workout_type": workout.get("workout_type") or "copied",
                    "status": "planned",
                    "notes": f"Copied from {source_date}",
                    "exercises": copied_exercises,
                }
            )

        await save_training_plan(
            telegram_user_id=telegram_user_id,
            plan_name=f"Copied workout from {source_date}",
            period_type="day",
            start_date=target_date,
            end_date=target_date,
            source_text=source_text,
            notes=f"Copied from {source_date}",
            planned_workouts=planned_workouts,
        )
        return len(planned_workouts)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            UPDATE planned_workouts
            SET planned_date = :target_date,
                notes = COALESCE(notes, '') || :note
            WHERE telegram_user_id = :telegram_user_id
              AND planned_date = :source_date
              AND status = 'planned'
            RETURNING id
            """),
            {
                "telegram_user_id": telegram_user_id,
                "source_date": to_date(source_date),
                "target_date": to_date(target_date),
                "note": f"\nMoved from {source_date}",
            },
        )

        rows = result.mappings().all()
        ids = [row["id"] for row in rows]

        for workout_id in ids:
            await session.execute(
                text("""
                INSERT INTO planned_workout_events
                (planned_workout_id, event_type, old_value_json, new_value_json, source_text, notes)
                VALUES
                (:planned_workout_id, 'moved', CAST(:old_value_json AS JSONB), CAST(:new_value_json AS JSONB), :source_text, :notes)
                """),
                {
                    "planned_workout_id": workout_id,
                    "old_value_json": json.dumps({"planned_date": source_date}, ensure_ascii=False),
                    "new_value_json": json.dumps({"planned_date": target_date}, ensure_ascii=False),
                    "source_text": source_text,
                    "notes": "Moved planned workout between dates",
                },
            )

        await session.commit()
        return len(ids)


async def cleanup_empty_planned_workouts(
    telegram_user_id: str | None,
) -> int:
    """
    Cancel planned workouts that have no exercises and no focus.
    Does not touch actual workout history.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            UPDATE planned_workouts pw
            SET status = 'cancelled',
                notes = COALESCE(pw.notes, '') || '\nCancelled by empty workout cleanup'
            WHERE pw.telegram_user_id = :telegram_user_id
              AND pw.status = 'planned'
              AND COALESCE(pw.focus, '') = ''
              AND COALESCE(pw.focus_label, '') = ''
              AND NOT EXISTS (
                  SELECT 1
                  FROM planned_exercises ex
                  WHERE ex.planned_workout_id = pw.id
              )
            RETURNING id
            """),
            {"telegram_user_id": telegram_user_id},
        )

        rows = result.mappings().all()
        ids = [row["id"] for row in rows]

        for workout_id in ids:
            await session.execute(
                text("""
                INSERT INTO planned_workout_events
                (planned_workout_id, event_type, old_value_json, new_value_json, source_text, notes)
                VALUES
                (:planned_workout_id, 'cancelled', NULL, CAST(:new_value_json AS JSONB), :source_text, :notes)
                """),
                {
                    "planned_workout_id": workout_id,
                    "new_value_json": json.dumps(
                        {"status": "cancelled", "reason": "empty workout cleanup"},
                        ensure_ascii=False,
                    ),
                    "source_text": "/fitness_cleanup_empty_planned",
                    "notes": "Cancelled by empty planned workout cleanup",
                },
            )

        await session.commit()
        return len(ids)


async def replace_exercise_in_planned_workout(
    telegram_user_id: str | None,
    target_date: str,
    old_exercise_name: str,
    new_exercise_name: str,
    source_text: str | None = None,
) -> dict:
    """
    Replace one exercise inside the best matching active planned workout on target_date.

    Safety:
    - Does NOT replace whole workout.
    - Preserves sets/reps/weight/notes/order.
    - If old exercise is not found, returns available exercises.
    """
    from app.modules.fitness.exercise_normalizer import normalize_exercise_name

    old_norm = normalize_exercise_name(old_exercise_name)
    new_norm = normalize_exercise_name(new_exercise_name)

    old_key = old_norm.get("exercise_key")
    new_title = new_norm.get("canonical_ru") or new_exercise_name

    async with AsyncSessionLocal() as session:
        workouts_result = await session.execute(
            text("""
            SELECT pw.id
            FROM planned_workouts pw
            WHERE pw.telegram_user_id = :telegram_user_id
              AND pw.planned_date = :target_date
              AND pw.status = 'planned'
            ORDER BY
              CASE WHEN EXISTS (
                SELECT 1
                FROM planned_exercises ex
                WHERE ex.planned_workout_id = pw.id
              ) THEN 0 ELSE 1 END,
              pw.id DESC
            """),
            {
                "telegram_user_id": telegram_user_id,
                "target_date": to_date(target_date),
            },
        )

        workout_ids = [row["id"] for row in workouts_result.mappings().all()]

        if not workout_ids:
            return {
                "ok": False,
                "message": f"На {target_date} активная плановая тренировка не найдена.",
                "available_exercises": [],
            }

        available_exercises = []

        for workout_id in workout_ids:
            ex_result = await session.execute(
                text("""
                SELECT
                    id,
                    exercise_name,
                    exercise_order,
                    target_sets,
                    target_reps_min,
                    target_reps_max,
                    target_reps_text,
                    target_weight_kg,
                    notes
                FROM planned_exercises
                WHERE planned_workout_id = :planned_workout_id
                ORDER BY exercise_order ASC, id ASC
                """),
                {"planned_workout_id": workout_id},
            )

            exercises = [dict(row) for row in ex_result.mappings().all()]

            if not exercises:
                continue

            available_exercises = [ex.get("exercise_name") for ex in exercises if ex.get("exercise_name")]

            target_exercise = None

            for ex in exercises:
                candidate_name = ex.get("exercise_name") or ""
                candidate_norm = normalize_exercise_name(candidate_name)
                candidate_key = candidate_norm.get("exercise_key")

                direct_match = old_exercise_name.strip().lower().replace("ё", "е") in candidate_name.lower().replace("ё", "е")
                reverse_direct_match = candidate_name.lower().replace("ё", "е") in old_exercise_name.strip().lower().replace("ё", "е")

                key_match = old_key and candidate_key and old_key == candidate_key

                if key_match or direct_match or reverse_direct_match:
                    target_exercise = ex
                    break

            if not target_exercise:
                continue

            old_name = target_exercise.get("exercise_name")

            await session.execute(
                text("""
                UPDATE planned_exercises
                SET exercise_name = :new_exercise_name,
                    notes = COALESCE(notes, '')
                WHERE id = :exercise_id
                """),
                {
                    "exercise_id": target_exercise["id"],
                    "new_exercise_name": new_title,
                },
            )

            await session.execute(
                text("""
                INSERT INTO planned_workout_events
                (planned_workout_id, event_type, old_value_json, new_value_json, source_text, notes)
                VALUES
                (:planned_workout_id, 'exercise_replaced', CAST(:old_value_json AS JSONB), CAST(:new_value_json AS JSONB), :source_text, :notes)
                """),
                {
                    "planned_workout_id": workout_id,
                    "old_value_json": json.dumps(
                        {
                            "exercise_id": target_exercise["id"],
                            "exercise_name": old_name,
                        },
                        ensure_ascii=False,
                    ),
                    "new_value_json": json.dumps(
                        {
                            "exercise_id": target_exercise["id"],
                            "exercise_name": new_title,
                            "preserved_parameters": True,
                        },
                        ensure_ascii=False,
                    ),
                    "source_text": source_text,
                    "notes": "Replaced one planned exercise while preserving parameters",
                },
            )

            await session.commit()

            return {
                "ok": True,
                "planned_workout_id": workout_id,
                "exercise_id": target_exercise["id"],
                "old_exercise_name": old_name,
                "new_exercise_name": new_title,
            }

        return {
            "ok": False,
            "message": f"Не нашёл “{old_exercise_name}” в тренировке на {target_date}.",
            "available_exercises": available_exercises,
        }


async def get_best_planned_workout_for_edit(
    telegram_user_id: str | None,
    target_date: str | None = None,
    planned_workout_id: int | None = None,
) -> dict:
    """
    Return best active planned workout for editing.

    Priority:
    1. explicit planned_workout_id
    2. target_date with active non-empty workout, newest first
    """
    from datetime import date

    async with AsyncSessionLocal() as session:
        if planned_workout_id:
            result = await session.execute(
                text("""
                SELECT pw.id
                FROM planned_workouts pw
                WHERE pw.id = :planned_workout_id
                  AND pw.telegram_user_id = :telegram_user_id
                  AND pw.status = 'planned'
                LIMIT 1
                """),
                {
                    "planned_workout_id": planned_workout_id,
                    "telegram_user_id": telegram_user_id,
                },
            )
        else:
            date_value = to_date(target_date) if target_date else date.today()

            result = await session.execute(
                text("""
                SELECT pw.id
                FROM planned_workouts pw
                WHERE pw.telegram_user_id = :telegram_user_id
                  AND pw.planned_date = :target_date
                  AND pw.status = 'planned'
                ORDER BY
                  CASE WHEN EXISTS (
                    SELECT 1
                    FROM planned_exercises ex
                    WHERE ex.planned_workout_id = pw.id
                  ) THEN 0 ELSE 1 END,
                  pw.id DESC
                LIMIT 1
                """),
                {
                    "telegram_user_id": telegram_user_id,
                    "target_date": date_value,
                },
            )

        row = result.mappings().first()

    if not row:
        return {
            "ok": False,
            "message": "Активная плановая тренировка для редактирования не найдена.",
        }

    workout = await get_planned_workout_by_id(row["id"])
    if not workout:
        return {
            "ok": False,
            "message": "Не смог загрузить плановую тренировку для редактирования.",
        }

    return {
        "ok": True,
        "planned_workout_id": row["id"],
        "workout": workout,
    }


async def _renumber_planned_exercises(
    session,
    planned_workout_id: int,
) -> None:
    result = await session.execute(
        text("""
        SELECT id
        FROM planned_exercises
        WHERE planned_workout_id = :planned_workout_id
        ORDER BY exercise_order ASC NULLS LAST, id ASC
        """),
        {"planned_workout_id": planned_workout_id},
    )

    ids = [row["id"] for row in result.mappings().all()]

    for index, exercise_id in enumerate(ids, start=1):
        await session.execute(
            text("""
            UPDATE planned_exercises
            SET exercise_order = :exercise_order
            WHERE id = :exercise_id
            """),
            {
                "exercise_order": index,
                "exercise_id": exercise_id,
            },
        )


async def add_exercise_to_planned_workout(
    telegram_user_id: str | None,
    planned_workout_id: int | None = None,
    target_date: str | None = None,
    exercise_name: str | None = None,
    exercise_position: int | None = None,
    position_mode: str | None = None,
    anchor_exercise_name: str | None = None,
    target_sets: int | None = None,
    target_reps_min: int | None = None,
    target_reps_max: int | None = None,
    target_reps_text: str | None = None,
    target_weight_kg: float | None = None,
    notes: str | None = None,
    source_text: str | None = None,
) -> dict:
    from app.modules.fitness.exercise_normalizer import normalize_exercise_name

    if not exercise_name:
        return {"ok": False, "message": "Не понял, какое упражнение добавить."}

    selected = await get_best_planned_workout_for_edit(
        telegram_user_id=telegram_user_id,
        target_date=target_date,
        planned_workout_id=planned_workout_id,
    )

    if not selected.get("ok"):
        return selected

    workout_id = selected["planned_workout_id"]
    normalized = normalize_exercise_name(exercise_name)
    final_name = normalized.get("canonical_ru") or exercise_name

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT id, exercise_name, exercise_order
            FROM planned_exercises
            WHERE planned_workout_id = :planned_workout_id
            ORDER BY exercise_order ASC NULLS LAST, id ASC
            """),
            {"planned_workout_id": workout_id},
        )

        exercises = [dict(row) for row in result.mappings().all()]
        count = len(exercises)

        insert_position = exercise_position

        if position_mode == "beginning":
            insert_position = 1
        elif position_mode == "end" or insert_position is None:
            insert_position = count + 1
        elif position_mode in {"before", "after"} and anchor_exercise_name:
            anchor_norm = normalize_exercise_name(anchor_exercise_name)
            anchor_key = anchor_norm.get("exercise_key")
            found_position = None

            for ex in exercises:
                candidate = normalize_exercise_name(ex.get("exercise_name"))
                if anchor_key and candidate.get("exercise_key") == anchor_key:
                    found_position = int(ex.get("exercise_order") or 0)
                    break
                if anchor_exercise_name.lower().replace("ё", "е") in (ex.get("exercise_name") or "").lower().replace("ё", "е"):
                    found_position = int(ex.get("exercise_order") or 0)
                    break

            if found_position:
                insert_position = found_position if position_mode == "before" else found_position + 1
            else:
                insert_position = count + 1

        insert_position = max(1, min(int(insert_position), count + 1))

        await session.execute(
            text("""
            UPDATE planned_exercises
            SET exercise_order = exercise_order + 1
            WHERE planned_workout_id = :planned_workout_id
              AND exercise_order >= :insert_position
            """),
            {
                "planned_workout_id": workout_id,
                "insert_position": insert_position,
            },
        )

        insert_result = await session.execute(
            text("""
            INSERT INTO planned_exercises
            (
                planned_workout_id,
                exercise_order,
                exercise_name,
                target_sets,
                target_reps_min,
                target_reps_max,
                target_reps_text,
                target_weight_kg,
                notes
            )
            VALUES
            (
                :planned_workout_id,
                :exercise_order,
                :exercise_name,
                :target_sets,
                :target_reps_min,
                :target_reps_max,
                :target_reps_text,
                :target_weight_kg,
                :notes
            )
            RETURNING id
            """),
            {
                "planned_workout_id": workout_id,
                "exercise_order": insert_position,
                "exercise_name": final_name,
                "target_sets": target_sets,
                "target_reps_min": target_reps_min,
                "target_reps_max": target_reps_max,
                "target_reps_text": target_reps_text,
                "target_weight_kg": target_weight_kg,
                "notes": notes,
            },
        )

        exercise_id = insert_result.mappings().first()["id"]

        await _renumber_planned_exercises(session, workout_id)

        await session.execute(
            text("""
            INSERT INTO planned_workout_events
            (planned_workout_id, event_type, old_value_json, new_value_json, source_text, notes)
            VALUES
            (:planned_workout_id, 'exercise_added', NULL, CAST(:new_value_json AS JSONB), :source_text, :notes)
            """),
            {
                "planned_workout_id": workout_id,
                "new_value_json": json.dumps(
                    {
                        "exercise_id": exercise_id,
                        "exercise_name": final_name,
                        "exercise_order": insert_position,
                    },
                    ensure_ascii=False,
                ),
                "source_text": source_text,
                "notes": "Added exercise to planned workout",
            },
        )

        await session.commit()

    return {
        "ok": True,
        "planned_workout_id": workout_id,
        "exercise_id": exercise_id,
        "exercise_name": final_name,
        "exercise_order": insert_position,
    }


async def remove_exercise_from_planned_workout(
    telegram_user_id: str | None,
    planned_workout_id: int | None = None,
    target_date: str | None = None,
    exercise_name: str | None = None,
    exercise_position: int | None = None,
    position_mode: str | None = None,
    source_text: str | None = None,
) -> dict:
    from app.modules.fitness.exercise_normalizer import normalize_exercise_name

    selected = await get_best_planned_workout_for_edit(
        telegram_user_id=telegram_user_id,
        target_date=target_date,
        planned_workout_id=planned_workout_id,
    )

    if not selected.get("ok"):
        return selected

    workout_id = selected["planned_workout_id"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT id, exercise_name, exercise_order
            FROM planned_exercises
            WHERE planned_workout_id = :planned_workout_id
            ORDER BY exercise_order ASC NULLS LAST, id ASC
            """),
            {"planned_workout_id": workout_id},
        )

        exercises = [dict(row) for row in result.mappings().all()]

        if not exercises:
            return {
                "ok": False,
                "message": "В выбранной тренировке нет упражнений.",
                "available_exercises": [],
            }

        target = None

        if position_mode == "last":
            target = exercises[-1]
        elif exercise_position:
            for ex in exercises:
                if int(ex.get("exercise_order") or 0) == int(exercise_position):
                    target = ex
                    break
        elif exercise_name:
            norm = normalize_exercise_name(exercise_name)
            key = norm.get("exercise_key")
            for ex in exercises:
                candidate = normalize_exercise_name(ex.get("exercise_name"))
                direct = exercise_name.lower().replace("ё", "е") in (ex.get("exercise_name") or "").lower().replace("ё", "е")
                if (key and candidate.get("exercise_key") == key) or direct:
                    target = ex
                    break

        if not target:
            return {
                "ok": False,
                "message": "Не нашёл упражнение для удаления.",
                "available_exercises": [ex.get("exercise_name") for ex in exercises],
            }

        await session.execute(
            text("""
            DELETE FROM planned_exercises
            WHERE id = :exercise_id
            """),
            {"exercise_id": target["id"]},
        )

        await _renumber_planned_exercises(session, workout_id)

        await session.execute(
            text("""
            INSERT INTO planned_workout_events
            (planned_workout_id, event_type, old_value_json, new_value_json, source_text, notes)
            VALUES
            (:planned_workout_id, 'exercise_removed', CAST(:old_value_json AS JSONB), NULL, :source_text, :notes)
            """),
            {
                "planned_workout_id": workout_id,
                "old_value_json": json.dumps(dict(target), ensure_ascii=False),
                "source_text": source_text,
                "notes": "Removed exercise from planned workout",
            },
        )

        await session.commit()

    return {
        "ok": True,
        "planned_workout_id": workout_id,
        "removed_exercise_name": target.get("exercise_name"),
    }


async def reorder_exercise_in_planned_workout(
    telegram_user_id: str | None,
    planned_workout_id: int | None = None,
    target_date: str | None = None,
    exercise_name: str | None = None,
    exercise_position: int | None = None,
    new_position: int | None = None,
    position_mode: str | None = None,
    anchor_exercise_name: str | None = None,
    source_text: str | None = None,
) -> dict:
    from app.modules.fitness.exercise_normalizer import normalize_exercise_name

    selected = await get_best_planned_workout_for_edit(
        telegram_user_id=telegram_user_id,
        target_date=target_date,
        planned_workout_id=planned_workout_id,
    )

    if not selected.get("ok"):
        return selected

    workout_id = selected["planned_workout_id"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT id, exercise_name, exercise_order
            FROM planned_exercises
            WHERE planned_workout_id = :planned_workout_id
            ORDER BY exercise_order ASC NULLS LAST, id ASC
            """),
            {"planned_workout_id": workout_id},
        )

        exercises = [dict(row) for row in result.mappings().all()]

        if not exercises:
            return {
                "ok": False,
                "message": "В выбранной тренировке нет упражнений.",
                "available_exercises": [],
            }

        target = None

        if exercise_position:
            for ex in exercises:
                if int(ex.get("exercise_order") or 0) == int(exercise_position):
                    target = ex
                    break
        elif exercise_name:
            norm = normalize_exercise_name(exercise_name)
            key = norm.get("exercise_key")
            for ex in exercises:
                candidate = normalize_exercise_name(ex.get("exercise_name"))
                direct = exercise_name.lower().replace("ё", "е") in (ex.get("exercise_name") or "").lower().replace("ё", "е")
                if (key and candidate.get("exercise_key") == key) or direct:
                    target = ex
                    break

        if not target:
            return {
                "ok": False,
                "message": "Не нашёл упражнение для перемещения.",
                "available_exercises": [ex.get("exercise_name") for ex in exercises],
            }

        remaining = [ex for ex in exercises if ex["id"] != target["id"]]

        insert_index = None

        if position_mode == "beginning":
            insert_index = 0
        elif position_mode == "end":
            insert_index = len(remaining)
        elif position_mode in {"before", "after"} and anchor_exercise_name:
            anchor_norm = normalize_exercise_name(anchor_exercise_name)
            anchor_key = anchor_norm.get("exercise_key")
            for idx, ex in enumerate(remaining):
                candidate = normalize_exercise_name(ex.get("exercise_name"))
                direct = anchor_exercise_name.lower().replace("ё", "е") in (ex.get("exercise_name") or "").lower().replace("ё", "е")
                if (anchor_key and candidate.get("exercise_key") == anchor_key) or direct:
                    insert_index = idx if position_mode == "before" else idx + 1
                    break

        if insert_index is None:
            if new_position:
                insert_index = max(0, min(int(new_position) - 1, len(remaining)))
            else:
                return {
                    "ok": False,
                    "message": "Не понял, куда переместить упражнение.",
                    "available_exercises": [ex.get("exercise_name") for ex in exercises],
                }

        new_order = remaining[:insert_index] + [target] + remaining[insert_index:]

        for index, ex in enumerate(new_order, start=1):
            await session.execute(
                text("""
                UPDATE planned_exercises
                SET exercise_order = :exercise_order
                WHERE id = :exercise_id
                """),
                {
                    "exercise_order": index,
                    "exercise_id": ex["id"],
                },
            )

        await session.execute(
            text("""
            INSERT INTO planned_workout_events
            (planned_workout_id, event_type, old_value_json, new_value_json, source_text, notes)
            VALUES
            (:planned_workout_id, 'exercise_reordered', CAST(:old_value_json AS JSONB), CAST(:new_value_json AS JSONB), :source_text, :notes)
            """),
            {
                "planned_workout_id": workout_id,
                "old_value_json": json.dumps(
                    {
                        "exercise_id": target["id"],
                        "old_order": target.get("exercise_order"),
                    },
                    ensure_ascii=False,
                ),
                "new_value_json": json.dumps(
                    {
                        "exercise_id": target["id"],
                        "new_order": insert_index + 1,
                    },
                    ensure_ascii=False,
                ),
                "source_text": source_text,
                "notes": "Reordered exercise in planned workout",
            },
        )

        await session.commit()

    return {
        "ok": True,
        "planned_workout_id": workout_id,
        "exercise_name": target.get("exercise_name"),
        "new_position": insert_index + 1,
    }


async def update_exercise_params_in_planned_workout(
    telegram_user_id: str | None,
    planned_workout_id: int | None = None,
    target_date: str | None = None,
    exercise_name: str | None = None,
    exercise_position: int | None = None,
    target_sets: int | None = None,
    target_reps_min: int | None = None,
    target_reps_max: int | None = None,
    target_reps_text: str | None = None,
    target_weight_kg: float | None = None,
    source_text: str | None = None,
) -> dict:
    from app.modules.fitness.exercise_normalizer import normalize_exercise_name

    selected = await get_best_planned_workout_for_edit(
        telegram_user_id=telegram_user_id,
        target_date=target_date,
        planned_workout_id=planned_workout_id,
    )

    if not selected.get("ok"):
        return selected

    workout_id = selected["planned_workout_id"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
            SELECT id, exercise_name, exercise_order
            FROM planned_exercises
            WHERE planned_workout_id = :planned_workout_id
            ORDER BY exercise_order ASC NULLS LAST, id ASC
            """),
            {"planned_workout_id": workout_id},
        )

        exercises = [dict(row) for row in result.mappings().all()]
        target = None

        if exercise_position:
            for ex in exercises:
                if int(ex.get("exercise_order") or 0) == int(exercise_position):
                    target = ex
                    break
        elif exercise_name:
            norm = normalize_exercise_name(exercise_name)
            key = norm.get("exercise_key")
            for ex in exercises:
                candidate = normalize_exercise_name(ex.get("exercise_name"))
                direct = exercise_name.lower().replace("ё", "е") in (ex.get("exercise_name") or "").lower().replace("ё", "е")
                if (key and candidate.get("exercise_key") == key) or direct:
                    target = ex
                    break

        if not target:
            return {
                "ok": False,
                "message": "Не нашёл упражнение для изменения параметров.",
                "available_exercises": [ex.get("exercise_name") for ex in exercises],
            }

        updates = {
            "target_sets": target_sets,
            "target_reps_min": target_reps_min,
            "target_reps_max": target_reps_max,
            "target_reps_text": target_reps_text,
            "target_weight_kg": target_weight_kg,
        }

        set_clauses = []
        params = {"exercise_id": target["id"]}

        for key, value in updates.items():
            if value is not None:
                set_clauses.append(f"{key} = :{key}")
                params[key] = value

        if not set_clauses:
            return {
                "ok": False,
                "message": "Не понял, какие параметры упражнения изменить.",
                "available_exercises": [ex.get("exercise_name") for ex in exercises],
            }

        await session.execute(
            text(f"""
            UPDATE planned_exercises
            SET {", ".join(set_clauses)}
            WHERE id = :exercise_id
            """),
            params,
        )

        await session.execute(
            text("""
            INSERT INTO planned_workout_events
            (planned_workout_id, event_type, old_value_json, new_value_json, source_text, notes)
            VALUES
            (:planned_workout_id, 'exercise_params_updated', NULL, CAST(:new_value_json AS JSONB), :source_text, :notes)
            """),
            {
                "planned_workout_id": workout_id,
                "new_value_json": json.dumps(
                    {
                        "exercise_id": target["id"],
                        "updates": {k: v for k, v in updates.items() if v is not None},
                    },
                    ensure_ascii=False,
                ),
                "source_text": source_text,
                "notes": "Updated planned exercise parameters",
            },
        )

        await session.commit()

    return {
        "ok": True,
        "planned_workout_id": workout_id,
        "exercise_name": target.get("exercise_name"),
        "updates": {k: v for k, v in updates.items() if v is not None},
    }


async def remove_multiple_exercises_from_planned_workout(
    telegram_user_id: str | None,
    planned_workout_id: int | None = None,
    target_date: str | None = None,
    exercise_positions: list[int] | None = None,
    source_text: str | None = None,
) -> dict:
    if not exercise_positions:
        return {
            "ok": False,
            "message": "Не понял, какие номера упражнений удалить.",
        }

    selected = await get_best_planned_workout_for_edit(
        telegram_user_id=telegram_user_id,
        target_date=target_date,
        planned_workout_id=planned_workout_id,
    )

    if not selected.get("ok"):
        return selected

    workout_id = selected["planned_workout_id"]
    removed = []

    for position in sorted(set(int(x) for x in exercise_positions), reverse=True):
        result = await remove_exercise_from_planned_workout(
            telegram_user_id=telegram_user_id,
            planned_workout_id=workout_id,
            exercise_position=position,
            source_text=source_text,
        )
        if result.get("ok"):
            removed.append(result.get("removed_exercise_name"))

    return {
        "ok": True,
        "planned_workout_id": workout_id,
        "removed_exercise_names": removed,
    }


async def create_planned_workout_from_program_day(
    telegram_user_id: str | None,
    target_date: str,
    program_day: dict,
    title_prefix: str | None = None,
    skip_existing: bool = True,
    source_text: str | None = None,
) -> dict:
    """
    Create one planned workout from parsed imported program day.

    Safety:
    - If skip_existing=True and active workout exists on target_date, skip.
    - Does not touch existing workouts.
    """
    if not program_day:
        return {
            "ok": False,
            "message": "Пустой тренировочный день.",
        }

    target_date_value = to_date(target_date)

    async with AsyncSessionLocal() as session:
        if skip_existing:
            existing_result = await session.execute(
                text("""
                SELECT id, title
                FROM planned_workouts
                WHERE telegram_user_id = :telegram_user_id
                  AND planned_date = :target_date
                  AND status = 'planned'
                ORDER BY id DESC
                LIMIT 1
                """),
                {
                    "telegram_user_id": telegram_user_id,
                    "target_date": target_date_value,
                },
            )
            existing = existing_result.mappings().first()
            if existing:
                return {
                    "ok": True,
                    "skipped": True,
                    "reason": "already_exists",
                    "target_date": target_date,
                    "existing_workout_id": existing["id"],
                    "existing_title": existing.get("title"),
                }

        day_title = program_day.get("title") or f"День {program_day.get('day_index') or ''}".strip()
        title = f"{title_prefix} — {day_title}" if title_prefix else day_title
        focus = program_day.get("focus") or "custom"

        workout_result = await session.execute(
            text("""
            INSERT INTO planned_workouts
            (
                telegram_user_id,
                planned_date,
                title,
                focus,
                focus_label,
                status,
                notes
            )
            VALUES
            (
                :telegram_user_id,
                :planned_date,
                :title,
                :focus,
                :focus_label,
                'planned',
                :notes
            )
            RETURNING id
            """),
            {
                "telegram_user_id": telegram_user_id,
                "planned_date": target_date_value,
                "title": title,
                "focus": focus,
                "focus_label": focus,
                "notes": source_text,
            },
        )

        workout_id = workout_result.mappings().first()["id"]

        exercises = program_day.get("exercises") or []
        exercise_order = 1

        for raw in exercises:
            name = raw.get("exercise_name")
            if not name:
                continue

            notes_parts = []

            if raw.get("notes"):
                notes_parts.append(str(raw.get("notes")))

            planned_reps = raw.get("planned_reps")
            if planned_reps:
                notes_parts.append(
                    "planned_sets:\n"
                    + "\n".join(
                        f"{i}) {rep}"
                        for i, rep in enumerate(planned_reps, start=1)
                    )
                )

            if raw.get("superset_group") is not None:
                notes_parts.append(f"superset_group={raw.get('superset_group')}")
            if raw.get("superset_item"):
                notes_parts.append(f"superset_item={raw.get('superset_item')}")

            notes = "\n".join(notes_parts) if notes_parts else None

            await session.execute(
                text("""
                INSERT INTO planned_exercises
                (
                    planned_workout_id,
                    exercise_order,
                    exercise_name,
                    target_sets,
                    target_reps_min,
                    target_reps_max,
                    target_reps_text,
                    target_weight_kg,
                    notes
                )
                VALUES
                (
                    :planned_workout_id,
                    :exercise_order,
                    :exercise_name,
                    :target_sets,
                    :target_reps_min,
                    :target_reps_max,
                    :target_reps_text,
                    :target_weight_kg,
                    :notes
                )
                """),
                {
                    "planned_workout_id": workout_id,
                    "exercise_order": exercise_order,
                    "exercise_name": name,
                    "target_sets": raw.get("target_sets"),
                    "target_reps_min": raw.get("target_reps_min"),
                    "target_reps_max": raw.get("target_reps_max"),
                    "target_reps_text": raw.get("target_reps_text"),
                    "target_weight_kg": raw.get("target_weight_kg"),
                    "notes": notes,
                },
            )

            exercise_order += 1

        await session.execute(
            text("""
            INSERT INTO planned_workout_events
            (planned_workout_id, event_type, old_value_json, new_value_json, source_text, notes)
            VALUES
            (:planned_workout_id, 'program_imported', NULL, CAST(:new_value_json AS JSONB), :source_text, :notes)
            """),
            {
                "planned_workout_id": workout_id,
                "new_value_json": json.dumps(
                    {
                        "program_day_index": program_day.get("day_index"),
                        "program_day_title": program_day.get("title"),
                        "exercise_count": len(exercises),
                    },
                    ensure_ascii=False,
                ),
                "source_text": source_text,
                "notes": "Created planned workout from imported training program",
            },
        )

        await session.commit()

    return {
        "ok": True,
        "skipped": False,
        "target_date": target_date,
        "planned_workout_id": workout_id,
        "title": title,
        "exercise_count": exercise_order - 1,
    }


async def import_training_program_to_calendar(
    telegram_user_id: str | None,
    program: dict,
    target_dates: list[str],
    title_prefix: str | None = None,
    skip_existing: bool = True,
    source_text: str | None = None,
) -> dict:
    """
    Import parsed training program days into calendar.

    target_dates maps by index:
    program.days[0] -> target_dates[0]
    program.days[1] -> target_dates[1]
    etc.

    If there are more target_dates than days, days are cycled.
    This supports repeating weekly programs over several weeks.
    """
    days = program.get("days") or []
    if not days:
        return {
            "ok": False,
            "message": "В программе нет тренировочных дней.",
            "created": [],
            "skipped": [],
        }

    if not target_dates:
        return {
            "ok": False,
            "message": "Не указаны даты для импорта.",
            "created": [],
            "skipped": [],
        }

    created = []
    skipped = []
    replaced = []

    for index, target_date in enumerate(target_dates):
        day = days[index % len(days)]

        if not skip_existing:
            from datetime import date as date_type

            target_date_value = (
                date_type.fromisoformat(target_date)
                if isinstance(target_date, str)
                else target_date
            )

            async with AsyncSessionLocal() as session:
                existing_result = await session.execute(
                    text(
                        """
                        SELECT id, title
                        FROM planned_workouts
                        WHERE telegram_user_id = :telegram_user_id
                          AND planned_date = :target_date
                          AND status = 'planned'
                        ORDER BY id
                        """
                    ),
                    {
                        "telegram_user_id": str(telegram_user_id),
                        "target_date": target_date_value,
                    },
                )
                existing_rows = list(existing_result.mappings())

                if existing_rows:
                    await session.execute(
                        text(
                            """
                            UPDATE planned_workouts
                            SET status = 'cancelled'
                            WHERE telegram_user_id = :telegram_user_id
                              AND planned_date = :target_date
                              AND status = 'planned'
                            """
                        ),
                        {
                            "telegram_user_id": str(telegram_user_id),
                            "target_date": target_date_value,
                        },
                    )
                    await session.commit()

                    for row in existing_rows:
                        replaced.append(
                            {
                                "target_date": target_date,
                                "planned_workout_id": int(row["id"]),
                                "title": row["title"] or "Плановая тренировка",
                            }
                        )

        result = await create_planned_workout_from_program_day(
            telegram_user_id=telegram_user_id,
            target_date=target_date,
            program_day=day,
            title_prefix=title_prefix,
            skip_existing=skip_existing,
            source_text=source_text,
        )

        if result.get("skipped"):
            skipped.append(result)
        elif result.get("ok"):
            created.append(result)
        else:
            skipped.append(result)

    return {
        "ok": True,
        "created": created,
        "skipped": skipped,
        "replaced": replaced,
    }
