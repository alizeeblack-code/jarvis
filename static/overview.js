/**
 * Global overview (home page): today, spiral day dial, "now", projects,
 * last 14 days, recent sessions. Data: GET /api/overview.
 * Depends on app.js globals: state, $, api, escapeHtml, enterProject,
 * refreshTasks, openTaskModal, openNewProjectModal.
 */
(function () {
  const ALL_PAGE_SIZE = 8;
  const ov = { data: null, sig: '', filterAgent: '', allProject: '',
              mode: (() => { try { return localStorage.getItem('ov.mode') === 'all' ? 'all' : 'now'; } catch { return 'now'; } })(),
              dailyMode: (() => { try { return localStorage.getItem('ov.dailyMode') === 'agent' ? 'agent' : 'project'; } catch { return 'project'; } })(),
              dailyFilter: '',
              dailyDay: null,
              usageExpanded: false,
              todoOpen: false,
              all: { rows: [], total: 0, projects: [], limit: ALL_PAGE_SIZE, loading: false, key: '', archived: 0 },
              allArchived: false,           // 全部 › 已归档
              todoProject: '', todoAgent: 'todo', todoAll: false, busy: false,
              projAll: (() => { try { return localStorage.getItem('ov.projAll') === '1'; } catch { return false; } })() };
  const PROJ_SHOWN = 8;
  const AGENT_ORDER = ['claude', 'codex', 'antigravity'];
  const agentTag = (a) => window.agentBadge
    ? window.agentBadge(a || 'claude', 'ov-agent')
    : `<span class="card-badge agent agent-${a || 'claude'} ov-agent">${esc(agentName(a || 'claude'))}</span>`;

  const L = {
    zh: {
      now: '现在', projects: '项目', daily: '最近 14 天', recent: '最近的会话',
      active: '协作', tokens: '产出 token', sessions: '会话', prompts: '次提问',
      vsAvg: (d) => `比前 7 天日均${d >= 0 ? '多' : '少'} ${fmtDur(Math.abs(d))}`,
      run: '在跑', bg: '等后台', wait: '等你', done: '完成', quiet: '安静', error: '出错', limit: '额度用完',
      ctx: '上下文', ctxWrap: '上下文偏长，可以收尾后开个新会话', ctxNew: '上下文太长，该换新会话了',
      idle: '空闲', archived: '已归档', allProjects: '全部项目', allStates: '全部状态',
      allAgents: '全部执行者', openCodex: '在 Codex 打开',
      nothingNow: '没有在跑或等你的会话', nothingNowFor: (a) => `没有在跑或等你的 ${a} 会话`, indexing: '正在读取本机会话…',
      today: '今天', open: '在桌面版打开', task: '任务',
      all: '全部', moreAll: '显示更多',
      moreAllCount: (n, tot) => `显示更多（${n} / ${tot}）`,
      loadingMore: '加载中…',
      allLoaded: (tot) => `已加载全部 ${tot} 条`,
      noSessions: '没有会话',
      onlyAgent: (a) => `只看 ${a}`, clearFilter: '取消筛选',
      todoPark: '先放 Todo', noTodo: '没有待分配的任务', moreTodo: (n) => `其余 ${n} 条`,
      lessTodo: '收起', addTo: '给这个项目加任务', regFail: '登记失败',
      moreProj: (n) => `其余 ${n} 个项目`,
      win: { five_hour: '5h周期', seven_day: '7天周期', seven_day_opus: '7天周期 Opus', seven_day_sonnet: '7天周期 Sonnet' },
      hours: (h) => (h % 24 ? `${h}小时周期` : `${h / 24}天周期`),
      resetIn: (left, date) => `距重置 ${left}（${date}）`,
      resetDue: (date) => `已到重置时间（${date}）`,
      resetLimited: (left, date) => `已用完 · ${left}后恢复（${date}）`,
      foldedWarn: (items) => `另有受限：${items}`,
      usedTip: (used, left) => `剩余 ${left}%（已用 ${used}%）`,
      noUsage: '暂无数据', refresh: '刷新用量', credits: 'credits',
      quota: '额度剩余', expand: '展开', collapse: '收起',
      leftPct: (p) => `${p}%`,
      durDaysHours: (d, h) => `${d}天${h ? `${h}小时` : ''}`,
      durHoursMins: (h, m) => `${h}小时${m ? `${m}分` : ''}`,
      durMins: (m) => `${m}分钟`,
      weekdays: ['周日', '周一', '周二', '周三', '周四', '周五', '周六'], tomorrow: '明天',
      dailyByProject: '按项目', dailyByAgent: '按 AI',
      dailyTotal14: (s) => `14天总计 ${s}`, dailyAvg: (s) => `日均 ${s}`,
      dailyActiveDays: (n, tot) => `活跃 ${n}/${tot} 天`,
      dailyPeak: (s) => `峰 ${s}`, dailyAvgLine: (s) => `均 ${s}`,
      dailyOther: '其它', dailyNoDay: '当天暂无协作记录',
      dailyTodayShort: '今', dailyTotalLabel: '当日总计',
    },
    en: {
      now: 'Now', projects: 'Projects', daily: 'Last 14 days', recent: 'Recent sessions',
      active: 'Active', tokens: 'Output tokens', sessions: 'Sessions', prompts: 'prompts',
      vsAvg: (d) => `${d >= 0 ? '+' : '−'}${fmtDur(Math.abs(d))} vs 7-day avg`,
      run: 'Running', bg: 'Bg wait', wait: 'Your turn', done: 'Done', quiet: 'Quiet', error: 'Error', limit: 'Limit hit',
      ctx: 'Context', ctxWrap: 'Context is getting long — wrap up and start a new session', ctxNew: 'Context is too long — time for a new session',
      idle: 'Idle', archived: 'Archived', allProjects: 'All projects', allStates: 'All states',
      allAgents: 'All executors', openCodex: 'Open in Codex',
      nothingNow: 'Nothing running or waiting', nothingNowFor: (a) => `No ${a} session running or waiting`, indexing: 'Reading local sessions…',
      today: 'Today', open: 'Open in desktop app', task: 'Task',
      all: 'All', moreAll: 'Show more',
      moreAllCount: (n, tot) => `Show more (${n} / ${tot})`,
      loadingMore: 'Loading…',
      allLoaded: (tot) => `All ${tot} loaded`,
      noSessions: 'No sessions',
      onlyAgent: (a) => `Only ${a}`, clearFilter: 'Clear filter',
      todoPark: 'Keep in Todo', noTodo: 'Nothing waiting to be assigned', moreTodo: (n) => `${n} more`,
      lessTodo: 'Show less', addTo: 'Add a task here', regFail: 'Could not add the project',
      moreProj: (n) => `${n} more projects`,
      win: { five_hour: '5h cycle', seven_day: '7d cycle', seven_day_opus: '7d Opus', seven_day_sonnet: '7d Sonnet' },
      hours: (h) => (h % 24 ? `${h}h cycle` : `${h / 24}d cycle`),
      resetIn: (left, date) => `Resets in ${left} (${date})`,
      resetDue: (date) => `Reset due (${date})`,
      resetLimited: (left, date) => `Exhausted · ${left} (${date})`,
      foldedWarn: (items) => `Exhausted: ${items}`,
      usedTip: (used, left) => `${left}% left (${used}% used)`,
      noUsage: 'No data', refresh: 'Refresh usage', credits: 'credits',
      quota: 'Quota left', expand: 'Expand', collapse: 'Collapse',
      leftPct: (p) => `${p}%`,
      durDaysHours: (d, h) => `${d}d${h ? ` ${h}h` : ''}`,
      durHoursMins: (h, m) => `${h}h${m ? ` ${m}m` : ''}`,
      durMins: (m) => `${m}m`,
      weekdays: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'], tomorrow: 'Tomorrow',
      dailyByProject: 'By project', dailyByAgent: 'By AI',
      dailyTotal14: (s) => `14d total ${s}`, dailyAvg: (s) => `Avg ${s}/d`,
      dailyActiveDays: (n, tot) => `Active ${n}/${tot}d`,
      dailyPeak: (s) => `Peak ${s}`, dailyAvgLine: (s) => `Avg ${s}`,
      dailyOther: 'Other', dailyNoDay: 'No activity on this day',
      dailyTodayShort: 'Today', dailyTotalLabel: 'Total',
    },
  };
  const tr = (k, ...a) => {
    const v = (L[state.lang] || L.zh)[k] ?? L.zh[k];
    return typeof v === 'function' ? v(...a) : v;
  };

  // High-contrast palette that reads cleanly on both light and dark surfaces.
  const PALETTE = ['#6366f1', '#10b981', '#f59e0b', '#ec4899', '#06b6d4', '#8b5cf6',
                   '#ef4444', '#84cc16', '#f97316', '#14b8a6', '#a855f7', '#64748b'];
  const AGENT_COLORS = { claude: '#d97757', codex: '#0e7490', antigravity: '#6d28d9' };
  const OTHER = '#9a948b';
  const color = (i) => (i == null || i < 0 ? OTHER : PALETTE[i % PALETTE.length]);
  const STATE_ORDER = ['error', 'limit', 'wait', 'run', 'done', 'quiet', 'idle', 'archived'];

  // ── formatting ──
  function fmtDur(ms) {
    const m = Math.round((ms || 0) / 60000);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60), r = m % 60;
    return r ? `${h}h ${r}m` : `${h}h`;
  }
  function fmtDurShort(ms) {
    const m = Math.round((ms || 0) / 60000);
    if (m <= 0) return '';
    if (m < 60) return `${m}m`;
    const h = m / 60;
    if (h >= 10) return `${Math.round(h)}h`;
    return `${h.toFixed(1).replace(/\.0$/, '')}h`;
  }
  function fmtTok(n) {
    n = n || 0;
    if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`;
    if (n >= 1e3) return `${Math.round(n / 1e3)}k`;
    return String(n);
  }
  // How big the conversation has grown — shown only once it is worth wrapping up.
  function ctxChipHtml(c, tag = 'span', attrs = '') {
    if (!c || !c.ctx_level) return '';
    const tip = c.ctx_level >= 2 ? tr('ctxNew') : tr('ctxWrap');
    return `<${tag} class="ctx-chip lv-${c.ctx_level}" title="${esc(tip)}" ${attrs}>${esc(tr('ctx'))} ${fmtTok(c.ctx)}</${tag}>`;
  }

  function ago(ms) {
    const s = Math.max(0, (Date.now() - ms) / 1000);
    const zh = state.lang !== 'en';
    if (s < 60) return zh ? '刚刚' : 'now';
    if (s < 3600) return zh ? `${Math.floor(s / 60)} 分钟前` : `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return zh ? `${Math.floor(s / 3600)} 小时前` : `${Math.floor(s / 3600)}h ago`;
    return zh ? `${Math.floor(s / 86400)} 天前` : `${Math.floor(s / 86400)}d ago`;
  }
  // Clock time relative to the server's day start, so a phone in another
  // timezone shows the same hours as the machine the sessions ran on.
  const hm = (ms) => {
    const m = Math.floor((ms - (ov.data?.day_start ?? 0)) / 60000);
    const mm = ((m % 1440) + 1440) % 1440;
    return `${String(Math.floor(mm / 60)).padStart(2, '0')}:${String(mm % 60).padStart(2, '0')}`;
  };
  const esc = (s) => escapeHtml(String(s ?? ''));

  // ── data ──
  async function refreshOverview() {
    let data;
    try { data = await api('/overview'); } catch { return; }
    if (!data || data.error) return;
    const { generated_at, ...rest } = data;
    const sig = JSON.stringify(rest) + Math.floor(generated_at / 60000);
    if (sig === ov.sig) return;      // nothing visible changed
    ov.sig = sig;
    ov.data = data;
    if (!state.activeProjectId) renderOverview();
  }

  function projColor(key) {
    const p = ov.data?.projects.find((x) => x.key === key);
    return color(p?.color);
  }

  // ── render ──
  function renderOverview() {
    const d = ov.data;
    if (!d) return false;
    $('#ovModeNow').textContent = tr('now');
    $('#ovModeAll').firstChild.textContent = `${tr('all')} `;
    $('#ovAllCount').textContent = d.all_count || '';
    $('#projectViewTitle').textContent = tr('projects');
    $('#ovDailyTitle').textContent = tr('daily');
    $('#ovDailyModeProj').textContent = tr('dailyByProject');
    $('#ovDailyModeAgent').textContent = tr('dailyByAgent');
    renderStats(d);
    renderSpiral(d);
    renderUsage(d.usage);
    renderNowArea(d);
    renderTodo(d);
    renderProjects(d);
    renderDaily(d);
    return true;
  }

  function renderStats(d) {
    const t = d.today;
    let delta = '';
    if (t.prev_avg_ms != null) {
      const diff = t.active_ms - t.prev_avg_ms;
      delta = `<div class="ov-stat-sub ${diff >= 0 ? 'up' : 'down'}">${esc(tr('vsAvg', diff))}</div>`;
    }
    $('#ovStats').innerHTML = `
      <div class="ov-stat ov-stat-main">
        <div class="ov-stat-num">${fmtDur(t.active_ms)}</div>
        <div class="ov-stat-label">${tr('active')}</div>${delta}
      </div>
      <div class="ov-stat">
        <div class="ov-stat-num">${fmtTok(t.out_tokens)}</div>
        <div class="ov-stat-label">${tr('tokens')}</div>
      </div>
      <div class="ov-stat">
        <div class="ov-stat-num">${t.sessions}</div>
        <div class="ov-stat-label">${tr('sessions')} · ${t.prompts} ${tr('prompts')}</div>
      </div>
      ${agentSplit(t.by_agent || {})}`;
  }

  // Today per executor; each one filters "now" and "recent" to its sessions.
  // Every executor is listed (one with nothing today shows just its name), so each
  // can be picked to filter; a second click — or the chip by "现在 | 全部" — clears it.
  function agentSplit(byAgent) {
    return `<div class="ov-agent-split">${AGENT_ORDER.map((a) => {
      const v = byAgent[a];
      const on = ov.filterAgent === a;
      return `<button class="agent-${a} ${on ? 'on' : ''} ${v ? '' : 'quiet'}" data-filter-agent="${a}" aria-pressed="${on}"><i></i>${esc(agentName(a))}${v ? ` ${fmtDur(v.active_ms)}${v.out ? ` · ${fmtTok(v.out)}` : ''}` : ''}</button>`;
    }).join('')}</div>`;
  }

  function renderFilterChip() {
    const chip = $('#ovFilterChip');
    if (!chip) return;
    chip.classList.toggle('hidden', !ov.filterAgent);
    chip.innerHTML = ov.filterAgent
      ? `${esc(tr('onlyAgent', agentName(ov.filterAgent)))} <span aria-hidden="true">✕</span>` : '';
    chip.title = tr('clearFilter');
  }

  function setAgentFilter(a) {
    ov.filterAgent = ov.filterAgent === a ? '' : a;
    ov.all.limit = ALL_PAGE_SIZE;
    if (ov.data) { renderStats(ov.data); renderNowArea(ov.data, true); }
  }

  // Spiral: one day in two turns, outer = 00:00, winding inward to 24:00.
  const SP = { size: 300, r0: 132, r1: 66, track: 20 };
  function spiralPoint(ms, dayStart) {
    const f = Math.min(Math.max((ms - dayStart) / 86400000, 0), 1);
    const ang = f * 2 * 2 * Math.PI - Math.PI / 2;          // 2 turns, 0h at top
    const r = SP.r0 - (SP.r0 - SP.r1) * f;
    const c = SP.size / 2;
    return [c + r * Math.cos(ang), c + r * Math.sin(ang), r, ang];
  }
  function spiralPath(a, b, dayStart, offset = 0) {
    const step = 4 * 60000;
    const pts = [];
    for (let t = a; t < b; t += step) pts.push(t);
    pts.push(b);
    return pts.map((t, i) => {
      const [x, y, r, ang] = spiralPoint(t, dayStart);
      const c = SP.size / 2;
      const px = c + (r + offset) * Math.cos(ang), py = c + (r + offset) * Math.sin(ang);
      return `${i ? 'L' : 'M'}${px.toFixed(1)} ${py.toFixed(1)}`;
    }).join('');
  }

  function renderSpiral(d) {
    const el = $('#ovSpiral');
    const ds = d.day_start, now = d.generated_at, c = SP.size / 2;
    if (d.indexing) {
      el.innerHTML = `<div class="ov-indexing">${tr('indexing')}</div>`;
      return;
    }
    // project → lane inside the track, busiest first
    const order = [...new Set(d.timeline.map((s) => s.project))];
    const tot = {};
    d.timeline.forEach((s) => { tot[s.project] = (tot[s.project] || 0) + (s.end - s.start); });
    order.sort((a, b) => tot[b] - tot[a]);
    const lanes = [0, -6, 6];
    const lane = (k) => lanes[Math.min(order.indexOf(k), lanes.length - 1)];

    const ticks = [];
    for (let h = 0; h < 24; h += 3) {
      const [, , r, ang] = spiralPoint(ds + h * 3600000, ds);
      const rr = h < 12 ? r + SP.track / 2 + 11 : r - SP.track / 2 - 10;
      ticks.push(`<text x="${(c + rr * Math.cos(ang)).toFixed(1)}" y="${(c + rr * Math.sin(ang) + 3.5).toFixed(1)}" class="ov-sp-tick">${h}</text>`);
    }
    const segs = d.timeline.map((s, i) => `
      <path class="ov-sp-seg" data-i="${i}" d="${spiralPath(s.start, Math.max(s.end, s.start + 60000), ds, lane(s.project))}"
        stroke="${projColor(s.project)}" />`).join('');
    const [nx, ny] = spiralPoint(now, ds);
    el.innerHTML = `
      <svg viewBox="0 0 ${SP.size} ${SP.size}" class="ov-sp-svg">
        <path class="ov-sp-track" d="${spiralPath(ds, ds + 86400000 - 1, ds)}" stroke-width="${SP.track}" />
        <path class="ov-sp-past" d="${spiralPath(ds, now, ds)}" stroke-width="${SP.track}" />
        ${segs}
        ${ticks.join('')}
        <line x1="${c}" y1="${c}" x2="${nx.toFixed(1)}" y2="${ny.toFixed(1)}" class="ov-sp-hand" />
        <circle cx="${nx.toFixed(1)}" cy="${ny.toFixed(1)}" r="5" class="ov-sp-now" />
        <text x="${c}" y="${c + 7}" class="ov-sp-clock">${hm(now)}</text>
      </svg>`;
    const info = $('#ovSpiralInfo');
    const show = (i) => {
      const s = d.timeline[i];
      if (!s) { info.innerHTML = '&nbsp;'; return; }
      const p = d.projects.find((x) => x.key === s.project);
      info.innerHTML = `<span class="ov-dot" style="background:${projColor(s.project)}"></span>
        ${esc(p?.name || '')} · ${esc(s.title)} · ${hm(s.start)}–${hm(s.end)}`;
      el.querySelectorAll('.ov-sp-seg').forEach((x) => x.classList.toggle('dim', x.dataset.i != i));
    };
    el.querySelectorAll('.ov-sp-seg').forEach((p) => {
      p.addEventListener('mouseenter', () => show(+p.dataset.i));
      p.addEventListener('click', () => show(+p.dataset.i));
    });
    el.querySelector('svg').addEventListener('mouseleave', () => {
      info.innerHTML = '&nbsp;';
      el.querySelectorAll('.ov-sp-seg').forEach((x) => x.classList.remove('dim'));
    });
  }

  // States whose card carries the progress pill (it says the state and the step itself).
  const PILL_STATES = ['run', 'wait', 'error', 'limit'];
  const dispState = (c) => (c && c.state === 'run' && c.bg_wait ? 'bg' : (c && c.state) || 'idle');

  // Runs are on subscriptions, so tokens say little: cards show how long the session
  // has been worked on instead (rows already have their own duration column).
  function sessionLine(c, pill = false, dur = false) {
    const bits = [esc(c.project_name)];
    if (pill) { /* state, progress and step are in the pill */ }
    else if (c.state === 'error' || c.state === 'limit') bits.push(esc(c.err));
    else if (c.todo && c.todo.total) bits.push(`${c.todo.done}/${c.todo.total}${c.todo.cur ? ' · ' + esc(c.todo.cur) : ''}`);
    else if (c.step) bits.push(`<span class="ov-mono">${esc(c.step)}</span>`);
    bits.push(ago(c.last_at));
    if (dur && c.active_ms) bits.push(fmtDur(c.active_ms));
    return bits.join(' · ') + (c.ctx_level ? ` ${ctxChipHtml(c)}` : '');
  }

  function resumeUrl(agent, sid) {
    if (!sid) return '';
    if (agent === 'codex') return `codex://threads/${encodeURIComponent(sid)}`;
    if (agent === 'claude' || !agent) return `claude://resume?session=${encodeURIComponent(sid)}`;
    return '';                                  // Antigravity has no per-conversation link
  }

  // Opening a session in the desktop app is gone: this page is used from the phone.
  const openArrow = () => '';

  // "现在 | 全部": the live sessions as cards; "all" adds every other session below as rows.
  function renderNowArea(d, refetch) {
    renderFilterChip();
    $$('[data-now-mode]').forEach((b) => b.classList.toggle('on', b.dataset.nowMode === ov.mode));
    $('#ovAllProject').classList.toggle('hidden', ov.mode !== 'all');
    if (ov.mode === 'all') {
      renderAll(d);
      if (refetch !== false) fetchAll();
    } else {
      renderNow(d);
    }
  }

  // 全部 › 已归档 (on) or back to 全部 (off)
  function openArchive(on) {
    ov.allArchived = Boolean(on);
    ov.all.limit = ALL_PAGE_SIZE;
    ov.all.rows = [];
    if (ov.mode !== 'all') setMode('all'); else if (ov.data) renderNowArea(ov.data);
  }

  function setMode(m) {
    if (m === 'now') ov.allArchived = false;
    if (ov.mode !== m && m === 'all') ov.all.limit = ALL_PAGE_SIZE;
    ov.mode = m;
    try { localStorage.setItem('ov.mode', m); } catch { /* private mode */ }
    if (ov.data) renderNowArea(ov.data);
  }

  async function fetchAll(more) {
    if (more) {
      if (ov.all.loading || (ov.all.total && ov.all.rows.length >= ov.all.total)) return;
      ov.all.limit += ALL_PAGE_SIZE;
    }
    ov.all.loading = true;
    const moreBtn = $('#ovNow [data-more-all]');
    if (moreBtn) {
      moreBtn.disabled = true;
      moreBtn.textContent = tr('loadingMore');
    }
    const q = new URLSearchParams({ offset: 0, limit: ov.all.limit, agent: ov.filterAgent, project: ov.allProject,
                                    archived: ov.allArchived ? '1' : '' });
    const key = q.toString();
    ov.all.key = key;
    let r;
    try { r = await api(`/sessions?${q}`); } catch { ov.all.loading = false; return; }
    ov.all.loading = false;
    if (!r || r.error || ov.all.key !== key) return;
    Object.assign(ov.all, { rows: r.rows, total: r.total, projects: r.projects, archived: r.archived || 0 });
    if (ov.mode === 'all' && ov.data) renderAll(ov.data, Boolean(more));
  }

  function rowHtml(c) {
    const ag = c.agent || 'claude';
    const st = dispState(c);
    const liveBadge = ['run', 'wait', 'error', 'limit'].includes(c.state)
      ? `<span class="ov-badge st-${st}">${tr(st)}</span>` : '';
    return `
      <div class="ov-row st-${st} agent-${esc(ag)}" data-sid="${esc(c.sid)}" data-agent="${esc(ag)}" ${c.task ? `data-pid="${c.task.project_id}" data-tid="${c.task.task_id}"` : ''}>
        <span class="ov-state-dot st-${st}" title="${tr(st)}"></span>
        <div class="ov-row-main">
          <div class="ov-row-title">${agentTag(ag)}${c.task ? `<span class="ov-task-tag">${tr('task')} #${c.task.task_id}</span>` : ''}<span class="ov-row-title-text">${esc(c.title)}</span></div>
          <div class="ov-row-sub"><span class="ov-dot" style="background:${projColor(c.project)}"></span>${sessionLine(c)}</div>
        </div>
        ${liveBadge}
        <div class="ov-row-dur">${c.active_ms ? fmtDur(c.active_ms) : ''}</div>
        ${openArrow(c)}
      </div>`;
  }

  function renderAll(d) {
    const sel = $('#ovAllProject');
    sel.innerHTML = `<option value="">${tr('allProjects')}</option>` + ov.all.projects.map((p) =>
      `<option value="${esc(p.key)}" ${p.key === ov.allProject ? 'selected' : ''}>${esc(p.name)}</option>`).join('');
    const prevScroll = $('#ovNow .ov-all-list')?.scrollTop || 0;
    const known = new Set(ov.all.rows.map((c) => c.sid));
    const extraLive = ov.allArchived ? [] : d.now.filter((c) => (!c.sid || !known.has(c.sid))
      && (!ov.filterAgent || c.agent === ov.filterAgent)
      && (!ov.allProject || c.project === ov.allProject));
    const rows = [...extraLive, ...ov.all.rows];
    const total = Math.max(ov.all.total || 0, rows.length);
    const hasMore = ov.all.rows.length < (ov.all.total || 0);
    let foot = '';
    if (hasMore) {
      foot = `<div class="ov-all-foot">
        <button type="button" class="ov-more" data-more-all ${ov.all.loading ? 'disabled' : ''}>${esc(ov.all.loading ? tr('loadingMore') : tr('moreAllCount', rows.length, total))}</button>
        ${ov.all.limit > ALL_PAGE_SIZE ? `<button type="button" class="ov-more ov-less" data-less-all>${esc(tr('lessTodo'))}</button>` : ''}
      </div>`;
    } else if (total > ALL_PAGE_SIZE) {
      foot = `<div class="ov-all-foot">
        <span class="ov-all-end">${esc(tr('allLoaded', total))}</span>
        <button type="button" class="ov-more ov-less" data-less-all>${esc(tr('lessTodo'))}</button>
      </div>`;
    }
    // archived sessions sit in their own list, reached from the end of "all"
    const archHead = ov.allArchived
      ? `<button type="button" class="ov-arch-link back" data-arch="0">‹ ${esc(tr('archived'))} · ${ov.all.total || 0}</button>` : '';
    const archFoot = !ov.allArchived && ov.all.archived
      ? `<button type="button" class="ov-arch-link" data-arch="1">${esc(tr('archived'))} · ${ov.all.archived} ›</button>` : '';
    $('#ovNow').innerHTML = `<div class="ov-all-list">${archHead}${rows.length
      ? rows.map(rowHtml).join('') : `<div class="ov-empty">${tr('noSessions')}</div>`}${foot}${archFoot}</div>`;
    if (prevScroll) {
      const list = $('#ovNow .ov-all-list');
      if (list) list.scrollTop = prevScroll;
    }
  }

  function renderNow(d) {
    const el = $('#ovNow');
    const now = ov.filterAgent ? d.now.filter((c) => c.agent === ov.filterAgent) : d.now;
    if (!now.length) {
      el.innerHTML = `<div class="ov-empty">${ov.filterAgent ? tr('nothingNowFor', agentName(ov.filterAgent)) : tr('nothingNow')}</div>`;
      return;
    }
    el.innerHTML = now.map(nowCardHtml).join('');
  }

  function nowCardHtml(c) {
    const ag = c.agent || 'claude';
    const st = dispState(c);
    const pill = PILL_STATES.includes(c.state);
    return `
      <div class="ov-sess st-${st} agent-${esc(ag)}" data-sid="${esc(c.sid)}" data-agent="${esc(ag)}" ${c.task ? `data-pid="${c.task.project_id}" data-tid="${c.task.task_id}"` : ''}>
        <div class="ov-sess-top">
          ${agentTag(ag)}
          ${c.task ? `<span class="ov-task-tag">#${c.task.task_id}</span>` : ''}
          <span class="ov-sess-title">${esc(c.title)}</span>
          ${pill ? '' : `<span class="ov-badge st-${st}">${tr(st)}</span>`}
          ${openArrow(c)}
        </div>
        <div class="ov-sess-line">
          <span class="ov-dot" style="background:${projColor(c.project)}"></span>${sessionLine(c, pill, true)}
        </div>
        ${pill ? pillHtml({ agent: ag }, c) : ''}
      </div>`;
  }

  const pct = (todo) => Math.round((todo.done / todo.total) * 100);

  // ── Todo: add a task to any project, hand out what is waiting ──
  const TODO_SHOWN = 8;
  function todoProjects(d) {
    return d.projects.filter((p) => p.key !== 'other' && (p.registered || p.path));
  }
  function renderTodo(d) {
    const pSel = $('#ovTodoProject'), aSel = $('#ovTodoAgent');
    const tgl = $('#ovTodoToggle');
    if (tgl) {
      tgl.textContent = ov.todoOpen
        ? (state.lang === 'en' ? '× Close' : '× 收起')
        : (state.lang === 'en' ? '+ New task' : '+ 添加任务');
    }
    const sub = $('#ovTodoSubmit');
    if (sub) sub.textContent = state.lang === 'en' ? 'Add' : '添加';
    const addTodoBtn = $('#ovTodoAddBtn');
    if (addTodoBtn) {
      const label = state.lang === 'en' ? 'Add Todo' : '添加 Todo';
      addTodoBtn.textContent = '＋';
      addTodoBtn.title = label;
      addTodoBtn.setAttribute('aria-label', label);
    }
    const plist = todoProjects(d);
    if (!plist.some((p) => p.key === ov.todoProject)) ov.todoProject = plist[0]?.key || '';
    pSel.innerHTML = plist.map((p) =>
      `<option value="${esc(p.key)}" ${p.key === ov.todoProject ? 'selected' : ''}>${esc(p.name)}</option>`).join('');
    aSel.innerHTML = ['todo', ...AGENT_ORDER, 'me'].map((a) =>
      `<option value="${a}" ${a === ov.todoAgent ? 'selected' : ''}>${esc(a === 'todo' ? tr('todoPark') : agentName(a))}</option>`).join('');
    $('#ovTodoInput').placeholder = state.lang === 'en' ? 'Describe a task, press Enter to add…' : '输入任务内容，回车添加…';

    const todos = d.todos || [];
    $('#ovTodoCount').textContent = todos.length || '';
    const shown = ov.todoAll ? todos : todos.slice(0, TODO_SHOWN);
    const rows = shown.map((x) => {
      const mine = x.agent === 'me';
      const ico = (a) => window.agentIcon ? window.agentIcon(a) : '';
      const hand = [...AGENT_ORDER, ...(mine ? [] : ['me'])].map((a) =>
        `<button class="card-action-btn assign agent-${a}" data-assign="${a}">${ico(a)}<span>${esc(agentName(a))}</span></button>`).join('');
      const badge = x.agent ? `${agentTag(x.agent)} ` : '';
      return `
        <div class="ov-todo-row ${x.agent ? `agent-${esc(x.agent)}` : ''}" data-pid="${x.project_id}" data-tid="${x.task_id}" data-agent="${esc(x.agent || 'claude')}">
          ${mine ? `<button class="ov-todo-check" data-finish title="${esc(t('finish'))}"></button>` : ''}
          <div class="ov-todo-main">
            <div class="ov-todo-text">${badge}${esc(x.description)}</div>
            <div class="ov-todo-sub"><span class="ov-dot" style="background:${projColor(x.project)}"></span>${esc(x.project_name)}</div>
          </div>
          <div class="ov-todo-hand">${hand}</div>
        </div>`;
    }).join('');
    const more = todos.length > TODO_SHOWN
      ? `<button class="ov-todo-more" data-more>${esc(ov.todoAll ? tr('lessTodo') : tr('moreTodo', todos.length - TODO_SHOWN))}</button>`
      : '';
    $('#ovTodoList').innerHTML = todos.length ? rows + more : `<div class="ov-empty">${tr('noTodo')}</div>`;
  }

  // Registered project id for an overview key; folders found in session logs
  // are registered on the spot without writing anything into them.
  async function ensureProject(key) {
    if (/^p\d+$/.test(key)) return +key.slice(1);
    const p = ov.data?.projects.find((x) => x.key === key);
    if (!p || !p.path) return null;
    const res = await api('/projects', { method: 'POST', body: { name: p.name, path: p.path, inject: false } });
    if (!res.project) { alert(`${tr('regFail')}: ${res.error || ''}`); return null; }
    await refreshProjects();
    return res.project.id;
  }

  function setTodoOpen(open, focus = false) {
    ov.todoOpen = Boolean(open);
    const form = $('#ovTodoForm');
    const btn = $('#ovTodoToggle');
    if (form) form.classList.toggle('hidden', !ov.todoOpen);
    const atTodo = form?.parentElement?.classList.contains('ov-todo');
    $('#ovTodoAddBtn')?.classList.toggle('is-open', ov.todoOpen && atTodo);
    if (btn) {
      btn.setAttribute('aria-expanded', String(ov.todoOpen && !atTodo));
      btn.classList.toggle('is-open', ov.todoOpen && !atTodo);
      btn.textContent = ov.todoOpen && !atTodo
        ? (state.lang === 'en' ? '× Close' : '× 收起')
        : (state.lang === 'en' ? '+ New task' : '+ 添加任务');
    }
    if (ov.todoOpen && focus) {
      const input = $('#ovTodoInput');
      input?.focus({ preventScroll: true });
    }
  }

  async function addTodo() {
    const input = $('#ovTodoInput');
    const desc = input.value.trim();
    if (!desc || ov.busy) return;
    ov.busy = true;
    try {
      const pid = await ensureProject(ov.todoProject);
      if (pid == null) return;
      const res = await api(`/projects/${pid}/tasks`, { method: 'POST', body: { description: desc, agent: ov.todoAgent } });
      if (res.error) { alert(res.error); return; }
      input.value = '';
      setTodoOpen(false);
      ov.todoProject = `p${pid}`;
      ov.sig = '';
      await refreshOverview();
    } finally {
      ov.busy = false;
    }
  }

  function sparkSvg(values, col) {
    const max = Math.max(...values, 1), w = 6, gap = 2, h = 26;
    const bars = values.map((v, i) => {
      const bh = v ? Math.max(2, Math.round((v / max) * h)) : 1;
      return `<rect x="${i * (w + gap)}" y="${h - bh}" width="${w}" height="${bh}" rx="1.5"
        fill="${v ? col : 'var(--border-light)'}" />`;
    }).join('');
    return `<svg class="ov-spark" viewBox="0 0 ${values.length * (w + gap) - gap} ${h}">${bars}</svg>`;
  }

  function renderProjects(d) {
    const grid = $('#projectGrid');
    // Recent ones first (the list is already ordered: live, then last active);
    // anything running, waiting or with queued work always stays visible.
    const busy = (p) => p.run || p.bg_wait || p.wait || ['pending', 'in_progress', 'review'].some((s) => p.tasks?.[s]);
    const shown = ov.projAll ? d.projects : d.projects.filter((p, i) => i < PROJ_SHOWN || busy(p));
    const hidden = d.projects.length - shown.length;
    const more = d.projects.length > PROJ_SHOWN && (hidden || ov.projAll)
      ? `<button class="ov-proj-more" data-proj-more>${esc(ov.projAll ? tr('lessTodo') : tr('moreProj', hidden))}</button>`
      : '';
    grid.innerHTML = shown.map((p) => {
      const col = color(p.color);
      const tasks = p.tasks || {};
      const live = [];
      if (p.run) live.push(`<span class="ov-badge st-run">${p.run} ${tr('run')}</span>`);
      if (p.bg_wait) live.push(`<span class="ov-badge st-bg">${p.bg_wait} ${tr('bg')}</span>`);
      if (p.wait) live.push(`<span class="ov-badge st-wait">${p.wait} ${tr('wait')}</span>`);
      ['pending', 'in_progress', 'review'].forEach((s) => {
        if (tasks[s]) live.push(`<span class="project-stat ${s === 'pending' ? 'pending' : 'active'}">${tasks[s]} ${esc(t(s))}</span>`);
      });
      const today = p.today_ms
        ? `${tr('today')} ${fmtDur(p.today_ms)}${p.today_out ? ` · ${fmtTok(p.today_out)} tok` : ''}`
        : (p.last_at ? ago(p.last_at) : '');
      const attrs = p.registered
        ? `data-project-id="${p.project_id}"`
        : `data-key="${esc(p.key)}"`;
      return `
        <div class="project-card ov-pcard ${p.registered ? '' : 'unregistered'}" ${attrs} style="--pc:${col}">
          ${p.registered ? `<button class="project-card-delete" data-project-id="${p.project_id}" title="Delete">&times;</button>` : ''}
          <div class="ov-pcard-head">
            <span class="ov-dot" style="background:${col}"></span>
            <span class="project-card-name">${esc(p.name)}</span>
            ${p.key !== 'other' ? `<button class="ov-add" data-add="${esc(p.key)}" title="${esc(tr('addTo'))}">+</button>` : ''}
          </div>
          <div class="ov-pcard-body">
            <div class="ov-pcard-meta">
              <div class="ov-pcard-today">${today || '&nbsp;'}</div>
              <div class="ov-pcard-badges">${live.join('')}</div>
            </div>
            ${sparkSvg(p.spark, col)}
          </div>
        </div>`;
    }).join('') + more;
  }

  let _dailyModel = null;

  function buildDailyModel(d) {
    const days = d.daily || [];
    const byAgent = ov.dailyMode === 'agent';
    const totalsByKey = {};
    const rawMaps = days.map((day) => {
      const src = byAgent ? (day.agents || {}) : (day.projects || {});
      const out = {};
      for (const [k, v] of Object.entries(src)) {
        if (v > 0) {
          out[k] = v;
          totalsByKey[k] = (totalsByKey[k] || 0) + v;
        }
      }
      return out;
    });

    const sortedKeys = Object.keys(totalsByKey).sort((a, b) => totalsByKey[b] - totalsByKey[a]);
    let items = [];
    let dayMaps = rawMaps;

    if (byAgent) {
      items = sortedKeys.map((k) => ({
        key: k,
        name: agentName(k),
        color: AGENT_COLORS[k] || OTHER,
        total: totalsByKey[k],
      }));
    } else {
      const topKeys = sortedKeys.filter((k) => k !== 'other').slice(0, 5);
      const topSet = new Set(topKeys);
      let otherTotal = 0;
      dayMaps = rawMaps.map((m) => {
        const grouped = {};
        for (const [k, v] of Object.entries(m)) {
          if (topSet.has(k)) {
            grouped[k] = v;
          } else {
            grouped.other = (grouped.other || 0) + v;
            otherTotal += v;
          }
        }
        return grouped;
      });
      items = topKeys.map((k) => ({
        key: k,
        name: d.projects.find((p) => p.key === k)?.name || k,
        color: projColor(k),
        total: totalsByKey[k],
      }));
      if (otherTotal > 0) {
        items.push({ key: 'other', name: tr('dailyOther'), color: OTHER, total: otherTotal });
      }
    }

    if (ov.dailyFilter && !items.some((it) => it.key === ov.dailyFilter)) {
      ov.dailyFilter = '';
    }
    const dayTotals = dayMaps.map((m) => (
      ov.dailyFilter
        ? (m[ov.dailyFilter] || 0)
        : Object.values(m).reduce((a, b) => a + b, 0)
    ));
    return { days, items, dayMaps, dayTotals };
  }

  function dailyDateMeta(dateStr, idx, total) {
    const [y, m, dayNum] = dateStr.split('-').map(Number);
    const dt = new Date(y, m - 1, dayNum);
    const wd = dt.getDay();
    const isWeekend = wd === 0 || wd === 6;
    const isToday = idx === total - 1;
    const axisLabel = isToday
      ? tr('dailyTodayShort')
      : (idx === 0 || dayNum === 1 ? `${m}/${dayNum}` : String(dayNum));
    return {
      weekday: tr('weekdays')[wd] || '',
      isWeekend,
      isToday,
      axisLabel,
    };
  }

  function renderDailyDetail(model, idx) {
    const day = model.days[idx];
    if (!day) return '';
    const meta = dailyDateMeta(day.date, idx, model.days.length);
    const m = model.dayMaps[idx] || {};
    const total = model.dayTotals[idx] || 0;
    const entries = model.items
      .filter((it) => (!ov.dailyFilter || it.key === ov.dailyFilter) && (m[it.key] || 0) > 0)
      .map((it) => ({ ...it, ms: m[it.key] }))
      .sort((a, b) => b.ms - a.ms);
    const denom = entries.reduce((a, b) => a + b.ms, 0) || 1;
    const todayBadge = meta.isToday ? `<span class="ov-daily-today-tag">${esc(tr('today'))}</span>` : '';

    return `
      <div class="ov-daily-detail-head">
        <div class="ov-daily-detail-date">
          <span>${esc(day.date)}</span>
          <span class="ov-daily-detail-wd">${esc(meta.weekday)}</span>
          ${todayBadge}
        </div>
        <div class="ov-daily-detail-total">
          ${total > 0 ? `${esc(tr('dailyTotalLabel'))} <strong>${esc(fmtDur(total))}</strong>` : ''}
        </div>
      </div>
      ${entries.length ? `<div class="ov-daily-detail-list">${entries.map((it) => {
        const pct = Math.max(1, Math.round((it.ms / denom) * 100));
        return `
          <div class="ov-daily-detail-item">
            <span class="ov-daily-dot" style="background:${it.color}"></span>
            <span class="ov-daily-item-name">${esc(it.name)}</span>
            <div class="ov-daily-item-bar"><span style="width:${pct}%;background:${it.color}"></span></div>
            <span class="ov-daily-item-val">${esc(fmtDur(it.ms))}</span>
            <span class="ov-daily-item-pct">${pct}%</span>
          </div>`;
      }).join('')}</div>` : `<div class="ov-daily-detail-empty">${esc(tr('dailyNoDay'))}</div>`}`;
  }

  function selectDailyDay(idx) {
    if (!_dailyModel || idx < 0 || idx >= _dailyModel.days.length) return;
    ov.dailyDay = idx;
    const el = $('#ovDaily');
    el.querySelectorAll('.ov-bar').forEach((bar) => {
      bar.classList.toggle('is-selected', Number(bar.dataset.dayIdx) === idx);
    });
    const detail = el.querySelector('#ovDailyDetail');
    if (detail) detail.innerHTML = renderDailyDetail(_dailyModel, idx);
  }

  function renderDaily(d) {
    const el = $('#ovDaily');
    const summaryEl = $('#ovDailySummary');
    const seg = $('#ovDailySeg');
    if (seg) {
      seg.querySelectorAll('[data-daily-mode]').forEach((b) => {
        b.classList.toggle('on', b.dataset.dailyMode === ov.dailyMode);
      });
    }

    const model = _dailyModel = buildDailyModel(d);
    const { days, items, dayMaps, dayTotals } = model;
    const activeDays = dayTotals.filter((v) => v > 0);
    const sum14 = dayTotals.reduce((a, b) => a + b, 0);
    const avgActive = activeDays.length ? Math.round(sum14 / activeDays.length) : 0;
    const max = Math.max(...dayTotals, 1);
    const peakIdx = dayTotals.indexOf(Math.max(...dayTotals));

    if (summaryEl) {
      summaryEl.innerHTML = sum14 > 0
        ? `<span>${esc(tr('dailyTotal14', fmtDurShort(sum14)))}</span>`
          + `<span>${esc(tr('dailyAvg', fmtDurShort(avgActive)))}</span>`
          + `<span>${esc(tr('dailyActiveDays', activeDays.length, days.length))}</span>`
        : '';
    }

    if (ov.dailyDay == null || ov.dailyDay < 0 || ov.dailyDay >= days.length) {
      let def = days.length - 1;
      if (!dayTotals[def]) {
        const lastActive = [...dayTotals].reverse().findIndex((v) => v > 0);
        if (lastActive >= 0) def = days.length - 1 - lastActive;
      }
      ov.dailyDay = def;
    }

    const legendHtml = items.length ? `
      <div class="ov-daily-legend">
        ${items.map((it) => {
          const active = ov.dailyFilter === it.key;
          const dimmed = ov.dailyFilter && !active;
          return `<button type="button" class="ov-legend-pill ${active ? 'is-active' : ''} ${dimmed ? 'is-dim' : ''}" data-daily-filter="${esc(it.key)}">
            <span class="ov-daily-dot" style="background:${it.color}"></span>
            <span class="ov-legend-name">${esc(it.name)}</span>
            <span class="ov-legend-val">${esc(fmtDurShort(it.total))}</span>
          </button>`;
        }).join('')}
      </div>` : '';

    const avgPct = avgActive > 0 ? Math.min(96, Math.max(4, Math.round((avgActive / max) * 100))) : 0;
    const gridHtml = sum14 > 0 ? `
      <div class="ov-daily-grid" aria-hidden="true">
        <div class="ov-daily-ref is-peak"><span>${esc(tr('dailyPeak', fmtDurShort(max)))}</span></div>
        ${avgPct > 12 && avgPct < 88 ? `<div class="ov-daily-ref is-avg" style="bottom:${avgPct}%"><span>${esc(tr('dailyAvgLine', fmtDurShort(avgActive)))}</span></div>` : ''}
      </div>` : '';

    const itemOrder = items.map((it) => it.key);
    const colorOf = Object.fromEntries(items.map((it) => [it.key, it.color]));
    const barsHtml = days.map((day, i) => {
      const m = dayMaps[i];
      const tot = dayTotals[i];
      const meta = dailyDateMeta(day.date, i, days.length);
      const keys = ov.dailyFilter ? (m[ov.dailyFilter] ? [ov.dailyFilter] : []) : itemOrder.filter((k) => m[k]);
      const stack = keys.map((k) => {
        const pct = tot > 0 ? (m[k] / tot) * 100 : 0;
        return `<div class="ov-bar-part" style="height:${pct}%;background:${colorOf[k]}"></div>`;
      }).join('');
      const valStr = fmtDurShort(tot);
      const isSel = i === ov.dailyDay;
      const isPeak = tot > 0 && i === peakIdx;
      return `
        <button type="button" class="ov-bar ${meta.isToday ? 'today' : ''} ${meta.isWeekend ? 'is-weekend' : ''} ${isSel ? 'is-selected' : ''} ${isPeak ? 'is-peak' : ''}" data-day-idx="${i}">
          <div class="ov-bar-val">${esc(valStr)}</div>
          <div class="ov-bar-plot">
            ${tot > 0 ? `<div class="ov-bar-stack" style="height:${Math.max(4, (tot / max) * 100)}%">${stack}</div>` : '<div class="ov-bar-empty"></div>'}
          </div>
          <div class="ov-bar-label">${esc(meta.axisLabel)}</div>
        </button>`;
    }).join('');

    el.innerHTML = `
      ${legendHtml}
      <div class="ov-daily-chart">
        ${gridHtml}
        <div class="ov-bars">${barsHtml}</div>
      </div>
      <div class="ov-daily-detail" id="ovDailyDetail">${renderDailyDetail(model, ov.dailyDay)}</div>`;
  }



  // ── project page: today strip, live line on cards, open in desktop ──
  const pv = { pid: null, data: null, sig: '' };

  async function refreshProjectLive() {
    const pid = state.activeProjectId;
    if (!pid) return;
    let data;
    try { data = await api(`/projects/${pid}/live`); } catch { return; }
    if (!data || data.error || state.activeProjectId !== pid) return;
    const sig = JSON.stringify(data);
    if (sig === pv.sig && pv.pid === pid) return;
    const tasksChanged = pv.pid !== pid || JSON.stringify(pv.data?.tasks) !== JSON.stringify(data.tasks)
      || JSON.stringify(pv.data?.resumable) !== JSON.stringify(data.resumable)
      || JSON.stringify(pv.data?.board) !== JSON.stringify(data.board);
    Object.assign(pv, { pid, data, sig });
    renderStrip();
    if (tasksChanged) renderBoard();
  }

  function renderStrip() {
    const el = $('#pvStrip'), d = pv.data;
    const col = color(d.color);
    const today = d.today_ms
      ? `<span class="pv-today">${tr('today')} <b>${fmtDur(d.today_ms)}</b>${d.today_out ? ` · ${fmtTok(d.today_out)} tok` : ''}</span>`
      : '';
    const chips = d.now.map((c) => {
      const ag = c.agent || 'claude';
      const st = dispState(c);
      return `
      <button class="pv-chip st-${st} agent-${esc(ag)}" data-sid="${esc(c.sid)}" data-agent="${esc(ag)}" ${c.task ? `data-pid="${c.task.project_id}" data-tid="${c.task.task_id}"` : ''}
        title="${esc(c.step || c.title)}">
        ${agentTag(ag)}
        <span class="pv-chip-title">${esc(c.title)}</span>
        <span class="pv-chip-state">${tr(st)}</span>
      </button>`;
    }).join('');
    if (!today && !chips) { el.classList.add('hidden'); return; }
    el.classList.remove('hidden');
    el.innerHTML = `${today}${d.spark?.some((x) => x) ? sparkSvg(d.spark, col) : ''}<div class="pv-chips">${chips}</div>`;
  }

  // Running task as a pill that fills with the agent's own checklist
  // (TodoWrite / update_plan), after Agent Deck's delegate pill.
  function pillHtml(task, live) {
    const todo = live.todo;
    const st = dispState(live);
    const bits = [];
    if ((live.state === 'error' || live.state === 'limit') && live.err) {
      bits.push(esc(live.err));
    } else if (todo && todo.total) {
      bits.push(`${todo.done}/${todo.total}`);
      if (todo.cur) bits.push(esc(todo.cur));
    } else if (live.step) {
      bits.push(`<span class="ov-mono">${esc(live.step)}</span>`);
    }
    const tip = [todo ? checklistText(todo) : live.step, live.out ? `${fmtTok(live.out)} tok` : '']
      .filter(Boolean).join('\n');
    const cls = [`card-pill`, `st-${st}`, `agent-${task.agent || 'claude'}`,
                 todo && todo.total ? 'is-determinate' : ''].join(' ');
    return `<div class="${cls}" style="--p:${todo && todo.total ? pct(todo) : 0}%" title="${esc(tip)}">
        <span class="card-pill-fill"></span><span class="card-pill-wave"></span>
        <span class="card-pill-label">${tr(st)}</span>
        <span class="card-pill-meta">${bits.join(' · ')}</span>
      </div>`;
  }

  const MARK = { completed: '✓', done: '✓', in_progress: '▸' };
  function checklistText(todo) {
    return (todo.items || []).map((i) => `${MARK[i.s] || '○'} ${i.t}`).join('\n');
  }

  // Full checklist for the task modal.
  function taskChecklistHtml(task) {
    const live = pv.data && pv.pid === state.activeProjectId && task.session_id
      ? pv.data.tasks[task.session_id] : null;
    const todo = live?.todo;
    if (!todo || !todo.items?.length) return '';
    return `<div class="task-checklist">
        <div class="task-checklist-head"><span>${todo.done}/${todo.total}</span>
          <span class="task-checklist-bar" style="--p:${pct(todo)}%"></span></div>
        ${todo.items.map((i) => `<div class="task-checklist-item s-${esc(i.s)}">
          <span class="task-checklist-mark">${MARK[i.s] || ''}</span><span>${esc(i.t)}</span></div>`).join('')}
      </div>`;
  }

  function taskLiveHtml(task) {
    const d = pv.data;
    if (!d || pv.pid !== state.activeProjectId) return '';
    const live = task.session_id ? d.tasks[task.session_id] : null;
    if (task.status === 'in_progress') return pillHtml(task, live || { state: 'run' });
    return '';
  }

  // ── usage: how much of each window is remaining, when it resets ──
  function whenText(ms) {
    const d = new Date(ms);
    const t = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    return `${d.getMonth() + 1}/${d.getDate()} ${tr('weekdays')[d.getDay()]} ${t}`;
  }

  function leftTimeText(diffMs) {
    const totalMins = Math.max(1, Math.ceil(diffMs / 60000));
    const days = Math.floor(totalMins / 1440);
    const hrs = Math.floor((totalMins % 1440) / 60);
    const mins = totalMins % 60;
    if (days > 0) return tr('durDaysHours', days, hrs);
    if (hrs > 0) return tr('durHoursMins', hrs, mins);
    return tr('durMins', mins);
  }

  function winLabel(name, w) {
    if (w.minutes) return tr('hours', Math.round(w.minutes / 60));
    if (w.group) return `${w.group} · ${tr('win')[w.window === '5h' ? 'five_hour' : 'seven_day']}`;
    return tr('win')[name] || name.replace(/_/g, ' ');
  }

  function windowMetrics(w) {
    const now = Date.now();
    const past = !!(w.resets_at && w.resets_at <= now);
    const blocked = !past && (w.status === 'rejected' || (w.used ?? 0) >= 100);
    const used = past ? 0 : (w.used == null ? null : Math.max(0, Math.min(100, w.used)));
    const left = blocked ? 0 : used == null ? null : Math.max(0, Math.min(100, 100 - used));
    const lvl = blocked ? 'bad' : (left != null && left <= 20) ? 'warn' : '';
    const date = w.resets_at ? whenText(w.resets_at) : '';
    const leftDur = !past && w.resets_at ? leftTimeText(w.resets_at - now) : '';
    const note = past ? tr('resetDue', date) : blocked ? (date ? tr('resetLimited', leftDur, date) : tr('limit')) : date ? tr('resetIn', leftDur, date) : '';
    return { past, blocked, used, left, lvl, note, date };
  }

  function windowHtml(name, w) {
    const m = windowMetrics(w);
    const leftPct = m.left == null ? null : Math.round(m.left);
    const usedPct = m.used == null ? null : Math.round(m.used);
    const pctTxt = leftPct == null ? '—' : tr('leftPct', leftPct);
    const tip = leftPct == null || usedPct == null ? '' : tr('usedTip', usedPct, leftPct);
    const barW = leftPct == null ? 0 : leftPct;
    return `<div class="ov-usage-win ${m.lvl}"${tip ? ` title="${esc(tip)}"` : ''}>
        <div class="ov-usage-win-head">
          <span class="ov-usage-lbl">${esc(winLabel(name, w))}</span>
          <span class="ov-usage-pct">${esc(pctTxt)}</span>
        </div>
        <span class="ov-usage-bar"><i style="width:${barW}%"></i></span>
        <span class="ov-usage-reset">${esc(m.note)}</span>
      </div>`;
  }

  // Multiple windows fold into the tightest one (Antigravity prioritizes core groups over Argon); click +N to expand all.
  const usageOpen = (() => { try { return JSON.parse(localStorage.getItem('ov.usageOpen') || '{}'); } catch { return {}; } })();
  function tightness(w) {
    if (w.resets_at && w.resets_at <= Date.now()) return -1;           // already reset
    if (w.status === 'rejected') return 1000;
    return w.used ?? -1;
  }

  function primaryWindowEntry(agent, entries) {
    if (!entries.length) return null;
    const core = agent === 'antigravity' ? entries.filter(([, w]) => w.group !== 'Argon') : [];
    const pool = core.length ? core : entries;
    return pool.reduce((a, b) => (tightness(b[1]) > tightness(a[1]) ? b : a));
  }

  function usageSummaryChip(agent, d) {
    const entries = d && d.windows ? Object.entries(d.windows) : [];
    const pick = primaryWindowEntry(agent, entries);
    if (!pick) {
      return `<span class="ov-usage-chip agent-${agent} is-empty">${agentIcon(agent)}<span>—</span></span>`;
    }
    const [name, w] = pick;
    const m = windowMetrics(w);
    const txt = m.left == null ? '—' : tr('leftPct', Math.round(m.left));
    const tip = `${winLabel(name, w)}${m.note ? ' · ' + m.note : ''}`;
    return `<span class="ov-usage-chip agent-${agent} ${m.lvl}" title="${esc(tip)}">${agentIcon(agent)}<span>${esc(txt)}</span></span>`;
  }

  function renderUsage(u) {
    const el = $('#ovUsage');
    if (!el) return;
    if (!u) { el.innerHTML = ''; return; }
    const row = (agent, d, extra = '', tool = '') => {
      const all = d && d.windows ? Object.entries(d.windows) : [];
      const folded = all.length > 1 && !usageOpen[agent];
      const pick = folded ? primaryWindowEntry(agent, all) : null;
      const wins = folded ? (pick ? [pick] : []) : all;
      const fold = all.length > 1
        ? `<button class="ov-usage-fold" data-usage-fold="${agent}" aria-expanded="${!folded}">${folded ? `+${all.length - 1}` : '−'}</button>` : '';
      let foldedNotice = '';
      if (folded && pick) {
        const exhausted = all.filter(([n, w]) => n !== pick[0] && windowMetrics(w).blocked);
        if (exhausted.length) {
          const desc = exhausted.map(([n, w]) => {
            const m = windowMetrics(w);
            return `${winLabel(n, w)}${m.date ? ` (${m.date})` : ''}`;
          }).join(state.lang === 'en' ? ', ' : '、');
          foldedNotice = `<div class="ov-usage-extra warn">${esc(tr('foldedWarn', desc))}</div>`;
        }
      }
      const body = wins.length
        ? wins.map(([n, w]) => windowHtml(n, w)).join('')
        : `<div class="ov-usage-none">${tr('noUsage')}</div>`;
      return `<div class="ov-usage-row">
          <span class="ov-usage-who">${agentTag(agent)}</span>
          <div class="ov-usage-wins">${body}${foldedNotice}${extra}</div>
          <span class="ov-usage-age">${fold}${d && d.at ? esc(ago(d.at)) : ''}${tool}</span>
        </div>`;
    };
    const credits = u.codex && u.codex.credits != null
      ? `<div class="ov-usage-extra">${tr('credits')} ${Math.round(u.codex.credits).toLocaleString()}</div>` : '';
    const errLine = (m) => (m ? `<div class="ov-usage-extra err">${esc(m)}</div>` : '');
    const btn = `<button class="ov-usage-refresh ${u.probing || ov.usageBusy ? 'busy' : ''}" data-usage-refresh
        title="${esc(tr('refresh'))}" aria-label="${esc(tr('refresh'))}">↻</button>`;
    const open = ov.usageExpanded;
    const head = `
      <div class="ov-usage-head">
        <button type="button" class="ov-usage-toggle" data-usage-toggle aria-expanded="${open}">
          <span class="ov-usage-title">${esc(tr('quota'))}</span>
          <span class="ov-usage-chips">${usageSummaryChip('claude', u.claude)}${usageSummaryChip('codex', u.codex)}${usageSummaryChip('antigravity', u.antigravity)}</span>
          <span class="ov-usage-chevron">${esc(open ? tr('collapse') : tr('expand'))} ${open ? '▴' : '▾'}</span>
        </button>
        ${open ? '' : btn}
      </div>`;
    const rowsHtml = open
      ? `<div class="ov-usage-rows">${row('claude', u.claude, errLine(ov.usageErr), btn) + row('codex', u.codex, credits) + row('antigravity', u.antigravity, errLine(ov.agyErr))}</div>`
      : '';
    el.innerHTML = head + rowsHtml;
  }

  async function refreshUsage(btn) {
    if (ov.usageBusy) return;
    ov.usageBusy = true;
    btn.classList.add('busy');
    let r;
    try { r = await api('/usage/refresh', { method: 'POST' }); } catch { r = null; }
    ov.usageBusy = false;
    ov.usageErr = r ? r.error || '' : 'Error';
    ov.agyErr = r ? r.agy_error || '' : '';
    if (r && ov.data) ov.data.usage = { claude: r.claude, codex: r.codex, antigravity: r.antigravity };
    renderUsage(ov.data?.usage);
  }

  // ── interactions ──
  async function openFolder(key) {
    if (ov.busy) return;
    ov.busy = true;
    try {
      const pid = await ensureProject(key);
      if (pid != null) enterProject(pid);
    } finally {
      ov.busy = false;
    }
  }

  async function openSession(el) {
    const sid = el.dataset.sid || (el.dataset.pid && el.dataset.tid ? `task:${el.dataset.pid}:${el.dataset.tid}` : '');
    if (sid && window.openSessionPanel) {
      window.openSessionPanel(sid, el.dataset.agent || 'claude', {
        projectId: el.dataset.pid ? +el.dataset.pid : null,
        taskId: el.dataset.tid ? +el.dataset.tid : null,
      });
    } else if (el.dataset.pid) {
      enterProject(+el.dataset.pid);
      await refreshTasks();
      openTaskModal(+el.dataset.tid);
    } else {
      const url = resumeUrl(el.dataset.agent, el.dataset.sid);
      if (url) location.href = url;
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const view = $('#projectView');
    view.addEventListener('click', (e) => {
      if (e.target.closest('[data-stop]')) return;      // the ↗ link handles itself
      const uf = e.target.closest('[data-usage-fold]');
      if (uf) {
        e.stopPropagation();
        const a = uf.dataset.usageFold;
        usageOpen[a] = !usageOpen[a];
        try { localStorage.setItem('ov.usageOpen', JSON.stringify(usageOpen)); } catch { /* private mode */ }
        renderUsage(ov.data?.usage);
        return;
      }
      const ut = e.target.closest('[data-usage-toggle]');
      if (ut) {
        e.stopPropagation();
        ov.usageExpanded = !ov.usageExpanded;
        renderUsage(ov.data?.usage);
        return;
      }
      const ur = e.target.closest('[data-usage-refresh]');
      if (ur) { e.stopPropagation(); refreshUsage(ur); return; }
      if (e.target.closest('[data-proj-more]')) {
        e.stopPropagation();
        ov.projAll = !ov.projAll;
        try { localStorage.setItem('ov.projAll', ov.projAll ? '1' : '0'); } catch { /* private mode */ }
        renderProjects(ov.data);
        return;
      }
      const add = e.target.closest('[data-add]');
      if (add) {
        e.stopPropagation();
        ov.todoProject = add.dataset.add;
        $('#ovTodoProject').value = add.dataset.add;
        setTodoOpen(true, false);
        const input = $('#ovTodoInput');
        input.scrollIntoView({ block: 'center', behavior: 'smooth' });
        input.focus({ preventScroll: true });
        return;
      }
      const card = e.target.closest('.ov-pcard');
      if (card && !card.dataset.projectId && !e.target.closest('.project-card-delete')) {
        e.stopPropagation();
        if (card.dataset.key === 'other') {
          ov.allProject = 'other';
          setMode('all');
          $('#ovNow').closest('.ov-now-wrap').scrollIntoView({ block: 'start', behavior: 'smooth' });
        } else if (card.dataset.key) {
          openFolder(card.dataset.key);
        }
        return;
      }
      const s = e.target.closest('.ov-sess, .ov-row');
      if (s) openSession(s);
    }, true);
    $('#ovTodoToggle')?.addEventListener('click', () => {
      const form = $('#ovTodoForm');
      if (form?.parentElement?.classList.contains('ov-todo')) {   // back to its place above "now"
        $('#ovNow')?.before(form);
        if (ov.todoOpen) { setTodoOpen(true, true); return; }
      }
      setTodoOpen(!ov.todoOpen, true);
    });
    $('#ovTodoAddBtn')?.addEventListener('click', () => {
      // opened from the Todo heading: the form shows right under it, parked in Todo
      const form = $('#ovTodoForm');
      const here = form?.parentElement?.classList.contains('ov-todo');
      if (ov.todoOpen && here) { setTodoOpen(false); return; }
      $('.ov-todo-head')?.after(form);
      ov.todoAgent = 'todo';
      const aSel = $('#ovTodoAgent');
      if (aSel) aSel.value = 'todo';
      setTodoOpen(true, true);
    });
    $('#ovTodoInput')?.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') setTodoOpen(false);
    });
    $('#ovTodoForm').addEventListener('submit', (e) => { e.preventDefault(); addTodo(); });
    $('#ovTodoProject').addEventListener('change', (e) => { ov.todoProject = e.target.value; });
    $('#ovTodoAgent').addEventListener('change', (e) => { ov.todoAgent = e.target.value; });
    $('#ovTodoList').addEventListener('click', async (e) => {
      if (e.target.closest('[data-more]')) { ov.todoAll = !ov.todoAll; renderTodo(ov.data); return; }
      const row = e.target.closest('.ov-todo-row');
      if (!row) return;
      const pid = +row.dataset.pid, tid = +row.dataset.tid;
      const btn = e.target.closest('[data-assign], [data-finish]');
      if (btn) {
        btn.disabled = true;
        if (btn.dataset.assign) {
          await api(`/projects/${pid}/tasks/${tid}/assign`, { method: 'POST', body: { agent: btn.dataset.assign } });
        } else {
          await api(`/projects/${pid}/tasks/${tid}/complete`, { method: 'POST', body: {} });
        }
        ov.sig = '';
        refreshOverview();
        return;
      }
      if (window.openSessionPanel) {
        window.openSessionPanel(`task:${pid}:${tid}`, row.dataset.agent || 'claude', { projectId: pid, taskId: tid });
      } else {
        enterProject(pid);
        await refreshTasks();
        openTaskModal(tid);
      }
    });
    $('#pvStrip').addEventListener('click', (e) => {
      const c = e.target.closest('.pv-chip');
      if (c) openSession(c);
    });
    $('.ov-now-head').addEventListener('click', (e) => {
      const b = e.target.closest('[data-now-mode]');
      if (b) setMode(b.dataset.nowMode);
      if (e.target.closest('[data-clear-filter]') && ov.filterAgent) setAgentFilter(ov.filterAgent);
    });
    $('#ovAllProject').addEventListener('change', (e) => { ov.allProject = e.target.value; ov.all.limit = ALL_PAGE_SIZE; fetchAll(); });
    $('#ovNow').addEventListener('click', (e) => {
      const arch = e.target.closest('[data-arch]');
      if (arch) {
        e.stopPropagation();
        openArchive(arch.dataset.arch === '1');
        return;
      }
      if (e.target.closest('[data-more-all]')) { e.stopPropagation(); fetchAll(true); }
      if (e.target.closest('[data-less-all]')) {
        e.stopPropagation();
        ov.all.limit = ALL_PAGE_SIZE;
        fetchAll();
      }
    });
    $('#ovStats').addEventListener('click', (e) => {
      const b = e.target.closest('[data-filter-agent]');
      if (b) setAgentFilter(b.dataset.filterAgent);
    });
    $('#ovDailySeg')?.addEventListener('click', (e) => {
      const b = e.target.closest('[data-daily-mode]');
      if (!b || b.dataset.dailyMode === ov.dailyMode) return;
      ov.dailyMode = b.dataset.dailyMode;
      ov.dailyFilter = '';
      try { localStorage.setItem('ov.dailyMode', ov.dailyMode); } catch { /* private mode */ }
      if (ov.data) renderDaily(ov.data);
    });
    const dailyEl = $('#ovDaily');
    dailyEl?.addEventListener('click', (e) => {
      const f = e.target.closest('[data-daily-filter]');
      if (f) {
        const k = f.dataset.dailyFilter;
        ov.dailyFilter = ov.dailyFilter === k ? '' : k;
        if (ov.data) renderDaily(ov.data);
        return;
      }
      const bar = e.target.closest('[data-day-idx]');
      if (bar) selectDailyDay(Number(bar.dataset.dayIdx));
    });
    dailyEl?.addEventListener('mouseover', (e) => {
      if (e.sourceCapabilities?.firesTouchEvents) return;
      const bar = e.target.closest('[data-day-idx]');
      if (bar) selectDailyDay(Number(bar.dataset.dayIdx));
    });
    // States age with the clock (running → quiet → idle) even when no log changes.
    setInterval(() => { if (!state.activeProjectId) refreshOverview(); }, 30000);
  });

  window.refreshProjectLive = refreshProjectLive;
  window.projectBoard = () => (pv.data && pv.pid === state.activeProjectId ? pv.data.board : null);
  window.taskLiveHtml = taskLiveHtml;
  window.pillHtml = pillHtml;
  window.openArchive = openArchive;
  window.ctxChipHtml = ctxChipHtml;
  window.taskChecklistHtml = taskChecklistHtml;
  window.refreshOverview = refreshOverview;
  window.renderOverview = renderOverview;
})();
