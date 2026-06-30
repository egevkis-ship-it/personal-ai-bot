// ── tiny helpers ─────────────────────────────────────────────────────────
const view = document.getElementById('view');
async function api(path, method = 'GET', body) {
  const opt = { method, headers: {}, credentials: 'include' };
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch('/api' + path, opt);
  if (r.status === 401) { const e = new Error('unauthorized'); e.code = 401; e.status = 401; throw e; }
  if (!r.ok) {
    let j = null; try { j = await r.json(); } catch {}
    const e = new Error((j && j.detail) || r.status); e.status = r.status; e.body = j; throw e;
  }
  const t = await r.text(); return t ? JSON.parse(t) : null;
}
const fmt = n => (n == null ? '' : (Math.round(n * 100) / 100).toString());
const esc = s => (s || '').replace(/[&<>"'`]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;', '`': '&#96;' }[c]));
function toast(t) { const d = document.createElement('div'); d.className = 'toast'; d.textContent = t; document.body.appendChild(d); setTimeout(() => d.remove(), 1800); }
// UX3-2: swipe-left-to-delete wrapper (CSS scroll-snap). `inner` = row content;
// `delExpr` = delete onclick (same as the desktop 🗑 kept inside `inner`);
// `openExpr` (optional) = the body's tap action. Use only single-quote exprs.
function swipeRow(inner, delExpr, openExpr) {
  const o = openExpr ? ` onclick="${openExpr}"` : '';
  return `<div class="swipe"><div class="swipe-body"${o}>${inner}</div><div class="swipe-del" onclick="${delExpr}">Удалить</div></div>`;
}
// DB-2: exercise thumbnail (Free Exercise DB image) with a placeholder fallback.
function _exThumb(image) {
  return `<div class="ex-thumb"><span class="ex-ph">🏋️</span>${image ? `<img src="${esc(image)}" loading="lazy" onerror="this.remove()">` : ''}</div>`;
}
function mmss(sec) { const m = Math.floor(sec / 60), s = sec % 60; return m + ':' + String(s).padStart(2, '0'); }
function plural(n, one, few, many) { const a = Math.abs(n) % 100, b = a % 10; if (a > 10 && a < 20) return many; if (b > 1 && b < 5) return few; if (b === 1) return one; return many; }
let STATE = { tab: 'home' };

// iOS sticky compact title: reveal the blurred top bar with the screen title on scroll.
(function navbarInit() {
  const nb = document.getElementById('navbar');
  if (!nb) return;
  let raf = 0;
  addEventListener('scroll', () => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const t = view.querySelector('h1, h2');
      if (t && scrollY > 52) { nb.textContent = t.textContent.trim(); nb.classList.add('show'); }
      else nb.classList.remove('show');
    });
  }, { passive: true });
})();

// ── navigation ───────────────────────────────────────────────────────────
const TABS = [['home', '🏠', 'Главная'], ['train', '🏋️', 'Тренировка'], ['measure', '📏', 'Замеры'], ['history', '📖', 'История']];
function renderTabs() {
  document.getElementById('tabbar').innerHTML = TABS.map(([k, i, l]) =>
    `<div class="tab ${STATE.tab === k ? 'active' : ''}" onclick="go('${k}')"><span class="i">${i}</span>${l}</div>`).join('');
}
async function go(tab, param) {
  STATE.tab = tab; renderTabs(); window.scrollTo(0, 0);
  const _nb = document.getElementById('navbar'); if (_nb) _nb.classList.remove('show');
  try {
    if (tab === 'login') return Login();
    if (tab === 'home') return Home();
    if (tab === 'train') return Train();
    if (tab === 'active') return Active(param);
    if (tab === 'measure') return Measure();
    if (tab === 'measureHistory') return MeasureHistory();
    if (tab === 'history') return History();
    if (tab === 'workout') return WorkoutDetail(param);
    if (tab === 'chooseDay') return ChooseDay();
    if (tab === 'plans') return Plans();
    if (tab === 'planView') return PlanView(param);
    if (tab === 'planEdit') return PlanEdit(param);
    if (tab === 'schedule') return Schedule();
    if (tab === 'settings') return Settings();
    if (tab === 'reports') return Reports();
    if (tab === 'photos') return Photos();
    if (tab === 'exercise') return ExerciseDetail(param);
    if (tab === 'routines') return Routines();
    if (tab === 'routineEdit') return RoutineEdit(param);
    if (tab === 'routineDay') return RoutineDay(param);
  } catch (e) {
    if (e.code === 401) { document.getElementById('tabbar').style.display = 'none'; return Login(); }
    view.innerHTML = `<div class="card">Ошибка: ${esc(e.message)}<br><span class="small muted">Сервер запущен?</span></div>`;
  }
}

// ── Login (Telegram widget) ───────────────────────────────────────────────
async function Login() {
  document.getElementById('tabbar').style.display = 'none';
  let cfg = {}; try { cfg = await api('/config'); } catch {}
  view.innerHTML = `<div style="text-align:center;padding:48px 16px">
    <div style="font-size:40px">🏋️</div>
    <h1 style="margin-top:10px">Дневник тренировок</h1>
    <p class="muted" style="margin:6px 0 28px">Войдите через Telegram, чтобы синхронизировать данные с ботом</p>
    <div id="tgbtn" style="display:flex;justify-content:center"></div>
    ${cfg.bot_username ? '' : '<p class="muted small" style="margin-top:18px">Бот не настроен (нет TELEGRAM_BOT_USERNAME)</p>'}
  </div>`;
  if (cfg.bot_username) {
    const s = document.createElement('script');
    s.async = true; s.src = 'https://telegram.org/js/telegram-widget.js?22';
    s.setAttribute('data-telegram-login', cfg.bot_username);
    s.setAttribute('data-size', 'large');
    s.setAttribute('data-onauth', 'onTelegramAuth(user)');
    s.setAttribute('data-request-access', 'write');
    document.getElementById('tgbtn').appendChild(s);
  }
}
window.onTelegramAuth = async function (user) {
  try {
    await api('/auth/telegram', 'POST', user);
    document.getElementById('tabbar').style.display = '';
    go('home');
  } catch (e) {
    const st = e.body && e.body.status;
    if (e.status === 403 && (st === 'pending' || st === 'blocked')) return AccessGate(st);
    toast(e.message || 'вход не удался');
  }
};
function AccessGate(kind) {
  document.getElementById('tabbar').style.display = 'none';
  const pending = kind === 'pending';
  view.innerHTML = `<div style="text-align:center;padding:64px 20px">
    <div style="font-size:46px">${pending ? '⏳' : '🚫'}</div>
    <h1 style="margin-top:12px">${pending ? 'Заявка отправлена' : 'Доступ ограничен'}</h1>
    <p class="muted" style="margin-top:8px">${pending
      ? 'Запрос на доступ отправлен администратору. Как только его одобрят — войдите снова.'
      : 'Доступ к приложению закрыт. Обратитесь к администратору.'}</p>
    <button class="btn ghost" style="margin:24px auto 0;max-width:200px" onclick="location.reload()">Обновить</button>
  </div>`;
}
async function logout() { try { await api('/auth/logout', 'POST'); } catch {} location.reload(); }

// ── Home ────────────────────────────────────────────────────────────────
async function Home() {
  const d = await api('/dashboard');
  const lm = d.last_measurement;
  let banner = '';
  if (d.active_workout) {
    banner = `<div class="banner warn"><div class="small" style="color:var(--warn)">⏸ Незавершённая тренировка</div>
      <div class="b-title" style="color:var(--warn)">${esc(d.active_workout.focus_label || 'Тренировка')}</div>
      <button class="btn" style="margin-top:9px;background:var(--warn)" onclick="go('active',${d.active_workout.id})">Продолжить</button>
      <button class="btn ghost" style="margin-top:8px;color:var(--danger)" onclick="cancelActiveFromHome(${d.active_workout.id})">✖ Отменить</button></div>`;
  } else if (d.today_plan) {
    banner = `<div class="banner info"><div class="small" style="color:var(--info)">📅 Сегодня по плану</div>
      <div class="b-title" style="color:var(--info)">${esc(d.today_plan.focus_label || '')} · ${(d.today_plan.exercises || []).length} упр.</div>
      <button class="btn" style="margin-top:9px" onclick="startFromPlan(${d.today_plan.id})">▶ Начать тренировку</button></div>`;
  } else {
    banner = `<div class="banner info"><div class="b-title" style="color:var(--info)">На сегодня плана нет</div>
      <button class="btn" style="margin-top:9px" onclick="go('train')">Начать тренировку</button></div>`;
  }
  const streak = d.streak || 0;
  const summaryCard = `<div class="card"><div class="row sp">
      <div><div class="muted small">На этой неделе</div>
        <div style="font-size:20px;font-weight:700;margin-top:2px">${d.week_workouts} ${plural(d.week_workouts, 'тренировка', 'тренировки', 'тренировок')}</div></div>
      <div style="text-align:right"><div class="muted small">тоннаж</div>
        <div style="font-size:18px;font-weight:700">${(d.week_tonnage || 0).toLocaleString('ru-RU')} <span class="small muted">кг</span></div></div></div>
    ${streak > 0 ? `<div class="pill ok" style="margin-top:8px">🔥 серия ${streak} ${plural(streak, 'неделя', 'недели', 'недель')}</div>` : ''}
    ${d.weekly_goal ? `<div class="small muted" style="margin-top:6px">Цель недели: ${Math.min(d.week_workouts, d.weekly_goal)}/${d.weekly_goal}${d.week_workouts >= d.weekly_goal ? ' ✅' : ''}</div>` : ''}</div>`;
  const trend = d.weight_trend || [];
  const last_w = trend.length ? trend[trend.length - 1].weight_kg : (lm ? lm.weight_kg : null);
  const delta = trend.length >= 2 ? Math.round((trend[trend.length - 1].weight_kg - trend[0].weight_kg) * 10) / 10 : null;
  const toGoal = (d.target_weight != null && last_w != null) ? Math.round((last_w - d.target_weight) * 10) / 10 : null;
  const weightCard = last_w != null ? `<div class="card" onclick="go('measure')" style="cursor:pointer">
    <div class="row sp"><span class="muted small">Вес${d.target_weight != null ? ` · цель ${fmt(d.target_weight)} кг` : ''}</span>
      <span class="small">${fmt(last_w)} кг${delta != null && delta !== 0 ? ` <span class="muted">(${delta > 0 ? '+' : ''}${fmt(delta)})</span>` : ''}</span></div>
    ${toGoal != null && toGoal !== 0 ? `<div class="small muted" style="margin-top:2px">до цели ${fmt(Math.abs(toGoal))} кг ${toGoal > 0 ? '↓' : '↑'}</div>` : (toGoal === 0 ? '<div class="small" style="margin-top:2px;color:var(--success)">цель достигнута 🎯</div>' : '')}
    ${trend.length >= 2 ? lineChart(trend.map(p => ({ label: shortDate(p.date), value: p.weight_kg })), 'var(--info)') : ''}</div>` : '';
  const prs = d.recent_prs || [];
  const prCard = prs.length ? `<div class="card"><div class="muted small" style="margin-bottom:4px">🏆 Свежие рекорды</div>
    ${prs.map(p => `<div class="row sp" style="padding:3px 0"><span>${esc(p.name)}</span><span class="small muted">${fmt(p.weight_kg)} кг · ${shortDate(p.date)}</span></div>`).join('')}</div>` : '';
  const np = d.next_plan;
  const nextCard = (np && !d.active_workout && !d.today_plan) ? `<div class="card list-item" onclick="go('schedule')"><div class="ic">🗓</div><div style="flex:1"><b>Ближайший план</b><div class="small muted">${esc(fmtDate(np.planned_date, { weekday: 'short' }))} · ${esc(np.focus_label || 'тренировка')}</div></div><span class="muted">›</span></div>` : '';
  const isNew = !d.last_workout && (d.week_workouts || 0) === 0 && !d.active_workout;
  const onboardCard = isNew ? `<div class="card" style="border:1px dashed var(--line)"><b>Добро пожаловать! 👋</b>
    <div class="small muted" style="margin-top:4px">Начни первую тренировку или запланируй неделю в «Планы». Записывай подходы — здесь появятся графики, рекорды и прогресс.</div></div>` : '';
  view.innerHTML = `<div class="row sp"><h1 style="font-size:22px;text-transform:capitalize">${fmtDate(todayISO(), { weekday: 'long' })}</h1><span style="font-size:24px;cursor:pointer;line-height:1" onclick="go('settings')" title="Настройки">⚙️</span></div>
    <div class="muted small" style="margin-bottom:14px">${streak > 0 ? `🔥 серия ${streak} ${plural(streak, 'неделя', 'недели', 'недель')} · ` : ''}${d.week_workouts} ${plural(d.week_workouts, 'тренировка', 'тренировки', 'тренировок')} на этой неделе</div>
    ${banner}
    ${onboardCard}
    ${summaryCard}
    ${nextCard}
    ${weightCard}
    ${prCard}
    <div class="muted small" style="margin:4px 0 8px">Быстрые действия</div>
    <div class="grid2">
      <div class="tile" onclick="go('measure')">📏<div class="small" style="margin-top:6px">Записать замер</div></div>
      <div class="tile" onclick="repeatLast(${d.last_workout?d.last_workout.id:0})">🔁<div class="small" style="margin-top:6px">Повторить прошлую</div></div>
      <div class="tile" onclick="go('train')">🏋️<div class="small" style="margin-top:6px">Тренировка</div></div>
      <div class="tile" onclick="go('history')">📖<div class="small" style="margin-top:6px">История</div></div>
    </div>`;
}
async function startFromPlan(pid) { const r = await api('/workouts', 'POST', { from_plan_id: pid }); go('active', r.id); }
async function repeatLast(id) { if (!id) return toast('Нет прошлых тренировок'); const r = await api('/workouts', 'POST', { repeat_from: id }); go('active', r.id); }

// ── Train (start) ─────────────────────────────────────────────────────────
async function Train() {
  const plan = await api('/plans/today');
  view.innerHTML = `<h1>Тренировка</h1><div class="muted small" style="margin-bottom:14px">Что тренируем?</div>
    ${plan ? `<div class="banner info" onclick="openPlan(${plan.id},'train')"><div class="small" style="color:var(--info)">📅 План на сегодня</div>
      <div class="b-title" style="color:var(--info)">${esc(plan.focus_label || '')} ›</div></div>` : ''}
    <div class="card list-item" onclick="go('chooseDay')"><div class="ic">🗓</div><div style="flex:1"><b>Другой день недели</b><div class="small muted">взять пропущенную</div></div><span class="muted">›</span></div>
    <div class="card list-item" onclick="freeWorkout()"><div class="ic">➕</div><div style="flex:1"><b>Свободная</b><div class="small muted">с нуля, без плана</div></div><span class="muted">›</span></div>
    <div class="muted small" style="margin:18px 0 8px">Планы и расписание</div>
    <div class="card list-item" onclick="go('plans')"><div class="ic">📝</div><div style="flex:1"><b>Запланировать</b><div class="small muted">создать план: день, неделя, AI или текстом</div></div><span class="muted">›</span></div>
    <div class="card list-item" onclick="go('schedule')"><div class="ic">📅</div><div style="flex:1"><b>Расписание</b><div class="small muted">посмотреть запланированное: день / неделя / месяц</div></div><span class="muted">›</span></div>`;
}
async function freeWorkout() { const r = await api('/workouts', 'POST', {}); go('active', r.id); }
async function ChooseDay() {
  const wk = await api('/workouts/week');
  const ic = s => s === 'completed' ? '✅' : s === 'skipped' ? '⚠️' : '⚪️';
  view.innerHTML = `<span class="back" onclick="go('train')">‹ Назад</span><h2>Тренировки недели</h2>
    ${wk.length ? wk.map(p => `<div class="card list-item ex-row" onclick="openPlan(${p.id},'chooseDay')">
      <div class="ic">${ic(p.status)}</div><div style="flex:1"><b>${esc(p.focus_label || '')}</b>
      <div class="small muted">${esc(fmtDate(p.planned_date, { weekday: 'short' }))}${p.is_today ? ' · сегодня' : ''}${p.status === 'skipped' ? ' · пропущено' : ''}</div></div><span class="muted">›</span></div>`).join('')
      : '<div class="card muted">На этой неделе нет планов.</div>'}`;
}

// ── Active workout ────────────────────────────────────────────────────────
async function Active(id) {
  let w;
  if (!window._SETTINGS) { try { window._SETTINGS = await api('/settings'); _cacheSettings(); } catch { window._SETTINGS = _cachedSettings() || window._SETTINGS; } }  // rest-timer prefs
  try { w = id ? await api('/workouts/' + id) : await api('/workouts/active'); }
  catch (e) {
    if (isNetworkErr(e)) { w = loadActiveCache(id); if (w) { await overlayQueue(w); return renderActive(w); } }
    throw e;  // 401 / real errors handled by go()
  }
  if (!w) return go('train');
  await overlayQueue(w);     // show any not-yet-synced offline sets
  renderActive(w);
}
function renderActive(w) {
  STATE.activeId = w.id; window._WO = w; saveActiveCache(w);
  const items = w.exercises.map((ex, i) => {
    const working = ex.sets.filter(s => !s.is_warmup);
    const done = ex.done;
    const next = !done && working.length >= 0 && i === w.exercises.findIndex(e => !e.done);
    const sub = working.length ? working.map(s => setLabel(s)).join(' · ')
      : (ex.target ? `цель ${ex.target_sets || ''}×${ex.target.reps || (ex.target.duration_seconds ? mmss(ex.target.duration_seconds) : '')}${ex.target.weight_kg ? ' · ' + fmt(ex.target.weight_kg) : ''}` : 'нет подходов');
    return `<div class="card list-item ex-row" style="${next ? 'border:2px solid var(--info)' : ''}" onclick="openExercise(${w.id},${i})">
      <div class="ic">${done ? '✅' : next ? '▶️' : '⚪️'}</div>
      <div style="flex:1"><b>${esc(ex.name)}</b><div class="small muted">${esc(sub)}</div></div>
      <span class="muted" style="padding:4px 8px;cursor:pointer" title="Прогресс упражнения" onclick="event.stopPropagation();exDetailIdx(${i})">📈</span></div>`;
  }).join('');
  view.innerHTML = `<div class="row sp"><span class="back" onclick="go('home')">‹ Главная</span><span class="muted small" onclick="workoutMenu(${w.id})" style="cursor:pointer">···</span></div>
    <h2 style="margin-bottom:2px">${esc(w.focus_label || 'Тренировка')}</h2>
    <div class="muted small" style="margin-bottom:12px">${navigator.onLine ? 'идёт' : '⚠️ оффлайн — подходы сохранятся при сети'}</div>
    ${items || '<div class="card muted">Пусто</div>'}
    <button class="btn ghost" style="margin-top:6px" onclick="openPicker(${w.id})">➕ Добавить упражнение</button>
    <button class="btn ghost" style="margin-top:8px" onclick="restTimer(restSecs())">⏱ Таймер отдыха</button>
    <button class="btn success" style="margin-top:10px" onclick="finishWorkout(${w.id})">Завершить тренировку</button>
    <button class="btn ghost" style="margin-top:8px;color:var(--danger)" onclick="cancelWorkout(${w.id})">✖ Отменить тренировку</button>`;
}
// Phase 1Б: explicit cancel. Empty workout (accidental start) → delete in one tap;
// with sets → confirm. delWorkout clears cache + returns Home.
function cancelWorkout(wid) {
  const w = window._WO;
  const hasSets = w && w.exercises.some(e => (e.sets || []).length);
  if (!hasSets) return delWorkout(wid);
  confirmSheet('Отменить тренировку?', 'Удалить тренировку со всеми подходами? Действие необратимо.', 'Удалить', true, () => delWorkout(wid));
}
async function cancelActiveFromHome(wid) {
  let hasSets = false;
  try { const w = await api('/workouts/' + wid); hasSets = (w.exercises || []).some(e => (e.sets || []).length); } catch {}
  if (!hasSets) return delWorkout(wid);
  confirmSheet('Отменить тренировку?', 'Удалить тренировку со всеми подходами? Действие необратимо.', 'Удалить', true, () => delWorkout(wid));
}

