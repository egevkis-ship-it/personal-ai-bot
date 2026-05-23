"""FSM state groups for the fitness bot."""
from aiogram.fsm.state import State, StatesGroup


class WorkoutStates(StatesGroup):
    # Active workout session
    active = State()              # default: waiting for set input
    confirm_set = State()         # waiting for confirm/cancel after parse
    confirm_finish = State()      # confirm ending the workout
    enter_set_note = State()      # waiting for text comment to last set
    enter_workout_note = State()  # post-finish: optional workout-level comment

    # post-finish AI planning flow
    plan_next_mode = State()      # manual / AI
    ai_plan_period = State()      # 1 day / week
    ai_plan_pick_day = State()    # pick a date next week
    ai_plan_confirm = State()     # confirm generated plan


class PlanStates(StatesGroup):
    # Plan loading flow
    choose_load_mode = State()    # text-paste OR manual entry
    paste_text = State()          # waiting for plan text paste
    confirm_parsed = State()      # confirm AI-parsed days
    choose_dates = State()        # assign parsed days to real dates
    manual_day = State()          # manually entering a day's exercises
    manual_exercise = State()     # adding one exercise manually

    # Plan management (browse / edit)
    browsing = State()            # browsing existing plan


class HistoryStates(StatesGroup):
    browsing = State()            # browsing history list
    choose_export = State()       # text vs CSV
