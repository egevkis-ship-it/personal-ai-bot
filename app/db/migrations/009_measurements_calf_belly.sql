-- Расширение body_measurements для голени и живота

ALTER TABLE body_measurements ADD COLUMN IF NOT EXISTS calf_cm NUMERIC;
ALTER TABLE body_measurements ADD COLUMN IF NOT EXISTS belly_cm NUMERIC;
