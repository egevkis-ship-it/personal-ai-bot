-- Удаление всех тренировок (сессии + планы).
-- Справочник exercises и body_measurements не трогаем.
TRUNCATE TABLE workout_sessions RESTART IDENTITY CASCADE;
TRUNCATE TABLE workout_plans RESTART IDENTITY CASCADE;
