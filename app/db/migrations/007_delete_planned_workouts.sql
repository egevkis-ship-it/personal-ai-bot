-- Удаление всех запланированных (ещё не выполненных) тренировок.
-- Сессии и история не затрагиваются: workout_sessions.plan_id имеет ON DELETE SET NULL.
DELETE FROM workout_plans
WHERE status = 'planned';
