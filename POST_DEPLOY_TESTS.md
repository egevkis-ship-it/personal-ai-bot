# Post-deploy smoke tests

После каждого Redeploy проверить в Telegram:

1. /status
   Ожидаемо: бот отвечает, PostgreSQL healthy, Redis healthy.

2. /fitness_debug_week
   Ожидаемо: бот отвечает, не падает.

3. /week_plan
   Ожидаемо: cancelled не отображаются.

4. Перенеси спину на субботу
   Ожидаемо: fitness-модуль, не task.

5. Перенеси встречу на субботу
   Ожидаемо: не fitness.

6. /fitness_reset_week
   Ожидаемо: отвечает текстом, не молчит.
