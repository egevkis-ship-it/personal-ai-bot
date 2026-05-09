from __future__ import annotations
import re
from functools import lru_cache


EXERCISE_LIBRARY = {
    # Chest
    "barbell_bench_press": {
        "canonical_ru": "Жим штанги лёжа",
        "muscle_group": "chest",
        "aliases": [
            "жим штанги лежа", "жим штанги лёжа", "жим лежа", "жим лёжа",
            "жим на горизонтальной", "bench press", "barbell bench press",
        ],
    },
    "incline_barbell_press": {
        "canonical_ru": "Жим штанги под углом",
        "muscle_group": "chest",
        "aliases": [
            "жим штанги под углом", "жим штанги на наклонной",
            "наклонный жим штанги", "incline barbell press",
        ],
    },
    "incline_dumbbell_press": {
        "canonical_ru": "Жим гантелей под углом",
        "muscle_group": "chest",
        "aliases": [
            "жим гантелей под углом", "жим гантелей на наклонной",
            "наклонный жим гантелей", "incline dumbbell press",
        ],
    },
    "dumbbell_bench_press": {
        "canonical_ru": "Жим гантелей лёжа",
        "muscle_group": "chest",
        "aliases": [
            "жим гантелей лежа", "жим гантелей лёжа", "dumbbell bench press",
        ],
    },
    "machine_chest_press": {
        "canonical_ru": "Жим на грудь в тренажёре",
        "muscle_group": "chest",
        "aliases": [
            "жим на грудь в тренажере", "жим на грудь в тренажёре",
            "жим в тренажере на грудь", "machine chest press",
        ],
    },
    "cable_chest_fly": {
        "canonical_ru": "Сведение рук на грудь в кроссовере",
        "muscle_group": "chest",
        "aliases": [
            "сведение рук на грудь в кроссовере", "сведение в кроссовере",
            "кроссовер на грудь", "сведение рук в кроссовере", "cable fly",
        ],
    },
    "dumbbell_fly": {
        "canonical_ru": "Разведение гантелей лёжа",
        "muscle_group": "chest",
        "aliases": [
            "разведение гантелей лежа", "разведение гантелей лёжа",
            "разводка гантелей лежа", "разводка гантелей лёжа", "dumbbell fly",
        ],
    },
    "pec_deck": {
        "canonical_ru": "Бабочка / Pec Deck",
        "muscle_group": "chest",
        "aliases": ["бабочка", "pec deck", "пек дек", "сведение в бабочке"],
    },

    # Back
    "pull_up_wide": {
        "canonical_ru": "Подтягивания широким хватом",
        "muscle_group": "back",
        "aliases": [
            "подтягивания широким хватом", "подтягивания широкие",
            "широкие подтягивания", "wide grip pull up",
        ],
    },
    "assisted_pull_up": {
        "canonical_ru": "Подтягивания в гравитроне",
        "muscle_group": "back",
        "aliases": [
            "подтягивания в гравитроне", "гравитрон",
            "подтягивания с противовесом", "assisted pull up",
        ],
    },
    "lat_pulldown": {
        "canonical_ru": "Вертикальная тяга",
        "muscle_group": "back",
        "aliases": [
            "вертикальная тяга", "тяга верхнего блока", "верхний блок",
            "верхняя тяга", "lat pulldown",
        ],
    },
    "seated_cable_row": {
        "canonical_ru": "Горизонтальная тяга блока",
        "muscle_group": "back",
        "aliases": [
            "горизонтальная тяга блока", "горизонтальная тяга",
            "тяга нижнего блока", "нижний блок", "seated cable row",
        ],
    },
    "one_arm_dumbbell_row": {
        "canonical_ru": "Тяга гантели в наклоне",
        "muscle_group": "back",
        "aliases": [
            "тяга гантели в наклоне", "тяга гантели одной рукой",
            "тяга гантели", "one arm dumbbell row",
        ],
    },
    "barbell_bent_over_row": {
        "canonical_ru": "Тяга штанги в наклоне",
        "muscle_group": "back",
        "aliases": [
            "тяга штанги в наклоне", "тяга штанги", "barbell row", "bent over row",
        ],
    },
    "cable_pullover": {
        "canonical_ru": "Пуловер в кроссовере",
        "muscle_group": "back",
        "aliases": [
            "пуловер в кроссовере", "пуловер на блоке",
            "пуловер в блоке", "тяга прямыми руками", "cable pullover",
        ],
    },
    "deadlift": {
        "canonical_ru": "Становая тяга",
        "muscle_group": "posterior_chain",
        "aliases": ["становая тяга", "становая", "deadlift"],
    },
    "romanian_deadlift": {
        "canonical_ru": "Румынская тяга",
        "muscle_group": "posterior_chain",
        "aliases": ["румынская тяга", "румынка", "romanian deadlift", "rdl"],
    },
    "back_extension": {
        "canonical_ru": "Гиперэкстензия",
        "muscle_group": "posterior_chain",
        "aliases": ["гиперэкстензия", "гиперы", "back extension", "hyperextension"],
    },

    # Shoulders
    "seated_dumbbell_press": {
        "canonical_ru": "Жим гантелей сидя",
        "muscle_group": "shoulders",
        "aliases": [
            "жим гантелей сидя", "жим гантелей на плечи", "жим на плечи",
            "жим сидя", "жим гантелей сидя на плечи",
            "seated dumbbell press", "shoulder dumbbell press",
        ],
    },
    "barbell_overhead_press": {
        "canonical_ru": "Жим штанги стоя",
        "muscle_group": "shoulders",
        "aliases": [
            "жим штанги стоя", "армейский жим", "жим стоя",
            "overhead press", "military press",
        ],
    },
    "machine_shoulder_press": {
        "canonical_ru": "Жим на плечи в тренажёре",
        "muscle_group": "shoulders",
        "aliases": [
            "жим на плечи в тренажере", "жим на плечи в тренажёре",
            "machine shoulder press",
        ],
    },
    "lateral_raise_standing": {
        "canonical_ru": "Махи на среднюю дельту стоя",
        "muscle_group": "shoulders",
        "aliases": [
            "махи на среднюю дельту стоя", "махи в стороны стоя",
            "разведение гантелей в стороны стоя", "разводка в стороны стоя",
            "lateral raise standing",
        ],
    },
    "lateral_raise_seated": {
        "canonical_ru": "Махи на среднюю дельту сидя",
        "muscle_group": "shoulders",
        "aliases": [
            "махи на среднюю дельту сидя", "махи в стороны сидя",
            "разведение гантелей в стороны сидя", "разводка гантелей в сторону",
            "махи", "lateral raise seated",
        ],
    },
    "front_dumbbell_raise": {
        "canonical_ru": "Фронтальный подъём гантелей",
        "muscle_group": "shoulders",
        "aliases": [
            "фронтальный подъем гантелей", "фронтальный подъём гантелей",
            "подъем гантелей перед собой", "подъём гантелей перед собой",
            "front dumbbell raise", "front raise",
        ],
    },
    "rear_delt_raise_seated": {
        "canonical_ru": "Махи на заднюю дельту сидя",
        "muscle_group": "shoulders",
        "aliases": [
            "махи на заднюю дельту сидя", "задняя дельта сидя",
            "rear delt raise seated",
        ],
    },
    "rear_delt_cable_fly": {
        "canonical_ru": "Задняя дельта в кроссовере",
        "muscle_group": "shoulders",
        "aliases": [
            "задняя дельта в кроссовере", "задняя дельта в блоке",
            "rear delt cable fly",
        ],
    },
    "face_pull": {
        "canonical_ru": "Тяга к лицу",
        "muscle_group": "shoulders",
        "aliases": ["тяга к лицу", "face pull", "фейс пул"],
    },
    "arnold_press": {
        "canonical_ru": "Жим Арнольда",
        "muscle_group": "shoulders",
        "aliases": ["жим арнольда", "arnold press"],
    },

    # Legs
    "barbell_squat": {
        "canonical_ru": "Приседания со штангой",
        "muscle_group": "legs",
        "aliases": ["приседания со штангой", "присед", "приседания", "barbell squat", "squat"],
    },
    "front_squat": {
        "canonical_ru": "Фронтальный присед",
        "muscle_group": "legs",
        "aliases": ["фронтальный присед", "front squat"],
    },
    "hack_squat": {
        "canonical_ru": "Гакк-присед",
        "muscle_group": "legs",
        "aliases": ["гакк присед", "гакк-присед", "hack squat"],
    },
    "leg_press": {
        "canonical_ru": "Жим ногами",
        "muscle_group": "legs",
        "aliases": ["жим ногами", "leg press"],
    },
    "leg_press_high_feet": {
        "canonical_ru": "Жим ногами высокая постановка ног",
        "muscle_group": "legs",
        "aliases": [
            "жим ногами высокая постановка", "жим ногами высокая постановка ног",
            "leg press high feet",
        ],
    },
    "leg_press_standard_feet": {
        "canonical_ru": "Жим ногами стандартная постановка ног",
        "muscle_group": "legs",
        "aliases": [
            "жим ногами стандартная постановка", "жим ногами стандартная постановка ног",
        ],
    },
    "reverse_lunge_dumbbells": {
        "canonical_ru": "Выпады назад с гантелями",
        "muscle_group": "legs",
        "aliases": [
            "выпады назад с гантелями", "обратные выпады с гантелями",
            "reverse lunge dumbbells",
        ],
    },
    "bulgarian_split_squat": {
        "canonical_ru": "Болгарские выпады",
        "muscle_group": "legs",
        "aliases": ["болгарские выпады", "болгарский сплит присед", "bulgarian split squat"],
    },
    "lying_leg_curl": {
        "canonical_ru": "Сгибание ног лёжа",
        "muscle_group": "legs",
        "aliases": ["сгибание ног лежа", "сгибание ног лёжа", "lying leg curl"],
    },
    "seated_leg_curl": {
        "canonical_ru": "Сгибание ног сидя",
        "muscle_group": "legs",
        "aliases": ["сгибание ног сидя", "seated leg curl"],
    },
    "leg_extension": {
        "canonical_ru": "Разгибание ног сидя",
        "muscle_group": "legs",
        "aliases": ["разгибание ног сидя", "разгибание ног", "leg extension"],
    },
    "hip_adduction_machine": {
        "canonical_ru": "Сведение ног в тренажёре",
        "muscle_group": "legs",
        "aliases": [
            "сведение ног", "сведение ног в тренажере", "сведение ног в тренажёре",
            "hip adduction",
        ],
    },
    "hip_abduction_machine": {
        "canonical_ru": "Отведение ног в тренажёре",
        "muscle_group": "legs",
        "aliases": [
            "отведение ног", "отведение ног в тренажере", "отведение ног в тренажёре",
            "hip abduction",
        ],
    },
    "standing_calf_raise": {
        "canonical_ru": "Икры стоя",
        "muscle_group": "calves",
        "aliases": ["икры стоя", "подъем на икры стоя", "standing calf raise"],
    },
    "seated_calf_raise": {
        "canonical_ru": "Икры сидя",
        "muscle_group": "calves",
        "aliases": ["икры сидя", "seated calf raise"],
    },
    "leg_press_calf_raise": {
        "canonical_ru": "Икры в жиме ногами",
        "muscle_group": "calves",
        "aliases": ["икры в жиме ногами", "икры в жиме", "calf raise leg press"],
    },
    "hip_thrust": {
        "canonical_ru": "Ягодичный мост / Hip Thrust",
        "muscle_group": "glutes",
        "aliases": ["ягодичный мост", "hip thrust", "хип траст"],
    },

    # Biceps
    "standing_dumbbell_curl": {
        "canonical_ru": "Бицепс с гантелями стоя",
        "muscle_group": "biceps",
        "aliases": [
            "бицепс с гантелями стоя", "подъем гантелей на бицепс стоя",
            "standing dumbbell curl",
        ],
    },
    "seated_dumbbell_curl": {
        "canonical_ru": "Бицепс с гантелями сидя",
        "muscle_group": "biceps",
        "aliases": [
            "бицепс с гантелями сидя", "подъем гантелей на бицепс сидя",
            "seated dumbbell curl",
        ],
    },
    "standing_barbell_curl": {
        "canonical_ru": "Бицепс со штангой стоя",
        "muscle_group": "biceps",
        "aliases": ["бицепс со штангой стоя", "подъем штанги на бицепс", "barbell curl"],
    },
    "hammer_curl": {
        "canonical_ru": "Молот стоя",
        "muscle_group": "biceps",
        "aliases": ["молот стоя", "молот", "молотки", "hammer curl"],
    },
    "preacher_curl": {
        "canonical_ru": "Скамья Скотта",
        "muscle_group": "biceps",
        "aliases": ["скамья скотта", "бицепс на скамье скотта", "preacher curl"],
    },
    "cable_biceps_curl": {
        "canonical_ru": "Сгибание верхнего блока на бицепс",
        "muscle_group": "biceps",
        "aliases": [
            "сгибание верхнего блока на бицепс", "бицепс на блоке",
            "cable biceps curl",
        ],
    },
    "incline_dumbbell_curl": {
        "canonical_ru": "Подъём гантелей на бицепс 45°",
        "muscle_group": "biceps",
        "aliases": [
            "подъем гантелей на бицепс 45", "подъём гантелей на бицепс 45",
            "incline dumbbell curl",
        ],
    },

    # Triceps
    "cable_triceps_pushdown": {
        "canonical_ru": "Разгибание верхнего блока на трицепс",
        "muscle_group": "triceps",
        "aliases": [
            "разгибание верхнего блока на трицепс", "разгибание рук на блоке",
            "трицепс на блоке", "cable triceps pushdown", "triceps pushdown",
        ],
    },
    "overhead_dumbbell_triceps_extension": {
        "canonical_ru": "Разгибание рук с гантелью из-за головы",
        "muscle_group": "triceps",
        "aliases": [
            "разгибание рук с гантелью из-за головы",
            "гантель из-за головы на трицепс",
            "overhead dumbbell triceps extension",
        ],
    },
    "close_grip_dips": {
        "canonical_ru": "Брусья узко",
        "muscle_group": "triceps",
        "aliases": ["брусья узко", "узкие брусья", "close grip dips"],
    },
    "dips_chest": {
        "canonical_ru": "Брусья на грудь",
        "muscle_group": "chest",
        "aliases": ["брусья на грудь", "широкие брусья", "dips chest"],
    },
    "close_grip_bench_press": {
        "canonical_ru": "Жим узким хватом",
        "muscle_group": "triceps",
        "aliases": ["жим узким хватом", "close grip bench press"],
    },
    "skull_crusher": {
        "canonical_ru": "Французский жим",
        "muscle_group": "triceps",
        "aliases": ["французский жим", "skull crusher"],
    },
    "rope_triceps_pushdown": {
        "canonical_ru": "Разгибание рук с канатом",
        "muscle_group": "triceps",
        "aliases": ["разгибание рук с канатом", "трицепс канат", "rope triceps pushdown"],
    },

    # Abs / cardio
    "v_up": {
        "canonical_ru": "Пресс V-складка",
        "muscle_group": "abs",
        "aliases": ["пресс v складка", "пресс v-складка", "v up"],
    },
    "crunch_bent_knees": {
        "canonical_ru": "Пресс подъёмы корпуса, ноги согнуты",
        "muscle_group": "abs",
        "aliases": [
            "пресс подъемы корпуса ноги согнуты", "пресс подъёмы корпуса ноги согнуты",
            "подъемы корпуса", "подъёмы корпуса", "crunch",
        ],
    },
    "leg_raise": {
        "canonical_ru": "Пресс подъёмы ног",
        "muscle_group": "abs",
        "aliases": ["пресс подъемы ног", "пресс подъёмы ног", "подъем ног", "leg raise"],
    },
    "plank": {
        "canonical_ru": "Планка",
        "muscle_group": "core",
        "aliases": ["планка", "plank"],
    },
    "bike": {
        "canonical_ru": "Велосипед",
        "muscle_group": "cardio",
        "aliases": ["велосипед", "bike", "exercise bike", "кардио велосипед"],
    },
    "treadmill": {
        "canonical_ru": "Беговая дорожка",
        "muscle_group": "cardio",
        "aliases": ["беговая дорожка", "дорожка", "treadmill"],
    },
    "elliptical": {
        "canonical_ru": "Эллипс",
        "muscle_group": "cardio",
        "aliases": ["эллипс", "эллипсоид", "elliptical"],
    },
}


