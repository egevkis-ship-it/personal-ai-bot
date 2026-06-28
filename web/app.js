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
    if (tab === 'plans') return Plans();
    if (tab === 'planEdit') return PlanEdit(param);
    if (tab === 'schedule') return Schedule();
    if (tab === 'settings') return Settings();
    if (tab === 'reports') return Reports();
    if (tab === 'photos') return Photos();
    if (tab === 'exercise') return ExerciseDetail(param);
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
      <button class="btn" style="margin-top:9px;background:var(--warn)" onclick="go('active',${d.active_workout.id})">Продолжить</button></div>`;
  } else if (d.today_plan) {
    banner = `<div class="banner info"><div class="small" style="color:var(--info)">📅 Сегодня по плану</div>
      <div class="b-title" style="color:var(--info)">${esc(d.today_plan.focus_label || '')} · ${(d.today_plan.exercises || []).length} упр.</div>
      <button class="btn" style="margin-top:9px" onclick="startFromPlan(${d.today_plan.id})">▶ Начать тренировку</button></div>`;
  } else {
    banner = `<div class="banner info"><div class="b-title" style="color:var(--info)">На сегодня плана нет</div>
      <button class="btn" style="margin-top:9px" onclick="go('train')">Начать тренировку</button></div>`;
  }
  view.innerHTML = `<div class="row sp"><h1>Привет!</h1><span style="font-size:24px;cursor:pointer;line-height:1" onclick="go('settings')" title="Настройки">⚙️</span></div><div class="muted small" style="margin-bottom:14px">${new Date().toLocaleDateString('ru-RU',{weekday:'long',day:'numeric',month:'long'})}</div>
    ${banner}
    <div class="muted small" style="margin:4px 0 8px">Быстрые действия</div>
    <div class="grid2">
      <div class="tile" onclick="go('measure')">📏<div class="small" style="margin-top:6px">Записать замер</div></div>
      <div class="tile" onclick="go('photos')">📷<div class="small" style="margin-top:6px">Прогресс-фото</div></div>
      <div class="tile" onclick="repeatLast(${d.last_workout?d.last_workout.id:0})">🔁<div class="small" style="margin-top:6px">Повторить прошлую</div></div>
      <div class="tile" onclick="go('train')">📅<div class="small" style="margin-top:6px">Тренировки</div></div>
    </div>
    <button class="btn ghost" style="margin-top:12px" onclick="go('reports')">📄 Отчёты (PDF)</button>
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
    <div class="card list-item" onclick="freeWorkout()"><div class="ic">➕</div><div style="flex:1"><b>Свободная</b><div class="small muted">с нуля, без плана</div></div><span class="muted">›</span></div>
    <div class="muted small" style="margin:18px 0 8px">Планирование</div>
    <div class="card list-item" onclick="go('plans')"><div class="ic">📅</div><div style="flex:1"><b>Запланировать тренировки</b><div class="small muted">расписание на дни и неделю</div></div><span class="muted">›</span></div>
    <div class="card list-item" onclick="go('schedule')"><div class="ic">📆</div><div style="flex:1"><b>Что запланировано</b><div class="small muted">расписание: день, неделя, месяц</div></div><span class="muted">›</span></div>`;
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
  let w;
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
    return `<div class="card list-item" style="${next ? 'border:2px solid var(--info)' : ''}" onclick="openExercise(${w.id},${i})">
      <div class="ic">${done ? '✅' : next ? '▶️' : '⚪️'}</div>
      <div style="flex:1"><b>${esc(ex.name)}</b><div class="small muted">${esc(sub)}</div></div>
      <span class="muted" style="padding:4px 8px;cursor:pointer" title="Прогресс упражнения" onclick="event.stopPropagation();exDetailIdx(${i})">📈</span></div>`;
  }).join('');
  view.innerHTML = `<div class="row sp"><span class="back" onclick="go('home')">‹ Главная</span><span class="muted small" onclick="workoutMenu(${w.id})" style="cursor:pointer">···</span></div>
    <h2 style="margin-bottom:2px">${esc(w.focus_label || 'Тренировка')}</h2>
    <div class="muted small" style="margin-bottom:12px">${navigator.onLine ? 'идёт' : '⚠️ оффлайн — подходы сохранятся при сети'}</div>
    ${items || '<div class="card muted">Пусто</div>'}
    <button class="btn ghost" style="margin-top:6px" onclick="openPicker(${w.id})">➕ Добавить упражнение</button>
    <button class="btn success" style="margin-top:10px" onclick="finishWorkout(${w.id})">Завершить тренировку</button>`;
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
    <div class="field"><input id="freetext" placeholder="80x10, до отказа…"><span onclick="recToField('freetext',this)" style="cursor:pointer">🎤</span><span onclick="confirmText(${wid})" style="color:var(--info);cursor:pointer">↑</span></div>`;
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
  closeSheet(); stopTimer();
  await submitSet(wid, body);
  restTimer();
}
async function confirmText(wid) {
  const t = document.getElementById('freetext').value.trim(); if (!t) return;
  try { await api('/workouts/' + wid + '/sets', 'POST', { text: t }); closeSheet(); go('active', wid); }
  catch (e) { toast(e.message); }
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
  const list = await api('/workouts?days=60');
  view.innerHTML = `<h1>История</h1>
    ${list.length ? list.map(w => `<div class="card list-item" onclick="go('workout',${w.id})">
      <div style="flex:1"><b>${esc(w.focus_label || 'Тренировка')}</b><div class="small muted">${w.workout_date} · ${w.set_count} подх · ${w.tonnage.toLocaleString('ru-RU')} кг</div></div><span class="muted">›</span></div>`).join('')
      : '<div class="card muted">Пока нет завершённых тренировок.</div>'}`;
}
async function WorkoutDetail(id) {
  const w = await api('/workouts/' + id);
  window._WDid = id; window._WDex = w.exercises.filter(e => e.sets.length);
  const ex = window._WDex.map((e, idx) => `<div class="row sp" style="padding:8px 0;border-bottom:1px solid var(--line)">
    <div style="flex:1"><b>${esc(e.name)}</b><div class="small muted">${esc(e.sets.map(setLabel).join(' · '))}</div></div>
    <span class="muted" style="cursor:pointer;padding:4px 6px" title="Прогресс упражнения" onclick="exDetailWD(${idx})">📈</span></div>`).join('');
  view.innerHTML = `<span class="back" onclick="go('history')">‹ История</span>
    <h2 style="margin-bottom:2px">${esc(w.focus_label || 'Тренировка')}</h2><div class="muted small" style="margin-bottom:10px">${w.workout_date}</div>
    <div class="card">${ex || '<span class="muted">Нет подходов</span>'}</div>
    ${w.notes ? `<div class="card small muted">📝 ${esc(w.notes)}</div>` : ''}
    <button class="btn ghost" onclick="repeatLast(${w.id})">🔁 Повторить эту тренировку</button>`;
}

