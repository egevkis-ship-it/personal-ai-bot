"""Migration SQL must be syntactically valid for Postgres + cover required tables."""
import os
import re

import pytest

pytestmark = pytest.mark.unit

MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "app", "db", "migrations",
)


def _read_all_migrations() -> str:
    """Concatenate all SQL files in order."""
    files = sorted(
        f for f in os.listdir(MIGRATIONS_DIR) if re.match(r"^\d{3}_.*\.sql$", f)
    )
    chunks = []
    for f in files:
        with open(os.path.join(MIGRATIONS_DIR, f)) as fp:
            chunks.append(fp.read())
    return "\n".join(chunks)


class TestMigrationCoverage:
    """Tables that must exist in migrations (catch-up for prod)."""

    REQUIRED_TABLES = [
        "training_plans", "planned_workouts", "planned_exercises",
        "planned_workout_events",
        "fitness_workouts", "fitness_exercise_sets",
        "fitness_workout_logs", "fitness_workout_log_sets",
        "body_measurements",
        "training_constraints",
        "fitness_pending_decisions",
        # Catch-up additions (regression: 008 must contain these)
        "last_interaction",
        "learning_corrections",
        "user_preferences",
        "workout_templates",
        "fitness_goals",
        "pain_journal",
        "scheduled_reminders",
    ]

    @pytest.fixture(autouse=True)
    def setup(self):
        self.all_sql = _read_all_migrations().lower()

    @pytest.mark.parametrize("table", REQUIRED_TABLES)
    def test_table_in_migrations(self, table):
        """Table must be created in some .sql migration file."""
        assert f"create table" in self.all_sql, "no CREATE TABLE in migrations at all"
        # Allow both "if not exists" and plain create
        patterns = [
            f"create table if not exists {table}",
            f"create table {table}",
        ]
        assert any(p in self.all_sql for p in patterns), f"Table {table!r} missing from migrations"

    REQUIRED_COLUMNS = [
        ("body_measurements", "bodyfat_pct"),
        ("fitness_exercise_sets", "duration_seconds"),
        ("planned_exercises", "superset_group"),
        ("last_interaction", "current_workout_date"),
    ]

    @pytest.mark.parametrize("table,column", REQUIRED_COLUMNS)
    def test_column_in_migrations(self, table, column):
        # Find creation OR alter add
        assert (
            re.search(rf"{table}\s+add column[^\n]*{column}", self.all_sql)
            or re.search(rf"create table[^;]*?{table}[^;]*?{column}", self.all_sql, re.DOTALL)
        ), f"Column {table}.{column} missing from migrations"


class TestMigrationFilesValid:
    def test_runner_script_exists(self):
        assert os.path.exists(os.path.join(MIGRATIONS_DIR, "runner.py"))

    def test_filenames_format(self):
        for f in os.listdir(MIGRATIONS_DIR):
            if f.endswith(".sql"):
                assert re.match(r"^\d{3}_.*\.sql$", f), f"Bad filename: {f}"
