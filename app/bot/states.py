"""FSM state groups for the fitness bot."""
from aiogram.fsm.state import State, StatesGroup


class WorkoutStates(StatesGroup):
    # Active workout session
    active = State()              # default: waiting for set input
    confirm_set = State()         # waiting for confirm/cancel after parse
    confirm_finish = State()      # confirm ending the workout
    enter_missing_reps = State()  # set parsed without reps/duration — ask reps
    enter_set_note = State()      # waiting for text comment to last set
    enter_workout_note = State()  # post-finish: optional workout-level comment

    # confirm new (unknown) exercises before set save
    confirm_new_exercise = State()    # user accepts/renames AI-suggested canonical
    enter_new_exercise_name = State() # user types correct canonical name

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


class MeasurementStates(StatesGroup):
    enter_input = State()         # waiting for free-text or voice
    confirm = State()             # showing parsed values, confirm or fill
    fill_field = State()          # asking for one specific missing field


class PhotoStates(StatesGroup):
    waiting_photo = State()       # user pressed "New photo", waiting for upload
    waiting_note = State()        # optional note after upload


class ReportStates(StatesGroup):
    waiting_custom_range = State()  # custom from-to date