// ── Exercise progress (charts + PR) ─────────────────────────────────────────
function shortDate(iso) { return iso ? iso.slice(8, 10) + '.' + iso.slice(5, 7) : ''; }
// Simple line chart with axes (extends spark for a labelled progression view).
function lineChart(pts, color = 'var(--info)') {
  if (!pts.length) return '<div class="muted small">нет данных</div>';
  const W = 300, H = 130, padL = 34, padR = 10, padT = 10, padB = 20;
  const vals = pts.map(p => p.value);
  let mn = Math.min(...vals), mx = Math.max(...vals);
  if (mn === mx) { mn -= 1; mx += 1; }
  const rng = mx - mn;
  const x = i => padL + (pts.length <= 1 ? (W - padL - padR) / 2 : (i / (pts.length - 1)) * (W - padL - padR));
  const y = v => padT + (1 - (v - mn) / rng) * (H - padT - padB);
  const poly = pts.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
  const dots = pts.map((p, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="3" fill="${color}"/>`).join('');
  const ax = 'font-size:9px;fill:var(--muted)';
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:340px">
    <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${H - padB}" stroke="var(--line)"/>
    <line x1="${padL}" y1="${H - padB}" x2="${W - padR}" y2="${H - padB}" stroke="var(--line)"/>
    ${pts.length > 1 ? `<polyline points="${poly}" fill="none" stroke="${color}" stroke-width="2"/>` : ''}${dots}
    <text x="2" y="${padT + 7}" style="${ax}">${fmt(mx)}</text>
    <text x="2" y="${H - padB + 2}" style="${ax}">${fmt(mn)}</text>
    <text x="${padL}" y="${H - 5}" style="${ax}">${esc(pts[0].label)}</text>
    ${pts.length > 1 ? `<text x="${W - padR}" y="${H - 5}" text-anchor="end" style="${ax}">${esc(pts[pts.length - 1].label)}</text>` : ''}
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
  view.innerHTML = `<span class="back" onclick="go('home')">‹ Главная</span><h1>Отчёты</h1>
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
  view.innerHTML = `<span class="back" onclick="go('home')">‹ Главная</span><h1>Прогресс-фото</h1>
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
    if (!series.length) { box.innerHTML = '<div class="card muted">Пока нет фото.</div>'; return; }
    box.innerHTML = series.map(s => `<div class="card">
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

// ── Measurements ──────────────────────────────────────────────────────────
const MFIELDS = [['weight_kg', 'Вес, кг'], ['calf_cm', 'Голень, см'], ['thigh_cm', 'Бедро, см'], ['hips_cm', 'Бедра, см'], ['belly_cm', 'Живот, см'], ['waist_cm', 'Талия, см'], ['chest_cm', 'Грудь, см'], ['arm_cm', 'Рука, см'], ['neck_cm', 'Шея, см']];
async function Measure() {
  const last = await api('/measurements/last');
  view.innerHTML = `<div class="row sp"><h1>Замеры</h1><span class="back" onclick="go('measureHistory')">История ›</span></div>
    <div class="grid2" style="margin-top:8px">${MFIELDS.map(([k, l]) => `<div class="mfield"><label>${l}</label><input id="m_${k}" inputmode="decimal" value="${last && last[k] != null ? fmt(last[k]) : ''}" placeholder="—"></div>`).join('')}</div>
    <div class="field" style="margin-top:12px"><input id="mtext" placeholder="или: вес 82 талия 84"><span onclick="recToField('mtext',this)" style="cursor:pointer">🎤</span><span onclick="saveMeasureText()" style="color:var(--info);cursor:pointer">↑</span></div>
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
function fmtPlanDate(iso) {
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'short' });
}
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
async function Plans() {
  const list = await api('/plans?days=30');
  const rows = list.length ? list.map(p => `<div class="card list-item" onclick="go('planEdit',${p.id})">
      <div class="ic">${p.is_today ? '📍' : '📅'}</div>
      <div style="flex:1"><b>${esc(p.focus_label || 'Тренировка')}</b>
        <div class="small muted">${fmtPlanDate(p.planned_date)}${p.is_today ? ' · сегодня' : ''} · ${(p.exercises || []).length} упр.</div></div>
      <span class="muted">›</span></div>`).join('')
    : '<div class="card muted">Пока ничего не запланировано на 30 дней вперёд.</div>';
  view.innerHTML = `<span class="back" onclick="go('train')">‹ Тренировка</span>
    <h1>Планы</h1><div class="muted small" style="margin-bottom:14px">Расписание на ближайшие дни</div>
    <button class="btn" onclick="newPlan()">➕ Запланировать день</button>
    <button class="btn ghost" style="margin-top:8px" onclick="planPasteSheet()">📝 Вставить планом (ИИ)</button>
    <div style="margin-top:16px">${rows}</div>`;
}

function newPlan(dateISO) {
  window._PLAN = { id: null, date: dateISO || todayISO(), focus: '', notes: '', exercises: [] };
  go('planEdit', 'new');
}

// ── Schedule (view-only: day / week / month) ────────────────────────────────
function isoOf(d) { return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }
function addDaysISO(iso, n) { const d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() + n); return isoOf(d); }
function mondayISO(iso) { return addDaysISO(iso, -isoWeekday(iso)); }
function fmtFullDate(iso) { return new Date(iso + 'T00:00:00').toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' }); }
function fmtDM(iso) { return new Date(iso + 'T00:00:00').toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }); }
function _byDate(list) { const m = {}; list.forEach(p => { (m[p.planned_date] = m[p.planned_date] || []).push(p); }); return m; }

async function Schedule() {
  document.getElementById('tabbar').style.display = '';
  if (!STATE.schedMode) STATE.schedMode = 'day';
  if (!STATE.schedDate) STATE.schedDate = todayISO();
  const mode = STATE.schedMode;
  const pills = [['day', 'День'], ['week', 'Неделя'], ['month', 'Месяц']].map(([k, l]) =>
    `<span class="pill ${mode === k ? 'on' : ''}" onclick="schedSet('${k}')">${l}</span>`).join('');
  view.innerHTML = `<span class="back" onclick="go('train')">‹ Тренировка</span><h1>Что запланировано</h1>
    <div class="tag-row" style="justify-content:flex-start;margin-bottom:12px">${pills}</div>
    <div id="schedBody"><div class="card muted small">Загрузка…</div></div>`;
  try {
    if (mode === 'day') await schedDay();
    else if (mode === 'week') await schedWeek();
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
      <button class="btn ghost" onclick="newPlan('${iso}')">➕ Запланировать</button>`;
  } else {
    html += list.map(p => `<div class="card" style="cursor:pointer" onclick="go('planEdit',${p.id})">
      <div class="row sp"><b>${esc(p.focus_label || 'Тренировка')}</b><span class="muted">›</span></div>
      ${(p.exercises || []).map(ex => `<div class="small muted" style="margin-top:3px">• ${esc(ex.name)} — ${esc(exLine(ex))}</div>`).join('') || '<div class="small muted">без упражнений</div>'}
    </div>`).join('');
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
    const label = plans.length
      ? `${esc(first.focus_label || 'Тренировка')} · ${(first.exercises || []).length} упр.${plans.length > 1 ? ` (+${plans.length - 1})` : ''}`
      : '—';
    const tap = plans.length ? `go('planEdit',${first.id})` : `newPlan('${d}')`;
    rows += `<div class="card list-item" style="${isToday ? 'border:2px solid var(--info)' : ''}" onclick="${tap}">
      <div class="ic">${WD_SHORT[i]}</div>
      <div style="flex:1"><b>${esc(fmtDM(d))}</b>${isToday ? ' <span class="small" style="color:var(--info)">сегодня</span>' : ''}
        <div class="small muted">${label}</div></div>
      <span class="muted">${plans.length ? '›' : '＋'}</span></div>`;
  }
  document.getElementById('schedBody').innerHTML = schedHeader(`${fmtDM(mon)} – ${fmtDM(sun)}`) + rows;
}

async function schedMonth() {
  const a = new Date(STATE.schedDate + 'T00:00:00');
  const y = a.getFullYear(), mo = a.getMonth();
  const firstISO = isoOf(new Date(y, mo, 1));
  const lastDay = new Date(y, mo + 1, 0);
  const lastISO = isoOf(lastDay);
  const byDate = _byDate(await api(`/plans?from=${firstISO}&to=${lastISO}`));
  const today = todayISO();
  const gridStart = mondayISO(firstISO);
  const weeks = Math.ceil((isoWeekday(firstISO) + lastDay.getDate()) / 7);
  let cells = '';
  for (let i = 0; i < weeks * 7; i++) {
    const d = addDaysISO(gridStart, i);
    const dt = new Date(d + 'T00:00:00');
    const inMonth = dt.getMonth() === mo;
    const plans = byDate[d] || [];
    const isToday = d === today;
    const tap = plans.length === 1 ? `go('planEdit',${plans[0].id})` : `schedDayAt('${d}')`;
    const dot = plans.length ? `<div style="width:5px;height:5px;border-radius:50%;background:${isToday ? '#fff' : 'var(--info)'};margin:3px auto 0"></div>` : '<div style="height:8px"></div>';
    cells += `<div onclick="${tap}" style="text-align:center;padding:6px 0;border-radius:8px;cursor:pointer;${isToday ? 'background:var(--info);color:#fff' : (inMonth ? '' : 'opacity:.3')}">
      <div style="font-size:13px">${dt.getDate()}</div>${dot}</div>`;
  }
  const head = WD_SHORT.map(w => `<div style="text-align:center;font-size:11px;color:var(--txt2)">${w}</div>`).join('');
  const title = a.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' });
  document.getElementById('schedBody').innerHTML = schedHeader(title) +
    `<div class="card"><div style="display:grid;grid-template-columns:repeat(7,1fr);gap:2px">${head}${cells}</div></div>`;
}
function schedDayAt(iso) { STATE.schedDate = iso; STATE.schedMode = 'day'; Schedule(); }

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
  const curWd = isoWeekday(P.date);
  const chips = WD_SHORT.map((w, i) =>
    `<span class="pill ${i === curWd ? 'on' : ''}" onclick="planQuickDay(${i})">${w}</span>`).join('');
  const exItems = P.exercises.length ? P.exercises.map((ex, i) => `<div class="card list-item">
      <div style="flex:1"><b>${esc(ex.name)}</b><div class="small muted">${esc(exLine(ex))}</div></div>
      <span class="muted" onclick="planEditEx(${i})" style="cursor:pointer">✏️</span> &nbsp;
      <span style="color:var(--danger);cursor:pointer" onclick="planRemoveEx(${i})">🗑</span></div>`).join('')
    : '<div class="card muted small">Упражнения не добавлены</div>';
  view.innerHTML = `<span class="back" onclick="go('plans')">‹ Планы</span>
    <h2>${P.id ? 'Редактировать план' : 'Новый план'}</h2>
    <div class="mfield" style="margin-top:8px"><label>Дата</label>
      <input id="pl_date" type="date" value="${P.date}" onchange="planDateInput()"></div>
    <div class="tag-row" style="justify-content:flex-start;margin:8px 0 4px">${chips}</div>
    <div class="muted small" style="margin-top:4px">${fmtPlanDate(P.date)}</div>
    <div class="mfield" style="margin-top:12px"><label>Фокус (что тренируем)</label>
      <input id="pl_focus" value="${esc(P.focus)}" placeholder="напр. Грудь / Трицепс"></div>
    <div class="muted small" style="margin:16px 0 6px">Упражнения</div>
    ${exItems}
    <button class="btn ghost" style="margin-top:8px" onclick="planAddExercise()">➕ Добавить упражнение</button>
    <div class="mfield" style="margin-top:16px"><label>Заметка к дню (необязательно)</label>
      <input id="pl_notes" value="${esc(P.notes)}" placeholder="напр. разминка 5 мин"></div>
    <button class="btn success" style="margin-top:16px" onclick="savePlan()">${P.id ? 'Сохранить изменения' : 'Сохранить план'}</button>
    ${P.id ? `<button class="btn danger" style="margin-top:8px" onclick="deletePlan(${P.id})">Удалить план</button>` : ''}`;
}

function planSync() {
  const P = window._PLAN; if (!P) return;
  const d = document.getElementById('pl_date'); if (d) P.date = d.value || P.date;
  const f = document.getElementById('pl_focus'); if (f) P.focus = f.value;
  const n = document.getElementById('pl_notes'); if (n) P.notes = n.value;
}
function planDateInput() { planSync(); PlanEdit('new'); }
function planQuickDay(wd) { planSync(); window._PLAN.date = nextOccurrenceISO(wd); PlanEdit('new'); }
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
    body.innerHTML = r.length ? r.map(x => planPickRow(x.name)).join('') : '<div class="muted small">Пусто — выбери по группам.</div>';
  } else {
    const g = await api('/exercises/groups');
    body.innerHTML = g.map(x => `<div class="list-item" onclick="planPickGroup('${x.group}','${esc(x.label)}')"><div style="flex:1">${esc(x.label)}</div><span class="muted small">${x.count} ›</span></div>`).join('');
  }
}
async function planPickGroup(g, label) {
  const list = await api('/exercises/catalog?group=' + g);
  document.getElementById('ppickbody').innerHTML = `<div class="back" onclick="planPickTab('grp')">‹ ${label}</div>` + list.map(x => planPickRow(x.name)).join('');
}
async function planPickSearch() {
  const q = document.getElementById('pexq').value.trim(); if (q.length < 2) return;
  const r = await api('/exercises/search?q=' + encodeURIComponent(q));
  document.getElementById('ppickbody').innerHTML = r.map(x => planPickRow(x.name)).join('') || '<div class="muted small">Ничего не найдено</div>';
}
function planPickRow(name) {
  return `<div class="list-item" onclick='planChooseEx(${JSON.stringify(name)})'><div style="flex:1">${esc(name)}</div><span style="color:var(--info)">＋</span></div>`;
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
    <button class="btn" onclick='planSaveTarget(${idx},${JSON.stringify(name)})'>✓ ${idx >= 0 ? 'Сохранить' : 'Добавить'}</button>`);
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
  try {
    if (P.id) await api('/plans/' + P.id, 'PATCH', payload);
    else await api('/plans', 'POST', payload);
    window._PLAN = null; toast('План сохранён'); go('plans');
  } catch (e) { toast(e.message || 'не удалось сохранить'); }
}
async function deletePlan(id) {
  try { await api('/plans/' + id, 'DELETE'); window._PLAN = null; toast('План удалён'); go('plans'); }
  catch (e) { toast(e.message); }
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
async function planConfirmBulk() {
  try {
    const r = await api('/plans/bulk', 'POST', { days: window._PARSED });
    window._PARSED = null; closeSheet(); toast('Сохранено: ' + r.saved + ' дн.'); go('plans');
  } catch (e) { toast(e.message); }
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
  let tzr, s, admin = null;
  try {
    tzr = await api('/service/tz');
    s = await api('/service/stats');
    try { admin = await api('/admin/users'); }            // 403 → not an admin (hide section)
    catch (e) { if (e.status === 401) throw e; admin = null; }
  } catch (e) {
    if (e.status === 401 || e.code === 401) { document.getElementById('tabbar').style.display = 'none'; return Login(); }
    return toast(e.message || 'Не удалось загрузить настройки');
  }
  window._ADMIN = admin;
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
    <div class="muted small" style="margin:14px 0 6px">🧹 Очистка данных</div>
    <div class="card">
      <button class="btn ghost sm" onclick="wipeAsk('plans')">Очистить запланированные</button>
      <button class="btn ghost sm" style="margin-top:8px" onclick="wipeAsk('history')">Очистить историю</button>
      <button class="btn ghost sm" style="margin-top:8px" onclick="wipeAsk('measurements')">Очистить замеры</button>
      <button class="btn ghost sm" style="margin-top:8px" onclick="wipeAsk('photos')">Очистить фото</button>
      <button class="btn danger sm" style="margin-top:12px" onclick="wipeAsk('all')">⚠️ ПОЛНЫЙ СБРОС</button>
    </div>
    ${admin ? adminSection(admin) : ''}
    <button class="btn ghost" style="margin-top:20px" onclick="logout()">Выйти</button>`;
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
  bg.innerHTML = `<div class="sheet"><div class="grip"></div>${html}</div>`;
  document.body.appendChild(bg);
}
function closeSheet() { const b = document.getElementById('sheetbg'); if (b) b.remove(); stopTimer(); }

// boot — check session first
(async function boot() {
  try { await api('/auth/me'); renderTabs(); go('home'); flushQueue(); }
  catch { Login(); }
})();
