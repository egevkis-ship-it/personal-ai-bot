// ── tiny helpers ─────────────────────────────────────────────────────────
const view = document.getElementById('view');
async function api(path, method = 'GET', body) {
  const opt = { method, headers: {}, credentials: 'include' };
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch('/api' + path, opt);
  if (r.status === 401) { const e = new Error('unauthorized'); e.code = 401; throw e; }
  if (!r.ok) { let d = ''; try { d = (await r.json()).detail; } catch {} throw new Error(d || r.status); }
  const t = await r.text(); return t ? JSON.parse(t) : null;
}
const fmt = n => (n == null ? '' : (Math.round(n * 100) / 100).toString());
const esc = s => (s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
function toast(t) { const d = document.createElement('div'); d.className = 'toast'; d.textContent = t; document.body.appendChild(d); setTimeout(() => d.remove(), 1800); }
function mmss(sec) { const m = Math.floor(sec / 60), s = sec % 60; return m + ':' + String(s).padStart(2, '0'); }
function spark(vals, color = 'var(--info)') {
  if (!vals.length) return '';
  const mn = Math.min(...vals), mx = Math.max(...vals), rng = (mx - mn) || 1;
  const pts = vals.map((v, i) => `${(i / Math.max(1, vals.length - 1)) * 160},${44 - ((v - mn) / rng) * 40 - 2}`).join(' ');
  return `<svg class="spark" viewBox="0 0 160 44" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2"/></svg>`;
}
let STATE = { tab: 'home' };

// ── navigation ───────────────────────────────────────────────────────────
const TABS = [['home', '🏠', 'Главная'], ['train', '🏋️', 'Тренировка'], ['measure', '📏', 'Замеры'], ['history', '📖', 'История']];
function renderTabs() {
  document.getElementById('tabbar').innerHTML = TABS.map(([k, i, l]) =>
    `<div class="tab ${STATE.tab === k ? 'active' : ''}" onclick="go('${k}')"><span class="i">${i}</span>${l}</div>`).join('');
}
async function go(tab, param) {
  STATE.tab = tab; renderTabs(); view.scrollTo(0, 0);
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
  try { await api('/auth/telegram', 'POST', user); document.getElementById('tabbar').style.display = ''; go('home'); }
  catch (e) { toast(e.message || 'вход не удался'); }
};
async function logout() { try { await api('/auth/logout', 'POST'); } catch {} location.reload(); }

// ── Home ────────────────────────────────────────────────────────────────
async function Home() {
  const d = await api('/dashboard');
  const lm = d.last_measurement;
  let banner = '';
  if (d.active_workout) {
    banner = `<div class="banner warn"><div class="small" style="color:var(--warn)">⏸ Незавершённая тренировка</div>
      <div class="b-title" style="color:var(--warn)">${esc(d.active_workout.focus_label || 'Тренировка')}</div>
      <button class="btn" style="margin-top:9px;background:var(--warn)" onclick="go('active',${d.active_workout.id})">Продолжить</button></div>`;
  } else if (d.today_plan) {
    banner = `<div class="banner info"><div class="small" style="color:var(--info)">📅 Сегодня по плану</div>
      <div class="b-title" style="color:var(--info)">${esc(d.today_plan.focus_label || '')} · ${(d.today_plan.exercises || []).length} упр.</div>
      <button class="btn" style="margin-top:9px" onclick="startFromPlan(${d.today_plan.id})">▶ Начать тренировку</button></div>`;
  } else {
    banner = `<div class="banner info"><div class="b-title" style="color:var(--info)">На сегодня плана нет</div>
      <button class="btn" style="margin-top:9px" onclick="go('train')">Начать тренировку</button></div>`;
  }
  view.innerHTML = `<div class="row sp"><h1>Привет!</h1><span class="back" style="margin:0" onclick="logout()">Выйти</span></div><div class="muted small" style="margin-bottom:14px">${new Date().toLocaleDateString('ru-RU',{weekday:'long',day:'numeric',month:'long'})}</div>
    ${banner}
    <div class="muted small" style="margin:4px 0 8px">Быстрые действия</div>
    <div class="grid2">
      <div class="tile" onclick="go('measure')">📏<div class="small" style="margin-top:6px">Записать замер</div></div>
      <div class="tile" onclick="toast('Фото — в следующей версии')">📷<div class="small" style="margin-top:6px">Добавить фото</div></div>
      <div class="tile" onclick="repeatLast(${d.last_workout?d.last_workout.id:0})">🔁<div class="small" style="margin-top:6px">Повторить прошлую</div></div>
      <div class="tile" onclick="go('train')">📅<div class="small" style="margin-top:6px">Тренировки</div></div>
    </div>
    ${lm ? `<div class="card" style="margin-top:12px"><div class="row sp"><span class="muted small">Последний замер</span><span class="small muted">${lm.taken_on}</span></div>
      <div style="font-size:22px;font-weight:700;margin-top:4px">${fmt(lm.weight_kg)} <span class="small muted">кг</span></div></div>` : ''}`;
}
async function startFromPlan(pid) { const r = await api('/workouts', 'POST', { from_plan_id: pid }); go('active', r.id); }
async function repeatLast(id) { if (!id) return toast('Нет прошлых тренировок'); const r = await api('/workouts', 'POST', { repeat_from: id }); go('active', r.id); }

// ── Train (start) ─────────────────────────────────────────────────────────
async function Train() {
  const plan = await api('/plans/today');
  view.innerHTML = `<h1>Тренировка</h1><div class="muted small" style="margin-bottom:14px">Что тренируем?</div>
    ${plan ? `<div class="banner info" onclick="startFromPlan(${plan.id})"><div class="small" style="color:var(--info)">📅 План на сегодня</div>
      <div class="b-title" style="color:var(--info)">${esc(plan.focus_label || '')} →</div></div>` : ''}
    <div class="card list-item" onclick="go('chooseDay')"><div class="ic">🗓</div><div style="flex:1"><b>Другой день недели</b><div class="small muted">взять пропущенную</div></div><span class="muted">›</span></div>
    <div class="card list-item" onclick="freeWorkout()"><div class="ic">➕</div><div style="flex:1"><b>Свободная</b><div class="small muted">с нуля, без плана</div></div><span class="muted">›</span></div>`;
}
async function freeWorkout() { const r = await api('/workouts', 'POST', {}); go('active', r.id); }
async function ChooseDay() {
  const wk = await api('/workouts/week');
  const ic = s => s === 'completed' ? '✅' : s === 'skipped' ? '⚠️' : '⚪️';
  view.innerHTML = `<span class="back" onclick="go('train')">‹ Назад</span><h2>Тренировки недели</h2>
    ${wk.length ? wk.map(p => `<div class="card list-item" onclick="startFromPlan(${p.id})">
      <div class="ic">${ic(p.status)}</div><div style="flex:1"><b>${esc(p.focus_label || '')}</b>
      <div class="small muted">${p.planned_date}${p.is_today ? ' · сегодня' : ''}${p.status === 'skipped' ? ' · пропущено' : ''}</div></div><span class="muted">›</span></div>`).join('')
      : '<div class="card muted">На этой неделе нет планов.</div>'}`;
}

// ── Active workout ────────────────────────────────────────────────────────
async function Active(id) {
  const w = id ? await api('/workouts/' + id) : await api('/workouts/active');
  if (!w) return go('train');
  STATE.activeId = w.id;
  const items = w.exercises.map((ex, i) => {
    const working = ex.sets.filter(s => !s.is_warmup);
    const done = ex.done;
    const next = !done && working.length >= 0 && i === w.exercises.findIndex(e => !e.done);
    const sub = working.length ? working.map(s => setLabel(s)).join(' · ')
      : (ex.target ? `цель ${ex.target_sets || ''}×${ex.target.reps || (ex.target.duration_seconds ? mmss(ex.target.duration_seconds) : '')}${ex.target.weight_kg ? ' · ' + fmt(ex.target.weight_kg) : ''}` : 'нет подходов');
    return `<div class="card list-item" style="${next ? 'border:2px solid var(--info)' : ''}" onclick="openExercise(${w.id},${i})">
      <div class="ic">${done ? '✅' : next ? '▶️' : '⚪️'}</div>
      <div style="flex:1"><b>${esc(ex.name)}</b><div class="small muted">${esc(sub)}</div></div><span class="muted">›</span></div>`;
  }).join('');
  window._WO = w;
  view.innerHTML = `<div class="row sp"><span class="back" onclick="go('home')">‹ Главная</span><span class="muted small" onclick="workoutMenu(${w.id})" style="cursor:pointer">···</span></div>
    <h2 style="margin-bottom:2px">${esc(w.focus_label || 'Тренировка')}</h2>
    <div class="muted small" style="margin-bottom:12px">идёт</div>
    ${items || '<div class="card muted">Пусто</div>'}
    <button class="btn ghost" style="margin-top:6px" onclick="openPicker(${w.id})">➕ Добавить упражнение</button>
    <button class="btn success" style="margin-top:10px" onclick="finishWorkout(${w.id})">Завершить тренировку</button>`;
}
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
    <span>${s.is_warmup ? 'Р · ' : ''}${setLabel(s)}</span>
    <span><span class="muted" onclick="editSet(${s.id},${wid})" style="cursor:pointer">✏️</span> &nbsp; <span style="color:var(--danger);cursor:pointer" onclick="rmSet(${s.id},${wid})">🗑</span></span></div>`).join('');
  const last = ex.last ? `Прошлый раз ${ex.last.duration_seconds ? mmss(ex.last.duration_seconds) : fmt(ex.last.weight_kg) + '×' + ex.last.reps}` : '';
  sheet(`<div class="muted small">Из текущей тренировки</div><h2>${esc(ex.name)}</h2>
    ${last ? `<div class="banner info small" style="color:var(--info)">📈 ${last}</div>` : ''}
    <div class="muted small" style="margin:6px 0">Подходы сегодня</div>
    ${sets || '<div class="muted small" style="padding:10px 0">Пока пусто</div>'}
    <button class="btn" style="margin-top:12px" onclick="openAddSet(${wid},${idx})">➕ Добавить подход</button>`);
}

// add-set sheet — adapts to type
function openAddSet(wid, idx, exObj) {
  const ex = exObj || window._WO.exercises[idx];
  const type = ex.type || 'strength';
  const tgt = ex.target || {}, last = ex.last || {};
  let w = tgt.weight_kg ?? last.weight_kg ?? 20;
  let reps = tgt.reps ?? last.reps ?? 10;
  let dur = tgt.duration_seconds ?? last.duration_seconds ?? 60;
  let useWeight = false;
  let html = `<div class="muted small">${esc(ex.name)}</div><h2>Новый подход</h2>`;
  if (type === 'time') {
    html += stepRow([['min', Math.floor(dur / 60), 'мин', 1], ['sec', dur % 60, 'сек', 10]]);
    html += `<button class="btn ghost" id="tbtn" onclick="toggleTimer()">▶ Запустить таймер</button>
      <div id="timerbox"></div>`;
  } else if (type === 'bodyweight') {
    html += stepRow([['reps', reps, 'повт.', 1]]);
    html += `<div class="row sp" style="background:var(--sec);border-radius:10px;padding:9px 12px;margin-bottom:12px">
      <span class="small">➕ Доп. вес</span><input type="checkbox" id="usew" onchange="document.getElementById('wbox').style.display=this.checked?'block':'none'"></div>
      <div id="wbox" style="display:none">${stepRow([['weight', 10, 'кг', 2.5]])}</div>`;
  } else {
    html += stepRow([['weight', w, 'кг', 2.5], ['reps', reps, 'повт.', 1]]);
  }
  html += `<div class="tag-row">
    <span class="pill" data-tag="warmup" onclick="tag(this)">Разминка</span>
    <span class="pill" data-tag="failure" onclick="tag(this)">До отказа</span></div>
    <button class="btn" onclick="confirmSet(${wid},'${type}')">✓ Подтвердить</button>
    <div class="muted small" style="text-align:center;margin:12px 0 4px">или ввести другое</div>
    <div class="field"><input id="freetext" placeholder="80x10, до отказа…"><span onclick="confirmText(${wid})" style="color:var(--info);cursor:pointer">↑</span></div>`;
  sheet(html); window._addCtx = { wid, ex };
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
async function confirmSet(wid, type) {
  const g = id => { const e = document.getElementById('f_' + id); return e ? parseFloat(e.value) : null; };
  const body = { exercise_name: window._addCtx.ex.name, ...getTags() };
  if (type === 'time') body.duration_seconds = (g('min') || 0) * 60 + (g('sec') || 0);
  else if (type === 'bodyweight') { body.reps = g('reps'); if (document.getElementById('usew')?.checked) body.weight_kg = g('weight'); }
  else { body.weight_kg = g('weight'); body.reps = g('reps'); }
  await api('/workouts/' + wid + '/sets', 'POST', body);
  closeSheet(); stopTimer(); restTimer(); go('active', wid);
}
async function confirmText(wid) {
  const t = document.getElementById('freetext').value.trim(); if (!t) return;
  try { await api('/workouts/' + wid + '/sets', 'POST', { text: t }); closeSheet(); go('active', wid); }
  catch (e) { toast(e.message); }
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

// rest timer overlay
function restTimer() {
  let s = 90;
  const bg = document.createElement('div'); bg.className = 'sheet-bg'; bg.id = 'restbg';
  const draw = () => bg.innerHTML = `<div class="sheet" style="text-align:center"><div class="grip"></div>
    <div class="muted small">Отдых</div><div class="timer">${mmss(s)}</div>
    <div class="grid2"><button class="btn sec sm" onclick="restAdd(30)">+30 сек</button>
    <button class="btn sm" onclick="closeRest()">Пропустить</button></div></div>`;
  draw(); document.body.appendChild(bg);
  window._restAdd = n => { s += n; };
  bg._iv = setInterval(() => { s--; if (s <= 0) { closeRest(); toast('Отдых окончен'); } else draw(); }, 1000);
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
    body.innerHTML = r.length ? r.map(x => pickRow(wid, x.name, x.key)).join('') : '<div class="muted small">Пока пусто — выбери по группам.</div>';
  } else {
    const g = await api('/exercises/groups');
    body.innerHTML = g.map(x => `<div class="list-item" onclick="pickGroup(${wid},'${x.group}','${x.label}')"><div style="flex:1">${x.label}</div><span class="muted small">${x.count} ›</span></div>`).join('');
  }
}
async function pickGroup(wid, g, label) {
  const list = await api('/exercises/catalog?group=' + g);
  document.getElementById('pickbody').innerHTML = `<div class="back" onclick="pickTab(${wid},'grp')">‹ ${label}</div>` + list.map(x => pickRow(wid, x.name, x.exercise_key)).join('');
}
async function pickSearch(wid) {
  const q = document.getElementById('exq').value.trim();
  if (q.length < 2) return;
  const r = await api('/exercises/search?q=' + encodeURIComponent(q));
  document.getElementById('pickbody').innerHTML = r.map(x => pickRow(wid, x.name, x.exercise_key)).join('') || '<div class="muted small">Ничего не найдено</div>';
}
function pickRow(wid, name, key) {
  return `<div class="list-item" onclick='chooseEx(${wid},${JSON.stringify(name)},${JSON.stringify(key || '')})'><div style="flex:1">${esc(name)}</div><span style="color:var(--info)">＋</span></div>`;
}
function chooseEx(wid, name, key) {
  const type = key && /план|велосипед|кардио/.test(name.toLowerCase()) ? 'time'
    : /подтяг|отжим|брус/.test(name.toLowerCase()) ? 'bodyweight' : 'strength';
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
async function delWorkout(wid) { await api('/workouts/' + wid, 'DELETE'); closeSheet(); go('home'); }
async function finishWorkout(wid) {
  const r = await api('/workouts/' + wid + '/finish', 'POST');
  sheet(`<div style="text-align:center"><div style="font-size:34px">✅</div><h2>Тренировка завершена</h2>
    <div class="muted small">${r.set_count} рабочих подходов</div></div>
    <div class="card" style="margin-top:10px"><div class="muted small">✨ Резюме</div><div style="margin-top:6px">${esc(r.summary)}</div></div>
    <button class="btn" style="margin-top:12px" onclick="closeSheet();go('home')">Готово</button>`);
}

// ── History ───────────────────────────────────────────────────────────────
async function History() {
  const list = await api('/workouts?days=60');
  view.innerHTML = `<h1>История</h1>
    ${list.length ? list.map(w => `<div class="card list-item" onclick="go('workout',${w.id})">
      <div style="flex:1"><b>${esc(w.focus_label || 'Тренировка')}</b><div class="small muted">${w.workout_date} · ${w.set_count} подх · ${w.tonnage.toLocaleString('ru-RU')} кг</div></div><span class="muted">›</span></div>`).join('')
      : '<div class="card muted">Пока нет завершённых тренировок.</div>'}`;
}
async function WorkoutDetail(id) {
  const w = await api('/workouts/' + id);
  const ex = w.exercises.filter(e => e.sets.length).map(e => `<div style="padding:8px 0;border-bottom:1px solid var(--line)">
    <b>${esc(e.name)}</b><div class="small muted">${e.sets.map(setLabel).join(' · ')}</div></div>`).join('');
  view.innerHTML = `<span class="back" onclick="go('history')">‹ История</span>
    <h2 style="margin-bottom:2px">${esc(w.focus_label || 'Тренировка')}</h2><div class="muted small" style="margin-bottom:10px">${w.workout_date}</div>
    <div class="card">${ex || '<span class="muted">Нет подходов</span>'}</div>
    ${w.notes ? `<div class="card small muted">📝 ${esc(w.notes)}</div>` : ''}
    <button class="btn ghost" onclick="repeatLast(${w.id})">🔁 Повторить эту тренировку</button>`;
}

// ── Measurements ──────────────────────────────────────────────────────────
const MFIELDS = [['weight_kg', 'Вес, кг'], ['calf_cm', 'Голень, см'], ['thigh_cm', 'Бедро, см'], ['hips_cm', 'Бедра, см'], ['belly_cm', 'Живот, см'], ['waist_cm', 'Талия, см'], ['chest_cm', 'Грудь, см'], ['arm_cm', 'Рука, см'], ['neck_cm', 'Шея, см']];
async function Measure() {
  const last = await api('/measurements/last');
  view.innerHTML = `<div class="row sp"><h1>Замеры</h1><span class="back" onclick="go('measureHistory')">История ›</span></div>
    <div class="grid2" style="margin-top:8px">${MFIELDS.map(([k, l]) => `<div class="mfield"><label>${l}</label><input id="m_${k}" inputmode="decimal" value="${last && last[k] != null ? fmt(last[k]) : ''}" placeholder="—"></div>`).join('')}</div>
    <div class="field" style="margin-top:12px"><input id="mtext" placeholder="или: вес 82 талия 84"><span onclick="saveMeasureText()" style="color:var(--info);cursor:pointer">↑</span></div>
    <button class="btn" style="margin-top:12px" onclick="saveMeasure()">Сохранить замер</button>`;
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
  const vals = rows.slice().reverse().map(r => r[metric]).filter(v => v != null).map(Number);
  const label = (MFIELDS.find(f => f[0] === metric) || [])[1] || '';
  view.innerHTML = `<span class="back" onclick="go('measure')">‹ Замеры</span><h2>История</h2>
    <div class="tag-row" style="justify-content:flex-start">${MFIELDS.map(([k, l]) => `<span class="pill ${k === metric ? 'on' : ''}" onclick="setMetric('${k}')">${l.split(',')[0]}</span>`).join('')}</div>
    <div class="card">${vals.length ? spark(vals, 'var(--success)') : '<span class="muted small">Нет данных</span>'}</div>
    ${rows.filter(r => r[metric] != null).map(r => `<div class="row sp" style="padding:9px 0;border-bottom:1px solid var(--line)"><span class="muted small">${r.taken_on}</span><span>${fmt(r[metric])}</span></div>`).join('')}`;
}
function setMetric(m) { STATE._metric = m; MeasureHistory(); }

// ── sheet system ──────────────────────────────────────────────────────────
function sheet(html) {
  closeSheet();
  const bg = document.createElement('div'); bg.className = 'sheet-bg'; bg.id = 'sheetbg';
  bg.onclick = e => { if (e.target === bg) closeSheet(); };
  bg.innerHTML = `<div class="sheet"><div class="grip"></div>${html}</div>`;
  document.body.appendChild(bg);
}
function closeSheet() { const b = document.getElementById('sheetbg'); if (b) b.remove(); stopTimer(); }

// boot — check session first
(async function boot() {
  try { await api('/auth/me'); renderTabs(); go('home'); }
  catch { Login(); }
})();