// ── offline support: IndexedDB queue + optimistic set logging ───────────────
function isNetworkErr(e) { return !navigator.onLine || !e || e.status === undefined || e.status === 0; }
function _opId() { return 'op-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8); }
function saveActiveCache(w) { try { localStorage.setItem('active_wo', JSON.stringify(w)); } catch {} }
function loadActiveCache(id) { try { const w = JSON.parse(localStorage.getItem('active_wo') || 'null'); if (w && (!id || w.id === id)) return w; } catch {} return null; }
function clearActiveCache() { try { localStorage.removeItem('active_wo'); } catch {} }
function _idb() {
  return new Promise((res, rej) => {
    let r; try { r = indexedDB.open('fitq', 1); } catch (e) { return rej(e); }
    r.onupgradeneeded = () => { if (!r.result.objectStoreNames.contains('ops')) r.result.createObjectStore('ops', { keyPath: 'op_id' }); };
    r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
  });
}
async function _qPut(op) { try { const db = await _idb(); await new Promise(r => { const t = db.transaction('ops', 'readwrite'); t.objectStore('ops').put(op); t.oncomplete = r; t.onerror = r; }); } catch {} }
async function _qAll() { try { const db = await _idb(); return await new Promise(r => { const rq = db.transaction('ops').objectStore('ops').getAll(); rq.onsuccess = () => r(rq.result || []); rq.onerror = () => r([]); }); } catch { return []; } }
async function _qDel(op_id) { try { const db = await _idb(); await new Promise(r => { const t = db.transaction('ops', 'readwrite'); t.objectStore('ops').delete(op_id); t.oncomplete = r; t.onerror = r; }); } catch {} }
function _insertSet(W, body) {
  const name = (body.exercise_name || '').trim();
  let ex = W.exercises.find(e => (e.name || '').trim().toLowerCase() === name.toLowerCase());
  if (!ex) { ex = { name, key: null, type: 'strength', sets: [], target: null, target_sets: null, last: null, in_plan: false, done: false }; W.exercises.push(ex); }
  ex.sets.push({ id: 'tmp-' + body.client_op_id, set_number: ex.sets.length + 1, weight_kg: body.weight_kg ?? null, reps: body.reps ?? null, reps_text: body.reps_text ?? null, duration_seconds: body.duration_seconds ?? null, is_warmup: !!body.is_warmup, is_failure: !!body.is_failure, superset_group: body.superset_group ?? null, notes: null, _pending: true });
}
async function overlayQueue(w) { const ops = (await _qAll()).filter(o => o.wid === w.id).sort((a, b) => a.ts - b.ts); for (const o of ops) _insertSet(w, o.body); }
async function submitSet(wid, body) {
  body.client_op_id = _opId();
  try { await api('/workouts/' + wid + '/sets', 'POST', body); go('active', wid); }
  catch (e) {
    if (isNetworkErr(e)) {
      await _qPut({ op_id: body.client_op_id, wid, body, ts: Date.now() });
      const W = window._WO; if (W && W.id === wid) { _insertSet(W, body); renderActive(W); }
      toast('Оффлайн — сохранится при сети');
    } else { toast(e.message || 'не удалось'); }
  }
}
async function flushQueue() {
  if (!navigator.onLine) return;
  const ops = (await _qAll()).sort((a, b) => a.ts - b.ts);
  for (const o of ops) {
    try { await api('/workouts/' + o.wid + '/sets', 'POST', o.body); await _qDel(o.op_id); }
    catch (e) { if (isNetworkErr(e)) break; await _qDel(o.op_id); }  // drop permanently-failed ops
  }
}
window.addEventListener('online', () => { flushQueue().then(() => { if (STATE.tab === 'active' && STATE.activeId) go('active', STATE.activeId); }); });
function setLabel(s) {
  if (s.duration_seconds) return mmss(s.duration_seconds);
  let v = (s.weight_kg != null ? fmt(s.weight_kg) + '×' : '') + (s.reps != null ? s.reps : (s.reps_text || ''));
  if (s.is_failure) v += '⚡';
  return v || '—';
}

// exercise card sheet
function openExercise(wid, idx) {
  const ex = window._WO.exercises[idx];
  const sets = ex.sets.map(s => `<div class="row sp" style="padding:8px 0;border-bottom:1px solid var(--line)">
    <span>${s.is_warmup ? 'Р · ' : ''}${esc(setLabel(s))}</span>
    <span><span class="muted" onclick="editSet(${s.id},${wid})" style="cursor:pointer">✏️</span> &nbsp; <span style="color:var(--danger);cursor:pointer" onclick="rmSet(${s.id},${wid})">🗑</span></span></div>`).join('');
  const last = ex.last ? `Прошлый раз ${ex.last.duration_seconds ? mmss(ex.last.duration_seconds) : fmt(ex.last.weight_kg) + '×' + ex.last.reps}` : '';
  sheet(`<div class="muted small">Из текущей тренировки</div><h2>${esc(ex.name)}</h2>
    ${last ? `<div class="banner info small" style="color:var(--info)">📈 ${last}</div>` : ''}
    <div class="muted small" style="margin:6px 0">Подходы сегодня</div>
    ${sets || '<div class="muted small" style="padding:10px 0">Пока пусто</div>'}
    <button class="btn" style="margin-top:12px" onclick="openAddSet(${wid},${idx})">➕ Добавить подход</button>`);
}

// add-set sheet — WK-2: several structured rows at once + voice/text fallback.
// onSave (HIST-1): when provided, confirmSets hands the sets to it instead of
// POSTing — used by the archive composer to collect sets into a client-side draft.
function openAddSet(wid, idx, exObj, onSave) {
  const ex = exObj || window._WO.exercises[idx];
  const type = ex.type || 'strength';
  const tgt = ex.target || {}, last = ex.last || {};
  window._setCtx = {
    wid, idx, ex, type, onSave,
    w: tgt.weight_kg ?? last.weight_kg ?? 20,
    reps: tgt.reps ?? last.reps ?? 10,
    dur: tgt.duration_seconds ?? last.duration_seconds ?? 60,
  };
  // default row count = planned sets (1..12); no plan → 1 (time) / 3 (else)
  const planned = ex.target_sets ?? tgt.target_sets ?? null;
  const n = Math.max(1, Math.min(12, planned || (type === 'time' ? 1 : 3)));
  window._setRows = Array.from({ length: n }, () => ({}));
  const timer = type === 'time'
    ? `<button class="btn ghost sm" id="tbtn" style="margin-bottom:8px" onclick="toggleSetTimer()">▶ Таймер (запишет подход)</button><div id="timerbox"></div>`
    : '';
  const freetext = onSave ? '' :   // draft mode → structured rows only (text import is HIST-2)
    `<div class="muted small" style="text-align:center;margin:14px 0 4px">или голосом / текстом — «80×10, 82×8, 80×8»</div>
    <div class="field"><input id="freetext" placeholder="80x10, 82x8, до отказа…"><span onclick="recToField('freetext',this)" style="cursor:pointer">🎤</span><span onclick="confirmText(${wid})" style="color:var(--info);cursor:pointer">↑</span></div>`;
  sheet(`<div class="muted small">${esc(ex.name)}</div><h2>Подходы</h2>
    ${timer}
    <div id="setrows"></div>
    <button class="btn ghost sm" style="margin-top:2px" onclick="addSetRow()">➕ Добавить ещё подход</button>
    <button class="btn" id="savesets" style="margin-top:12px" onclick="confirmSets()">✓ Сохранить</button>
    ${freetext}`);
  renderSetRows();
}
function _setAttr(v) { return esc(String(v ?? '')); }
function setInputRow(c, i, r) {
  const del = window._setRows.length > 1
    ? `<span class="srdel" onclick="rmSetRow(${i})">✕</span>`
    : `<span class="srdel" style="visibility:hidden">✕</span>`;
  const wu = `<span class="pill srwu ${r.warmup ? 'on' : ''}" onclick="this.classList.toggle('on')" title="Разминка">Р</span>`;
  let fields;
  if (c.type === 'time') {
    const dur = r.dur ?? c.dur;
    fields = `<input class="srin sr-min" inputmode="numeric" value="${_setAttr(Math.floor(dur / 60))}" placeholder="мин"><span class="x">:</span><input class="srin sr-sec" inputmode="numeric" value="${_setAttr(dur % 60)}" placeholder="сек">`;
  } else if (c.type === 'bodyweight') {
    fields = `<input class="srin sr-r" inputmode="numeric" value="${_setAttr(r.reps ?? c.reps)}" placeholder="повт."><span class="x" style="flex:0 0 auto">повт.</span>`;
  } else {
    fields = `<input class="srin sr-w" inputmode="decimal" value="${_setAttr(r.weight ?? c.w)}" placeholder="кг"><span class="x">×</span><input class="srin sr-r" inputmode="numeric" value="${_setAttr(r.reps ?? c.reps)}" placeholder="повт.">`;
  }
  return `<div class="setrow row" data-i="${i}"><span class="srnum">${i + 1}</span>${fields}${wu}${del}</div>`;
}
function renderSetRows() {
  const box = document.getElementById('setrows'); if (!box) return;
  box.innerHTML = window._setRows.map((r, i) => setInputRow(window._setCtx, i, r)).join('');
  const b = document.getElementById('savesets'); if (b) b.textContent = `✓ Сохранить (${window._setRows.length})`;
}
function _readSetRows() {
  const c = window._setCtx;
  document.querySelectorAll('#setrows .setrow').forEach((el, i) => {
    const r = window._setRows[i] = (window._setRows[i] || {});
    const wuEl = el.querySelector('.srwu');
    r.warmup = !!(wuEl && wuEl.classList.contains('on'));
    if (c.type === 'time') {
      const mn = parseInt((el.querySelector('.sr-min') || {}).value || '0', 10) || 0;
      const sc = parseInt((el.querySelector('.sr-sec') || {}).value || '0', 10) || 0;
      r.dur = mn * 60 + sc;
    } else {
      const wv = el.querySelector('.sr-w'); if (wv) r.weight = wv.value;
      const rv = el.querySelector('.sr-r'); if (rv) r.reps = rv.value;
    }
  });
}
function addSetRow() { _readSetRows(); window._setRows.push({}); renderSetRows(); }
function rmSetRow(i) { _readSetRows(); window._setRows.splice(i, 1); if (!window._setRows.length) window._setRows.push({}); renderSetRows(); }
// live count-up timer for time-based exercises: stop appends a row with the elapsed time
function toggleSetTimer() {
  if (TMR) {
    stopTimer(); const v = window.TMR_VAL || 0;
    const b = document.getElementById('tbtn'); if (b) b.textContent = '▶ Таймер (запишет подход)';
    const box = document.getElementById('timerbox'); if (box) box.innerHTML = '';
    if (v) { _readSetRows(); window._setRows.push({ dur: v }); renderSetRows(); }
    return;
  }
  let s = 0; const box = document.getElementById('timerbox');
  const b = document.getElementById('tbtn'); if (b) b.textContent = '■ Стоп и записать';
  window.TMR_VAL = 0;
  TMR = setInterval(() => { s++; window.TMR_VAL = s; if (box) box.innerHTML = `<div class="timer">${mmss(s)}</div>`; }, 1000);
}
function stepRow(fields) {
  const cells = fields.map(([id, val, unit, step]) =>
    `<div class="step"><div class="chev" onclick="bump('${id}',${step})">▲</div>
      <input class="val" id="f_${id}" value="${val}" data-step="${step}" inputmode="decimal">
      <div class="chev" onclick="bump('${id}',${-step})">▼</div><div class="u">${unit}</div></div>`);
  return `<div class="stepwrap">${cells.join('<span class="x">×</span>')}</div>`;
}
function bump(id, d) { const el = document.getElementById('f_' + id); let v = parseFloat(el.value || 0) + d; if (v < 0) v = 0; el.value = (Math.round(v * 100) / 100); }
function tag(el) { el.classList.toggle('on'); }
function getTags() { const t = {}; document.querySelectorAll('.pill[data-tag].on').forEach(e => t['is_' + e.dataset.tag] = true); return t; }
async function confirmSets() {
  _readSetRows();
  const c = window._setCtx;
  const sets = window._setRows.map(r => {
    const o = { is_warmup: !!r.warmup };
    if (c.type === 'time') o.duration_seconds = r.dur || null;
    else {
      o.reps = (r.reps === '' || r.reps == null) ? null : parseInt(r.reps, 10);
      if (c.type !== 'bodyweight') o.weight_kg = (r.weight === '' || r.weight == null) ? null : parseFloat(r.weight);
    }
    return o;
  }).filter(o => o.weight_kg != null || o.reps != null || o.duration_seconds != null);
  if (!sets.length) return toast('Заполни хотя бы один подход');
  closeSheet(); stopTimer();
  if (c.onSave) { c.onSave(sets); return; }  // HIST-1: collect into a draft, no POST / no rest timer
  await submitSets(c.wid, { exercise_name: c.ex.name, sets });
  if (restEnabled()) restTimer(restSecs());  // auto-start unless persisted-disabled
}
// post several structured sets as ONE idempotent op; offline-aware like submitSet
async function submitSets(wid, body) {
  body.client_op_id = _opId();
  try { await api('/workouts/' + wid + '/sets', 'POST', body); go('active', wid); }
  catch (e) {
    if (isNetworkErr(e)) {
      await _qPut({ op_id: body.client_op_id, wid, body, ts: Date.now() });
      const W = window._WO;
      if (W && W.id === wid) { (body.sets || []).forEach((s, k) => _insertSet(W, { ...s, exercise_name: body.exercise_name, client_op_id: body.client_op_id + '-' + k })); renderActive(W); }
      toast('Оффлайн — сохранится при сети');
    } else { toast(e.message || 'не удалось'); }
  }
}
async function confirmText(wid) {
  const t = document.getElementById('freetext').value.trim(); if (!t) return;
  // the field is scoped to the current exercise: pass its name so numbers-only
  // input («80x10, 82x8») attaches to it (the backend uses it as the parse hint).
  const name = (window._setCtx && window._setCtx.ex && window._setCtx.ex.name) || undefined;
  try { await api('/workouts/' + wid + '/sets', 'POST', { text: t, exercise_name: name }); closeSheet(); go('active', wid); }
  catch (e) { toast(e.message || 'не удалось разобрать'); }
}

// voice → text: record with MediaRecorder, transcribe via Whisper, fill a field
let _voiceRec = null, _voiceChunks = [];
async function recToField(targetId, btn) {
  if (_voiceRec && _voiceRec.state === 'recording') { _voiceRec.stop(); return; }
  if (!navigator.mediaDevices || !window.MediaRecorder) return toast('Запись не поддерживается');
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch { return toast('Микрофон недоступен'); }
  const mime = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
  _voiceChunks = [];
  _voiceRec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
  _voiceRec.ondataavailable = e => { if (e.data && e.data.size) _voiceChunks.push(e.data); };
  _voiceRec.onstop = async () => {
    stream.getTracks().forEach(t => t.stop());
    if (btn) btn.textContent = '🎤';
    const el = document.getElementById(targetId);
    const blob = new Blob(_voiceChunks, { type: (_voiceChunks[0] && _voiceChunks[0].type) || 'audio/webm' });
    if (!blob.size) return;
    const ext = blob.type.indexOf('mp4') >= 0 ? 'mp4' : 'webm';
    const fd = new FormData(); fd.append('file', blob, 'voice.' + ext);
    const ph = el ? el.placeholder : ''; if (el) el.placeholder = 'распознаю…';
    try {
      const r = await fetch('/api/voice/transcribe', { method: 'POST', body: fd, credentials: 'include' });
      if (!r.ok) { let d = ''; try { d = (await r.json()).detail; } catch {} throw new Error(d || r.status); }
      const j = await r.json();
      if (el) el.value = (el.value ? el.value + ' ' : '') + (j.text || '');
    } catch (e) { toast(e.message || 'не удалось распознать'); }
    finally { if (el) el.placeholder = ph; }
  };
  _voiceRec.start();
  if (btn) btn.textContent = '⏹';
  toast('Запись… нажми ещё раз для остановки');
}

// in-set timer
let TMR = null;
function toggleTimer() {
  if (TMR) return stopTimer(true);
  let s = 0; document.getElementById('tbtn').textContent = '■ Стоп и записать';
  const box = document.getElementById('timerbox');
  TMR = setInterval(() => { s++; box.innerHTML = `<div class="timer">${mmss(s)}</div>`; TMR_VAL = s; }, 1000);
  window.TMR_VAL = 0;
}
function stopTimer(write) {
  if (TMR) { clearInterval(TMR); TMR = null; }
  if (write && window.TMR_VAL) { document.getElementById('f_min').value = Math.floor(window.TMR_VAL / 60); document.getElementById('f_sec').value = window.TMR_VAL % 60; const b = document.getElementById('tbtn'); if (b) b.textContent = '▶ Запустить таймер'; }
}

// WK-4: the persisted rest-timer setting is the single source of truth. It lives
// in window._SETTINGS (loaded from /settings at boot, refreshed on every Settings
// visit) and is mirrored to localStorage so a freshly-disabled timer survives an
// offline / next-session launch and is never silently reset to ON.
function _cacheSettings() { try { localStorage.setItem('settings_v1', JSON.stringify(window._SETTINGS || {})); } catch {} }
function _cachedSettings() { try { return JSON.parse(localStorage.getItem('settings_v1') || 'null'); } catch { return null; } }
function restEnabled() { const s = window._SETTINGS || _cachedSettings() || {}; return s.rest_timer_enabled !== false; }
function restSecs() { const s = window._SETTINGS || _cachedSettings() || {}; return s.rest_timer_seconds || 90; }

// rest timer overlay — WK-5: build the sheet ONCE; the interval updates only the
// number (textContent) and the ring offset (CSS-animated), never re-rendering the
// DOM, so the countdown is smooth and the sheet never flickers.
const _RING_C = 2 * Math.PI * 54;  // circumference of the r=54 progress circle
function restTimer(seconds) {
  closeRest();  // never stack two overlays
  let s = seconds || 90, total = s;
  const bg = document.createElement('div'); bg.className = 'sheet-bg'; bg.id = 'restbg';
  bg.innerHTML = `<div class="sheet" style="text-align:center"><div class="grip"></div>
    <div class="muted small">Отдых</div>
    <div class="rest-ring">
      <svg viewBox="0 0 120 120" aria-hidden="true">
        <circle class="rr-track" cx="60" cy="60" r="54"></circle>
        <circle class="rr-prog" cx="60" cy="60" r="54" style="stroke-dasharray:${_RING_C.toFixed(2)}"></circle>
      </svg>
      <div class="timer" id="restNum">${mmss(s)}</div>
    </div>
    <div class="grid2"><button class="btn sec sm" onclick="restAdd(30)">+30 сек</button>
    <button class="btn sm" onclick="closeRest()">Пропустить</button></div></div>`;
  document.body.appendChild(bg);
  const numEl = bg.querySelector('#restNum'), progEl = bg.querySelector('.rr-prog');
  const paint = () => {
    numEl.textContent = mmss(Math.max(0, s));
    const frac = total > 0 ? Math.max(0, Math.min(1, s / total)) : 0;
    progEl.style.strokeDashoffset = (_RING_C * (1 - frac)).toFixed(2);  // ring empties as time runs out
  };
  paint();
  window._restAdd = n => { s += n; total += n; paint(); };
  bg._iv = setInterval(() => { s--; if (s <= 0) { closeRest(); toast('Отдых окончен'); } else paint(); }, 1000);
}
function restAdd(n) { window._restAdd(n); }
function closeRest() { const b = document.getElementById('restbg'); if (b) { clearInterval(b._iv); b.remove(); } }

// edit / delete set
async function editSet(sid, wid) {
  sheet(`<h2>Редактировать подход</h2>
    ${stepRow([['weight', 0, 'кг', 2.5], ['reps', 0, 'повт.', 1]])}
    <div class="tag-row"><span class="pill" data-tag="warmup" onclick="tag(this)">Разминка</span>
      <span class="pill" data-tag="failure" onclick="tag(this)">До отказа</span></div>
    <button class="btn" onclick="saveSet(${sid},${wid})">Сохранить</button>
    <button class="btn danger" style="margin-top:8px" onclick="rmSet(${sid},${wid})">Удалить</button>`);
}
async function saveSet(sid, wid) {
  const g = id => parseFloat(document.getElementById('f_' + id).value);
  await api('/sets/' + sid, 'PATCH', { weight_kg: g('weight'), reps: g('reps'), ...getTags() });
  closeSheet(); go('active', wid);
}
async function rmSet(sid, wid) { await api('/sets/' + sid, 'DELETE'); closeSheet(); go('active', wid); }

// exercise picker
async function openPicker(wid) {
  sheet(`<h2>Добавить упражнение</h2>
    <div class="field" style="margin-bottom:10px"><input id="exq" placeholder="поиск…" oninput="pickSearch(${wid})"><span>🔎</span></div>
    <div class="tag-row"><span class="pill on" id="tabRec" onclick="pickTab(${wid},'rec')">Недавние</span>
      <span class="pill" id="tabGrp" onclick="pickTab(${wid},'grp')">По группам</span></div>
    <div id="pickbody"></div>`);
  pickTab(wid, 'rec');
}
async function pickTab(wid, t) {
  document.getElementById('tabRec').classList.toggle('on', t === 'rec');
  document.getElementById('tabGrp').classList.toggle('on', t === 'grp');
  const body = document.getElementById('pickbody');
  if (t === 'rec') {
    const r = await api('/exercises/recent');
    body.innerHTML = r.length ? r.map(x => pickRow(wid, x.name, x.key, x.image)).join('') : '<div class="muted small">Пока пусто — выбери по группам.</div>';
  } else {
    const g = await api('/exercises/groups');
    body.innerHTML = g.map(x => `<div class="list-item" onclick="pickGroup(${wid},'${x.group}','${x.label}')"><div style="flex:1">${x.label}</div><span class="muted small">${x.count} ›</span></div>`).join('');
  }
}
async function pickGroup(wid, g, label) {
  const list = await api('/exercises/catalog?group=' + g);
  document.getElementById('pickbody').innerHTML = `<div class="back" onclick="pickTab(${wid},'grp')">‹ ${label}</div>` + list.map(x => pickRow(wid, x.name, x.exercise_key, x.image)).join('');
}
async function pickSearch(wid) {
  const q = document.getElementById('exq').value.trim();
  if (q.length < 2) return;
  const r = await api('/exercises/search?q=' + encodeURIComponent(q));
  document.getElementById('pickbody').innerHTML = r.map(x => pickRow(wid, x.name, x.exercise_key, x.image)).join('') || '<div class="muted small">Ничего не найдено</div>';
}
function pickRow(wid, name, key, image) {
  return `<div class="list-item" onclick='chooseEx(${wid},${esc(JSON.stringify(name))},${esc(JSON.stringify(key || ''))})'>${_exThumb(image)}<div style="flex:1">${esc(name)}</div><span style="color:var(--info)">＋</span></div>`;
}
function chooseEx(wid, name, key) {
  const type = key && /план|велосипед|кардио/.test(name.toLowerCase()) ? 'time'
    : /подтяг|отжим|брус/.test(name.toLowerCase()) ? 'bodyweight' : 'strength';
  if (wid === 0 && window._arch) {   // HIST-1 archive draft: collect sets, don't POST
    openAddSet(0, null, { name, key, type, target: null, last: null }, sets => {
      window._arch.exercises.push({ name, key, type, sets });
      renderArchive();
    });
    return;
  }
  openAddSet(wid, null, { name, key, type, target: null, last: null });
}

// workout menu / finish
function workoutMenu(wid) {
  sheet(`<h2>Тренировка</h2>
    <div class="list-item" onclick="noteSheet(${wid})"><div class="ic">📝</div>Заметка к тренировке</div>
    <div class="list-item" onclick="closeSheet();go('home')"><div class="ic">⏸</div>Свернуть (продолжу позже)</div>
    <div class="list-item" style="color:var(--danger)" onclick="delWorkout(${wid})"><div class="ic">🗑</div>Удалить тренировку</div>`);
}
function noteSheet(wid) {
  sheet(`<h2>Заметка</h2><textarea id="note" style="width:100%;min-height:90px;border:1px solid var(--line);border-radius:10px;padding:10px;background:var(--card);color:var(--txt);font-size:15px"></textarea>
    <button class="btn" style="margin-top:10px" onclick="saveNote(${wid})">Сохранить</button>`);
}
async function saveNote(wid) { await api('/workouts/' + wid + '/notes', 'PATCH', { notes: document.getElementById('note').value }); closeSheet(); toast('Заметка сохранена'); }
async function delWorkout(wid) { await api('/workouts/' + wid, 'DELETE'); clearActiveCache(); closeSheet(); go('home'); }
async function finishWorkout(wid) {
  const r = await api('/workouts/' + wid + '/finish', 'POST');
  clearActiveCache();
  sheet(`<div style="text-align:center"><div style="font-size:34px">✅</div><h2>Тренировка завершена</h2>
    <div class="muted small">${r.set_count} рабочих подходов</div></div>
    <div class="card" style="margin-top:10px"><div class="muted small">✨ Резюме</div><div style="margin-top:6px">${esc(r.summary)}</div></div>
    <button class="btn ghost" style="margin-top:10px" id="coachBtn" onclick="coachReview(${wid})">🤖 AI-разбор</button>
    <div id="coachBox"></div>
    <button class="btn" style="margin-top:12px" onclick="closeSheet();go('home')">Готово</button>`);
}
async function coachReview(wid) {
  const btn = document.getElementById('coachBtn');
  if (btn) { btn.disabled = true; btn.textContent = '🤖 Думаю…'; }
  const box = document.getElementById('coachBox');
  if (box) box.innerHTML = `<div class="card muted small" style="margin-top:10px">⏳ AI разбирает тренировку… (Opus небыстрый)</div>`;
  try {
    const r = await api('/workouts/' + wid + '/coach');
    if (box) box.innerHTML = `<div class="card" style="margin-top:10px"><div class="muted small">🤖 AI-разбор</div><div style="margin-top:6px;white-space:pre-line">${esc(r.summary)}</div></div>`;
    if (btn) btn.style.display = 'none';
  } catch (e) {
    if (box) box.innerHTML = `<div class="card small" style="margin-top:10px;color:var(--danger)">${esc(e.message || 'AI-разбор недоступен')}</div>`;
    if (btn) { btn.disabled = false; btn.textContent = '🤖 Повторить'; }
  }
}

// ── History ───────────────────────────────────────────────────────────────
async function History() {
  const q = STATE.histQ || '';
  const list = await api('/workouts?days=4000' + (q ? '&q=' + encodeURIComponent(q) : ''));
  view.innerHTML = `<div class="row sp"><h1>История</h1><span class="back" style="margin:0" onclick="go('reports')">📄 Отчёты (PDF) ›</span></div>
    <button class="btn" style="margin-bottom:12px" onclick="archiveNew()">➕ Добавить тренировку</button>
    <div class="field" style="margin-bottom:12px"><input id="histQ" placeholder="поиск: фокус или упражнение…" value="${esc(q)}" oninput="histSearch(this.value)"><span>🔎</span></div>
    ${list.length ? list.map(w => swipeRow(
      `<div class="row sp"><div style="flex:1"><b>${esc(w.focus_label || 'Тренировка')}</b><div class="small muted">${esc(fmtDate(w.workout_date, { weekday: 'short' }))} · ${w.set_count} подх · ${w.tonnage.toLocaleString('ru-RU')} кг</div></div><span class="muted">›</span></div>`,
      `askDelWorkout(${w.id})`, `go('workout',${w.id})`)).join('')
      : `<div class="card muted">${q ? 'Ничего не найдено по запросу.' : 'Пока нет завершённых тренировок.'}</div>`}`;
  const inp = document.getElementById('histQ');
  if (inp && q) { inp.focus(); inp.setSelectionRange(q.length, q.length); }
}
let _histT = null;
function histSearch(v) { STATE.histQ = v; clearTimeout(_histT); _histT = setTimeout(() => { if (STATE.tab === 'history') History(); }, 350); }
async function WorkoutDetail(id) {
  const w = await api('/workouts/' + id);
  window._WDid = id; window._WDex = w.exercises.filter(e => e.sets.length);
  const ex = window._WDex.map((e, idx) => `<div class="row sp" style="padding:8px 0;border-bottom:1px solid var(--line)">
    <div style="flex:1"><b>${esc(e.name)}</b><div class="small muted">${esc(e.sets.map(setLabel).join(' · '))}</div></div>
    <span class="muted" style="cursor:pointer;padding:4px 6px" title="Прогресс упражнения" onclick="exDetailWD(${idx})">📈</span></div>`).join('');
  view.innerHTML = `<span class="back" onclick="go('history')">‹ История</span>
    <h2 style="margin-bottom:2px">${esc(w.focus_label || 'Тренировка')}</h2><div class="muted small" style="margin-bottom:10px">${esc(fmtDate(w.workout_date, { weekday: 'long' }))}</div>
    <div class="card">${ex || '<span class="muted">Нет подходов</span>'}</div>
    ${w.notes ? `<div class="card small muted">📝 ${esc(w.notes)}</div>` : ''}
    <button class="btn ghost" onclick="go('active',${w.id})">✏️ Редактировать подходы</button>
    <button class="btn ghost" style="margin-top:8px" onclick="repeatLast(${w.id})">🔁 Повторить эту тренировку</button>
    <button class="btn ghost" style="margin-top:8px" onclick="workoutToTemplate(${w.id})">💾 В шаблон</button>`;
}

// ── HIST-1: add a PAST (archive) workout — mirrors the plan editor (calendar +
// client-side draft), but each exercise carries actual WK-2 sets; saving creates a
// backdated FINISHED workout (POST /workouts/archive), names canonicalized (DB-5).
function archiveNew() {
  window._arch = { date: todayISO(), focus: '', notes: '', exercises: [], cal: null };
  renderArchive();
}
function archSync() {
  const A = window._arch; if (!A) return;
  const d = document.getElementById('ar_date'); if (d && d.value) A.date = d.value;
  const f = document.getElementById('ar_focus'); if (f) A.focus = f.value;
  const n = document.getElementById('ar_notes'); if (n) A.notes = n.value;
}
function archPickDate(iso) { archSync(); window._arch.date = iso; window._arch.cal = iso.slice(0, 7) + '-01'; renderArchive(); }
function archCalNav(dir) {
  archSync();
  const a = window._arch.cal || (window._arch.date.slice(0, 7) + '-01');
  const d = new Date(a + 'T00:00:00'); d.setMonth(d.getMonth() + dir);
  window._arch.cal = isoOf(new Date(d.getFullYear(), d.getMonth(), 1));
  renderArchive();
}
function archDateInput() { archSync(); window._arch.cal = (window._arch.date || todayISO()).slice(0, 7) + '-01'; renderArchive(); }
function archRemoveEx(i) { archSync(); window._arch.exercises.splice(i, 1); renderArchive(); }
function archAddExercise() { archSync(); openPicker(0); }   // wid=0 + window._arch → draft mode (chooseEx)
function renderArchive() {
  const A = window._arch; if (!A) return go('history');
  const cal = monthCalendar(A.cal || (A.date.slice(0, 7) + '-01'), {}, A.date, 'archPickDate', 'archCalNav');
  const exItems = A.exercises.length ? A.exercises.map((ex, i) => `<div class="card list-item ex-row">
      <div style="flex:1"><b>${esc(ex.name)}</b><div class="small muted">${esc(ex.sets.map(setLabel).join(' · ')) || 'нет подходов'}</div></div>
      <span style="color:var(--danger);cursor:pointer;padding:4px 6px" onclick="archRemoveEx(${i})">🗑</span></div>`).join('')
    : '<div class="card muted small">Упражнения не добавлены</div>';
  view.innerHTML = `<span class="back" onclick="go('history')">‹ История</span>
    <h2 style="margin-bottom:2px">Прошлая тренировка</h2>
    <button class="btn ghost" style="margin:8px 0 4px" onclick="archPasteSheet()">📝 Вставить текстом (несколько сразу)</button>
    <div class="muted small" style="margin:6px 0">Дата · <b style="color:var(--txt);text-transform:capitalize">${esc(fmtDate(A.date, { weekday: 'long' }))}</b></div>
    ${cal}
    <div class="mfield"><label>Точная дата</label><input id="ar_date" type="date" max="${todayISO()}" value="${A.date}" onchange="archDateInput()"></div>
    <div class="mfield" style="margin-top:14px"><label>Фокус (что тренировал)</label>
      <input id="ar_focus" value="${esc(A.focus)}" placeholder="напр. Грудь / Трицепс"></div>
    <div class="muted small" style="margin:16px 0 6px">Упражнения и подходы</div>
    ${exItems}
    <button class="btn ghost" style="margin-top:8px" onclick="archAddExercise()">➕ Добавить упражнение</button>
    <div class="mfield" style="margin-top:16px"><label>Заметка (необязательно)</label>
      <input id="ar_notes" value="${esc(A.notes)}" placeholder=""></div>
    <button class="btn success" style="margin-top:16px" onclick="archSave()">💾 Сохранить тренировку</button>`;
}
async function archSave() {
  archSync();
  const A = window._arch;
  if (!A.exercises.length) return toast('Добавь хотя бы одно упражнение');
  try {
    const r = await api('/workouts/archive', 'POST', {
      workout_date: A.date, focus_label: A.focus, notes: A.notes,
      exercises: A.exercises.map(e => ({ name: e.name, sets: e.sets })),
    });
    window._arch = null;
    toast('Тренировка добавлена');
    go('workout', r.id);
  } catch (e) { toast(e.message || 'не удалось'); }
}

// ── HIST-2: paste one/several PAST workouts as text → AI preview → bulk save ───
function archPasteSheet() {
  sheet(`<h2>Вставить текстом</h2>
    <div class="muted small" style="margin-bottom:8px">Вставь одну или несколько прошлых тренировок — с датами и подходами (вес×повторы). ИИ разберёт, покажу превью для проверки.</div>
    <textarea id="archText" style="width:100%;min-height:130px;border:1px solid var(--line);border-radius:10px;padding:10px;background:var(--card);color:var(--txt);font-size:15px" placeholder="15.06.2026 Грудь&#10;Жим лёжа 80×10, 82×8, 80×8&#10;Разводка 20×12&#10;&#10;17.06.2026 Ноги&#10;Присед 100×5, 100×5, 100×5"></textarea>
    <button class="btn" id="archParseBtn" style="margin-top:10px" onclick="archParse()">⏳ Разобрать</button>`);
}
async function archParse() {
  const t = document.getElementById('archText').value.trim();
  if (!t) return toast('Пустой текст');
  const btn = document.getElementById('archParseBtn') || {};
  btn.textContent = '⏳ Разбираю…'; btn.disabled = true;
  try {
    const r = await api('/workouts/parse', 'POST', { text: t });
    window._archParsed = (r.workouts || []).map(w => ({
      date: w.date || '', date_text: w.date_text || '', focus_label: w.focus_label || '',
      notes: w.notes || null, exercises: w.exercises || [],
    }));
    if (!window._archParsed.length) { toast('Не распознано'); btn.textContent = '⏳ Разобрать'; btn.disabled = false; return; }
    renderArchPreview();
  } catch (e) {
    toast(e.message || 'не удалось разобрать');
    btn.textContent = '⏳ Разобрать'; btn.disabled = false;
  }
}
function renderArchPreview() {
  const ws = window._archParsed || [];
  const cards = ws.map((w, i) => {
    const exHtml = w.exercises.map((e, j) => `<div class="row sp" style="padding:5px 0;border-bottom:1px solid var(--line)">
      <div style="flex:1"><b>${esc(e.name)}</b><div class="small muted">${esc(e.sets.map(setLabel).join(' · '))}</div></div>
      <span style="color:var(--danger);cursor:pointer;padding:2px 6px" onclick="archPrevRmEx(${i},${j})">✕</span></div>`).join('');
    const warn = w.date ? '' : ' <span style="color:var(--warn)">— укажи дату</span>';
    return `<div class="card" style="margin-bottom:10px">
      <div class="row sp"><b>Тренировка ${i + 1}</b><span style="color:var(--danger);cursor:pointer" onclick="archPrevRmWk(${i})">🗑</span></div>
      <div class="mfield" style="margin-top:6px"><label>Дата${w.date_text ? ` (из текста: «${esc(w.date_text)}»)` : ''}${warn}</label>
        <input type="date" max="${todayISO()}" value="${esc(w.date)}" onchange="archPrevSet(${i},'date',this.value)"></div>
      <div class="mfield" style="margin-top:8px"><label>Фокус</label>
        <input value="${esc(w.focus_label)}" placeholder="напр. Грудь" onchange="archPrevSet(${i},'focus_label',this.value)"></div>
      <div class="muted small" style="margin:10px 0 2px">Упражнения и подходы</div>
      ${exHtml || '<div class="muted small">нет</div>'}
    </div>`;
  }).join('');
  sheet(`<h2>Превью · ${ws.length}</h2>
    <div style="max-height:55vh;overflow:auto">${cards}</div>
    <button class="btn success" style="margin-top:12px" onclick="archBulkSave()">💾 Сохранить всё</button>
    <button class="btn ghost" style="margin-top:8px" onclick="archPasteSheet()">‹ Назад к тексту</button>`);
}
function archPrevSet(i, k, v) { if (window._archParsed[i]) window._archParsed[i][k] = v; }
function archPrevRmEx(i, j) {
  const w = window._archParsed[i]; if (!w) return;
  w.exercises.splice(j, 1);
  if (!w.exercises.length) window._archParsed.splice(i, 1);
  if (!window._archParsed.length) { closeSheet(); return toast('Пусто'); }
  renderArchPreview();
}
function archPrevRmWk(i) {
  window._archParsed.splice(i, 1);
  if (!window._archParsed.length) { closeSheet(); return toast('Пусто'); }
  renderArchPreview();
}
async function archBulkSave() {
  const ws = window._archParsed || [];
  if (!ws.length) return toast('Нет тренировок');
  if (ws.some(w => !w.date)) return toast('Укажи дату у всех тренировок');
  try {
    const r = await api('/workouts/archive-bulk', 'POST', {
      workouts: ws.map(w => ({
        workout_date: w.date, focus_label: w.focus_label, notes: w.notes,
        exercises: w.exercises.map(e => ({ name: e.name, sets: e.sets })),
      })),
    });
    window._archParsed = null;
    closeSheet();
    toast(`Добавлено тренировок: ${r.count}`);
    go('history');
  } catch (e) { toast(e.message || 'не удалось'); }
}

// ── Exercise progress (charts + PR) ─────────────────────────────────────────
function shortDate(iso) { return iso ? iso.slice(8, 10) + '.' + iso.slice(5, 7) : ''; }
// Nice round axis ticks (step 1/2/2.5/5/10 × 10^n, round bounds) for chart Y
// axes (UX2-5) — avoids ugly fractions like 102.23 / 103.87.
function niceTicks(dmn, dmx, count = 4) {
  if (!isFinite(dmn) || !isFinite(dmx) || dmn === dmx) { dmn = (dmn || 0) - 1; dmx = (dmx || 0) + 1; }
  const rawStep = (dmx - dmn) / Math.max(1, count - 1);
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const norm = rawStep / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  const lo = Math.floor(dmn / step) * step, hi = Math.ceil(dmx / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step * 0.5; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
  return { ticks, lo, hi };
}
// Line chart with axes: round Y ticks + horizontal gridlines and several X date
// ticks. pts = [{label, value}]. Gridlines use var(--line), labels var(--txt2).
function lineChart(pts, color = 'var(--info)') {
  if (!pts.length) return '<div class="muted small">нет данных</div>';
  const W = 300, H = 130, padL = 36, padR = 10, padT = 10, padB = 22;
  const vals = pts.map(p => p.value);
  const nt = niceTicks(Math.min(...vals), Math.max(...vals), 4);
  const mn = nt.lo, mx = nt.hi, rng = (mx - mn) || 1, plotW = W - padL - padR, plotH = H - padT - padB;
  const x = i => padL + (pts.length <= 1 ? plotW / 2 : (i / (pts.length - 1)) * plotW);
  const y = v => padT + (1 - (v - mn) / rng) * plotH;
  const ax = 'font-size:9px;fill:var(--txt2)';
  // Round Y ticks + horizontal gridlines.
  let grid = '', yLabels = '';
  nt.ticks.forEach(val => {
    const yy = y(val).toFixed(1);
    grid += `<line x1="${padL}" y1="${yy}" x2="${W - padR}" y2="${yy}" stroke="var(--line)" stroke-width="1"/>`;
    yLabels += `<text x="${padL - 3}" y="${(+yy + 3).toFixed(1)}" text-anchor="end" style="${ax}">${fmt(val)}</text>`;
  });
  // X date ticks: evenly-spaced indices, always incl. first & last, deduped.
  const xN = Math.min(pts.length, 4), idxs = [];
  for (let t = 0; t < xN; t++) {
    const idx = pts.length <= 1 ? 0 : Math.round((t / (xN - 1)) * (pts.length - 1));
    if (!idxs.includes(idx)) idxs.push(idx);
  }
  let xTicks = '', xLabels = '';
  idxs.forEach(idx => {
    const xx = x(idx).toFixed(1);
    xTicks += `<line x1="${xx}" y1="${H - padB}" x2="${xx}" y2="${H - padB + 3}" stroke="var(--line)" stroke-width="1"/>`;
    const anchor = idx === 0 ? 'start' : (idx === pts.length - 1 ? 'end' : 'middle');
    xLabels += `<text x="${xx}" y="${H - 5}" text-anchor="${anchor}" style="${ax}">${esc(pts[idx].label)}</text>`;
  });
  const poly = pts.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
  const dots = pts.map((p, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="3" fill="${color}"/>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:340px">
    ${grid}
    <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${H - padB}" stroke="var(--line)"/>
    <line x1="${padL}" y1="${H - padB}" x2="${W - padR}" y2="${H - padB}" stroke="var(--line)"/>
    ${xTicks}
    ${pts.length > 1 ? `<polyline points="${poly}" fill="none" stroke="${color}" stroke-width="2"/>` : ''}${dots}
    ${yLabels}
    ${xLabels}
  </svg>`;
}
// Entry points stash the source screen so "back" returns there.
function exDetailIdx(i) { const ex = window._WO && window._WO.exercises[i]; if (ex) { STATE.exFrom = ['active', window._WO.id]; go('exercise', ex.key || ex.name); } }
function exDetailWD(i) { const ex = window._WDex && window._WDex[i]; if (ex) { STATE.exFrom = ['workout', window._WDid]; go('exercise', ex.key || ex.name); } }
function exBack() { const f = STATE.exFrom; if (f) go(f[0], f[1]); else go('home'); }
async function ExerciseDetail(keyOrName) {
  document.getElementById('tabbar').style.display = '';
  let d;
  try { d = await api('/exercises/' + encodeURIComponent(keyOrName) + '/stats'); }
  catch (e) { if (e.code === 401) throw e; view.innerHTML = `<span class="back" onclick="exBack()">‹ Назад</span><div class="card">Ошибка: ${esc(e.message)}</div>`; return; }
  const pr = d.pr || {};
  const prCard = (label, p, val) => p ? `<div class="card" style="flex:1;min-width:0;text-align:center;padding:12px 4px;margin:0">
    <div style="font-size:17px;font-weight:700;white-space:nowrap">${val(p)}</div>
    <div class="small muted">${label}</div><div class="small muted" style="margin-top:2px">${shortDate(p.date)}</div></div>` : '';
  const series = d.series || [];
  const weightPts = series.map(s => ({ label: shortDate(s.date), value: s.top_weight }));
  const volPts = series.map(s => ({ label: shortDate(s.date), value: s.volume }));
  view.innerHTML = `<span class="back" onclick="exBack()">‹ Назад</span>
    <h2 style="margin-bottom:12px">${esc(d.name)}</h2>
    ${series.length ? `
      <div style="display:flex;gap:8px;margin-bottom:16px">
        ${prCard('рекорд веса', pr.weight, p => fmt(p.weight) + ' кг')}
        ${prCard('1ПМ (оценка)', pr.one_rm, p => fmt(p.value) + ' кг')}
        ${prCard('вес×повт', pr.volume, p => fmt(p.value))}
      </div>
      <div class="muted small" style="margin:4px 0 2px">Рабочий вес по сессиям</div>
      <div class="card" style="text-align:center">${lineChart(weightPts, 'var(--info)')}</div>
      <div class="muted small" style="margin:12px 0 2px">Объём (тоннаж) по сессиям</div>
      <div class="card" style="text-align:center">${lineChart(volPts, '#3fb950')}</div>
      <div class="muted small" style="margin:12px 0 2px">История · ${d.sessions} ${d.sessions === 1 ? 'сессия' : 'сессий'}</div>
      <div class="card">${series.slice().reverse().map(s => `<div class="row sp" style="padding:5px 0;border-bottom:1px solid var(--line)"><span>${shortDate(s.date)}</span><span class="muted small">топ ${fmt(s.top_weight)} кг · объём ${fmt(s.volume)}</span></div>`).join('')}</div>
    ` : `<div class="card muted">Пока нет рабочих подходов с весом. Запиши пару подходов — здесь появятся графики прогресса и личные рекорды.</div>`}`;
}

// ── Reports (PDF) ──────────────────────────────────────────────────────────
function Reports() {
  document.getElementById('tabbar').style.display = '';
  const t = todayISO();
  const from30 = new Date(new Date(t + 'T00:00:00').getTime() - 30 * 864e5);
  const fromDefault = new Date(from30.getTime() - from30.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  view.innerHTML = `<span class="back" onclick="go('history')">‹ История</span><h1>Отчёты</h1>
    <div class="muted small" style="margin-bottom:12px">PDF за период: тренировки, тоннаж, замеры и фото.</div>
    <div class="card">
      <button class="btn ghost sm" onclick="openReport('days=7')">За неделю</button>
      <button class="btn ghost sm" style="margin-top:8px" onclick="openReport('days=14')">За 2 недели</button>
      <button class="btn ghost sm" style="margin-top:8px" onclick="openReport('days=30')">За месяц</button>
      <button class="btn ghost sm" style="margin-top:8px" onclick="openReport('days=60')">За 2 месяца</button>
    </div>
    <div class="muted small" style="margin:14px 0 6px">Произвольный период</div>
    <div class="card">
      <div class="mfield" style="margin-bottom:8px"><label>С</label><input id="rFrom" type="date" value="${fromDefault}"></div>
      <div class="mfield" style="margin-bottom:10px"><label>По</label><input id="rTo" type="date" value="${t}"></div>
      <button class="btn sm" onclick="openReportCustom()">📄 Сформировать PDF</button>
    </div>
    <div class="muted small" style="margin-top:10px">PDF откроется в новой вкладке/скачается.</div>`;
}
function openReport(q) { toast('Готовлю PDF…'); window.open('/api/reports?' + q, '_blank'); }
function openReportCustom() {
  const f = document.getElementById('rFrom').value, t = document.getElementById('rTo').value;
  if (!f || !t) return toast('Укажите период');
  if (f > t) return toast('«С» должно быть раньше «По»');
  openReport(`from=${encodeURIComponent(f)}&to=${encodeURIComponent(t)}`);
}

// ── Progress photos ─────────────────────────────────────────────────────────
function Photos() {
  document.getElementById('tabbar').style.display = '';
  view.innerHTML = `<span class="back" onclick="go('measure')">‹ Замеры</span><h1>Прогресс-фото</h1>
    <div class="muted small" style="margin-bottom:10px">Загрузи фото — ИИ опишет серию. Хранится на сервере.</div>
    <input id="photoFiles" type="file" accept="image/*" multiple style="display:none" onchange="uploadPhotos()">
    <button class="btn" onclick="document.getElementById('photoFiles').click()">📷 Добавить фото</button>
    <div id="photoList" style="margin-top:14px"><div class="card muted small">Загрузка…</div></div>`;
  loadPhotos();
}
async function loadPhotos() {
  const box = document.getElementById('photoList');
  try {
    const series = await api('/photos?limit=30');
    window._PHOTOS = series;
    if (!series.length) { box.innerHTML = '<div class="card muted">Пока нет фото.</div>'; return; }
    box.innerHTML = (series.length >= 2 ? `<button class="btn ghost" style="margin-bottom:12px" onclick="openPhotoCompare()">⇄ Сравнить «было / стало»</button>` : '') + series.map(s => `<div class="card">
      <div class="row sp"><b>${fmtPlanDate(s.taken_on)}</b><span class="small muted">${s.photo_count} фото</span></div>
      <div style="display:flex;gap:6px;overflow-x:auto;margin:8px 0">
        ${(s.photo_ids || []).map(id => `<img src="/api/photos/${id}/image" style="height:120px;border-radius:8px;object-fit:cover" loading="lazy">`).join('')}
      </div>
      ${s.ai_short ? `<div class="small">🤖 ${esc(s.ai_short)}</div>` : ''}
      ${s.notes ? `<div class="small muted" style="margin-top:4px">📝 ${esc(s.notes)}</div>` : ''}
      <button class="btn danger sm" style="margin-top:8px" onclick="delPhotoSeries('${esc(s.series_id)}')">Удалить</button>
    </div>`).join('');
  } catch (e) {
    if (e.status === 401 || e.code === 401) { document.getElementById('tabbar').style.display = 'none'; return Login(); }
    box.innerHTML = `<div class="card small" style="color:var(--danger)">${esc(e.message || 'ошибка')}</div>`;
  }
}
async function uploadPhotos() {
  const input = document.getElementById('photoFiles');
  if (!input.files || !input.files.length) return;
  const fd = new FormData();
  for (const f of input.files) fd.append('files', f);
  const box = document.getElementById('photoList');
  box.innerHTML = '<div class="card muted small">⏳ Загружаю и распознаю…</div>';
  try {
    const r = await fetch('/api/photos', { method: 'POST', body: fd, credentials: 'include' });
    if (!r.ok) { let d = ''; try { d = (await r.json()).detail; } catch {} throw new Error(d || r.status); }
    const j = await r.json();
    toast('Добавлено фото: ' + j.count);
  } catch (e) { toast(e.message || 'не удалось загрузить'); }
  input.value = '';
  loadPhotos();
}
function delPhotoSeries(sid) {
  confirmSheet('Удалить эти фото?', 'Серия и файлы будут удалены безвозвратно.', 'Удалить', true, async () => {
    try { await api('/photos/series/' + encodeURIComponent(sid), 'DELETE'); toast('Удалено'); loadPhotos(); }
    catch (e) { toast(e.message || 'не удалось'); }
  });
}
// ── Photo compare (before / after) ──────────────────────────────────────────
function nearestWeight(dateISO, meas) {
  if (!meas || !meas.length || !dateISO) return null;
  const t = new Date(dateISO + 'T00:00:00').getTime();
  let best = null, bestDiff = Infinity;
  for (const m of meas) {
    if (m.weight_kg == null || !m.taken_on) continue;
    const diff = Math.abs(new Date(m.taken_on + 'T00:00:00').getTime() - t);
    if (diff < bestDiff) { bestDiff = diff; best = m; }
  }
  return (best && bestDiff <= 21 * 864e5) ? best.weight_kg : null;  // within 3 weeks
}
async function openPhotoCompare() {
  const series = window._PHOTOS || [];
  if (series.length < 2) return toast('Нужно минимум 2 серии фото');
  document.getElementById('tabbar').style.display = '';
  let meas = [];
  try { meas = await api('/measurements'); } catch {}
  window._PCmeas = meas;
  const opts = sel => series.map((s, i) => `<option value="${i}" ${i === sel ? 'selected' : ''}>${fmtPlanDate(s.taken_on)} · ${s.photo_count} фото</option>`).join('');
  const ss = 'width:100%;border:none;background:transparent;color:var(--txt);font-size:14px;outline:none';
  view.innerHTML = `<span class="back" onclick="go('photos')">‹ Фото</span><h2>Сравнение «было / стало»</h2>
    <div style="display:flex;gap:8px;margin-bottom:12px">
      <div class="mfield" style="flex:1"><label>Было</label><select id="pcA" onchange="renderPhotoCompare()" style="${ss}">${opts(series.length - 1)}</select></div>
      <div class="mfield" style="flex:1"><label>Стало</label><select id="pcB" onchange="renderPhotoCompare()" style="${ss}">${opts(0)}</select></div>
    </div><div id="pcResult"></div>`;
  renderPhotoCompare();
}
function renderPhotoCompare() {
  const series = window._PHOTOS || [], meas = window._PCmeas || [];
  const a = series[+document.getElementById('pcA').value], b = series[+document.getElementById('pcB').value];
  if (!a || !b) return;
  const col = (s, label) => {
    const wt = nearestWeight(s.taken_on, meas), img = (s.photo_ids || [])[0];
    return `<div style="flex:1;min-width:0;text-align:center">
      <div class="muted small">${label}</div>
      <div style="font-weight:600;margin:2px 0">${fmtPlanDate(s.taken_on)}</div>
      ${img != null ? `<img src="/api/photos/${img}/image" style="width:100%;border-radius:10px;object-fit:cover" loading="lazy">` : '<div class="card muted small">нет фото</div>'}
      <div class="small" style="margin-top:4px">${wt != null ? fmt(wt) + ' кг' : '<span class="muted">вес —</span>'}</div>
      ${(s.photo_ids || []).length > 1 ? `<div style="display:flex;gap:4px;overflow-x:auto;margin-top:6px">${s.photo_ids.slice(1).map(id => `<img src="/api/photos/${id}/image" style="height:52px;border-radius:6px;object-fit:cover" loading="lazy">`).join('')}</div>` : ''}</div>`;
  };
  const wa = nearestWeight(a.taken_on, meas), wb = nearestWeight(b.taken_on, meas);
  const delta = (wa != null && wb != null) ? Math.round((wb - wa) * 10) / 10 : null;
  document.getElementById('pcResult').innerHTML = `<div style="display:flex;gap:10px">${col(a, 'Было')}${col(b, 'Стало')}</div>
    ${delta != null ? `<div class="card" style="text-align:center;margin-top:14px"><span class="muted small">Δ веса между датами</span>
      <div style="font-size:22px;font-weight:700;color:${delta < 0 ? 'var(--success)' : delta > 0 ? 'var(--warn)' : 'var(--txt)'}">${delta > 0 ? '+' : ''}${fmt(delta)} кг</div></div>` : ''}`;
}

// ── Measurements ──────────────────────────────────────────────────────────
const MFIELDS = [['weight_kg', 'Вес, кг'], ['calf_cm', 'Голень, см'], ['thigh_cm', 'Бедро, см'], ['hips_cm', 'Бедра, см'], ['belly_cm', 'Живот, см'], ['waist_cm', 'Талия, см'], ['chest_cm', 'Грудь, см'], ['arm_cm', 'Рука, см'], ['neck_cm', 'Шея, см']];
async function Measure() {
  const last = await api('/measurements/last');
  view.innerHTML = `<div class="row sp"><h1>Замеры</h1><span class="back" onclick="go('measureHistory')">История ›</span></div>
    <div class="grid2" style="margin-top:8px">${MFIELDS.map(([k, l]) => `<div class="mfield"><label>${l}</label><input id="m_${k}" inputmode="decimal" value="${last && last[k] != null ? fmt(last[k]) : ''}" placeholder="—"></div>`).join('')}</div>
    <div class="field" style="margin-top:12px"><input id="mtext" placeholder="или: вес 82 талия 84"><span onclick="recToField('mtext',this)" style="cursor:pointer">🎤</span><span onclick="saveMeasureText()" style="color:var(--info);cursor:pointer">↑</span></div>
    <button class="btn" style="margin-top:12px" onclick="saveMeasure()">Сохранить замер</button>
    <div class="muted small" style="margin:18px 0 8px">Прогресс тела</div>
    <div class="card list-item" onclick="go('photos')"><div class="ic">📷</div><div style="flex:1"><b>Прогресс-фото</b><div class="small muted">снимки и сравнение «было / стало»</div></div><span class="muted">›</span></div>
    <div class="card list-item" onclick="go('measureHistory')"><div class="ic">📈</div><div style="flex:1"><b>Графики и история замеров</b><div class="small muted">динамика веса и объёмов</div></div><span class="muted">›</span></div>`;
}
async function saveMeasure() {
  const values = {};
  MFIELDS.forEach(([k]) => { const v = document.getElementById('m_' + k).value.trim(); if (v) values[k] = parseFloat(v.replace(',', '.')); });
  if (!Object.keys(values).length) return toast('Заполни хотя бы одно поле');
  await api('/measurements', 'POST', { values }); toast('Замер сохранён'); go('measureHistory');
}
async function saveMeasureText() {
  const t = document.getElementById('mtext').value.trim(); if (!t) return;
  await api('/measurements', 'POST', { text: t }); toast('Замер сохранён'); go('measureHistory');
}
async function MeasureHistory() {
  const rows = await api('/measurements?limit=30');
  STATE._m = rows;
  const metric = STATE._metric || 'weight_kg';
  const pts = rows.slice().reverse().filter(r => r[metric] != null).map(r => ({ label: shortDate(r.taken_on), value: Number(r[metric]) }));
  view.innerHTML = `<span class="back" onclick="go('measure')">‹ Замеры</span><h2>История</h2>
    <div class="tag-row" style="justify-content:flex-start">${MFIELDS.map(([k, l]) => `<span class="pill ${k === metric ? 'on' : ''}" onclick="setMetric('${k}')">${l.split(',')[0]}</span>`).join('')}</div>
    <div class="card" style="text-align:center">${pts.length ? lineChart(pts, 'var(--success)') : '<span class="muted small">Нет данных</span>'}</div>
    ${rows.filter(r => r[metric] != null).map(r => swipeRow(
      `<div class="row sp"><span class="muted small">${esc(fmtDate(r.taken_on, { weekday: 'short' }))}</span><span>${fmt(r[metric])}</span></div>`,
      `askDelMeasure(${r.id})`)).join('')}`;
}
function setMetric(m) { STATE._metric = m; MeasureHistory(); }

// ── Planning ────────────────────────────────────────────────────────────────
const WD_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];        // Monday = 0
const WD_FULL = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
function todayISO() { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }
function isoWeekday(iso) { const d = new Date(iso + 'T00:00:00'); return (d.getDay() + 6) % 7; } // 0=Mon
function nextOccurrenceISO(wd) { // wd: 0=Mon..6=Sun → next date (today counts)
  const t = new Date(todayISO() + 'T00:00:00'); const cur = (t.getDay() + 6) % 7;
  const ahead = (wd - cur + 7) % 7; t.setDate(t.getDate() + ahead);
  return new Date(t.getTime() - t.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}
// One configurable date formatter (UX-3) used app-wide. Reads the user's chosen
// date_format from window._SETTINGS (DMY default | YMD | MDY); optional weekday
// prefix. Display-only — storage stays ISO. weekday: false | 'short' | 'long'.
function fmtDate(iso, opts) {
  opts = opts || {};
  if (!iso || typeof iso !== 'string' || iso.length < 10) return iso || '';
  const Y = iso.slice(0, 4), M = iso.slice(5, 7), D = iso.slice(8, 10);
  const f = (window._SETTINGS && window._SETTINGS.date_format) || 'DMY';
  const base = f === 'YMD' ? `${Y}-${M}-${D}` : f === 'MDY' ? `${M}/${D}/${Y}` : `${D}-${M}-${Y}`;
  if (!opts.weekday) return base;
  const d = new Date(iso + 'T00:00:00');
  if (isNaN(d)) return base;
  return d.toLocaleDateString('ru-RU', { weekday: opts.weekday === 'long' ? 'long' : 'short' }) + ', ' + base;
}
function fmtPlanDate(iso) { return fmtDate(iso, { weekday: 'short' }); }
function repsLabel(ex) {
  if (ex.reps_text) return ex.reps_text;
  const a = ex.target_reps_min, b = ex.target_reps_max;
  if (a && b && a !== b) return a + '–' + b;
  if (a) return String(a);
  return '?';
}
function exLine(ex) {
  const sets = ex.target_sets || '?';
  const w = ex.target_weight ? ' · ' + fmt(ex.target_weight) + ' кг' : '';
  return `${sets}×${repsLabel(ex)}${w}`;
}

// list of upcoming plans
// UX3-3: Plans is create-only; the schedule overview lives in «Расписание».
function Plans() {
  view.innerHTML = `<span class="back" onclick="go('train')">‹ Тренировка</span>
    <h1>Планы</h1><div class="muted small" style="margin-bottom:16px">Создать план тренировок</div>
    <button class="btn" onclick="coachStart()">🧠 AI: собрать неделю по моим данным</button>
    <button class="btn ghost" style="margin-top:8px" onclick="planPasteSheet()">📝 Вставить готовый план текстом</button>
    <button class="btn ghost" style="margin-top:8px" onclick="newPlan()">➕ Запланировать день вручную</button>
    <div class="muted small" style="margin-top:14px">Есть готовый сплит? <span style="color:var(--info);cursor:pointer" onclick="go('routines')">🗂 Шаблоны ›</span></div>
    <div class="muted small" style="margin-top:18px">Посмотреть запланированное — в <span style="color:var(--info);cursor:pointer" onclick="go('schedule')">Расписании ›</span></div>`;
}

// ── Flagship: AI coach «Собери следующую неделю» ─────────────────────────────
// Thin client: the survey is optional; the heavy lifting (reading the user's own
// training data + deep analysis) happens server-side in app/bot/services/week_coach.
const COACH_Q = [
  ['energy', 'Самочувствие / энергия', 'бодрый · средне · вымотан'],
  ['sleep', 'Сон последние дни', 'хорошо · 5–6 ч · плохо'],
  ['stress', 'Стресс / нагрузка вне зала', 'норма · высокий'],
  ['injury', 'Травмы / боли', 'нет · тянет поясницу'],
  ['focus', 'На чём сделать акцент', 'грудь · ноги · общая форма'],
];
function nextWeekMondayISO() { // Monday of the NEXT calendar week
  const t = new Date(todayISO() + 'T00:00:00'); const cur = (t.getDay() + 6) % 7;
  t.setDate(t.getDate() + (7 - cur));
  return new Date(t.getTime() - t.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}
async function coachStart() {
  let cfg = {}; try { cfg = await api('/coach/context'); } catch {}
  const a = cfg.answers || {}; window._coachMode = cfg.recovery_mode || 'natural';
  const mon = nextWeekMondayISO();
  sheet(`<h2 style="margin-bottom:4px">🧠 Собрать следующую неделю</h2>
    <div class="muted small" style="margin-bottom:12px">Неделя с ${fmtPlanDate(mon)}. Пару слов о себе — всё опционально, можно пропустить. Остальное наставник возьмёт из твоих тренировок.</div>
    ${COACH_Q.map(([k, l, ph]) => `<div class="mfield" style="margin-bottom:8px"><label>${l}</label><input id="cq_${k}" value="${esc(a[k] || '')}" placeholder="${esc(ph)}"></div>`).join('')}
    <div class="muted small" style="margin:10px 0 6px">Режим восстановления</div>
    <div class="tag-row" style="justify-content:flex-start">
      <span class="pill ${window._coachMode !== 'enhanced' ? 'on' : ''}" id="cm_natural" onclick="coachMode('natural')">Натуральное</span>
      <span class="pill ${window._coachMode === 'enhanced' ? 'on' : ''}" id="cm_enhanced" onclick="coachMode('enhanced')">Усиленное</span>
    </div>
    <div class="muted small" style="margin:6px 0 12px">Влияет только на объём, частоту и прогрессию нагрузки. Наставник не даёт медицинских или фарм-советов.</div>
    <button class="btn" onclick="coachGenerate('${mon}')">🧠 Собрать неделю</button>`);
}
function coachMode(m) {
  window._coachMode = m;
  const n = document.getElementById('cm_natural'), e = document.getElementById('cm_enhanced');
  if (n) n.classList.toggle('on', m === 'natural');
  if (e) e.classList.toggle('on', m === 'enhanced');
}
async function coachGenerate(fromDate) {
  const answers = {};
  COACH_Q.forEach(([k]) => { const el = document.getElementById('cq_' + k); if (el && el.value.trim()) answers[k] = el.value.trim(); });
  const mode = window._coachMode || 'natural';
  try { await api('/coach/context', 'POST', { answers, recovery_mode: mode }); } catch {}
  closeSheet();
  document.getElementById('tabbar').style.display = '';
  view.scrollTo(0, 0);
  view.innerHTML = `<div class="card" style="text-align:center;padding:46px 16px">
    <div style="font-size:34px">🧠</div>
    <div style="margin-top:12px"><b>Наставник анализирует твои данные…</b></div>
    <div class="muted small" style="margin-top:6px">Разбирает прогресс, объём и заметки и собирает неделю. Это глубокая модель — обычно 10–40 секунд.</div></div>`;
  try {
    const r = await api('/coach/generate-week', 'POST', { from_date: fromDate || null });
    window._COACHWEEK = r;
    coachPreview();
  } catch (e) {
    view.innerHTML = `<span class="back" onclick="go('plans')">‹ Планы</span>
      <div class="banner warn"><div class="b-title" style="color:var(--warn)">Наставник недоступен</div>
        <div class="small" style="margin-top:4px;color:var(--warn)">${esc(e.message || 'попробуй ещё раз')}</div></div>
      <button class="btn" onclick="coachStart()">↻ Попробовать снова</button>`;
  }
}
function coachPreview() {
  const r = window._COACHWEEK || { days: [] };
  const days = r.days || [];
  const dayCard = (d, i) => `<div class="card">
      <div class="row sp"><b>${WD_FULL[d.weekday] || ''}${d.focus_label ? ' · ' + esc(d.focus_label) : ''}</b>
        <span style="color:var(--danger);cursor:pointer;font-size:13px" onclick="coachDropDay(${i})">убрать</span></div>
      <div class="small muted" style="margin:2px 0 4px">${fmtPlanDate(d.date)}</div>
      ${(d.exercises || []).map(ex => `<div class="small" style="margin-top:2px">• ${esc(ex.name)} <span class="muted">— ${exLine(ex)}</span></div>`).join('')}
      ${d.notes ? `<div class="small muted" style="margin-top:5px">📝 ${esc(d.notes)}</div>` : ''}</div>`;
  view.innerHTML = `<span class="back" onclick="go('plans')">‹ Планы</span>
    <h1 style="margin-bottom:2px">🧠 Неделя от наставника</h1>
    <div class="muted small" style="margin-bottom:12px">Предпросмотр — ничего ещё не сохранено.</div>
    ${r.rationale ? `<div class="banner info"><div class="small" style="color:var(--info)"><b>Почему так</b></div><div class="small" style="margin-top:4px;color:var(--info)">${esc(r.rationale)}</div></div>` : ''}
    ${(r.flags || []).length ? `<div class="banner warn"><div class="small" style="color:var(--warn)"><b>⚠️ Обрати внимание</b></div>${r.flags.map(f => `<div class="small" style="margin-top:3px;color:var(--warn)">• ${esc(f)}</div>`).join('')}</div>` : ''}
    ${days.length ? days.map(dayCard).join('') : '<div class="card muted">Наставник не вернул дней. Попробуй перегенерировать.</div>'}
    ${days.length ? `<button class="btn success" style="margin-top:6px" onclick="coachApply()">💾 Сохранить в расписание</button>` : ''}
    <button class="btn ghost" style="margin-top:8px" onclick="coachStart()">↻ Перегенерировать</button>
    <div class="muted small" style="margin-top:12px;text-align:center">Тренировочные рекомендации, не медицинский совет.</div>`;
}
function coachDropDay(i) {
  if (!window._COACHWEEK) return;
  window._COACHWEEK.days.splice(i, 1); coachPreview();
}
// UX2-4: guard for MASS plan creation (coach week, paste bulk, template apply).
// doCreate(mode) POSTs; on 409 (some target days already have plans) we ask
// Отменить / Заменить / Добавить and retry with the chosen mode.
async function createGuard(doCreate, onDone) {
  try { onDone(await doCreate(null)); }
  catch (e) {
    const occ = e.status === 409 && e.body && e.body.detail && e.body.detail.occupied;
    if (occ && occ.length) {
      window._cgFn = async (mode) => {
        closeSheet();
        try { onDone(await doCreate(mode)); } catch (err) { toast(err.message || 'не удалось'); }
      };
      sheet(`<h2>На некоторые дни уже есть планы</h2>
        <div class="muted small" style="margin:4px 0 12px">Заняты: ${occ.map(d => esc(fmtDate(d, { weekday: 'short' }))).join(', ')}. Что сделать?</div>
        <button class="btn danger" onclick="_cgPick('replace')">Заменить старое</button>
        <button class="btn" style="margin-top:8px" onclick="_cgPick('add')">Добавить вторым</button>
        <button class="btn ghost" style="margin-top:8px" onclick="closeSheet()">Отменить</button>`);
    } else { toast(e.message || 'не удалось сохранить'); }
  }
}
function _cgPick(mode) { const f = window._cgFn; window._cgFn = null; if (f) f(mode); }
function coachApply() {
  const r = window._COACHWEEK || { days: [] };
  if (!(r.days || []).length) return toast('Нет дней для сохранения');
  createGuard(
    mode => api('/coach/apply', 'POST', { days: r.days, mode }),
    res => { window._COACHWEEK = null; toast(`Сохранено в расписание: ${res.saved}`); go('schedule'); });
}

function newPlan(dateISO) {
  window._PLAN = { id: null, date: dateISO || todayISO(), focus: '', notes: '', exercises: [] };
  go('planEdit', 'new');
}
// Phase 1: a tap on a planned workout opens read-only PlanView; starting is an
// explicit button only (no accidental start). `from` is where Back returns.
function openPlan(pid, from) { STATE.planFrom = from || 'schedule'; go('planView', pid); }
async function PlanView(pid) {
  document.getElementById('tabbar').style.display = '';
  let p;
  try { p = await api('/plans/' + pid); }
  catch (e) { if (e.code === 401) throw e; view.innerHTML = `<span class="back" onclick="go(STATE.planFrom||'schedule')">‹ Назад</span><div class="card">План не найден.</div>`; return; }
  const exItems = (p.exercises || []).length ? p.exercises.map(ex => `<div class="card ex-row">
      <b>${esc(ex.name)}</b><div class="small muted">${esc(exLine(ex))}</div>${ex.notes ? `<div class="small muted" style="margin-top:3px">📝 ${esc(ex.notes)}</div>` : ''}</div>`).join('')
    : '<div class="card muted small">Упражнения не добавлены</div>';
  view.innerHTML = `<span class="back" onclick="go(STATE.planFrom||'schedule')">‹ Назад</span>
    <h2 style="margin-bottom:2px">${esc(p.focus_label || 'Тренировка')}</h2>
    <div class="muted small" style="margin-bottom:12px">${fmtPlanDate(p.planned_date)}</div>
    ${p.notes ? `<div class="card small muted">📝 ${esc(p.notes)}</div>` : ''}
    ${exItems}
    <button class="btn success" style="margin-top:14px" onclick="startFromPlan(${p.id})">▶ Начать тренировку</button>
    <button class="btn ghost" style="margin-top:8px" onclick="go('planEdit',${p.id})">✏️ Редактировать</button>
    <button class="btn ghost" style="margin-top:8px" onclick="planToTemplate(${p.id})">💾 Сохранить как шаблон</button>
    <button class="btn danger" style="margin-top:8px" onclick="askDeletePlan(${p.id}, () => go(STATE.planFrom||'schedule'))">🗑 Удалить</button>`;
}

// ── Schedule (view-only: day / week / month) ────────────────────────────────
function isoOf(d) { return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }
function addDaysISO(iso, n) { const d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() + n); return isoOf(d); }
function mondayISO(iso) { return addDaysISO(iso, -isoWeekday(iso)); }
function fmtFullDate(iso) { return fmtDate(iso, { weekday: 'long' }); }
function fmtDM(iso) { return fmtDate(iso); }  // no weekday — Schedule week rows already show WD_SHORT icon
function _byDate(list) { const m = {}; list.forEach(p => { (m[p.planned_date] = m[p.planned_date] || []).push(p); }); return m; }
// A plan day counts as «rest» when its focus is «Отдых» or it has no exercises (UX3-5).
function _isRestPlan(p) { return !(p.exercises || []).length || /отдых/i.test(p.focus_label || ''); }
// Shared month calendar grid (UX3-4 manual create, UX3-5 schedule month).
// `marks` = {iso:'plan'|'rest'} → dot; `sel` = highlighted ISO; `pick` = fn name
// called pick('<iso>'); `nav` = fn name called nav(±1) for prev/next month.
function monthCalendar(anchorISO, marks, sel, pick, nav) {
  const a = new Date(anchorISO + 'T00:00:00');
  const y = a.getFullYear(), mo = a.getMonth();
  const firstISO = isoOf(new Date(y, mo, 1));
  const lastDay = new Date(y, mo + 1, 0);
  const gridStart = mondayISO(firstISO);
  const weeks = Math.ceil((isoWeekday(firstISO) + lastDay.getDate()) / 7);
  const today = todayISO();
  let cells = '';
  for (let i = 0; i < weeks * 7; i++) {
    const d = addDaysISO(gridStart, i);
    const dt = new Date(d + 'T00:00:00');
    const inMonth = dt.getMonth() === mo;
    const isToday = d === today, isSel = sel && d === sel, mk = marks && marks[d];
    // Today gets an outline ring (distinct from the filled blue «selected» day).
    const bg = isSel ? 'background:var(--info);color:#fff'
      : (isToday ? 'box-shadow:inset 0 0 0 2px var(--info);color:var(--info);font-weight:700' : (inMonth ? '' : 'opacity:.32'));
    const dotc = isSel ? '#fff' : (mk === 'rest' ? 'var(--txt3)' : 'var(--info)');
    const dot = mk ? `<div style="width:5px;height:5px;border-radius:50%;background:${dotc};margin:3px auto 0"></div>`
      : (isToday && !isSel ? '<div style="font-size:8px;color:var(--info);margin-top:1px;line-height:1">сегодня</div>' : '<div style="height:8px"></div>');
    cells += `<div onclick="${pick}('${d}')" style="text-align:center;padding:7px 0;border-radius:9px;cursor:pointer;${bg}">
      <div style="font-size:14px">${dt.getDate()}</div>${dot}</div>`;
  }
  const head = WD_SHORT.map(w => `<div style="text-align:center;font-size:11px;color:var(--txt2);padding-bottom:5px">${w}</div>`).join('');
  const title = a.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' });
  const navRow = nav
    ? `<div class="row sp" style="margin-bottom:10px"><span class="back" style="margin:0;font-size:21px" onclick="${nav}(-1)">‹</span><b style="text-transform:capitalize">${esc(title)}</b><span class="back" style="margin:0;font-size:21px" onclick="${nav}(1)">›</span></div>`
    : `<b style="text-transform:capitalize;display:block;margin-bottom:10px">${esc(title)}</b>`;
  return `<div class="card">${navRow}<div style="display:grid;grid-template-columns:repeat(7,1fr);gap:3px">${head}${cells}</div></div>`;
}

async function Schedule() {
  document.getElementById('tabbar').style.display = '';
  if (!STATE.schedMode) STATE.schedMode = 'day';
  if (!STATE.schedDate) STATE.schedDate = todayISO();
  const mode = STATE.schedMode;
  const segMode = mode === 'feed' ? 'month' : mode;   // feed is a drill-down from «Месяц»
  const seg = [['day', 'День'], ['week', 'Неделя'], ['month', 'Месяц']].map(([k, l]) =>
    `<button class="${segMode === k ? 'on' : ''}" onclick="schedSet('${k}')">${l}</button>`).join('');
  view.innerHTML = `<span class="back" onclick="go('train')">‹ Тренировка</span><h1>Что запланировано</h1>
    <div class="seg">${seg}</div>
    <div id="schedBody"><div class="card muted small">Загрузка…</div></div>`;
  try {
    if (mode === 'day') await schedDay();
    else if (mode === 'week') await schedWeek();
    else if (mode === 'feed') await schedFeed();
    else await schedMonth();
  } catch (e) {
    if (e.status === 401 || e.code === 401) { document.getElementById('tabbar').style.display = 'none'; return Login(); }
    document.getElementById('schedBody').innerHTML = `<div class="card small" style="color:var(--danger)">${esc(e.message || 'ошибка')}</div>`;
  }
}
function schedSet(mode) { STATE.schedMode = mode; Schedule(); }
function schedNav(dir) {
  const iso = STATE.schedDate;
  if (STATE.schedMode === 'day') STATE.schedDate = addDaysISO(iso, dir);
  else if (STATE.schedMode === 'week') STATE.schedDate = addDaysISO(iso, dir * 7);
  else { const d = new Date(iso + 'T00:00:00'); d.setDate(1); d.setMonth(d.getMonth() + dir); STATE.schedDate = isoOf(d); }
  Schedule();
}
function schedHeader(title) {
  return `<div class="row sp" style="margin-bottom:10px">
    <span class="back" style="margin:0;font-size:22px" onclick="schedNav(-1)">‹</span>
    <b style="text-transform:capitalize">${esc(title)}</b>
    <span class="back" style="margin:0;font-size:22px" onclick="schedNav(1)">›</span></div>`;
}

async function schedDay() {
  const iso = STATE.schedDate;
  const list = await api(`/plans?from=${iso}&to=${iso}`);
  let html = schedHeader(fmtFullDate(iso));
  if (!list.length) {
    html += `<div class="card muted">На этот день ничего не запланировано</div>
      <button class="btn ghost" onclick="newPlan('${iso}')">➕ Запланировать тренировку</button>
      <button class="btn ghost" style="margin-top:8px" onclick="quickRest('${iso}')">💤 Отметить отдыхом</button>`;
  } else {
    html += list.map(p => swipeRow(
      `<div class="row sp"><b${_isRestPlan(p) ? ' class="muted"' : ''}>${_isRestPlan(p) ? '💤 Отдых' : esc(p.focus_label || 'Тренировка')}</b><span class="muted">›</span></div>
      ${_isRestPlan(p) ? '' : ((p.exercises || []).map(ex => `<div class="small muted" style="margin-top:3px">• ${esc(ex.name)} — ${esc(exLine(ex))}</div>`).join('') || '<div class="small muted">без упражнений</div>')}`,
      `askDeletePlan(${p.id}, schedDay)`, `openPlan(${p.id},'schedule')`)).join('');
  }
  document.getElementById('schedBody').innerHTML = html;
}

async function schedWeek() {
  const mon = mondayISO(STATE.schedDate), sun = addDaysISO(mon, 6);
  const byDate = _byDate(await api(`/plans?from=${mon}&to=${sun}`));
  const today = todayISO();
  let rows = '';
  for (let i = 0; i < 7; i++) {
    const d = addDaysISO(mon, i);
    const plans = byDate[d] || [];
    const isToday = d === today;
    const first = plans[0];
    const label = !plans.length ? '—'
      : (plans.every(_isRestPlan) ? '💤 Отдых'
        : `${esc(first.focus_label || 'Тренировка')} · ${(first.exercises || []).length} упр.${plans.length > 1 ? ` (+${plans.length - 1})` : ''}`);
    const tap = plans.length > 1 ? `schedDayAt('${d}')` : `newPlan('${d}')`;
    const trail = plans.length
      ? '<span class="muted">›</span>'
      : `<span style="display:flex;gap:12px;align-items:center"><span style="cursor:pointer" onclick="event.stopPropagation();quickRest('${d}')" title="Отметить отдыхом">💤</span><span class="muted">＋</span></span>`;
    const rowContent = `<div class="ic">${WD_SHORT[i]}</div>
      <div style="flex:1"><b>${esc(fmtDM(d))}</b>${isToday ? ' <span class="small" style="color:var(--info)">сегодня</span>' : ''}
        <div class="small muted">${label}</div></div>
      ${trail}`;
    // exactly 1 plan → swipe-left deletes it; 0 or ≥2 → tap (create / open day list) (UX3-FIX-2)
    rows += plans.length === 1
      ? swipeRow(`<div class="row" style="gap:12px">${rowContent}</div>`, `askDeletePlan(${first.id}, Schedule)`, `feedOpenPlan(${first.id},'${d}')`)
      : `<div class="card list-item" style="${isToday ? 'box-shadow:inset 0 0 0 2px var(--info)' : ''}" onclick="${tap}">${rowContent}</div>`;
  }
  document.getElementById('schedBody').innerHTML = schedHeader(`${fmtDM(mon)} – ${fmtDM(sun)}`) + rows +
    `<button class="btn ghost sm" style="margin-top:12px" onclick="weekToTemplate()">💾 Сохранить неделю как шаблон</button>`;
}

// Month = overview grid (dots) via the shared monthCalendar; tapping a day opens
// the centered scrolling day-feed (UX3-5, Apple-calendar style).
async function schedMonth() {
  const a = new Date(STATE.schedDate + 'T00:00:00');
  const y = a.getFullYear(), mo = a.getMonth();
  const firstISO = isoOf(new Date(y, mo, 1)), lastISO = isoOf(new Date(y, mo + 1, 0));
  const byDate = _byDate(await api(`/plans?from=${firstISO}&to=${lastISO}`));
  const marks = {};
  Object.keys(byDate).forEach(d => { marks[d] = byDate[d].every(_isRestPlan) ? 'rest' : 'plan'; });
  document.getElementById('schedBody').innerHTML =
    monthCalendar(firstISO, marks, STATE.feedCenter || null, 'schedFeedAt', 'schedMonthNav') +
    '<div class="muted small" style="text-align:center;margin-top:2px">Тап по дню — лента дней с выбранным по центру</div>';
}
function schedMonthNav(dir) {
  const d = new Date(STATE.schedDate + 'T00:00:00'); d.setDate(1); d.setMonth(d.getMonth() + dir);
  STATE.schedDate = isoOf(d); Schedule();
}
function schedDayAt(iso) { STATE.schedDate = iso; STATE.schedMode = 'day'; Schedule(); }

// ── Schedule month → centered scrolling day-feed (UX3-5) ────────────────────
function schedFeedAt(iso) {
  STATE.schedMode = 'feed'; STATE.feedCenter = iso; STATE.schedDate = iso;  // one source of truth
  STATE.feedStart = addDaysISO(iso, -14); STATE.feedEnd = addDaysISO(iso, 14);
  STATE._feedScroll = 'center'; Schedule();
}
// Opening a plan from the feed keeps that day selected, so returning re-centers on it (UX3-FIX-1).
function feedOpenPlan(pid, iso) { STATE.feedCenter = iso; STATE.schedDate = iso; STATE._feedScroll = 'center'; openPlan(pid, 'schedule'); }
// UX3-FIX-4: one-tap rest day on an empty day — no editor.
async function quickRest(iso) {
  try { await api('/plans', 'POST', { date: iso, focus_label: 'Отдых', exercises: [] }); toast('💤 Отдых'); Schedule(); }
  catch (e) { toast(e.message || 'не удалось'); }
}
function schedFeedMore(dir) {
  if (dir < 0) { STATE._feedAnchor = STATE.feedStart; STATE.feedStart = addDaysISO(STATE.feedStart, -14); }
  else { STATE.feedEnd = addDaysISO(STATE.feedEnd, 14); }
  Schedule();
}
function dayFeedCard(iso, plans, isCenter, isToday) {
  const hb = isToday ? ' style="color:var(--info)"' : (isCenter ? '' : ' class="muted"');
  const head = `<div class="small" style="margin:8px 2px 6px"><b${hb}>${WD_SHORT[isoWeekday(iso)]}, ${esc(fmtDate(iso))}${isToday ? ' · сегодня' : ''}</b></div>`;
  // each plan is its own swipe row (swipe left = delete that plan); rest is swipeable too (UX3-FIX-2)
  const body = !plans.length
    ? `<div class="card small muted">— ничего · <span style="color:var(--info);cursor:pointer" onclick="newPlan('${iso}')">＋ план</span> · <span style="color:var(--info);cursor:pointer" onclick="quickRest('${iso}')">💤 отдых</span></div>`
    : plans.map(p => swipeRow(
        _isRestPlan(p) ? '💤 <span class="muted">Отдых</span>'
          : `<b>${esc(p.focus_label || 'Тренировка')}</b> <span class="muted">· ${(p.exercises || []).length} упр. ›</span>`,
        `askDeletePlan(${p.id}, Schedule)`,
        _isRestPlan(p) ? null : `feedOpenPlan(${p.id},'${iso}')`)).join('');
  return `<div id="feed-${iso}" style="${isCenter ? 'box-shadow:inset 0 0 0 2px var(--info);border-radius:16px;padding:0 6px 4px;margin-bottom:10px' : 'margin-bottom:2px'}">${head}${body}</div>`;
}
async function schedFeed() {
  const center = STATE.feedCenter || todayISO();
  if (!STATE.feedStart) STATE.feedStart = addDaysISO(center, -14);
  if (!STATE.feedEnd) STATE.feedEnd = addDaysISO(center, 14);
  const byDate = _byDate(await api(`/plans?from=${STATE.feedStart}&to=${STATE.feedEnd}`));
  const today = todayISO();
  let rows = '';
  for (let d = STATE.feedStart; d <= STATE.feedEnd; d = addDaysISO(d, 1)) {
    rows += dayFeedCard(d, byDate[d] || [], d === center, d === today);
  }
  const head = `<div class="row sp" style="margin-bottom:10px"><span class="back" style="margin:0" onclick="schedSet('month')">‹ Месяц</span><b style="text-transform:capitalize">${esc(fmtDate(center, { weekday: 'long' }))}</b><span style="width:54px"></span></div>`;
  document.getElementById('schedBody').innerHTML = head +
    `<div style="text-align:center;margin-bottom:10px"><span class="back" onclick="schedFeedMore(-1)">↑ Раньше</span></div>` +
    rows +
    `<div style="text-align:center;margin-top:4px"><span class="back" onclick="schedFeedMore(1)">↓ Позже</span></div>`;
  requestAnimationFrame(() => {
    if (STATE._feedScroll === 'center') { const el = document.getElementById('feed-' + center); if (el) el.scrollIntoView({ block: 'center' }); }
    else if (STATE._feedAnchor) { const el = document.getElementById('feed-' + STATE._feedAnchor); if (el) el.scrollIntoView({ block: 'start' }); }
    STATE._feedScroll = null; STATE._feedAnchor = null;
  });
}

async function PlanEdit(param) {
  if (param && param !== 'new') {
    const p = await api('/plans/' + param);
    window._PLAN = {
      id: p.id, date: p.planned_date, focus: p.focus_label || '',
      notes: p.notes || '', exercises: (p.exercises || []).map(e => ({ ...e })),
    };
  } else if (!window._PLAN) {
    window._PLAN = { id: null, date: todayISO(), focus: '', notes: '', exercises: [] };
  }
  const P = window._PLAN;
  // UX3-4: calendar date picker that marks days already holding a plan.
  const calAnchor = P._cal || (P.date.slice(0, 7) + '-01');
  const marks = {};
  try {
    const ca = new Date(calAnchor + 'T00:00:00');
    const f = isoOf(new Date(ca.getFullYear(), ca.getMonth(), 1));
    const l = isoOf(new Date(ca.getFullYear(), ca.getMonth() + 1, 0));
    (await api(`/plans?from=${f}&to=${l}`)).forEach(p => { marks[p.planned_date] = _isRestPlan(p) ? 'rest' : 'plan'; });
  } catch {}
  const cal = monthCalendar(calAnchor, marks, P.date, 'planPickDate', 'planCalNav');
  const exItems = P.exercises.length ? P.exercises.map((ex, i) => `<div class="card list-item">
      <div style="flex:1"><b>${esc(ex.name)}</b><div class="small muted">${esc(exLine(ex))}</div></div>
      <span class="muted" onclick="planEditEx(${i})" style="cursor:pointer">✏️</span> &nbsp;
      <span style="color:var(--danger);cursor:pointer" onclick="planRemoveEx(${i})">🗑</span></div>`).join('')
    : '<div class="card muted small">Упражнения не добавлены</div>';
  view.innerHTML = `<span class="back" onclick="go('plans')">‹ Планы</span>
    <h2>${P.id ? 'Редактировать план' : 'Новый план'}</h2>
    <div class="muted small" style="margin:6px 0 6px">Дата · <b style="color:var(--txt);text-transform:capitalize">${esc(fmtDate(P.date, { weekday: 'long' }))}</b></div>
    ${cal}
    <div class="mfield"><label>Точная дата</label><input id="pl_date" type="date" value="${P.date}" onchange="planDateInput()"></div>
    <div class="muted small" style="margin-top:8px">Точки на календаре — дни, где уже есть план (серым — отдых).</div>
    <div class="mfield" style="margin-top:14px"><label>Фокус (что тренируем)</label>
      <input id="pl_focus" value="${esc(P.focus)}" placeholder="напр. Грудь / Трицепс"></div>
    <div class="muted small" style="margin:16px 0 6px">Упражнения</div>
    ${exItems}
    <button class="btn ghost" style="margin-top:8px" onclick="planAddExercise()">➕ Добавить упражнение</button>
    <div class="mfield" style="margin-top:16px"><label>Заметка к дню (необязательно)</label>
      <input id="pl_notes" value="${esc(P.notes)}" placeholder="напр. разминка 5 мин"></div>
    <button class="btn success" style="margin-top:16px" onclick="savePlan()">${P.id ? 'Сохранить изменения' : 'Сохранить план'}</button>
    ${P.id ? `<button class="btn danger" style="margin-top:8px" onclick="askDeletePlan(${P.id}, () => go('plans'))">Удалить план</button>` : ''}`;
}

function planSync() {
  const P = window._PLAN; if (!P) return;
  const d = document.getElementById('pl_date'); if (d) P.date = d.value || P.date;
  const f = document.getElementById('pl_focus'); if (f) P.focus = f.value;
  const n = document.getElementById('pl_notes'); if (n) P.notes = n.value;
}
function planDateInput() { planSync(); window._PLAN._cal = (window._PLAN.date || todayISO()).slice(0, 7) + '-01'; PlanEdit('new'); }
function planQuickDay(wd) { planSync(); window._PLAN.date = nextOccurrenceISO(wd); PlanEdit('new'); }
// UX3-4: pick a day from the calendar / page months.
function planPickDate(iso) { planSync(); window._PLAN.date = iso; window._PLAN._cal = iso.slice(0, 7) + '-01'; PlanEdit('new'); }
function planCalNav(dir) {
  planSync();
  const a = window._PLAN._cal || (window._PLAN.date.slice(0, 7) + '-01');
  const d = new Date(a + 'T00:00:00'); d.setMonth(d.getMonth() + dir);
  window._PLAN._cal = isoOf(new Date(d.getFullYear(), d.getMonth(), 1));
  PlanEdit('new');
}
function planRemoveEx(i) { planSync(); window._PLAN.exercises.splice(i, 1); PlanEdit('new'); }

// exercise picker (plan context) — reuses /exercises/* endpoints
function planAddExercise() {
  planSync();
  sheet(`<h2>Добавить упражнение</h2>
    <div class="field" style="margin-bottom:10px"><input id="pexq" placeholder="поиск…" oninput="planPickSearch()"><span>🔎</span></div>
    <div class="tag-row"><span class="pill on" id="ptabRec" onclick="planPickTab('rec')">Недавние</span>
      <span class="pill" id="ptabGrp" onclick="planPickTab('grp')">По группам</span></div>
    <div id="ppickbody"></div>`);
  planPickTab('rec');
}
async function planPickTab(t) {
  document.getElementById('ptabRec').classList.toggle('on', t === 'rec');
  document.getElementById('ptabGrp').classList.toggle('on', t === 'grp');
  const body = document.getElementById('ppickbody');
  if (t === 'rec') {
    const r = await api('/exercises/recent');
    body.innerHTML = r.length ? r.map(x => planPickRow(x.name, x.image)).join('') : '<div class="muted small">Пусто — выбери по группам.</div>';
  } else {
    const g = await api('/exercises/groups');
    body.innerHTML = g.map(x => `<div class="list-item" onclick="planPickGroup('${x.group}','${esc(x.label)}')"><div style="flex:1">${esc(x.label)}</div><span class="muted small">${x.count} ›</span></div>`).join('');
  }
}
async function planPickGroup(g, label) {
  const list = await api('/exercises/catalog?group=' + g);
  document.getElementById('ppickbody').innerHTML = `<div class="back" onclick="planPickTab('grp')">‹ ${label}</div>` + list.map(x => planPickRow(x.name, x.image)).join('');
}
async function planPickSearch() {
  const q = document.getElementById('pexq').value.trim(); if (q.length < 2) return;
  const r = await api('/exercises/search?q=' + encodeURIComponent(q));
  document.getElementById('ppickbody').innerHTML = r.map(x => planPickRow(x.name, x.image)).join('') || '<div class="muted small">Ничего не найдено</div>';
}
function planPickRow(name, image) {
  return `<div class="list-item" onclick='planChooseEx(${esc(JSON.stringify(name))})'>${_exThumb(image)}<div style="flex:1">${esc(name)}</div><span style="color:var(--info)">＋</span></div>`;
}
function planChooseEx(name) { openPlanTarget(name, -1); }

// target sheet (sets / reps / weight) — for new (idx=-1) or existing exercise
function openPlanTarget(name, idx) {
  const ex = idx >= 0 ? window._PLAN.exercises[idx] : { name, target_sets: 3, target_reps_min: 10, target_reps_max: 10, target_weight: null, reps_text: null };
  const wVal = ex.target_weight != null ? fmt(ex.target_weight) : '';
  sheet(`<div class="muted small">${esc(name)}</div><h2>${idx >= 0 ? 'Цель' : 'Новое упражнение'}</h2>
    ${stepRow([['psets', ex.target_sets || 3, 'подх.', 1], ['prmin', ex.target_reps_min || 10, 'повт. от', 1]])}
    ${stepRow([['prmax', ex.target_reps_max || ex.target_reps_min || 10, 'повт. до', 1], ['pweight', wVal === '' ? 0 : wVal, 'кг', 2.5]])}
    <div class="tag-row"><span class="pill ${ex.reps_text ? 'on' : ''}" id="pfail" onclick="tag(this)" data-tag="x">До отказа</span></div>
    <div class="muted small" style="margin:2px 0 10px">Вес можно оставить 0, если без веса/по самочувствию</div>
    <button class="btn" onclick='planSaveTarget(${idx},${esc(JSON.stringify(name))})'>✓ ${idx >= 0 ? 'Сохранить' : 'Добавить'}</button>`);
}
function planEditEx(i) { planSync(); openPlanTarget(window._PLAN.exercises[i].name, i); }
function planSaveTarget(idx, name) {
  const g = id => { const e = document.getElementById('f_' + id); return e ? parseFloat(e.value) : null; };
  const failure = document.getElementById('pfail').classList.contains('on');
  const sets = g('psets') || null;
  let rmin = g('prmin') || null, rmax = g('prmax') || null;
  if (rmin && rmax && rmax < rmin) rmax = rmin;
  const w = g('pweight'); const weight = (w && w > 0) ? w : null;
  const ex = {
    name, target_sets: sets,
    target_reps_min: failure ? null : rmin,
    target_reps_max: failure ? null : rmax,
    target_weight: weight,
    reps_text: failure ? 'до отказа' : null,
  };
  if (idx >= 0) window._PLAN.exercises[idx] = ex;
  else window._PLAN.exercises.push(ex);
  closeSheet(); PlanEdit('new');
}

async function savePlan() {
  planSync();
  const P = window._PLAN;
  if (!P.date) return toast('Укажи дату');
  if (!P.exercises.length && !P.focus) return toast('Добавь фокус или упражнения');
  const payload = { date: P.date, focus_label: P.focus || null, notes: P.notes || null, exercises: P.exercises };
  if (P.id) {  // editing an existing plan — keep its identity, no busy-day prompt
    try { await api('/plans/' + P.id, 'PATCH', payload); window._PLAN = null; toast('План сохранён'); go('plans'); }
    catch (e) { toast(e.message || 'не удалось сохранить'); }
    return;
  }
  // NEW plan: if the day already has plan(s), ask Replace / Add-second.
  let existing = [];
  try { existing = await api(`/plans?from=${P.date}&to=${P.date}`); } catch {}
  if (existing.length) {
    window._BUSY = existing.map(p => p.id);
    sheet(`<h2>На этот день уже есть план</h2>
      <div class="muted small" style="margin:4px 0 14px">${esc(fmtPlanDate(P.date))} — ${existing.length} ${plural(existing.length, 'план', 'плана', 'планов')}. Что сделать?</div>
      <button class="btn danger" onclick="planResolveBusy('replace')">Заменить существующий</button>
      <button class="btn" style="margin-top:8px" onclick="planResolveBusy('add')">Добавить вторым</button>
      <button class="btn ghost" style="margin-top:8px" onclick="closeSheet()">Отмена</button>`);
    return;
  }
  planCreate(payload);
}
async function planCreate(payload) {
  try { await api('/plans', 'POST', payload); window._PLAN = null; toast('План сохранён'); go('plans'); }
  catch (e) { toast(e.message || 'не удалось сохранить'); }
}
async function planResolveBusy(mode) {
  const P = window._PLAN; if (!P) { closeSheet(); return; }
  const payload = { date: P.date, focus_label: P.focus || null, notes: P.notes || null, exercises: P.exercises };
  closeSheet();
  try {
    if (mode === 'replace') { for (const id of (window._BUSY || [])) await api('/plans/' + id, 'DELETE'); }
    window._BUSY = null;
    await planCreate(payload);
  } catch (e) { toast(e.message || 'не удалось сохранить'); }
}
async function deletePlan(id, after) {
  try {
    await api('/plans/' + id, 'DELETE'); window._PLAN = null; toast('План удалён');
    if (typeof after === 'function') after(); else go('plans');
  } catch (e) { toast(e.message); }
}
// Confirm wrapper shared by PlanView, PlanEdit and the day-list quick delete.
function askDeletePlan(id, after) {
  confirmSheet('Удалить план?', 'План будет убран из расписания.', 'Да, удалить', true, () => deletePlan(id, after));
}
// Swipe/🗑 delete for History and MeasureHistory (UX3-2), with confirmation.
function askDelWorkout(id) {
  confirmSheet('Удалить тренировку?', 'Тренировка и её подходы будут удалены.', 'Да, удалить', true, async () => {
    try { await api('/workouts/' + id, 'DELETE'); toast('Удалено'); History(); } catch (e) { toast(e.message || 'не удалось'); }
  });
}
function askDelMeasure(id) {
  confirmSheet('Удалить замер?', 'Запись замера за эту дату будет удалена.', 'Да, удалить', true, async () => {
    try { await api('/measurements/' + id, 'DELETE'); toast('Удалено'); MeasureHistory(); } catch (e) { toast(e.message || 'не удалось'); }
  });
}

// AI free-text → preview → bulk save
function planPasteSheet() {
  sheet(`<h2>План текстом</h2>
    <div class="muted small" style="margin-bottom:8px">Вставь план в свободной форме — ИИ разберёт по дням.<br>
      Напр.: «Пн — Грудь: жим 4×8-12 80кг, разводка 3×12. Ср — Спина: тяга 4×8 90кг»</div>
    <textarea id="planText" style="width:100%;min-height:120px;border:1px solid var(--line);border-radius:10px;padding:10px;background:var(--card);color:var(--txt);font-size:15px"></textarea>
    <button class="btn" style="margin-top:10px" onclick="planParse()">⏳ Разобрать</button>`);
}
async function planParse() {
  const t = document.getElementById('planText').value.trim();
  if (!t) return toast('Пустой текст');
  const btn = event.target; btn.textContent = '⏳ Разбираю…'; btn.disabled = true;
  try {
    const r = await api('/plans/parse', 'POST', { text: t });
    window._PARSED = r.days;
    const preview = r.days.map(d => `<div class="card" style="margin-bottom:8px">
      <div class="row sp"><b>${esc(d.focus_label || 'Тренировка')}</b><span class="small muted">${fmtPlanDate(d.date)}</span></div>
      ${(d.exercises || []).map(ex => `<div class="small muted" style="margin-top:3px">• ${esc(ex.name)} — ${esc(exLine(ex))}</div>`).join('') || '<div class="small muted">отдых / без упражнений</div>'}
    </div>`).join('');
    sheet(`<h2>Разобрано: ${r.days.length} дн.</h2>
      <div style="max-height:50vh;overflow:auto">${preview}</div>
      <button class="btn success" style="margin-top:12px" onclick="planConfirmBulk()">✓ Сохранить все</button>
      <button class="btn ghost" style="margin-top:8px" onclick="planPasteSheet()">Назад к тексту</button>`);
  } catch (e) {
    toast(e.message || 'не удалось разобрать');
    btn.textContent = '⏳ Разобрать'; btn.disabled = false;
  }
}
function planConfirmBulk() {
  const days = window._PARSED;
  createGuard(
    mode => api('/plans/bulk', 'POST', { days, mode }),
    r => { window._PARSED = null; closeSheet(); toast('Сохранено: ' + r.saved + ' дн.'); go('plans'); });
}

// ── Routines (reusable weekly templates) ────────────────────────────────────
async function Routines() {
  document.getElementById('tabbar').style.display = '';
  const list = await api('/routines');
  view.innerHTML = `<span class="back" onclick="go('plans')">‹ Планы</span><h1>Шаблоны</h1>
    <div class="muted small" style="margin-bottom:10px">Недельный сплит, который можно раскатать на несколько недель вперёд.</div>
    <div class="muted small" style="margin:4px 0 6px">Создать шаблон</div>
    <button class="btn ghost" onclick="weekToTemplate()">📅 Из недели расписания</button>
    <button class="btn ghost" style="margin-top:8px" onclick="routineEditNew()">✍️ Вручную</button>
    <div class="muted small" style="margin-top:10px">Ещё: «💾 Сохранить как шаблон» в дне плана и «💾 В шаблон» в тренировке из Истории.</div>
    <div style="margin-top:14px">${list.length ? list.map(r => `<div class="card">
      <div class="row sp"><b>${esc(r.name)}</b><span class="small muted">${(r.days || []).length} ${plural((r.days || []).length, 'день', 'дня', 'дней')}</span></div>
      <div class="small muted" style="margin:4px 0 8px">${(r.days || []).map(d => WD_SHORT[d.weekday]).join(' · ') || 'нет дней'}</div>
      <div style="display:flex;gap:8px">
        <button class="btn ghost sm" style="flex:1;margin:0" onclick="go('routineEdit',${r.id})">Изменить</button>
        <button class="btn sm" style="flex:1;margin:0" onclick="routineApplySheet(${r.id})">Применить</button></div></div>`).join('') : '<div class="card muted">Пока нет шаблонов.</div>'}</div>`;
}
function routineEditNew() { window._ROUTINE = { id: null, name: '', days: [] }; go('routineEdit', 'new'); }
// UX3-FEAT-1: «Сохранить как шаблон» from existing data (plan day / week / workout),
// with a mandatory editable name. Reuses POST /api/routines (apply keeps its dup guard).
function saveAsTemplate(days, defaultName) {
  window._TPL = days;
  sheet(`<h2>Сохранить как шаблон</h2>
    <div class="muted small" style="margin-bottom:10px">${days.length} ${plural(days.length, 'день', 'дня', 'дней')} · название можно изменить</div>
    <div class="mfield" style="margin-bottom:12px"><label>Название шаблона</label><input id="tplName" value="${esc(defaultName || '')}" placeholder="напр. Мой сплит"></div>
    <button class="btn" onclick="saveTemplateDo()">💾 Сохранить шаблон</button>`);
}
async function saveTemplateDo() {
  const name = (document.getElementById('tplName').value || '').trim();
  if (!name) return toast('Укажите название');
  try { await api('/routines', 'POST', { name, days: window._TPL || [] }); window._TPL = null; closeSheet(); toast('Шаблон сохранён'); go('routines'); }
  catch (e) { toast(e.message || 'не удалось'); }
}
async function planToTemplate(pid) {
  try {
    const p = await api('/plans/' + pid);
    saveAsTemplate([{ weekday: isoWeekday(p.planned_date), focus_label: _isRestPlan(p) ? 'Отдых' : (p.focus_label || null), exercises: _isRestPlan(p) ? [] : (p.exercises || []) }], p.focus_label || 'Шаблон');
  } catch (e) { toast(e.message || 'не удалось'); }
}
async function weekToTemplate() {
  const mon = mondayISO(STATE.schedDate || todayISO()), sun = addDaysISO(mon, 6);
  const byDate = _byDate(await api(`/plans?from=${mon}&to=${sun}`));
  const days = [];
  for (let i = 0; i < 7; i++) (byDate[addDaysISO(mon, i)] || []).forEach(p =>
    days.push({ weekday: i, focus_label: _isRestPlan(p) ? 'Отдых' : (p.focus_label || null), exercises: _isRestPlan(p) ? [] : (p.exercises || []) }));
  if (!days.length) return toast('На этой неделе нет планов');
  saveAsTemplate(days, `Неделя ${fmtDM(mon)}`);
}
async function workoutToTemplate(wid) {
  try {
    const day = await api(`/workouts/${wid}/template-day`);
    if (!(day.exercises || []).length) return toast('В тренировке нет рабочих подходов');
    saveAsTemplate([day], day.focus_label || 'Шаблон');
  } catch (e) { toast(e.message || 'не удалось'); }
}
async function RoutineEdit(param) {
  document.getElementById('tabbar').style.display = '';
  if (param && param !== 'new') {
    const r = await api('/routines/' + param);
    window._ROUTINE = { id: r.id, name: r.name, days: (r.days || []).map(d => ({ ...d, exercises: (d.exercises || []).map(e => ({ ...e })) })) };
  } else if (!window._ROUTINE) { window._ROUTINE = { id: null, name: '', days: [] }; }
  const R = window._ROUTINE;
  const dayItems = R.days.length ? R.days.map((d, i) => `<div class="card list-item" onclick="go('routineDay',${i})">
    <div class="ic">${WD_SHORT[d.weekday]}</div>
    <div style="flex:1"><b>${WD_FULL[d.weekday]}</b><div class="small muted">${esc(d.focus_label || 'без фокуса')} · ${(d.exercises || []).length} упр.</div></div>
    <span style="color:var(--danger);cursor:pointer" onclick="event.stopPropagation();routineRemoveDay(${i})">🗑</span></div>`).join('') : '<div class="card muted small">Дни не добавлены</div>';
  view.innerHTML = `<span class="back" onclick="go('routines')">‹ Шаблоны</span>
    <h2>${R.id ? 'Редактировать шаблон' : 'Новый шаблон'}</h2>
    <div class="mfield" style="margin-top:8px"><label>Название</label><input id="r_name" value="${esc(R.name)}" placeholder="напр. Сплит 3 дня"></div>
    <div class="muted small" style="margin:16px 0 6px">Дни недели</div>
    ${dayItems}
    <button class="btn ghost" style="margin-top:8px" onclick="routineAddDay()">➕ Добавить день</button>
    <button class="btn success" style="margin-top:16px" onclick="saveRoutine()">${R.id ? 'Сохранить' : 'Создать шаблон'}</button>
    ${R.id ? `<button class="btn danger" style="margin-top:8px" onclick="deleteRoutine(${R.id})">Удалить шаблон</button>` : ''}`;
}
function routineSyncName() { const e = document.getElementById('r_name'); if (e && window._ROUTINE) window._ROUTINE.name = e.value; }
function routineAddDay() {
  routineSyncName();
  sheet(`<h2>День недели</h2><div class="tag-row" style="flex-wrap:wrap">${WD_FULL.map((w, i) => `<span class="pill" onclick="routinePickDay(${i})">${w}</span>`).join('')}</div>`);
}
function routinePickDay(wd) {
  closeSheet(); routineSyncName();
  const R = window._ROUTINE;
  if (R.days.findIndex(d => d.weekday === wd) < 0) { R.days.push({ weekday: wd, focus_label: '', exercises: [] }); R.days.sort((a, b) => a.weekday - b.weekday); }
  go('routineDay', R.days.findIndex(d => d.weekday === wd));
}
function routineRemoveDay(i) { routineSyncName(); window._ROUTINE.days.splice(i, 1); RoutineEdit('new'); }
async function saveRoutine() {
  routineSyncName();
  const R = window._ROUTINE;
  if (!R.name.trim()) return toast('Укажите название');
  try {
    if (R.id) await api('/routines/' + R.id, 'PATCH', { name: R.name, days: R.days });
    else { const r = await api('/routines', 'POST', { name: R.name, days: R.days }); R.id = r.id; }
    toast('Сохранено'); go('routines');
  } catch (e) { toast(e.message || 'не удалось'); }
}
function deleteRoutine(id) { confirmSheet('Удалить шаблон?', 'Действие необратимо.', 'Удалить', true, async () => { try { await api('/routines/' + id, 'DELETE'); toast('Удалено'); go('routines'); } catch (e) { toast(e.message || 'не удалось'); } }); }
// Routine day editor (self-contained exercise picker → writes to the routine day)
function RoutineDay(i) {
  document.getElementById('tabbar').style.display = '';
  const R = window._ROUTINE, d = R && R.days[i];
  if (!d) return go('routines');
  window._RDi = i;
  const exItems = d.exercises.length ? d.exercises.map((ex, j) => `<div class="card list-item">
    <div style="flex:1"><b>${esc(ex.name)}</b><div class="small muted">${esc(exLine(ex))}</div></div>
    <span class="muted" style="cursor:pointer" onclick="rEditEx(${j})">✏️</span> &nbsp;
    <span style="color:var(--danger);cursor:pointer" onclick="rRemoveEx(${j})">🗑</span></div>`).join('') : '<div class="card muted small">Упражнения не добавлены</div>';
  view.innerHTML = `<span class="back" onclick="rDayBack()">‹ Шаблон</span><h2>${WD_FULL[d.weekday]}</h2>
    <div class="mfield" style="margin-top:8px"><label>Фокус</label><input id="rd_focus" value="${esc(d.focus_label || '')}" placeholder="напр. Грудь / Трицепс"></div>
    <div class="muted small" style="margin:16px 0 6px">Упражнения</div>
    ${exItems}
    <button class="btn ghost" style="margin-top:8px" onclick="rAddExercise()">➕ Добавить упражнение</button>
    <button class="btn success" style="margin-top:16px" onclick="rDayBack()">Готово</button>`;
}
function rDaySync() { const e = document.getElementById('rd_focus'); if (e && window._ROUTINE) window._ROUTINE.days[window._RDi].focus_label = e.value; }
function rDayBack() { rDaySync(); RoutineEdit('new'); }
function rRemoveEx(j) { rDaySync(); window._ROUTINE.days[window._RDi].exercises.splice(j, 1); RoutineDay(window._RDi); }
function rAddExercise() {
  rDaySync();
  sheet(`<h2>Добавить упражнение</h2>
    <div class="field" style="margin-bottom:10px"><input id="rexq" placeholder="поиск…" oninput="rPickSearch()"><span>🔎</span></div>
    <div class="tag-row"><span class="pill on" id="rtabRec" onclick="rPickTab('rec')">Недавние</span>
      <span class="pill" id="rtabGrp" onclick="rPickTab('grp')">По группам</span></div>
    <div id="rpickbody"></div>`);
  rPickTab('rec');
}
async function rPickTab(t) {
  document.getElementById('rtabRec').classList.toggle('on', t === 'rec');
  document.getElementById('rtabGrp').classList.toggle('on', t === 'grp');
  const body = document.getElementById('rpickbody');
  if (t === 'rec') { const r = await api('/exercises/recent'); body.innerHTML = r.length ? r.map(x => rPickRow(x.name, x.image)).join('') : '<div class="muted small">Пусто — выбери по группам.</div>'; }
  else { const g = await api('/exercises/groups'); body.innerHTML = g.map(x => `<div class="list-item" onclick="rPickGroup('${x.group}','${esc(x.label)}')"><div style="flex:1">${esc(x.label)}</div><span class="muted small">${x.count} ›</span></div>`).join(''); }
}
async function rPickGroup(g, label) { const list = await api('/exercises/catalog?group=' + g); document.getElementById('rpickbody').innerHTML = `<div class="back" onclick="rPickTab('grp')">‹ ${label}</div>` + list.map(x => rPickRow(x.name, x.image)).join(''); }
async function rPickSearch() { const q = document.getElementById('rexq').value.trim(); if (q.length < 2) return; const r = await api('/exercises/search?q=' + encodeURIComponent(q)); document.getElementById('rpickbody').innerHTML = r.map(x => rPickRow(x.name, x.image)).join('') || '<div class="muted small">Ничего не найдено</div>'; }
function rPickRow(name, image) { return `<div class="list-item" onclick='rChooseEx(${esc(JSON.stringify(name))})'>${_exThumb(image)}<div style="flex:1">${esc(name)}</div><span style="color:var(--info)">＋</span></div>`; }
function rChooseEx(name) { rOpenTarget(name, -1); }
function rEditEx(j) { rOpenTarget(window._ROUTINE.days[window._RDi].exercises[j].name, j); }
function rOpenTarget(name, idx) {
  const arr = window._ROUTINE.days[window._RDi].exercises;
  const ex = idx >= 0 ? arr[idx] : { name, target_sets: 3, target_reps_min: 10, target_reps_max: 10, target_weight: null, reps_text: null };
  const wVal = ex.target_weight != null ? fmt(ex.target_weight) : '';
  sheet(`<div class="muted small">${esc(name)}</div><h2>${idx >= 0 ? 'Цель' : 'Новое упражнение'}</h2>
    ${stepRow([['rsets', ex.target_sets || 3, 'подх.', 1], ['rrmin', ex.target_reps_min || 10, 'повт. от', 1]])}
    ${stepRow([['rrmax', ex.target_reps_max || ex.target_reps_min || 10, 'повт. до', 1], ['rweight', wVal === '' ? 0 : wVal, 'кг', 2.5]])}
    <div class="tag-row"><span class="pill ${ex.reps_text ? 'on' : ''}" id="rfail" onclick="tag(this)" data-tag="x">До отказа</span></div>
    <button class="btn" onclick='rSaveTarget(${idx},${esc(JSON.stringify(name))})'>✓ ${idx >= 0 ? 'Сохранить' : 'Добавить'}</button>`);
}
function rSaveTarget(idx, name) {
  const g = id => { const e = document.getElementById('f_' + id); return e ? parseFloat(e.value) : null; };
  const failure = document.getElementById('rfail').classList.contains('on');
  const sets = g('rsets') || null; let rmin = g('rrmin') || null, rmax = g('rrmax') || null;
  if (rmin && rmax && rmax < rmin) rmax = rmin;
  const w = g('rweight'); const weight = (w && w > 0) ? w : null;
  const ex = { name, target_sets: sets, target_reps_min: failure ? null : rmin, target_reps_max: failure ? null : rmax, target_weight: weight, reps_text: failure ? 'до отказа' : null };
  const arr = window._ROUTINE.days[window._RDi].exercises;
  if (idx >= 0) arr[idx] = ex; else arr.push(ex);
  closeSheet(); RoutineDay(window._RDi);
}
function routineApplySheet(id) {
  sheet(`<h2>Применить шаблон</h2><div class="muted small" style="margin-bottom:10px">Создаст планы на выбранное число недель вперёд.</div>
    <div class="mfield" style="margin-bottom:10px"><label>С даты</label><input id="ra_from" type="date" value="${todayISO()}"></div>
    <div class="mfield" style="margin-bottom:12px"><label>Недель</label><input id="ra_weeks" type="number" min="1" max="12" value="4"></div>
    <button class="btn" onclick="routineApplyDo(${id})">Применить</button>`);
}
function routineApplyDo(id) {
  const from = document.getElementById('ra_from').value, weeks = parseInt(document.getElementById('ra_weeks').value || '1', 10);
  createGuard(
    mode => api('/routines/' + id + '/apply', 'POST', { from_date: from, weeks, mode }),
    r => { closeSheet(); toast(`Создано планов: ${r.created}`); go('schedule'); });
}

// ── Settings (service for all + access management for admins) ────────────────
const TZ_PRESETS = [
  ['Калининград (UTC+2)', 'Europe/Kaliningrad'], ['Москва (UTC+3)', 'Europe/Moscow'],
  ['Самара (UTC+4)', 'Europe/Samara'], ['Екатеринбург (UTC+5)', 'Asia/Yekaterinburg'],
  ['Новосибирск (UTC+7)', 'Asia/Novosibirsk'], ['Владивосток (UTC+10)', 'Asia/Vladivostok'],
  ['Лиссабон (UTC+1)', 'Europe/Lisbon'], ['Лондон (UTC+0/1)', 'Europe/London'],
  ['Дубай (UTC+4)', 'Asia/Dubai'], ['Бангкок (UTC+7)', 'Asia/Bangkok'],
  ['Нью-Йорк (UTC-5/4)', 'America/New_York'], ['UTC', 'UTC'],
];
const WIPE_CONFIRM = {
  plans: ['Удалить все запланированные тренировки?', 'Удалятся и активные, и пропущенные планы. Действие необратимо.'],
  history: ['Удалить всю историю тренировок?', 'Все записанные подходы и сессии будут удалены. Действие необратимо.'],
  measurements: ['Удалить все замеры тела?', 'Все сохранённые замеры (вес, обхваты) будут удалены. Действие необратимо.'],
  photos: ['Удалить все прогресс-фото?', 'Записи в дневнике (file_id и AI-описания) будут удалены. Сами файлы остаются в Telegram. Действие необратимо.'],
  all: ['ПОЛНЫЙ СБРОС', 'Удалит ВСЁ: планы, тренировки, замеры, фото. Действие необратимо. Точно?'],
};
const WIPE_DONE = {
  plans: n => `Удалено запланированных: ${n}`, history: n => `Удалено тренировок: ${n}`,
  measurements: n => `Удалено замеров: ${n}`, photos: n => `Удалено записей о фото: ${n}`,
  all: r => `Сброшено: планов ${r.planned}, трен. ${r.workouts}, замеров ${r.measurements}, фото ${r.photos}`,
};

async function Settings() {
  document.getElementById('tabbar').style.display = '';
  view.innerHTML = `<span class="back" onclick="go('home')">‹ Главная</span><h1>Настройки</h1>
    <div id="setBody"><div class="card muted small">Загрузка…</div></div>`;
  let tzr, s, cfg = {}, admin = null;
  try {
    tzr = await api('/service/tz');
    s = await api('/service/stats');
    try { cfg = await api('/settings'); } catch { cfg = _cachedSettings() || {}; }
    // refresh the single source of truth from the server on every Settings visit
    window._SETTINGS = { ...(window._SETTINGS || {}), ...cfg }; _cacheSettings();
    try { admin = await api('/admin/users'); }            // 403 → not an admin (hide section)
    catch (e) { if (e.status === 401) throw e; admin = null; }
  } catch (e) {
    if (e.status === 401 || e.code === 401) { document.getElementById('tabbar').style.display = 'none'; return Login(); }
    return toast(e.message || 'Не удалось загрузить настройки');
  }
  window._ADMIN = admin;
  window._recoveryMode = cfg.recovery_mode || 'natural';
  const statRow = (l, v) => `<div class="row sp" style="padding:6px 0"><span class="muted small">${l}</span><span>${v}</span></div>`;
  document.getElementById('setBody').innerHTML = `
    <div class="muted small" style="margin:8px 0 6px">🌍 Часовой пояс</div>
    <div class="card">
      <div class="row sp"><span>Текущий</span><b id="tzCur">${esc(tzr.tz || 'UTC')}</b></div>
      <button class="btn ghost sm" style="margin-top:10px" onclick="tzGeo()">📍 Поделиться геолокацией</button>
      <button class="btn ghost sm" style="margin-top:8px" onclick="tzAuto()">🕒 Определить автоматически</button>
      <button class="btn ghost sm" style="margin-top:8px" onclick="tzPick()">Выбрать из списка</button>
    </div>
    <div class="muted small" style="margin:14px 0 6px">📊 Статистика</div>
    <div class="card">
      ${statRow('Планы (активные / всего)', `${s.planned_active || 0} / ${s.planned_total || 0}`)}
      ${statRow('Тренировки (заверш. / всего)', `${s.workouts_finished || 0} / ${s.workouts_total || 0}`)}
      ${statRow('Подходов', s.sets_total || 0)}
      ${statRow('Замеров', s.measurements_total || 0)}
      ${statRow('Фото', s.photos_total || 0)}
      ${statRow('AI-алиасов', s.aliases_total || 0)}
    </div>
    <div class="muted small" style="margin:14px 0 6px">🎯 Цели</div>
    <div class="card">
      <div class="mfield" style="margin-bottom:8px"><label>Целевой вес, кг</label><input id="goalWeight" type="number" step="0.1" value="${cfg.target_weight != null ? cfg.target_weight : ''}" placeholder="напр. 78"></div>
      <div class="mfield" style="margin-bottom:10px"><label>Тренировок в неделю</label><input id="goalWeekly" type="number" min="0" max="14" value="${cfg.weekly_goal != null ? cfg.weekly_goal : ''}" placeholder="напр. 3"></div>
      <button class="btn sm" onclick="saveGoals()">Сохранить цели</button>
    </div>
    <div class="muted small" style="margin:14px 0 6px">⏱ Таймер отдыха</div>
    <div class="card">
      <div class="row sp" style="padding:2px 0"><span>Автозапуск после подхода</span>
        <span class="switch ${cfg.rest_timer_enabled !== false ? 'on' : ''}" id="rtEnabled" onclick="toggleRestTimer(this)"></span></div>
      <div class="mfield" style="margin-top:10px"><label>Длительность, сек</label><input id="rtSeconds" type="number" min="5" max="600" value="${cfg.rest_timer_seconds || 90}"></div>
      <div class="tag-row" style="justify-content:flex-start;margin-top:8px">${[60, 90, 120, 180].map(s => `<span class="pill" onclick="document.getElementById('rtSeconds').value=${s}">${s} сек</span>`).join('')}</div>
      <button class="btn sm" style="margin-top:10px" onclick="saveRestTimer()">Сохранить таймер</button>
    </div>
    <div class="muted small" style="margin:14px 0 6px">🧠 ИИ-наставник</div>
    <div class="card">
      <div class="row sp" style="padding:2px 0"><span>Режим восстановления</span></div>
      <div class="seg" style="margin-top:8px;margin-bottom:0">
        <button class="${cfg.recovery_mode !== 'enhanced' ? 'on' : ''}" id="rmNatural" onclick="setRecoveryPill('natural')">Натуральное</button>
        <button class="${cfg.recovery_mode === 'enhanced' ? 'on' : ''}" id="rmEnhanced" onclick="setRecoveryPill('enhanced')">Усиленное</button>
      </div>
      <div class="muted small" style="margin:8px 0 0">Влияет только на объём, частоту и прогрессию, которые подбирает наставник. Это не медицинский и не фарм-совет.</div>
      <button class="btn sm" style="margin-top:10px" onclick="saveRecoveryMode()">Сохранить режим</button>
      <button class="btn ghost sm" style="margin-top:8px" onclick="clearCoachContext()">Очистить ответы опроса</button>
    </div>
    <div class="muted small" style="margin:14px 0 6px">📅 Формат даты</div>
    <div class="card">
      <div class="seg">
        ${[['DMY', 'ДД-ММ-ГГГГ'], ['YMD', 'ГГГГ-ММ-ДД'], ['MDY', 'ММ/ДД/ГГГГ']].map(([k, l]) => `<button class="${(cfg.date_format || 'DMY') === k ? 'on' : ''}" onclick="saveDateFormat('${k}')">${l}</button>`).join('')}
      </div>
      <div class="muted small" style="margin-top:8px">Применяется везде, где показывается дата. На хранение не влияет.</div>
    </div>
    <div class="muted small" style="margin:14px 0 6px">📦 Экспорт данных</div>
    <div class="card">
      <button class="btn ghost sm" onclick="window.open('/api/export?format=json','_blank')">Скачать всё (JSON)</button>
      <button class="btn ghost sm" style="margin-top:8px" onclick="window.open('/api/export?format=csv','_blank')">Скачать подходы (CSV)</button>
    </div>
    <div id="installCard"></div>
    <div class="muted small" style="margin:14px 0 6px">🧹 Очистка данных</div>
    <div class="card">
      <button class="btn ghost sm" onclick="wipeAsk('plans')">Очистить запланированные</button>
      <button class="btn ghost sm" style="margin-top:8px" onclick="wipeAsk('history')">Очистить историю</button>
      <button class="btn ghost sm" style="margin-top:8px" onclick="wipeAsk('measurements')">Очистить замеры</button>
      <button class="btn ghost sm" style="margin-top:8px" onclick="wipeAsk('photos')">Очистить фото</button>
      <button class="btn danger sm" style="margin-top:12px" onclick="wipeAsk('all')">⚠️ ПОЛНЫЙ СБРОС</button>
    </div>
    ${admin ? adminSection(admin) : ''}
    <div class="muted small" style="margin:16px 0 6px">ℹ️ О приложении</div>
    <div class="card"><div class="muted small">Изображения и часть данных упражнений — <b>Free Exercise DB</b> (yuhonas/free-exercise-db), public domain.</div></div>
    <button class="btn ghost" style="margin-top:16px" onclick="logout()">Выйти</button>`;
  renderInstallButton();
}
async function saveGoals() {
  const tw = document.getElementById('goalWeight').value.trim();
  const wg = document.getElementById('goalWeekly').value.trim();
  const body = wg === '' ? { weekly_goal: 0 } : { weekly_goal: parseInt(wg, 10) };
  if (tw === '') body.clear_target = true; else body.target_weight = parseFloat(tw);
  try { await api('/settings', 'PATCH', body); toast('Цели сохранены'); }
  catch (e) { toast(e.message || 'не удалось'); }
}
async function saveRestTimer() {
  const enabled = document.getElementById('rtEnabled').classList.contains('on');
  const secs = Math.max(5, Math.min(600, parseInt(document.getElementById('rtSeconds').value || '90', 10) || 90));
  try {
    await api('/settings', 'PATCH', { rest_timer_enabled: enabled, rest_timer_seconds: secs });
    window._SETTINGS = { ...(window._SETTINGS || {}), rest_timer_enabled: enabled, rest_timer_seconds: secs }; _cacheSettings();
    toast('Таймер сохранён');
  } catch (e) { toast(e.message || 'не удалось'); }
}
// WK-1: the iOS switch applies immediately — updates the single source of truth
// (window._SETTINGS), persists, and kills any running rest countdown when turned off.
async function toggleRestTimer(el) {
  el.classList.toggle('on');
  const enabled = el.classList.contains('on');
  const secs = Math.max(5, Math.min(600, parseInt((document.getElementById('rtSeconds') || {}).value || '90', 10) || 90));
  window._SETTINGS = { ...(window._SETTINGS || {}), rest_timer_enabled: enabled, rest_timer_seconds: secs }; _cacheSettings();
  if (!enabled) { stopTimer(); closeRest(); }   // disabling stops any countdown immediately
  try { await api('/settings', 'PATCH', { rest_timer_enabled: enabled, rest_timer_seconds: secs }); }
  catch (e) { toast(e.message || 'не сохранилось'); }
}
function setRecoveryPill(m) {
  window._recoveryMode = m;
  const n = document.getElementById('rmNatural'), e = document.getElementById('rmEnhanced');
  if (n) n.classList.toggle('on', m === 'natural');
  if (e) e.classList.toggle('on', m === 'enhanced');
}
async function saveRecoveryMode() {
  const m = window._recoveryMode || 'natural';
  try { await api('/settings', 'PATCH', { recovery_mode: m }); toast('Режим сохранён'); }
  catch (e) { toast(e.message || 'не удалось'); }
}
async function clearCoachContext() {
  try { await api('/coach/context', 'DELETE'); toast('Ответы опроса очищены'); }
  catch (e) { toast(e.message || 'не удалось'); }
}
async function saveDateFormat(f) {
  try {
    await api('/settings', 'PATCH', { date_format: f });
    window._SETTINGS = { ...(window._SETTINGS || {}), date_format: f };
    toast('Формат даты сохранён');
    if (STATE.tab === 'settings') Settings();
  } catch (e) { toast(e.message || 'не удалось'); }
}

// ── PWA install (Add to Home Screen) ────────────────────────────────────────
let _installEvt = null;
window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); _installEvt = e; renderInstallButton(); });
window.addEventListener('appinstalled', () => { _installEvt = null; renderInstallButton(); toast('Добавлено на экран'); });
function renderInstallButton() {
  const box = document.getElementById('installCard');
  if (!box) return;
  box.innerHTML = _installEvt
    ? `<div class="muted small" style="margin:14px 0 6px">📲 Приложение</div>
       <div class="card"><button class="btn ghost sm" onclick="installApp()">Добавить на экран</button></div>` : '';
}
async function installApp() {
  if (!_installEvt) return;
  _installEvt.prompt();
  try { await _installEvt.userChoice; } catch {}
  _installEvt = null; renderInstallButton();
}

// timezone
async function tzAuto() {
  let z = '';
  try { z = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch {}
  if (!z) return toast('Не удалось определить пояс');
  try { const r = await api('/service/tz', 'POST', { tz: z }); const el = document.getElementById('tzCur'); if (el) el.textContent = r.tz; toast('Часовой пояс: ' + r.tz); }
  catch (e) { toast(e.message || 'не удалось'); }
}
function tzGeo() {
  if (!navigator.geolocation) return toast('Геолокация недоступна');
  toast('Запрашиваю геопозицию…');
  navigator.geolocation.getCurrentPosition(async pos => {
    try {
      const r = await api('/service/tz/coords', 'POST', { lat: pos.coords.latitude, lon: pos.coords.longitude });
      const el = document.getElementById('tzCur'); if (el) el.textContent = r.tz;
      toast('Часовой пояс: ' + r.tz);
    } catch (e) { toast(e.message || 'не удалось определить пояс'); }
  }, () => toast('Геолокация отклонена'), { timeout: 10000, enableHighAccuracy: false });
}
function tzPick() {
  const cur = (document.getElementById('tzCur') || {}).textContent || '';
  sheet(`<h2>Часовой пояс</h2>${TZ_PRESETS.map(([l, n]) =>
    `<div class="list-item" onclick="tzSet('${n}')"><div style="flex:1">${esc(l)}</div>${n === cur.trim() ? '<span style="color:var(--success)">✓</span>' : '<span class="muted">›</span>'}</div>`).join('')}`);
}
async function tzSet(name) {
  try { const r = await api('/service/tz', 'POST', { tz: name }); closeSheet(); const el = document.getElementById('tzCur'); if (el) el.textContent = r.tz; toast('Часовой пояс: ' + r.tz); }
  catch (e) { toast(e.message || 'не удалось'); }
}

// wipes (two-step confirm)
function wipeAsk(what) {
  const [title, msg] = WIPE_CONFIRM[what];
  confirmSheet(title, msg, what === 'all' ? 'Да, сбросить всё' : 'Да, удалить', true, () => wipeDo(what));
}
async function wipeDo(what) {
  try { const r = await api('/service/wipe/' + what, 'POST'); toast('✅ ' + WIPE_DONE[what](r.deleted)); Settings(); }
  catch (e) { toast(e.message || 'не удалось'); }
}

// generic two-step confirm sheet
function confirmSheet(title, message, yesLabel, danger, onYes) {
  window._confirmFn = onYes;
  sheet(`<h2>${esc(title)}</h2>
    <div class="muted small" style="margin:4px 0 16px;white-space:pre-line">${esc(message)}</div>
    <button class="btn ${danger ? 'danger' : ''}" onclick="confirmRun()">${esc(yesLabel)}</button>
    <button class="btn ghost" style="margin-top:8px" onclick="closeSheet()">Отмена</button>`);
}
function confirmRun() { const f = window._confirmFn; window._confirmFn = null; closeSheet(); if (f) f(); }

// access management (admin only)
function accName(u) { return u.display_name || (u.username ? '@' + u.username : 'id ' + u.uid); }
function adminSection(users) {
  const pending = users.filter(u => u.status === 'pending');
  const others = users.filter(u => u.status !== 'pending');
  const badge = u => u.is_owner ? '<span class="pill ok">владелец</span>'
    : u.status === 'blocked' ? '<span class="pill warn">заблокирован</span>'
    : u.role === 'admin' ? '<span class="pill ok">админ</span>' : '';
  const pendRows = pending.length ? pending.map(u => `<div class="list-item">
      <div style="flex:1"><b>${esc(accName(u))}</b><div class="small muted">${u.username ? '@' + esc(u.username) + ' · ' : ''}id ${esc(u.uid)}</div></div>
      <span class="pill ok" style="cursor:pointer" onclick="accSet('${u.uid}',{status:'approved'})">✓</span> &nbsp;
      <span class="pill warn" style="cursor:pointer" onclick="accAskBlock('${u.uid}')">✕</span></div>`).join('')
    : '<div class="muted small" style="padding:8px 0">Нет заявок</div>';
  const userRows = others.length ? others.map(u => `<div class="list-item">
      <div style="flex:1"><b>${esc(accName(u))}</b> ${badge(u)}<div class="small muted">id ${esc(u.uid)}</div></div>
      ${u.is_owner ? '<span class="muted small">—</span>' : `<span class="muted" style="cursor:pointer;font-size:20px" onclick="accMenu('${u.uid}')">···</span>`}</div>`).join('')
    : '<div class="muted small" style="padding:8px 0">Пусто</div>';
  return `<div class="muted small" style="margin:18px 0 6px">🔑 Управление доступом</div>
    <div class="card"><div class="small muted" style="margin-bottom:4px">Заявки${pending.length ? ' · ' + pending.length : ''}</div>${pendRows}</div>
    <div class="card"><div class="small muted" style="margin-bottom:4px">Пользователи</div>${userRows}</div>
    <button class="btn ghost sm" onclick="accAddSheet()">➕ Добавить пользователя</button>
    <div class="muted small" style="margin:16px 0 6px">🧹 Обслуживание (админ)</div>
    <div class="card"><button class="btn ghost sm" onclick="accWipeAliases()">Очистить AI-кэш упражнений</button>
      <div class="small muted" style="margin-top:6px">Глобальный кэш имён — общий для всех пользователей.</div></div>`;
}
function accWipeAliases() {
  confirmSheet('Очистить кэш AI-нормализации?',
    'Сохранённые AI-разрешения имён упражнений будут удалены (глобально, для всех). В следующий раз каждое неизвестное название снова спросит у ИИ.',
    'Да, очистить', true, async () => {
      try { const r = await api('/admin/wipe-aliases', 'POST'); toast('✅ Очищено AI-алиасов: ' + r.deleted); }
      catch (e) { toast(e.message || 'не удалось'); }
    });
}
function _accFind(uid) { return (window._ADMIN || []).find(x => x.uid === uid); }
function accMenu(uid) {
  const u = _accFind(uid); if (!u) return;
  const items = [];
  if (u.status !== 'approved') items.push(`<div class="list-item" onclick="accSet('${uid}',{status:'approved'})"><div class="ic">✓</div>Одобрить доступ</div>`);
  if (u.role === 'admin') items.push(`<div class="list-item" onclick="accAskDemote('${uid}')"><div class="ic">⬇️</div>Снять администратора</div>`);
  else items.push(`<div class="list-item" onclick="accSet('${uid}',{role:'admin'})"><div class="ic">⭐</div>Сделать администратором</div>`);
  if (u.status === 'blocked') items.push(`<div class="list-item" onclick="accSet('${uid}',{status:'approved'})"><div class="ic">↩️</div>Разблокировать</div>`);
  else items.push(`<div class="list-item" style="color:var(--danger)" onclick="accAskBlock('${uid}')"><div class="ic">🚫</div>Заблокировать</div>`);
  sheet(`<div class="muted small">id ${esc(uid)}</div><h2>${esc(accName(u))}</h2>${items.join('')}`);
}
function accAskBlock(uid) {
  const u = _accFind(uid);
  confirmSheet('Заблокировать доступ?', u ? accName(u) : ('id ' + uid), 'Заблокировать', true, () => accSet(uid, { status: 'blocked' }));
}
function accAskDemote(uid) {
  const u = _accFind(uid);
  confirmSheet('Снять администратора?', u ? accName(u) : ('id ' + uid), 'Снять', true, () => accSet(uid, { role: 'user' }));
}
async function accSet(uid, patch) {
  closeSheet();
  try { await api('/admin/users/' + uid, 'PATCH', patch); toast('Готово'); Settings(); }
  catch (e) { toast(e.message || 'не удалось'); }
}
function accAddSheet() {
  sheet(`<h2>Добавить пользователя</h2>
    <div class="muted small" style="margin-bottom:10px">Введите Telegram-ID пользователя — он сразу получит доступ. Узнать свой ID можно через @userinfobot.</div>
    <div class="mfield" style="margin-bottom:10px"><label>Telegram-ID</label><input id="accUid" inputmode="numeric" placeholder="напр. 123456789"></div>
    <div class="mfield" style="margin-bottom:12px"><label>Имя (необязательно)</label><input id="accName" placeholder="напр. Иван"></div>
    <button class="btn" onclick="accAdd()">Добавить и одобрить</button>`);
}
async function accAdd() {
  const uid = (document.getElementById('accUid').value || '').trim();
  const name = (document.getElementById('accName').value || '').trim();
  if (!uid) return toast('Введите Telegram-ID');
  try { await api('/admin/users', 'POST', { uid, display_name: name || null }); closeSheet(); toast('Пользователь добавлен'); Settings(); }
  catch (e) { toast(e.message || 'не удалось'); }
}

// ── sheet system ──────────────────────────────────────────────────────────
function sheet(html) {
  closeSheet();
  const bg = document.createElement('div'); bg.className = 'sheet-bg'; bg.id = 'sheetbg';
  bg.onclick = e => { if (e.target === bg) closeSheet(); };
  // UX3-FIX-5: tappable + swipe-down-to-dismiss grip; capped height (CSS) keeps the
  // dimmed top area tappable; content scrolls inside the sheet.
  bg.innerHTML = `<div class="sheet"><div class="grip-zone" onclick="closeSheet()"><div class="grip"></div></div>${html}</div>`;
  document.body.appendChild(bg);
  const sheetEl = bg.querySelector('.sheet'), grip = bg.querySelector('.grip-zone');
  let startY = 0, dragging = false;
  grip.addEventListener('touchstart', e => { startY = e.touches[0].clientY; dragging = true; sheetEl.style.transition = 'none'; }, { passive: true });
  grip.addEventListener('touchmove', e => {
    if (!dragging) return;
    const dy = e.touches[0].clientY - startY;
    if (dy > 0) sheetEl.style.transform = `translateY(${dy}px)`;
  }, { passive: true });
  grip.addEventListener('touchend', e => {
    if (!dragging) return; dragging = false; sheetEl.style.transition = '';
    if (e.changedTouches[0].clientY - startY > 90) closeSheet(); else sheetEl.style.transform = '';
  }, { passive: true });
}
function closeSheet() { const b = document.getElementById('sheetbg'); if (b) b.remove(); stopTimer(); }

// boot — check session first
(async function boot() {
  try {
    await api('/auth/me');
    try { window._SETTINGS = await api('/settings'); _cacheSettings(); }  // date_format + rest-timer for first paint
    catch { window._SETTINGS = _cachedSettings() || window._SETTINGS; }   // offline: keep the last persisted value
    renderTabs(); go('home'); flushQueue();
  } catch { Login(); }
})();
