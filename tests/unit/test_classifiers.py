"""Unit tests for routing classifiers — regex detectors only."""
import pytest

pytestmark = pytest.mark.unit


class TestHardFitnessSignal:
    """Detector that bypasses haiku classifier for obvious fitness messages."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.router import _has_hard_fitness_signal
        self.detect = _has_hard_fitness_signal

    @pytest.mark.parametrize("text", [
        "Запиши тренировку: жим 80×5 4 подхода",
        "Запланируем на следующую неделю 25-05-2026 Жим штанги 90 кг × 10 разминка",
        "Копируй тренировки этой недели на следующую",
        "Делал жим штанги 100 кг 5 раз",
        "сделал пуловер с канатами 25 кг 12 повторений",
        "первый подход 80 на 10",
    ])
    def test_fitness_text_detected(self, text):
        assert self.detect(text), f"должно быть fitness: {text!r}"

    @pytest.mark.parametrize("text", [
        "Какая погода в Москве",
        "Сколько время",
        "Привет, как дела",
        "Найди мне рецепт пасты",
    ])
    def test_non_fitness_not_detected(self, text):
        assert not self.detect(text), f"НЕ должно быть fitness: {text!r}"


class TestHardOpsSignal:
    """Detector that prevents misroute of 'удали тренировки' to ops module."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.router import _has_hard_ops_signal
        self.detect = _has_hard_ops_signal

    @pytest.mark.parametrize("text", [
        "задеплой бот",
        "установи pip install pandas",
        "напиши код для X",
        "добавь модуль notes",
        "сделай миграцию",
        "git commit",
        "пуш в main",
    ])
    def test_ops_detected(self, text):
        assert self.detect(text)

    @pytest.mark.parametrize("text", [
        "удали все мои сообщения",
        "удали все мои тренировки",
        "удали последние подходы",
        "копируй тренировки этой недели",
        "почисти историю",
        "перенеси план на завтра",
        "сотри последнюю запись",
    ])
    def test_not_ops_regression(self, text):
        """Регресс: эти фразы ошибочно классифицировались как ops, шли в codegen."""
        assert not self.detect(text), f"{text!r} не должно быть ops"


class TestMuscleGroupClassifier:
    @pytest.fixture(autouse=True)
    def setup(self):
        from app.modules.fitness.muscle_groups import classify_exercise, estimate_1rm
        self.classify = classify_exercise
        self.estimate_1rm = estimate_1rm

    @pytest.mark.parametrize("exercise,expected", [
        ("Жим штанги лёжа", "грудь"),
        ("Жим гантелей под углом", "грудь"),
        ("Pec deck / сведения", "грудь"),
        ("Горизонтальная тяга блока", "спина"),
        ("Подтягивания", "спина"),
        ("Становая тяга", "спина"),
        ("Жим гантелей сидя", "плечи"),
        ("Махи гантелей в стороны", "плечи"),
        ("Reverse pec deck", "плечи"),  # special case
        ("Бицепс на скамье Скотта", "бицепс"),
        ("Молотки", "бицепс"),
        ("Трицепс канат", "трицепс"),
        ("Брусья узким хватом", "трицепс"),
        ("Жим ногами", "квадрицепс"),
        ("Приседания со штангой", "квадрицепс"),
        ("Сгибание ног", "ягодицы"),
        ("Hip thrust", "ягодицы"),
        ("Икры в тренажёре", "икры"),
        ("Планка", "пресс"),
        ("V-складка", "пресс"),
        ("Дорожка", "кардио"),
        ("Бег", "кардио"),
        ("Что-то непонятное", "другое"),
    ])
    def test_classification(self, exercise, expected):
        assert self.classify(exercise) == expected, f"{exercise!r}"

    def test_1rm_single_rep(self):
        assert self.estimate_1rm(100, 1) == 100.0

    def test_1rm_multi_rep(self):
        assert self.estimate_1rm(80, 5) == 93.3  # Epley: 80 * (1 + 5/30)

    def test_1rm_zero_reps(self):
        assert self.estimate_1rm(0, 5) == 0.0


