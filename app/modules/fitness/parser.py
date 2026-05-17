from __future__ import annotations
import json
from datetime import date

from app.ai import claude_client
from app.modules.fitness.utils import week_bounds, next_week_bounds


def safe_json_loads(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= 0:
            return json.loads(text[start:end + 1])
        raise


async def parse_fitness_action(text: str) -> dict:
    today = date.today().isoformat()
    this_week_start, this_week_end = week_bounds()
    next_week_start, next_week_end = next_week_bounds()

    system_prompt = f"""
Ты fitness-router и parser персонального фитнес-ассистента Егора.

Сегодня: {today}
Текущая неделя: {this_week_start} — {this_week_end}
Следующая неделя: {next_week_start} — {next_week_end}

Определи действие пользователя и верни СТРОГО JSON.

Возможные action:
- create_plan
- get_today_workout
- get_next_workout
- get_week_plan
- get_focus_workout
- log_workout
- log_measurement
- skip_workout
- change_plan
- unknown

Формат ответа:
{{
  "action": "...",
  "confidence": 0.0,
  "date": "{today}",
  "period": {{
    "period_type": "week | month | custom | null",
    "start_date": null,
    "end_date": null
  }},
  "query": {{
    "focus": null,
    "focus_label": null,
    "sequence_number": null
  }},
  "plan": {{
    "plan_name": null,
    "period_type": null,
    "start_date": null,
    "end_date": null,
    "notes": null,
    "planned_workouts": [
      {{
        "planned_date": null,
        "weekday": null,
        "sequence_number": 1,
        "is_floating": true,
        "title": "Тренировка 1 — грудь",
        "focus": "chest",
        "focus_label": "грудь",
        "workout_type": "planned",
        "status": "planned",
        "notes": null,
        "exercises": [
          {{
            "exercise_order": 1,
            "exercise_name": "Жим лёжа",
            "target_sets": 4,
            "target_reps_min": 8,
            "target_reps_max": 10,
            "target_reps_text": "8-10",
            "target_weight_kg": null,
            "notes": null
          }}
        ]
      }}
    ]
  }},
  "completed_workout": {{
    "workout_date": "{today}",
    "workout_type": null,
    "focus": null,
    "focus_label": null,
    "bodyweight_kg": null,
    "completion_type": "custom",
    "notes": null,
    "exercises": [
      {{
        "name": "Жим лёжа",
        "sets": [
          {{
            "set_number": 1,
            "weight_kg": 80,
            "reps": 10,
            "rpe": null,
            "notes": null
          }}
        ]
      }}
    ]
  }},
  "body_measurements": {{
    "measurement_date": "{today}",
    "weight_kg": null,
    "waist_cm": null,
    "chest_cm": null,
    "hips_cm": null,
    "arm_cm": null,
    "thigh_cm": null,
    "neck_cm": null,
    "notes": null
  }},
  "summary": "краткое резюме"
}}

Правила определения action:

1. create_plan:
Если пользователь говорит "план", "запиши тренировки на неделю/месяц", "на следующую неделю 4 тренировки",
"понедельник грудь, среда спина..." — это create_plan.
Это НЕ факт выполнения.

2. get_today_workout:
"дай сегодняшнюю тренировку", "что сегодня по плану", "что у меня сегодня".

3. get_next_workout:
"дай следующую тренировку", "что дальше", "следующая тренировка".

4. get_week_plan:
"покажи план недели", "что запланировано на неделю", "что осталось на этой неделе".

5. get_focus_workout:
"дай грудь", "дай тренировку на грудь", "покажи спину", "что у меня на ноги".

6. log_workout:
"сделал", "выполнил", "потренил", "тренировка была", "сегодня грудь: жим 80 на 10..." — факт.
Если перечисляются упражнения с весами/повторениями без слова план — чаще всего log_workout.

7. log_measurement:
"вес утром 86.4", "талия 91", "замеры" без упражнений.

8. skip_workout:
"пропустил", "не буду тренироваться", "отметь пропуск".

9. change_plan:
"перенеси", "поменяй местами", "замени", "вместо ног поставь грудь".
В этом релизе только распознавай, но детальную структуру можно кратко положить в summary.

Правила дат:
- Если сказано "на этой неделе" — period start/end = текущая неделя.
- Если сказано "на следующей неделе" — period start/end = следующая неделя.
- Если сказан конкретный день недели, вычисли дату в указанной неделе.
- Если дни не названы, planned_date=null и is_floating=true.
- Если дни названы, is_floating=false.
- Если план на месяц, создай конкретные planned_workouts на весь месяц, если возможно.
- Если не уверен с месяцем, period_type="month", но создай хотя бы шаблонные planned_workouts.

Правила упражнений:
- "4 по 8-10" = target_sets 4, reps_min 8, reps_max 10, reps_text "8-10".
- "3 по 12" = target_sets 3, reps_min 12, reps_max 12.
- "по максимуму" = target_reps_text "максимум".
- "80 на 10" в факте = weight_kg 80, reps 10.
- "16 по 12 три подхода" в факте = 3 sets 16×12.
- Подтягивания 10, 9, 8 = 3 sets, weight_kg=null.

Фокусы нормализуй:
- грудь = chest
- спина = back
- плечи = shoulders
- ноги = legs
- руки = arms
- пресс = abs
- кардио = cardio
- фулбади = full_body

Ответ только JSON. Без markdown.
"""

    response = await claude_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": text}],
    )

    return safe_json_loads(response.content[0].text if response.content else "{}")