def clean_text(value: str | None) -> str:
    text = (value or "").lower().replace("ё", "е")
    text = re.sub(r"[^\w\s\-+]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


@lru_cache(maxsize=1)
def alias_index() -> dict[str, str]:
    index = {}
    for key, data in EXERCISE_LIBRARY.items():
        index[clean_text(data["canonical_ru"])] = key
        index[clean_text(key)] = key
        for alias in data.get("aliases", []):
            index[clean_text(alias)] = key
    return index


def normalize_exercise_name(name: str | None, context: dict | None = None) -> dict:
    cleaned = clean_text(name)

    if not cleaned:
        return {
            "exercise_key": None,
            "canonical_ru": name or "",
            "confidence": 0.0,
            "source": "empty",
        }

    index = alias_index()

    if cleaned in index:
        key = index[cleaned]
        item = EXERCISE_LIBRARY[key]
        return {
            "exercise_key": key,
            "canonical_ru": item["canonical_ru"],
            "confidence": 1.0,
            "source": "exact_alias",
            "muscle_group": item.get("muscle_group"),
        }

    sorted_aliases = sorted(index.keys(), key=len, reverse=True)
    for alias in sorted_aliases:
        if len(alias) < 4:
            continue
        if alias in cleaned or cleaned in alias:
            key = index[alias]
            item = EXERCISE_LIBRARY[key]
            return {
                "exercise_key": key,
                "canonical_ru": item["canonical_ru"],
                "confidence": 0.82,
                "source": "contains_alias",
                "muscle_group": item.get("muscle_group"),
            }

    if context:
        current_key = context.get("current_exercise_key")
        current_name = context.get("current_exercise")

        if current_key and any(word in cleaned for word in ["жим", "тяга", "махи", "подъем", "подъём"]):
            item = EXERCISE_LIBRARY.get(current_key)
            if item:
                return {
                    "exercise_key": current_key,
                    "canonical_ru": item["canonical_ru"],
                    "confidence": 0.72,
                    "source": "active_context",
                    "muscle_group": item.get("muscle_group"),
                }

        if current_name and cleaned in clean_text(current_name):
            normalized = normalize_exercise_name(current_name)
            normalized["source"] = "current_exercise_name"
            return normalized

    return {
        "exercise_key": None,
        "canonical_ru": name or cleaned,
        "confidence": 0.0,
        "source": "unknown",
    }


def get_exercise_title(exercise_key: str | None, fallback: str | None = None) -> str:
    if exercise_key and exercise_key in EXERCISE_LIBRARY:
        return EXERCISE_LIBRARY[exercise_key]["canonical_ru"]
    return fallback or exercise_key or "Упражнение"


def possible_matches(query: str, limit: int = 8) -> list[dict]:
    cleaned = clean_text(query)
    if not cleaned:
        return []

    matches = []
    for key, item in EXERCISE_LIBRARY.items():
        haystack = clean_text(" ".join([item["canonical_ru"]] + item.get("aliases", [])))
        if cleaned in haystack or any(part and part in haystack for part in cleaned.split()):
            matches.append({
                "exercise_key": key,
                "canonical_ru": item["canonical_ru"],
                "muscle_group": item.get("muscle_group"),
            })

    return matches[:limit]
