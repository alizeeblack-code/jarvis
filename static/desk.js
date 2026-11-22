// Desk display: one big lamp for how things stand, six frosted keys (one per session) beside it.
// Polls /api/desk; a soft wash and a chime when a session turns to you, finishes or fails.
// The iOS app (ios/JarvisDesk) draws the same screen natively; keep the two alike.
// Kept to what Safari on iOS 15 runs (an iPhone 6s is the target): no build step, no imports.
(function () {
  'use strict';

  const POLL_MS = 4000;
  const STALE_MS = 20000;            // no answer this long: say so instead of showing old states
  const REPEAT_MS = 5 * 60 * 1000;   // a turn still waiting on you chimes again this often
  const KEYS = 6;

  let lang = '';
  try { lang = localStorage.getItem('lang') || ''; } catch (e) { /* private mode */ }
  if (!lang) lang = (navigator.language || '').toLowerCase().indexOf('zh') === 0 ? 'zh' : 'en';
  const tr = (zh, en) => (lang === 'en' ? en : zh);
  document.documentElement.lang = lang;

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // ---- errors go on screen: there is no console on a phone on the desk ----
  function showError(msg) {
    const box = $('err');
    box.hidden = false;
    box.textContent = (box.textContent ? box.textContent + '\n' : '') + msg;
  }
  window.onerror = function (msg, src, line, col) { showError(msg + ' @' + (src || '').split('/').pop() + ':' + line + ':' + col); };
  window.addEventListener('unhandledrejection', (e) => showError('promise: ' + ((e.reason && e.reason.message) || e.reason)));

  // ---- text ----
  function dur(ms) {
    const m = Math.max(0, Math.floor(ms / 60000));
    if (m < 1) return tr('不到 1 分钟', '<1m');
    if (m < 60) return tr(m + ' 分钟', m + 'm');
    const h = Math.floor(m / 60), r = m % 60;
    if (h >= 24) return tr(Math.floor(h / 24) + ' 天', Math.floor(h / 24) + 'd');
    return r && h < 10 ? tr(h + ' 小时 ' + r + ' 分', h + 'h ' + r + 'm') : tr(h + ' 小时', h + 'h');
  }
  const ago = (ms) => (ms < 60000 ? tr('刚刚', 'just now') : tr(dur(ms) + '前', dur(ms) + ' ago'));
  // the short form for the status column: "18 分", "2 小时", "3 天"
  function short(ms) {
    const m = Math.max(0, Math.floor(ms / 60000));
    if (m < 1) return tr('<1 分', '<1m');
    if (m < 60) return tr(m + ' 分', m + 'm');
    const h = Math.floor(m / 60);
    return h < 24 ? tr(h + ' 小时', h + 'h') : tr(Math.floor(h / 24) + ' 天', Math.floor(h / 24) + 'd');
  }
  function clock() {
    const d = new Date();
    return d.getHours() + ':' + String(d.getMinutes()).padStart(2, '0');
  }

  // ---- state ----
  let data = null;          // last /api/desk answer
  let gotAt = 0;            // when it arrived (phone clock); ages are server time + time since
  let okAt = 0;
  let failure = '';
  let version = '';
  let prev = null;          // sid -> state at the previous poll (null until the first one)
  const firstAt = Date.now();
  const unacked = {};       // sid -> 'wait' | 'error'  (done only blinks once)
  let lastChime = 0;
  let audioReady = false;

  const age = (it) => (data.at - it.at) + (Date.now() - gotAt);
  const keyState = (it) => (it.state === 'run' && it.bg ? 'bg' : it.state);

  // ---- sound ----
  const snd = new Audio();
  snd.preload = 'auto';
  function chime(kind) {
    lastChime = Date.now();
    if (!audioReady) return;
    try {
      snd.src = '/static/sounds/' + kind + '.wav';
      const p = snd.play();
      if (p && p.catch) p.catch(() => {});
    } catch (e) { /* no audio, the flash still shows */ }
  }
  function unlockAudio() {
    // iOS lets a page make sound only after a tap; one play() inside the tap unlocks this element
    snd.src = '/static/sounds/done.wav';
    snd.muted = true;
    const p = snd.play();
    const done = () => { snd.pause(); snd.muted = false; audioReady = true; };
    if (p && p.then) p.then(done, done); else done();
  }

  // ---- alerts: one soft wash, never a flashing screen ----
  function wash(kind) {
    const box = $('flash');
    box.hidden = false;
    box.className = '';
    void box.offsetWidth;                          // restart the animation
    box.className = 'k-' + kind + ' go';
    setTimeout(() => { if (box.className === 'k-' + kind + ' go') { box.className = ''; box.hidden = true; } }, 2200);
  }
  function acknowledge() {
    Object.keys(unacked).forEach((k) => delete unacked[k]);   // stops the chime repeating
  }

  function compare(items) {
    const now = {};
    items.forEach((it) => { now[it.sid] = it.state; });
    // whatever no longer needs you stops flashing on its own
    Object.keys(unacked).forEach((sid) => {
      const st = now[sid];
      if (st !== 'wait' && st !== 'error' && st !== 'limit') delete unacked[sid];
    });
    let sound = '';
    if (prev) {
      items.forEach((it) => {
        if (!it.sid || prev[it.sid] === it.state) return;
        // a session seen for the first time only counts when it changed after the page opened
        if (prev[it.sid] === undefined && data.at - it.at > Date.now() - firstAt) return;
        if (it.state === 'wait') { unacked[it.sid] = 'wait'; if (sound !== 'error') sound = 'wait'; }
        else if (it.state === 'error' || it.state === 'limit') { unacked[it.sid] = 'error'; sound = 'error'; }
        else if (it.state === 'done' && prev[it.sid] !== undefined && prev[it.sid] !== 'idle') { if (!sound) sound = 'done'; }
      });
    }
    prev = now;
    if (sound) { chime(sound); wash(sound); }
    else if (Object.keys(unacked).some((k) => unacked[k] === 'wait') && Date.now() - lastChime >= REPEAT_MS) chime('wait');
  }

  // ---- render ----
  // keys are built once and only the parts that changed are touched, so the glow keeps breathing
  const slots = [];
  function buildKeys() {
    const box = $('keys');
    for (let i = 0; i < KEYS; i++) {
      const el = document.createElement('div');
      el.className = 'key s-off';
      el.innerHTML = '<div class="p"></div><div class="n"></div><div class="st"></div>';
      box.appendChild(el);
      el.addEventListener('click', (e) => { const it = shown[i]; if (it) { e.stopPropagation(); reveal(it); } });
      slots.push({ el: el, p: el.querySelector('.p'), n: el.querySelector('.n'), st: el.querySelector('.st'), cache: {} });
    }
  }
  function put(slot, part, html) {
    if (slot.cache[part] === html) return;
    slot.cache[part] = html;
    if (part === 'cls') slot.el.className = html;
    else slot[part].innerHTML = html;
  }
  // ---- a tap on a key: open that session on the Mac (after unlock, if it is locked) ----
  const notes = {};          // sid -> what the key says for a few seconds
  let shown = [];            // the items on the keys now
  let lampItem = null;       // the session the big lamp is showing
  function reveal(it) {
    acknowledge();
    notes[it.sid] = tr('正在打开…', 'Opening…');
    render();
    fetch('/api/sessions/' + encodeURIComponent(it.sid) + '/reveal', { method: 'POST', credentials: 'same-origin' })
      .then((res) => (res.ok ? res.json() : { ok: false }))
      .catch(() => ({ ok: false }))
      .then((a) => {
        let text = tr('打不开', "Couldn't open"), hold = 4000;
        if (a.ok && a.locked) { text = tr('解锁 Mac 后打开', 'Unlock the Mac'); hold = 12000; }
        else if (a.ok) text = a.how === 'jarvis' ? tr('已打开 Jarvis', 'Opened Jarvis')
          : a.how === 'app' ? tr('已切到 Mac', 'Switched on the Mac') : tr('已在 Mac 打开', 'Opened on the Mac');
        notes[it.sid] = text;
        render();
        setTimeout(() => { if (notes[it.sid] === text) { delete notes[it.sid]; render(); } }, hold);
      });
  }

  function status(it) {
    if (notes[it.sid]) return esc(notes[it.sid]);
    const st = keyState(it);
    if (st === 'wait') return tr('等你 ' + short(age(it)), 'waiting ' + short(age(it)));
    if (st === 'run') return tr('运行中', 'running');
    if (st === 'bg') return tr('等后台', 'background');
    if (st === 'quiet') return tr('没动静', 'quiet');
    if (st === 'done') return age(it) < 60000 ? tr('刚完成', 'just done') : tr('完成 · ' + short(age(it)) + '前', 'done ' + short(age(it)) + ' ago');
    if (st === 'idle') return tr('空闲', 'idle');
    if (st === 'limit') return tr('额度用完', 'out of quota');
    return tr('出错', 'error');
  }
  // the small line: which session, when one project has two on the keys; else what went wrong
  function note(it, twin) {
    if (twin) return esc(it.title);
    if (it.state === 'error' && it.err) return esc(it.err);
    return '';
  }
  function renderKeys(items) {
    shown = items.slice(0, KEYS);
    const per = {};
    items.slice(0, KEYS).forEach((it) => { per[it.project] = (per[it.project] || 0) + 1; });
    for (let i = 0; i < KEYS; i++) {
      const s = slots[i], it = items[i];
      if (!it) {
        put(s, 'cls', 'key s-off'); put(s, 'p', ''); put(s, 'n', ''); put(s, 'st', '');
        continue;
      }
      put(s, 'cls', 'key s-' + keyState(it));
      put(s, 'p', esc(it.project));
      put(s, 'n', note(it, per[it.project] > 1));
      put(s, 'st', status(it));
    }
  }

  // the big lamp: the one thing that most needs you, or else what is going on
  function overall(items) {
    const by = (sts) => items.filter((it) => sts.indexOf(it.state) >= 0);
    const err = by(['error', 'limit']), wait = by(['wait']), run = by(['run', 'quiet']), done = by(['done']);
    if (err.length) return { s: 'error', word: tr('出错了', 'error'), since: err[0].state === 'limit' ? tr('额度用完', 'no quota') : '', who: err[0].project, it: err[0] };
    if (wait.length) return { s: 'wait', word: tr('轮到你', 'your turn'), since: dur(age(wait[0])), who: wait[0].project, it: wait[0] };
    if (run.length) return { s: 'run', word: tr('运行中', 'running'), since: run.length > 1 ? tr(run.length + ' 个', run.length + ' sessions') : '', who: run.length > 1 ? '' : run[0].project, it: run[0] };
    if (done.length) return { s: 'done', word: tr('完成了', 'done'), since: ago(age(done[0])), who: done[0].project, it: done[0] };
    return { s: 'idle', word: tr('空闲', 'idle'), since: '', who: '' };
  }
  function counts() {
    const c = data.counts || {};
    return [[c.wait, '等你', 'waiting'], [(c.run || 0) + (c.bg || 0), '运行', 'running'], [c.error, '出错', 'error'], [c.done, '完成', 'done']]
      .filter((x) => x[0] > 0)
      .map((x) => x[0] + ' ' + tr(x[1], x[2]));
  }

  function setLamp(s, word, since, who, sub) {
    $('lamp').className = 's-' + s;
    $('word').textContent = word;
    $('since').textContent = since;
    $('who').textContent = who;
    $('sub').textContent = sub;
  }
  function render() {
    const stale = !data || Date.now() - okAt > STALE_MS;
    document.body.classList.toggle('offline', stale);
    if (stale) {
      setLamp('off', data ? tr('连不上', 'offline') : tr('连接中', 'connecting'),
        okAt ? ago(Date.now() - okAt) : '', failure, clock());
      if (data) renderKeys(data.items || []);
      return;
    }
    const items = data.items || [];
    const o = overall(items);
    lampItem = o.it || null;
    // after a tap, the lamp says what happened on the Mac for a few seconds
    const since = o.it && notes[o.it.sid] ? notes[o.it.sid] : o.since;
    setLamp(o.s, o.word, since, o.who, [clock()].concat(counts()).join(' · '));
    renderKeys(items);
  }

  // ---- polling ----
  function poll() {
    const ctl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = ctl ? setTimeout(() => ctl.abort(), 8000) : 0;
    fetch('/api/desk', { credentials: 'same-origin', cache: 'no-store', signal: ctl ? ctl.signal : undefined })
      .then((res) => {
        if (res.ok) return res.json();
        const e = new Error(res.status === 401 ? tr('需要访问令牌', 'Access token needed') : 'HTTP ' + res.status);
        e.ours = true;
        throw e;
      })
      .then((d) => {
        if (version && d.version && d.version !== version) { location.reload(); return; }
        version = d.version || version;
        data = d; gotAt = Date.now(); okAt = gotAt; failure = '';
        compare(d.items || []);
        render();
      })
      .catch((e) => {
        failure = e && e.ours ? e.message : '';
        render();
      })
      .then(() => { clearTimeout(timer); setTimeout(poll, POLL_MS); });
  }

  // ---- start ----
  buildKeys();
  document.addEventListener('click', acknowledge);
  // a tap on the big lamp opens on the Mac the session it is showing
  $('lamp').addEventListener('click', (e) => { if (lampItem) { e.stopPropagation(); reveal(lampItem); } });
  if (!audioReady) {
    const start = $('start');
    $('start-btn').textContent = tr('点一下开始', 'Tap to start');
    start.hidden = false;
    start.addEventListener('click', (e) => { e.stopPropagation(); unlockAudio(); start.hidden = true; });
  }
  setInterval(render, 15000);                    // clock and "waiting N min" keep moving between polls
  render();
  poll();

  // for the iOS shell and for checks: what the page currently shows
  window.jarvisDesk = { chime: chime, acknowledge: acknowledge, state: () => ({ data: data, unacked: unacked, audioReady: audioReady }) };
})();