class TestSelfLearningDetectors:
    @pytest.fixture(autouse=True)
    def setup(self):
        from app.modules.fitness.self_learning import is_correction_message, is_forget_message
        self.is_correction = is_correction_message
        self.is_forget = is_forget_message

    @pytest.mark.parametrize("text,expected", [
        ("ты понял неправильно", True),
        ("это не так, было 80 а не 90", True),
        ("запомни на будущее: жим — это жим лёжа", True),
        ("когда я говорю 'до отказа' — это AMRAP", True),
        ("имей в виду что у меня травма плеча", True),
        ("ты должен понимать суперсеты", True),
        ("запиши тренировку", False),
        ("что я делал вчера", False),
        ("привет", False),
    ])
    def test_correction(self, text, expected):
        assert self.is_correction(text) == expected

    @pytest.mark.parametrize("text,expected", [
        ("забудь правило #5", True),
        ("удали правило 7", True),
        ("это правило не нужно", True),
        ("обычное сообщение", False),
    ])
    def test_forget(self, text, expected):
        assert self.is_forget(text) == expected


class TestExerciseInputMode:
    """W3-6: catalog-driven input mode (time / bodyweight / strength). No DB."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.bot.services.catalog_v2 import classify_input, CATALOG
        self.classify = classify_input
        self.catalog = CATALOG

    @pytest.mark.parametrize("key,expected", [
        ("running_treadmill", "time"),        # cardio → time, not 20×10
        ("walking_treadmill", "time"),
        ("elliptical_trainer", "time"),
        ("rowing_stationary", "time"),
        ("stairmaster", "time"),
        ("rope_jumping", "time"),
        ("plank", "time"),                    # plank → time
        ("side_bridge", "time"),
        ("scapular_pull_up", "strength"),     # gravitron = assisted machine → weight
        ("pullups", "bodyweight"),
        ("pushups", "bodyweight"),
        ("dips_chest_version", "bodyweight"),
        ("air_bike", "bodyweight"),           # «Велосипед (пресс)» is NOT cardio/time
        ("barbell_bench_press_medium_grip", "strength"),
        ("crunches", "bodyweight"),
        ("weighted_crunches", "strength"),    # added weight → strength
    ])
    def test_input_mode(self, key, expected):
        it = self.catalog.get(key)
        assert it is not None, f"catalog missing {key}"
        assert it["input"] == expected, f"{key}: {it['input']} != {expected}"

    def test_every_catalog_entry_has_a_valid_mode(self):
        assert all(it.get("input") in ("time", "bodyweight", "strength")
                   for it in self.catalog.values())

    def test_no_cardio_is_weighted(self):
        for it in self.catalog.values():
            if it["muscle_group"] == "cardio":
                assert it["input"] == "time", f"cardio {it['canonical_ru']} → {it['input']}"


class TestSetQualifiers:
    """REC-6: per-set qualifiers keep the weight intact and become a note."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.bot.services.set_parser import parse_exercise_input
        self.P = parse_exercise_input

    def test_per_hand_qualifier_keeps_weight(self):
        r = self.P("Махи 20 кг на руку 12")
        assert r[0].weight_kg == 20.0 and r[0].reps == 12
        assert "на руку" in (r[0].notes or "")

    def test_counterweight_qualifier_keeps_weight(self):
        r = self.P("Гравитрон компенсация 50 кг 10")
        assert r[0].weight_kg == 50.0 and r[0].reps == 10
        assert "компенсаци" in (r[0].notes or "")

    def test_plain_input_unaffected(self):
        r = self.P("Жим 80x10")
        assert r[0].weight_kg == 80.0 and r[0].reps == 10 and r[0].notes is None
