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
        CREATE INDEX IF NOT EXISTS idx_fitness_workouts_user_date
        ON fitness_workouts (telegram_user_id, workout_date);
        """))

        await conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_body_measurements_user_date
        ON body_measurements (telegram_user_id, measurement_date);
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
            SELECT *
            FROM planned_workouts
            WHERE telegram_user_id = :telegram_user_id
              AND planned_date = :today
              AND status = 'planned'
            ORDER BY sequence_number NULLS LAST, id
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
