# TOTAL AUDIT — 2026-08-17 (workflow wa9igpsk8, 45 agents)

Deep multi-agent audit of the "workout sometimes does not save" bug + all
other correctness/reliability/security bugs. 31 confirmed findings after
adversarial verification (7 high · 8 medium · 15 low · 1 nit). Full details:
`tasks/wa9igpsk8.output`. Status: [ ] todo · [x] fixed · [~] partial.

## BATCH A — save-loss / offline reliability (web/app.js, web/sw.js) — DEPLOY FIRST
✅ DONE — verified in real browser (poison-pill drop 2→0; idempotent set save; finish→history). sw v60.

- [x] A1 (#1/#5 HIGH) flushQueue head-of-line: a poison-pill op (404 for a
      deleted/cancelled workout) jams the whole queue → every later workout
      can never finish → never appears in History. Fix: classify errors —
      drop non-retryable 4xx (not 401/408/429), keep transient (network/401/5xx);
      retrySync Login() only on real 401; purge queue for a wid on delWorkout.
- [x] A2 (#6 HIGH) _qPut swallows IndexedDB failures (oncomplete==onerror) →
      set lost while UI says "queued". Fix: _qPut rejects on error; submit paths
      only mark pending on a durable write, else hard-error toast.
- [x] A3 (#2/#4 HIGH) confirmText (free-text/voice, in EVERY accordion) drops
      sets on any error + no idempotency + double-tap dup. Fix: client_op_id,
      clear input before await, queue on error + banner, mirror submitSets.
- [x] A4 (#3 HIGH) Double-tap "✓ Записать" double-submits (confirmSets not
      re-entrant, fresh op_id each call). Fix: _savingSets guard + disable btn;
      mint client_op_id once per set-entry session (server dedups replays).
- [x] A5 (#12 MED) Offline re-entry double-displays optimistic sets. Fix: strip
      _pending before saveActiveCache (overlayQueue is the single overlay source).
- [x] A6 (#17 LOW) overlayQueue mishandles multi-set ops → phantom "—" set.
      Fix: expand body.sets[] in overlayQueue like the in-session path.
- [x] A7 (#21 LOW) sw caches non-OK responses → can serve cached error page.
      Fix: cache only r.ok. **Bump sw version here (last frontend change of batch).**

## BATCH B — active-workout state + UX-adjacent (web/app.js)

- [ ] B1 (#11 MED) Added 0-set exercise vanishes on next refetch. Fix: carry
      set-less non-plan additions across renderActive refetch. (also UX pain)
- [ ] B2 (#10 MED) activeExpanded is positional → wrong exercise after a delete.
      Fix: track expanded by canonical name, derive index in renderActive.
- [ ] B3 (#29 LOW) Typed-but-unsaved rows wiped on re-render (toggleDone/online).
      Fix: snapshot _readSetRows before re-render, restore if same exercise.
- [ ] B4 (#8 MED) In-set count-up timer leaks + records inflated set on collapse.
      Fix: stopTimer() at top of renderActive + reset TMR_VAL + self-clean interval.
- [ ] B5 (#27 LOW) toggleDone auto-expands first-undone, not next-after-idx.
      Fix: findIndex i>idx first, then fallback.
- [ ] B6 (#19 LOW) freeWorkout/startFromPlan/repeatLast swallow errors (offline/
      401/5xx → silent no-op). Fix: try/catch + toast + 401→login.
- [ ] B7 (#20 LOW) Reload mid-workout → Home, accordion collapsed. Fix: persist
      tiny nav state (activeId/activeExpanded), resume on boot.
- [ ] B8 (#28 LOW) Cross-month week shows duplicate divider. Fix: clamp week label to month.
- [ ] B9 (#22 LOW) XSS: coachPreview interpolates exLine(ex) without esc(). Fix: esc().

## BATCH C — backend correctness (api/main.py, app/db, ai_parser, tz, catalog, schema)

- [ ] C1 (#16 LOW) History hides 0-working+empty-focus finished workout. Fix:
      narrow rest-day HAVING to explicit "%отдых%" (not empty focus).
- [ ] C2 (#18 LOW) History upper-clamp to today hides post-westward-tz workouts.
      Fix: drop the "w.workout_date <= :td" upper bound (finished_at scopes it).
- [ ] C3 (#9 MED) Trailing-qualifier strip misfiles стоя/сидя/лежа/хватом variants
      onto a different catalog key (Французский жим стоя → skullcrusher). Fix:
      don't strip distinguishing qualifiers (or guard against a sibling key).
- [ ] C4 (#13 MED) parse_plan_text crashes on malformed-but-valid model JSON.
      Fix: isinstance guards mirroring parse_logged_workouts_text.
- [ ] C5 (#14 MED) parse_set_text_ai returns unvalidated items → non-dict crashes
      the set-logging handler. Fix: coerce/validate to list[dict].
- [ ] C6 (#30 LOW) Dead Whisper key returns 200 "" and burns AI quota. Fix:
      transcribe_voice raises on failure; count only on a real result.
- [ ] C7 (#23 LOW) Archive create/bulk lack list caps + weight/reps range guards.
      Fix: Field(ge/le) + max_length; also AddSet bounds.
- [ ] C8 (#24 LOW) Legacy-rename migration skips workout_exercise_notes/done →
      PDF export drops notes. Fix: rename those two tables in the same tx.
- [ ] C9 (#31 NIT) SQL renames chain sequentially vs JSONB once (latent; no chain
      in current 49-entry map). Fix: single-pass VALUES map, or reject chains at load.
- [ ] C10 (#25 LOW) tz cache never invalidated cross-process. Fix: 60s TTL.
- [ ] C11 (#26 LOW) set_number MAX+1 race → duplicate set_number. Fix: pg_advisory_xact_lock + unique constraint (new migration).
- [ ] C12 (#7 HIGH) exercise_aliases GLOBAL (no user_id, first-writer-wins) →
      one user's alias rewrites another's logged exercise names. Fix: add user_id +
      UNIQUE(user_id, alias_clean), thread uid through resolve/register, migrate.
- [ ] C13 (#15 MED) Telegram bot bypasses the per-user AI daily cap entirely. Fix:
      shared usage-gate module, call before every bot AI invocation; web side count-on-success.

## BATCH D — mid-workout editing UX rework (tasks #3/#4) — Egor's explicit ask

- [ ] D1 Remove exercise mid-workout: workout_exercise_removed marker table +
      DELETE endpoint (sets+note+done+plan-hide); assemble_workout filters removed;
      add_set clears marker on re-add. Confirm sheet if it has sets.
- [ ] D2 Replace/swap exercise (remove + pick new, keeps flow).
- [ ] D3 Reorder exercises (up/down, persisted order).
- [ ] D4 Easier/adjacent-add exercise; per-exercise "···" menu in the accordion.
