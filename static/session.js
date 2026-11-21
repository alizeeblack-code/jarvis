/**
 * Session panel: read any local session as a conversation and send it a new
 * instruction. Data: GET /api/sessions/{sid}, POST /api/sessions/{sid}/send.
 * Depends on app.js globals: state, $, api, escapeHtml, renderMarkdown,
 * agentName, enterProject, refreshTasks, openTaskModal, closeModal.
 */
(function () {
  const sp = {
    sid: '', agent: '', taskId: null, data: null, timer: 0,
    loading: false, reloadPending: false, note: '', sendErr: '', taskSig: '',
    optimistic: [], merging: false, headSig: '',
    images: [], newImages: [], uploading: 0, newUploading: 0,
  };
  const MAX_SESSION_IMAGES = 5;
  let optSeq = 0;
  const restoredRuns = new Set();     // failed runs whose text went back into the box
  const esc = (s) => escapeHtml(String(s ?? ''));
  const zh = () => state.lang !== 'en';
  const T = {
    run: ['在跑', 'Running'], wait: ['等你', 'Your turn'], done: ['完成', 'Done'], quiet: ['安静', 'Quiet'], error: ['出错', 'Error'],
    limit: ['额度用完', 'Limit hit'], idle: ['空闲', 'Idle'], archived: ['已归档', 'Archived'],
    older: [(n) => `更早的 ${n} 轮`, (n) => `${n} earlier rounds`],
    steps: [(n, e) => `${n} 个步骤${e ? ` · ${e} 处出错` : ''}`, (n, e) => `${n} steps${e ? ` · ${e} failed` : ''}`],
    interrupted: ['已中断', 'Interrupted'], working: ['进行中…', 'Working…'],
    images: [(n) => `${n} 张图片`, (n) => `${n} image(s)`],
    imgs: [(n) => `${n} 张图`, (n) => `${n} image(s)`],
    cat_cmd: [(n) => `运行了 ${n} 条命令`, (n) => `ran ${n} command${n > 1 ? 's' : ''}`],
    cat_edit: [(n) => `改了 ${n} 个文件`, (n) => `edited ${n} file${n > 1 ? 's' : ''}`],
    cat_read: [(n) => `读了 ${n} 个文件`, (n) => `read ${n} file${n > 1 ? 's' : ''}`],
    cat_search: [(n) => `搜索 ${n} 次`, (n) => `searched ${n} time${n > 1 ? 's' : ''}`],
    cat_web: [(n) => `访问网页 ${n} 次`, (n) => `${n} web step${n > 1 ? 's' : ''}`],
    cat_agent: [(n) => `派了 ${n} 个子代理`, (n) => `${n} subagent${n > 1 ? 's' : ''}`],
    cat_other: [(n) => `其它工具 ${n} 次`, (n) => `${n} other tool call${n > 1 ? 's' : ''}`],
    errs: [(n) => `${n} 处出错`, (n) => `${n} failed`],
    now: [(t) => `正在：${t}`, (t) => `Now: ${t}`],
    progress: [(n, m) => `第 ${n} 步 · 已 ${m} 分钟`, (n, m) => `step ${n} · ${m} min`],
    empty: ['这个会话还没有可显示的对话', 'Nothing to show yet'],
    noLog: ['会话日志已不在本机（Claude Code 默认保留 30 天）', 'The session log is no longer on this machine'],
    phTask: ['继续这个任务', 'Continue this task'], phDirect: ['给这个会话发指令', 'Send an instruction'],
    phBusy: ['正在运行，停下后才能发', 'Running — wait until it stops'],
    phQueue: ['输入下一条指令，当前完成后自动接着执行', 'Type next instruction — runs automatically after this turn'],
    phSteer: ['输入新指令，发送会打断当前这一轮', 'New instruction — sending interrupts this turn'],
    queueBtn: ['排队', 'Queue'],
    archive: ['归档', 'Archive'], unarchive: ['取消归档', 'Unarchive'], archivedToast: ['已归档，在「全部 › 已归档」里', 'Archived — under All › Archived'],
    brAhead: [(n) => `领先 ${n} 个提交`, (n) => `${n} ahead`], brBehind: [(n) => `落后 ${n} 个提交`, (n) => `${n} behind`],
    brDirty: [(n) => `${n} 个文件未提交`, (n) => `${n} uncommitted`], brFiles: [(n) => `${n} 个文件`, (n) => `${n} files`],
    brBase: [(sha) => `分出自 ${sha}`, (sha) => `from ${sha}`],
    brMerged: [(m) => `已合并进 ${m}`, (m) => `Merged into ${m}`], brGone: ['工作区已清理', 'Worktree removed'],
    brMore: [(n) => `还有 ${n} 个文件`, (n) => `${n} more files`],
    phNone: ['这个会话不能从网页续写', 'Cannot continue this session here'],
    sent: [(id) => `已创建续接任务 #${id}，交给 worker 执行`, (id) => `Follow-up task #${id} started`],
    queuedTask: [(id) => `已加入队列（续接任务 #${id}），当前完成后自动执行`, (id) => `Queued as follow-up task #${id} — runs after current turn`],
    queuedNote: ['已加入队列，当前指令完成后自动执行', 'Queued — runs automatically after current turn'],
    queuedBadge: [(i, n) => `${n > 1 ? `排队中 #${i}` : '排队中'} · 当前完成后自动执行`, (i, n) => `${n > 1 ? `Queued #${i}` : 'Queued'} · runs after current turn`],
    queuedIdle: ['排队中 · 等待执行…', 'Queued · waiting to start…'],
    cancelQueue: ['撤回', 'Withdraw'],
    sending: ['执行中', 'Running'], stop: ['停止', 'Stop'], open: ['在桌面版打开', 'Open in desktop app'],
    openCodex: ['在 Codex 打开', 'Open in Codex'], more: ['展开', 'More'], conv: ['对话', 'Conversation'],
    collapseTitle: ['收起', 'Collapse'], expandTitle: ['展开全文', 'Expand'],
    continue: ['继续对话', 'Continue'], failed: ['发送失败', 'Send failed'],
    fork: ['复制成新会话继续', 'Continue in a copy'], planGo: ['按方案开工', 'Go with the plan'],
    planApproved: ['已按方案开工', 'Plan approved'],
    effortDefault: ['默认', 'Default'],
    effortAgy: ['Antigravity 换档等于换模型，下一轮会重读整个上下文', 'For Antigravity a new effort is a new model: the next turn re-reads the whole context'], planRejected: ['方案未通过', 'Plan declined'],
    sendPicks: ['发送所选', 'Send'], unsent: ['没发出去，已放回输入框', 'Not sent — back in the input box'], forking: ['正在复制会话…', 'Copying the session…'],
    desc: ['任务描述', 'Task'], summary: ['完成总结', 'Summary'], details: ['详情', 'Details'],
    st_todo: ['Todo', 'Todo'], st_pending: ['待开发', 'Queued'], st_in_progress: ['开发中', 'In progress'],
    st_review: ['待 Review', 'Review'], st_done: ['已完成', 'Done'], st_failed: ['失败', 'Failed'],
    st_cancelled: ['已取消', 'Cancelled'],
    plan: ['方案', 'Plan'], approve: ['确认并创建任务', 'Approve & create tasks'],
    answer: [(n) => `回答 ${n} 个问题后确认`, (n) => `Answer ${n} question(s) to approve`],
    phRevise: ['说说方案要怎么改', 'What should change in the plan'],
    revised: ['已提交修改意见，方案重写中', 'Feedback sent — revising the plan'],
    hide: ['收起', 'Put away'], unhide: ['放回看板', 'Back on the board'],
    newSession: ['新会话', 'New session'], newPh: ['在这个项目里开一个新会话做…', 'Start a new session in this project to…'],
    start: ['开始', 'Start'], starting: ['新会话启动中…', 'Starting the new session…'],
    queued: ['已排队，并发槽位已满或同会话有任务在跑，轮到时会自动开始', 'Queued — starts when a worker slot and this session are free'],
    merge: [(n) => `合并到 main · ${n} 次提交`, (n) => `Merge to main · ${n} commit(s)`],
    mergeAsk: [(b, n, f, t) => `把 ${b} 的 ${n} 次提交（${f} 个文件）合并到 main？${t ? '\n会先在它的工作区里跑测试。' : ''}`,
               (b, n, f, t) => `Merge ${n} commit(s) (${f} files) on ${b} into main?${t ? '\nTests run first.' : ''}`],
    merging: ['合并中…', 'Merging…'], merged: ['已合并到 main', 'Merged into main'],
    finish: ['完成', 'Done'],
    retryTask: ['↻ 重试', '↻ Retry'],
    cancelTask: ['取消任务', 'Cancel task'],
    deleteTask: ['删除', 'Delete'],
    deleteAsk: [(id) => `确定删除任务 #${id}？`, (id) => `Delete task #${id}?`],
    waitingStart: ['排队等待 worker 执行…', 'Waiting for worker to start…'],
    attachImg: ['添加图片', 'Attach image'],
    uploadFail: ['图片上传失败', 'Image upload failed'],
    uploadingImg: ['图片上传中…', 'Uploading image…'],
  };
  const tr = (k, ...a) => {
    if (!T[k]) return '';
    const v = T[k][zh() ? 0 : 1];
    return typeof v === 'function' ? v(...a) : v;
  };

  function resumeUrl(agent, sid) {
    if (agent === 'codex') return `codex://threads/${encodeURIComponent(sid)}`;
    if (agent === 'claude') return `claude://resume?session=${encodeURIComponent(sid)}`;
    return '';
  }
  const hm = (ms) => {
    if (!ms) return '';
    const d = new Date(ms), now = new Date();
    const t = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    return d.toDateString() === now.toDateString() ? t : `${d.getMonth() + 1}/${d.getDate()} ${t}`;
  };

  // claude-opus-5-5 → Opus 5.5, claude-3-5-haiku-… → Haiku 3.5; others as they are
  function modelName(id) {
    const g = /^gemini-(?:[\d.]+-)?([a-z]+)-(low|medium|high|xhigh)$/.exec(id || '');   // agy: gemini-4-hydrogen-medium
    if (g) return `${g[1][0].toUpperCase()}${g[1].slice(1)} · ${g[2]}`;
    const m = /^claude-(opus|sonnet|haiku|fable)-(\d+)(?:-(\d{1,2}))?(?:-|$)/.exec(id || '')
      || /^claude-(\d+)-(\d+)-(opus|sonnet|haiku)/.exec(id || '');
    if (!m) return id || '';
    const [name, major, minor] = /^\d/.test(m[1]) ? [m[3], m[1], m[2]] : [m[1], m[2], m[3]];
    return `${name[0].toUpperCase()}${name.slice(1)} ${major}${minor ? `.${minor}` : ''}`;
  }
  const modelLabel = (model, effort) => model ? `${modelName(model)}${effort ? ` · ${effort}` : ''}` : '';
  const modelChip = (model, effort) => model
    ? `<span class="sp-model" title="${esc(model)}${effort ? ` · effort ${esc(effort)}` : ''}">${esc(modelLabel(model, effort))}</span>` : '';

  // The effort the next turns run at: the model chip opens a picker (sessions you can reply to).
  const EFFORTS = { claude: ['low', 'medium', 'high', 'xhigh', 'max'], codex: ['low', 'medium', 'high', 'xhigh'],
                    antigravity: ['low', 'medium', 'high', 'xhigh'] };
  function effortLevels(s) {
    const lv = EFFORTS[s.agent || 'claude'] || [];
    return s.agent === 'antigravity' && !/hydrogen/.test(s.model || '') ? lv.filter((x) => x !== 'xhigh') : lv;
  }
  // The branch the session works on, against main: a small chip, details on tap
  // (after VS Code / GitHub Desktop: name, ↑ ahead ↓ behind, ● uncommitted).
  async function loadGit(sid) {
    sp.gitAt = Date.now();
    let g = null;
    try {
      g = await api(`/sessions/${encodeURIComponent(sid)}/git${sp.taskId ? `?task_id=${sp.taskId}` : ''}`);
    } catch { /* keep what we had */ return; }
    if (sid !== sp.sid) return;
    sp.git = g && (g.branch || g.gone) ? g : null;
    if (sp.data) renderHead(sp.data);
  }

  const ARCHIVE_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M3 4h18v4H3zM5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8M10 12h4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const ATTACH_ICON = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2" stroke="currentColor" stroke-width="2" fill="none"/><circle cx="8.5" cy="8.5" r="1.5" fill="currentColor"/><path d="M21 15l-5-5L5 21" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const BRANCH_ICON = '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M5 3.25a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0Zm0 2.122a2.25 2.25 0 1 0-1.5 0v5.256a2.251 2.251 0 1 0 1.5 0V9.5h4.25A2.25 2.25 0 0 0 11.5 7.25v-.878a2.25 2.25 0 1 0-1.5 0v.878a.75.75 0 0 1-.75.75H5V5.372ZM4.25 12a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm6.5-8.75a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Z"/></svg>';

  function branchChipHtml(g, d) {
    if (!g) return d.merge ? `<button type="button" class="sp-branch ahead" data-branch>${BRANCH_ICON}<span>${esc(d.merge.branch || '')}</span><b>↑${d.merge.commits}</b></button>` : '';
    if (g.gone) return `<button type="button" class="sp-branch" data-branch>${BRANCH_ICON}<span>${esc(g.branch)}</span>${g.merged ? '<b>✓</b>' : ''}</button>`;
    const bits = [g.ahead ? `<b>↑${g.ahead}</b>` : '', g.behind ? `<i>↓${g.behind}</i>` : '', g.dirty ? '<em>●</em>' : ''].join('');
    return `<button type="button" class="sp-branch${g.ahead ? ' ahead' : ''}${sp.branchOpen ? ' open' : ''}" data-branch aria-expanded="${sp.branchOpen}">${BRANCH_ICON}<span>${esc(g.branch)}</span>${bits}</button>`;
  }

  function branchPopHtml(g, d, mergeBtn) {
    if (!g) return mergeBtn ? `<div class="sp-branch-pop">${mergeBtn}</div>` : '';
    if (g.gone) {
      return `<div class="sp-branch-pop"><div class="bp-line">${esc(g.merged ? tr('brMerged', g.main) : tr('brGone'))}</div></div>`;
    }
    const n = (k, v) => tr(k, v);
    const lines = [];
    if (g.branch !== g.main) {
      lines.push(`<div class="bp-head">${BRANCH_ICON}<b>${esc(g.branch)}</b> ← ${esc(g.main)}${g.base ? ` <span class="bp-dim">${esc(n('brBase', g.base.sha))} · ${esc(hm(g.base.at))}</span>` : ''}</div>`);
    } else {
      lines.push(`<div class="bp-head">${BRANCH_ICON}<b>${esc(g.branch)}</b></div>`);
    }
    const counts = [g.branch !== g.main ? esc(n('brAhead', g.ahead)) : '', g.branch !== g.main ? esc(n('brBehind', g.behind)) : '',
                    g.dirty ? `${esc(n('brDirty', g.dirty))}${g.dirty_add || g.dirty_del ? ` <span class="bp-add">+${g.dirty_add}</span><span class="bp-del">−${g.dirty_del}</span>` : ''}` : '']
      .filter(Boolean);
    if (counts.length) lines.push(`<div class="bp-line">${counts.join(' · ')}</div>`);
    if (g.upstream) {
      lines.push(`<div class="bp-line bp-dim">${esc(g.upstream.name)}　↑${g.upstream.ahead} ↓${g.upstream.behind}</div>`);
    }
    if (g.commits && g.commits.length) {
      lines.push(`<div class="bp-commits">${g.commits.map((c) => {
        const open = sp.openCommits.has(c.sha);
        const f = sp.commitFiles[c.sha];
        const files = !open ? '' : !f ? `<div class="bp-cfiles bp-dim">…</div>`
          : `<div class="bp-cfiles">${f.files.map((x) => `<div><span class="bp-file">${esc(x.file)}</span><span class="bp-add">+${x.add}</span><span class="bp-del">−${x.del}</span></div>`).join('')}${f.more ? `<div class="bp-dim">${esc(n('brMore', f.more))}</div>` : ''}</div>`;
        return `<button type="button" class="bp-commit${open ? ' open' : ''}" data-commit="${esc(c.sha)}"><code>${esc(c.sha)}</code><span>${esc(c.title)}</span><span class="bp-dim">${esc(hm(c.at))}</span></button>${files}`;
      }).join('')}</div>`);
    }
    if (mergeBtn) lines.push(`<div class="bp-actions">${mergeBtn}</div>`);
    return `<div class="sp-branch-pop">${lines.join('')}</div>`;
  }

  function effortChipHtml(d) {
    const s = d.session;
    if (!d.send?.mode || !effortLevels(s).length || !s.model) return modelChip(s.model, s.effort);
    const agyEffort = s.agent === 'antigravity' ? (/-(low|medium|high|xhigh)$/.exec(s.model) || [])[1] : '';
    const cur = s.effort_pick || s.effort || agyEffort;
    const name = modelName(s.model).replace(/ · (low|medium|high|xhigh)$/, '');
    return `<button type="button" class="sp-model pick${s.effort_pick ? ' set' : ''}" data-effort-menu title="${esc(s.model)}">${esc(name)}${cur ? ` · ${esc(cur)}` : ''} ▾</button>`;
  }
  function effortMenuHtml(d) {
    const s = d.session, pick = s.effort_pick || '';
    const opt = (v, label) => `<button type="button" class="cv-opt${pick === v ? ' chosen' : ''}" data-effort="${v}">${esc(label)}</button>`;
    return `<div class="sp-effort">${opt('', tr('effortDefault'))}${effortLevels(s).map((v) => opt(v, v)).join('')}
      ${s.agent === 'antigravity' ? `<div class="sp-effort-note">${esc(tr('effortAgy'))}</div>` : ''}</div>`;
  }

  // ── conversation rendering (shared with the task modal) ──
  // prev: {model, effort} of the turn before — a round that switched gets marked
  // Images live in the session log; the page asks for each one by its position.
  function imgsHtml(sid, refs) {
    if (!refs || !refs.length) return '';
    return `<div class="cv-imgs">${refs.map((x) => {
      const q = `o=${x.o}${x.i != null ? `&i=${x.i}` : ''}${x.j != null ? `&j=${x.j}` : ''}`;
      return `<img class="cv-img" loading="lazy" alt="" src="/api/sessions/${encodeURIComponent(sid)}/image?${q}">`;
    }).join('')}</div>`;
  }

  function urlImgsHtml(urls) {
    if (!urls || !urls.length) return '';
    return `<div class="cv-imgs">${urls.map((u) => `<img class="cv-img" loading="lazy" alt="" src="${esc(u)}">`).join('')}</div>`;
  }

  function roundImgsHtml(sid, r) {
    if (r.imgs && r.imgs.length) return imgsHtml(sid, r.imgs);
    return urlImgsHtml(r.image_urls);
  }

  // A round reads like a conversation: what the agent tells you is shown as it
  // comes; the tool calls between two of its messages fold into one summary line
  // ("ran 3 commands · edited 2 files"), opened only when you want the detail.
  const CAT_ORDER = ['cmd', 'edit', 'read', 'search', 'web', 'agent', 'other'];
  function toolCat(i) {
    if (i.c) return i.c;
    const t = i.t || '';                       // older cached rounds: guess from the text
    if (t.startsWith('$ ')) return 'cmd';
    if (t.startsWith('改')) return 'edit';
    if (t.startsWith('读网页')) return 'web';
    if (t.startsWith('读')) return 'read';
    if (t.startsWith('搜')) return 'search';
    if (t === '整理待办') return 'internal';
    return 'other';
  }

  function toolGroupHtml(tools, sid, key, current, prog) {
    const counts = {};
    tools.forEach((i) => { const c = toolCat(i); counts[c] = (counts[c] || 0) + 1; });
    const errs = tools.filter((i) => i.err).length;
    const shots = tools.reduce((n, i) => n + (i.imgs ? i.imgs.length : 0), 0);
    const parts = CAT_ORDER.filter((c) => counts[c]).map((c) => tr(`cat_${c}`, counts[c]));
    const head = current ? tr('now', tools[tools.length - 1].t) : parts.join(' · ');
    // a running round that has said nothing for a while still shows it is moving
    const moving = current && prog ? `<span class="cv-tools-prog">${esc(tr('progress', prog.steps, prog.min))}</span>` : '';
    return `<details class="cv-tools ${current ? 'live' : ''}" data-k="${key}">
        <summary><span class="cv-tools-sum">${esc(head)}</span>${moving}${errs ? ` <span class="cv-tools-err">· ${tr('errs', errs)}</span>` : ''}${shots && !current ? ` · ${tr('imgs', shots)}` : ''}</summary>
        <div class="cv-step-list">${tools.map((i) => `<div class="cv-step ${i.err ? 'err' : ''}">${esc(stepText(i.t))}</div>${imgsHtml(sid, i.imgs)}`).join('')}</div>
      </details>`;
  }

  function roundHtml(r, live, prev, sid, idx, askable = false) {
    const items = (r.items || []).filter((i) => i.k !== 'tool' || toolCat(i) !== 'internal');
    const last = items[items.length - 1];
    const reply = last && last.k === 'text' ? last : null;
    const process = reply ? items.slice(0, -1) : items;
    const switched = r.model && prev && prev.model && (r.model !== prev.model || (r.effort || '') !== (prev.effort || ''));
    const ask = r.ask || (r.imgs && r.imgs.length) || (r.image_urls && r.image_urls.length)
      ? `<div class="cv-ask">${r.ask ? `<div class="cv-ask-text md-content">${renderMarkdown(r.ask)}</div>` : ''}${roundImgsHtml(sid, r)}
           <div class="cv-meta">${hm(r.at)}${switched ? ` · <span class="cv-switch" title="${esc(r.model)}">→ ${esc(modelLabel(r.model, r.effort))}</span>` : ''}</div></div>`
      : '';
    // what the agent said along the way stays visible; tool runs between them fold
    let body = '', group = [], g = 0;
    const flush = (isLast) => {
      if (!group.length) return;
      const prog = { steps: process.filter((i) => i.k === 'tool').length,
                     min: Math.max(0, Math.round((Date.now() - (r.at || Date.now())) / 60000)) };
      body += toolGroupHtml(group, sid, `${idx}-${g++}`, isLast && live && !reply, prog);
      group = [];
    };
    process.forEach((i) => {
      if (i.k === 'tool') { group.push(i); return; }
      flush(false);
      body += `<div class="cv-note md-content">${renderMarkdown(i.t)}</div>`;
    });
    flush(true);
    const tail = reply
      ? `<div class="cv-reply md-content">${renderMarkdown(reply.t)}</div>`
      : (live && !process.length ? `<div class="cv-working">${tr('working')}</div>` : '');
    return `<div class="cv-round${r.failed ? ' cv-unsent' : ''}">${ask}${body}${tail}${promptHtml(r.prompt, askable)}
      ${r.failed ? `<div class="cv-flag err">${tr('unsent')}</div>` : ''}
      ${r.interrupted ? `<div class="cv-flag">${tr('interrupted')}</div>` : ''}</div>`;
  }

  // A question the turn left for you: the question, and (on the latest, unanswered
  // turn of a session you can reply to) buttons that send the answer.
  function promptHtml(p, active) {
    if (!p) return '';
    const live = active && !p.answered;
    const btn = (text, label = text, title = '') =>
      `<button type="button" class="cv-opt" data-reply="${esc(text)}" ${title ? `title="${esc(title)}"` : ''}>${esc(label)}</button>`;
    // answered (here or elsewhere): the options stay as a record, the chosen one marked
    const done = (label, chosen, title = '') =>
      `<span class="cv-opt off${chosen ? ' chosen' : ''}" ${title ? `title="${esc(title)}"` : ''}>${chosen ? '✓ ' : ''}${esc(label)}</span>`;
    const picked = (answer, label) => Boolean(answer) && String(answer).split(/[,，、]\s*/).some((x) => x.trim() === label);
    let html = '';
    if (p.kind === 'wrap') {
      const opts = p.options.length
        ? `<div class="cv-opts">${p.options.map((o) => live ? btn(o) : done(o, (p.answer || '').trim() === o)).join('')}</div>` : '';
      if (!p.ask && !opts) return '';
      html = `${p.ask ? `<div class="cv-prompt-q">${esc(p.ask)}</div>` : ''}${opts}`;
    } else if (p.kind === 'choice') {
      html = p.questions.map((q, qi) => {
        const ans = p.answer && typeof p.answer === 'object' ? p.answer[q.q] : '';
        return `<div class="cv-prompt-q">${esc(q.q)}</div>
        <div class="cv-opts" data-q="${qi}">${q.options.map((o) => live
          ? (q.multi
            ? `<button type="button" class="cv-opt" data-pick="${esc(o.label)}" title="${esc(o.desc)}">${esc(o.label)}</button>`
            : btn(p.questions.length > 1 ? `${q.q}：${o.label}` : o.label, o.label, o.desc))
          : done(o.label, picked(ans, o.label), o.desc)).join('')}
          ${live && q.multi ? `<button type="button" class="cv-opt send" data-pick-send="${esc(q.q)}">${esc(tr('sendPicks'))}</button>` : ''}
          ${!live && ans && !q.options.some((o) => picked(ans, o.label)) ? `<span class="cv-opt off chosen">✓ ${esc(ans)}</span>` : ''}</div>`;
      }).join('');
    } else if (p.kind === 'plan') {
      const verdict = p.answer === 'approved' ? tr('planApproved') : p.answer === 'rejected' ? tr('planRejected') : '';
      html = `<div class="cv-plan md-content">${renderMarkdown(p.plan)}</div>
        ${live ? `<div class="cv-opts">${btn(tr('planGo'))}</div>`
          : verdict ? `<div class="cv-opts"><span class="cv-opt off chosen">${p.answer === 'approved' ? '✓ ' : ''}${esc(verdict)}</span></div>` : ''}`;
    } else {
      return '';
    }
    return `<div class="cv-prompt${live ? '' : ' answered'}${p.blocked && live ? ' blocked' : ''}">${html}</div>`;
  }

  function convHtml(conv, live, sid, answerable = false) {
    const rounds = conv.rounds || [];
    if (!rounds.length) return `<div class="cv-empty">${tr('empty')}</div>`;
    const older = conv.start > 0
      ? `<button class="cv-older" data-older="${conv.start}">${tr('older', conv.start)}</button>` : '';
    let prev = conv.prev_model ? { model: conv.prev_model, effort: conv.prev_effort } : null;
    return older + rounds.map((r, i) => {
      const last = i === rounds.length - 1;
      const html = roundHtml(r, live && last, prev, sid, (conv.start || 0) + i, answerable && last && !live);
      if (r.model) prev = r;
      return html;
    }).join('');
  }

  function queueHtml(serverQueue, optQueue, live) {
    const sq = serverQueue || [];
    const oq = (optQueue || []).filter((o) => !o.synced || !sq.some((q) => q.id === o.serverId || (q.text === o.text && (q.images?.length || 0) === (o.images?.length || 0))));
    const items = [
      ...sq.map((q) => ({ id: q.id, text: q.text, images: q.images || [], at: q.at || q.queued_at || Date.now(), canCancel: true })),
      ...oq.map((o) => ({ id: o.serverId || '', text: o.text, images: o.images || [], at: o.at || Date.now(), canCancel: Boolean(o.serverId) })),
    ];
    if (!items.length) return '';
    return items.map((q, i) => `
      <div class="cv-round cv-queued" data-queue-id="${esc(q.id)}">
        <div class="cv-ask">
          ${q.text ? `<div class="cv-ask-text md-content">${renderMarkdown(q.text)}</div>` : ''}${urlImgsHtml(q.images)}
          <div class="cv-meta">${hm(q.at)}</div>
        </div>
        <div class="cv-queued-bar">
          <span class="cv-queued-status">${esc(live ? tr('queuedBadge', i + 1, items.length) : tr('queuedIdle'))}</span>
          ${q.canCancel ? `<button type="button" class="cv-unqueue" data-unqueue="${esc(q.id)}">${esc(tr('cancelQueue'))}</button>` : ''}
        </div>
      </div>`).join('');
  }

  // ── image upload (reply + new session) ──
  function composerCfg(key) {
    if (key === 'new') {
      return {
        listKey: 'newImages', countKey: 'newUploading',
        preview: '#spNewImgs', fileInput: '#spNewFileInput',
        submit: '#spNewSubmit', attach: '#spNewAttach',
      };
    }
    return {
      listKey: 'images', countKey: 'uploading',
      preview: '#spImgs', fileInput: '#spFileInput',
      submit: '#spSubmit', attach: '#spAttach',
    };
  }

  function renderComposerImgs(key) {
    const cfg = composerCfg(key);
    const bar = $(cfg.preview);
    if (!bar) return;
    const imgs = sp[cfg.listKey] || [];
    const hidden = imgs.length === 0 || (key === 'send' && !sp.data?.send?.mode && sp.data?.task?.status !== 'review');
    bar.classList.toggle('hidden', hidden);
    bar.innerHTML = imgs.map((img, i) => `
      <div class="sp-img-thumb">
        <img class="cv-img" src="${esc(img.url)}" alt="upload">
        <button type="button" class="sp-img-rm" data-rm-img="${i}" aria-label="remove">&times;</button>
      </div>`).join('');
  }

  function syncComposerUpload(key) {
    const cfg = composerCfg(key);
    const busy = sp[cfg.countKey] > 0;
    const btn = $(cfg.attach);
    if (btn) btn.classList.toggle('uploading', busy);
    if (key === 'new') {
      const sub = $(cfg.submit);
      if (sub) sub.disabled = busy;
      const note = $('#spNewNote');
      if (note && busy) { note.classList.remove('err'); note.textContent = tr('uploadingImg'); }
      else if (note && !busy && note.textContent === tr('uploadingImg')) note.textContent = '';
    } else if (sp.data) {
      renderSend(sp.data);
    }
  }

  async function addComposerImages(key, files) {
    const cfg = composerCfg(key);
    const imgs = sp[cfg.listKey];
    const picked = Array.from(files || []).filter((f) => !f.type || f.type.startsWith('image/'));
    if (!picked.length) return;
    const pid = sp.data?.project?.id || state.activeProjectId || null;
    const endpoint = pid ? `/api/projects/${pid}/uploads` : '/api/uploads';
    sp[cfg.countKey]++;
    syncComposerUpload(key);
    try {
      for (const file of picked) {
        if (imgs.length >= MAX_SESSION_IMAGES) break;
        const fd = new FormData();
        fd.append('file', file);
        const res = await fetch(endpoint, { method: 'POST', body: fd });
        const data = await res.json();
        if (!res.ok || data.error || !data.url) {
          throw new Error(data?.error || tr('uploadFail'));
        }
        imgs.push({ url: data.url, path: data.path });
        renderComposerImgs(key);
      }
    } catch (err) {
      const msg = err?.message || tr('uploadFail');
      if (key === 'new') {
        const note = $('#spNewNote');
        if (note) { note.textContent = msg; note.classList.add('err'); }
      } else {
        sp.sendErr = msg;
        if (sp.data) renderSend(sp.data);
      }
    } finally {
      sp[cfg.countKey] = Math.max(0, sp[cfg.countKey] - 1);
      syncComposerUpload(key);
    }
  }

  function bindImageDrop(boxEl, inputEl, key) {
    if (!boxEl || !inputEl) return;
    const row = boxEl.querySelector('.sp-send-row');
    inputEl.addEventListener('paste', (e) => {
      if (inputEl.disabled) return;
      const items = e.clipboardData?.items;
      if (!items) return;
      const files = [];
      for (const it of items) {
        if (it.type && it.type.startsWith('image/')) {
          const f = it.getAsFile();
          if (f) files.push(f);
        }
      }
      if (files.length) {
        e.preventDefault();
        addComposerImages(key, files);
      }
    });
    boxEl.addEventListener('dragover', (e) => {
      if (inputEl.disabled) return;
      e.preventDefault();
      row?.classList.add('drag-over');
    });
    boxEl.addEventListener('dragleave', (e) => {
      if (!boxEl.contains(e.relatedTarget)) row?.classList.remove('drag-over');
    });
    boxEl.addEventListener('drop', (e) => {
      row?.classList.remove('drag-over');
      if (inputEl.disabled) return;
      const files = Array.from(e.dataTransfer?.files || []).filter((f) => f.type.startsWith('image/'));
      if (files.length) {
        e.preventDefault();
        addComposerImages(key, files);
      }
    });
  }

  // ── panel ──
  function ensurePanel() {
    if ($('#sessionPanel')) return;
    document.body.insertAdjacentHTML('beforeend', `
      <div class="sp-overlay" id="spOverlay"></div>
      <aside class="sp" id="sessionPanel" role="dialog" aria-modal="true">
        <header class="sp-head" id="spHead"></header>
        <form class="sp-new hidden" id="spNew" autocomplete="off">
          <div class="sp-new-agents" role="radiogroup">
            <button type="button" class="on" data-new-agent="claude">Claude</button>
            <button type="button" data-new-agent="codex">Codex</button>
            <button type="button" data-new-agent="antigravity">Antigravity</button>
          </div>
          <div class="sp-imgs hidden" id="spNewImgs"></div>
          <div class="sp-send-row">
            <button type="button" class="sp-attach" id="spNewAttach" title="${esc(tr('attachImg'))}" aria-label="${esc(tr('attachImg'))}">${ATTACH_ICON}</button>
            <textarea id="spNewInput" rows="2"></textarea>
            <button type="submit" class="sp-submit" id="spNewSubmit" aria-label="start">
              <svg viewBox="0 0 24 24" width="18" height="18"><path d="M4 12l16-8-6 16-2-7-8-1z" fill="currentColor"/></svg>
            </button>
          </div>
          <input type="file" id="spNewFileInput" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden>
          <div class="sp-note" id="spNewNote"></div>
        </form>
        <div class="sp-body" id="spBody"></div>
        <button type="button" class="sp-to-bottom hidden" id="spToBottom" aria-label="scroll to bottom">↓</button>
        <form class="sp-send" id="spSend" autocomplete="off">
          <div class="sp-note-row"><div class="sp-note" id="spNote"></div><button type="button" class="sp-note-x hidden" id="spNoteX" aria-label="dismiss">✕</button></div>
          <button type="button" class="sp-fork hidden" id="spFork"></button>
          <div class="sp-imgs hidden" id="spImgs"></div>
          <div class="sp-send-row">
            <button type="button" class="sp-attach" id="spAttach" title="${esc(tr('attachImg'))}" aria-label="${esc(tr('attachImg'))}">${ATTACH_ICON}</button>
            <textarea id="spInput" rows="1"></textarea>
            <button type="button" class="sp-stop hidden" id="spStop"></button>
            <button type="button" class="sp-queue hidden" id="spQueue"></button>
            <button type="submit" class="sp-submit" id="spSubmit" aria-label="send">
              <svg viewBox="0 0 24 24" width="18" height="18"><path d="M4 12l16-8-6 16-2-7-8-1z" fill="currentColor"/></svg>
            </button>
          </div>
          <input type="file" id="spFileInput" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden>
        </form>
      </aside>`);
    $('#spOverlay').addEventListener('click', closePanel);
    $('#spNew').addEventListener('click', (e) => {
      const rm = e.target.closest('[data-rm-img]');
      if (rm) {
        sp.newImages.splice(+rm.dataset.rmImg, 1);
        renderComposerImgs('new');
        return;
      }
      const b = e.target.closest('[data-new-agent]');
      if (!b) return;
      $('#spNew').querySelectorAll('[data-new-agent]').forEach((x) => x.classList.toggle('on', x === b));
    });
    $('#spNewAttach').addEventListener('click', () => {
      if (sp.newImages.length < MAX_SESSION_IMAGES) $('#spNewFileInput').click();
    });
    $('#spNewFileInput').addEventListener('change', (e) => {
      if (e.target.files?.length) {
        addComposerImages('new', e.target.files);
        e.target.value = '';
      }
    });
    bindImageDrop($('#spNew'), $('#spNewInput'), 'new');
    $('#spNew').addEventListener('submit', (e) => { e.preventDefault(); startNewSession(); });
    $('#spNewInput').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); startNewSession(); }
    });
    $('#spAttach').addEventListener('click', () => {
      if (!$('#spAttach').disabled && sp.images.length < MAX_SESSION_IMAGES) $('#spFileInput').click();
    });
    $('#spFileInput').addEventListener('change', (e) => {
      if (e.target.files?.length) {
        addComposerImages('send', e.target.files);
        e.target.value = '';
      }
    });
    $('#spImgs').addEventListener('click', (e) => {
      const rm = e.target.closest('[data-rm-img]');
      if (!rm) return;
      e.stopPropagation();
      sp.images.splice(+rm.dataset.rmImg, 1);
      renderComposerImgs('send');
    });
    bindImageDrop($('#spSend'), $('#spInput'), 'send');
    $('#spSend').addEventListener('submit', (e) => { e.preventDefault(); send(); });
    $('#spFork').addEventListener('click', forkSession);
    $('#spNoteX').addEventListener('click', async () => {
      sp.sendErr = '';
      if (sp.data?.send?.run?.state === 'error') {
        await api(`/sessions/${encodeURIComponent(sp.sid)}/dismiss`, { method: 'POST' });
        load();
      } else if (sp.data) {
        renderSend(sp.data);
      }
    });
    $('#spStop').addEventListener('click', async () => {
      $('#spStop').disabled = true;
      try {
        await api(`/sessions/${encodeURIComponent(sp.sid)}/cancel`, { method: 'POST' });
      } finally {
        $('#spStop').disabled = false;
      }
      load();
    });
    $('#spQueue').addEventListener('click', () => send({ queue: true }));
    const input = $('#spInput');
    input.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send(); }
    });
    $('#spHead').addEventListener('click', async (e) => {
      if (e.target.closest('[data-close]')) closePanel();
      if (e.target.closest('[data-title-fold]') || (sp.titleLong && sp.titleCollapsed && e.target.closest('.sp-title'))) {
        sp.titleCollapsed = !sp.titleCollapsed;
        if (sp.data) renderHead(sp.data);
        return;
      }
      const ar = e.target.closest('[data-archive]');
      if (ar && sp.data) {
        const on = ar.dataset.archive === '1';
        const sid = sp.sid;
        ar.disabled = true;
        await api(`/sessions/${encodeURIComponent(sid)}/archive`, { method: 'POST', body: { archived: on } });
        if (on) {
          // filed under 全部 › 已归档; the panel is done with it
          closePanel();
          if (!state.activeProjectId && window.refreshOverview) window.refreshOverview();
          if (state.activeProjectId) refreshTasks();
          if (typeof showToast === 'function') showToast(tr('archivedToast'));
        } else {
          load();
        }
        return;
      }
      if (e.target.closest('[data-new]')) {
        const box = $('#spNew');
        box.classList.toggle('hidden');
        $('#spNewInput').placeholder = tr('newPh');
        if (!box.classList.contains('hidden')) $('#spNewInput').focus();
        return;
      }
      const cm = e.target.closest('[data-commit]');
      if (cm && sp.data) {
        const sha = cm.dataset.commit;
        if (sp.openCommits.has(sha)) sp.openCommits.delete(sha); else sp.openCommits.add(sha);
        renderHead(sp.data);
        if (sp.openCommits.has(sha) && !sp.commitFiles[sha]) {
          const sid = sp.sid;
          let f = null;
          try {
            f = await api(`/sessions/${encodeURIComponent(sid)}/git/commit?sha=${encodeURIComponent(sha)}${sp.taskId ? `&task_id=${sp.taskId}` : ''}`);
          } catch { f = { files: [] }; }
          if (sid !== sp.sid) return;
          sp.commitFiles[sha] = f || { files: [] };
          if (sp.data) renderHead(sp.data);
        }
        return;
      }
      if (e.target.closest('[data-branch]') && sp.data) {
        sp.branchOpen = !sp.branchOpen;
        sp.effortOpen = false;
        renderHead(sp.data);
        if (sp.branchOpen) loadGit(sp.sid);
        return;
      }
      if (e.target.closest('[data-effort-menu]') && sp.data) {
        sp.effortOpen = !sp.effortOpen;
        renderHead(sp.data);
        return;
      }
      const ef = e.target.closest('[data-effort]');
      if (ef && sp.data) {
        const r = await api(`/sessions/${encodeURIComponent(sp.sid)}/effort`, {
          method: 'POST', body: { effort: ef.dataset.effort, agent: sp.data.session.agent } });
        if (r?.ok) {
          sp.data.session.effort_pick = r.effort;
          sp.effortOpen = false;
          renderHead(sp.data);
        }
        return;
      }
      if (e.target.closest('[data-ctx-new]') && sp.data) {
        openHandoff(sp.data);
        return;
      }
      const mg = e.target.closest('[data-merge]');
      if (mg && !sp.merging && sp.data?.merge && sp.data.task && sp.data.project) {
        const m = sp.data.merge;
        const pid = sp.data.project.id;
        const tid = sp.data.task.id;
        const ask = tr('mergeAsk', m.branch, m.commits, m.files, m.test);
        const ok = window.showConfirm
          ? await window.showConfirm(ask, { primary: true })
          : confirm(ask);
        if (!ok || !sp.sid) return;
        sp.merging = true;
        sp.sendErr = '';
        sp.note = '';
        if (sp.data) {
          renderHead(sp.data);
          renderSend(sp.data);
        }
        let r;
        try {
          r = await api(`/projects/${pid}/tasks/${tid}/merge`, { method: 'POST' });
        } catch (err) {
          r = { ok: false, error: err?.message || tr('failed') };
        } finally {
          sp.merging = false;
        }
        if (r?.ok) {
          sp.note = tr('merged');
          sp.sendErr = '';
        } else {
          if (r.images?.length) {
            sp.images = [...sp.images, ...r.images].slice(0, MAX_SESSION_IMAGES);
            renderComposerImgs('send');
          }
          sp.note = '';
          sp.sendErr = `${r?.error || tr('failed')}${r?.output ? `\n${r.output.slice(-400)}` : ''}`;
        }
        if (state.activeProjectId) refreshTasks();
        load();
        return;
      }
      const hd = e.target.closest('[data-hide]');
      if (hd) {
        const hide = hd.dataset.hide === '1';
        await api(`/sessions/${encodeURIComponent(sp.sid)}/hide`, { method: 'POST', body: { hidden: hide } });
        if (state.activeProjectId) refreshTasks();
        if (hide) closePanel(); else load();
        return;
      }
      const pj = e.target.closest('[data-project]');
      if (pj && !e.target.closest('[data-task]')) { closePanel(); enterProject(+pj.dataset.project); return; }
      const tk = e.target.closest('[data-task]');
      if (tk) {
        if (sp.data?.task?.status === 'review' && (sp.data.task.questions > 0 || sp.data.task.subtasks > 1)) {
          openTask(+tk.dataset.project, +tk.dataset.task);
        } else {
          const desc = $('#spBody .sp-task-desc');
          if (desc) {
            desc.open = !desc.open;
            desc.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
          }
        }
      }
    });
    $('#spBody').addEventListener('click', async (e) => {
      // answering a question the session left: the button's text is the reply
      const pick = e.target.closest('[data-pick]');
      if (pick) { pick.classList.toggle('on'); return; }
      const picks = e.target.closest('[data-pick-send]');
      const reply = e.target.closest('[data-reply]');
      if (picks || reply) {
        const chosen = picks ? [...picks.parentElement.querySelectorAll('[data-pick].on')].map((b) => b.dataset.pick) : [];
        if (picks && !chosen.length) return;
        const text = reply ? reply.dataset.reply : `${picks.dataset.pickSend}：${chosen.join('、')}`;
        const input = $('#spInput');
        if (input.disabled) return;
        const box = (reply || picks).closest('.cv-prompt');
        box?.classList.add('answered');                 // no second click while it is sent
        box?.querySelectorAll('button.cv-opt').forEach((b) => { b.disabled = true; });
        (reply || picks).classList.add('chosen');
        input.value = text;
        send();
        return;
      }
      const o = e.target.closest('[data-older]');
      if (o) loadOlder(+o.dataset.older);
      const uq = e.target.closest('[data-unqueue]');
      if (uq && sp.sid) {
        const qid = uq.dataset.unqueue;
        uq.disabled = true;
        const r = await api(`/sessions/${encodeURIComponent(sp.sid)}/unqueue`, { method: 'POST', body: { id: qid } });
        if (r?.ok) {
          sp.optimistic = sp.optimistic.filter((x) => x.serverId !== qid);
          const input = $('#spInput');
          if (r.text && input && !input.value.trim()) {
            input.value = r.text;
            input.style.height = 'auto';
            input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
            input.focus();
          }
          if (r.images?.length) {
            sp.images = [...sp.images, ...r.images].slice(0, MAX_SESSION_IMAGES);
            renderComposerImgs('send');
          }
          sp.note = '';
          if (state.activeProjectId) refreshTasks();
        }
        load(true);
        return;
      }
      const d = sp.data;
      if (!d?.task || !d.project) return;
      const pid = d.project.id, tid = d.task.id;
      const asg = e.target.closest('[data-task-assign]');
      if (asg) {
        asg.disabled = true;
        await api(`/projects/${pid}/tasks/${tid}/assign`, { method: 'POST', body: { agent: asg.dataset.taskAssign } });
        if (state.activeProjectId) refreshTasks();
        load(true);
        return;
      }
      const fin = e.target.closest('[data-task-finish]');
      if (fin) {
        fin.disabled = true;
        await api(`/projects/${pid}/tasks/${tid}/complete`, { method: 'POST', body: {} });
        if (state.activeProjectId) refreshTasks();
        load(true);
        return;
      }
      const ret = e.target.closest('[data-task-retry]');
      if (ret) {
        ret.disabled = true;
        await api(`/projects/${pid}/tasks/${tid}/retry`, { method: 'POST' });
        if (state.activeProjectId) refreshTasks();
        load(true);
        return;
      }
      const can = e.target.closest('[data-task-cancel]');
      if (can) {
        can.disabled = true;
        await api(`/projects/${pid}/tasks/${tid}/cancel`, { method: 'POST' });
        if (state.activeProjectId) refreshTasks();
        load(true);
        return;
      }
      const del = e.target.closest('[data-task-delete]');
      if (del) {
        const ask = tr('deleteAsk', tid);
        const ok = window.showConfirm ? await window.showConfirm(ask) : confirm(ask);
        if (!ok) return;
        del.disabled = true;
        await api(`/projects/${pid}/tasks/${tid}`, { method: 'DELETE' });
        if (state.activeProjectId) refreshTasks();
        closePanel();
        return;
      }
      if (e.target.closest('[data-review]')) openTask(pid, tid);
      const ok = e.target.closest('[data-approve]');
      if (ok) {
        ok.disabled = true;
        await api(`/projects/${pid}/tasks/${tid}/approve`, { method: 'POST', body: {} });
        if (state.activeProjectId) refreshTasks();
        load(true);
      }
    });
    $('#spBody').addEventListener('scroll', () => {
      const b = $('#spBody');
      const far = b.scrollHeight - b.scrollTop - b.clientHeight > 220;
      $('#spToBottom')?.classList.toggle('hidden', !far);
    }, { passive: true });
    $('#spToBottom').addEventListener('click', () => {
      const b = $('#spBody');
      b.scrollTo({ top: b.scrollHeight, behavior: 'smooth' });
    });

    // Mobile swipe-right to close the session panel (from left edge or header)
    const panel = $('#sessionPanel');
    let pStartX = 0, pStartY = 0, pStartTime = 0, pDx = 0, pLocked = false, pDragging = false;
    panel.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1 || !sp.sid) return;
      const x = e.touches[0].clientX;
      const inHead = Boolean(e.target.closest('#spHead'));
      if (x > 48 && !inHead) return;
      pStartX = x;
      pStartY = e.touches[0].clientY;
      pStartTime = Date.now();
      pDx = 0;
      pLocked = false;
      pDragging = false;
    }, { passive: true });
    panel.addEventListener('touchmove', (e) => {
      if (!pStartTime || (pLocked && !pDragging)) return;
      const dx = e.touches[0].clientX - pStartX;
      const dy = e.touches[0].clientY - pStartY;
      if (!pLocked && (Math.abs(dx) > 10 || Math.abs(dy) > 10)) {
        pLocked = true;
        pDragging = dx > 0 && dx > Math.abs(dy) * 1.25;
        if (pDragging) panel.style.transition = 'none';
      }
      if (!pDragging) return;
      e.preventDefault();
      pDx = Math.max(0, dx);
      panel.style.transform = `translateX(${pDx}px)`;
    }, { passive: false });
    const endPanelSwipe = () => {
      if (!pStartTime) return;
      const dt = Math.max(1, Date.now() - pStartTime);
      const velocity = pDx / dt;
      pStartTime = 0;
      if (!pDragging) return;
      pDragging = false;
      panel.style.transition = '';
      panel.style.transform = '';
      if (pDx > 85 || (pDx > 36 && velocity > 0.42)) {
        closePanel();
      }
    };
    panel.addEventListener('touchend', endPanelSwipe);
    panel.addEventListener('touchcancel', () => {
      pStartTime = 0;
      pDragging = false;
      panel.style.transition = '';
      panel.style.transform = '';
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && sp.sid && !document.querySelector('.modal-overlay.active')) closePanel();
    });
  }

  async function openTask(pid, tid) {
    closePanel();
    enterProject(pid);
    await refreshTasks();
    openTaskModal(tid);
  }

  function openSessionPanel(sid, agent, opts = {}) {
    if (!sid && opts.projectId && opts.taskId) {
      sid = `task:${opts.projectId}:${opts.taskId}`;
    }
    if (!sid) return;
    ensurePanel();
    if (typeof closeModal === 'function' && document.querySelector('.modal-overlay.active')) closeModal();
    Object.assign(sp, {
      sid, agent: agent || '', taskId: opts.taskId || null, data: null,
      note: '', sendErr: '', taskSig: '', optimistic: [], reloadPending: false, effortOpen: false,
      git: null, gitAt: 0, branchOpen: false, openCommits: new Set(), commitFiles: {},
      merging: false, headSig: '', titleCollapsed: true, titleLong: false, titleKey: '',
      images: [], newImages: [], uploading: 0, newUploading: 0,
    });
    $('#spNew')?.classList.add('hidden');
    $('#spHead').innerHTML = '';
    $('#spBody').innerHTML = '';
    $('#spInput').value = '';
    $('#spInput').style.height = 'auto';
    renderComposerImgs('send');
    renderComposerImgs('new');
    document.body.classList.add('sp-open');
    $('#spOverlay').classList.add('show');
    $('#sessionPanel').classList.add('show');
    load(true);
  }

  function closePanel() {
    sp.sid = '';
    sp.merging = false;
    sp.headSig = '';
    sp.images = [];
    sp.newImages = [];
    sp.uploading = 0;
    sp.newUploading = 0;
    clearTimeout(sp.timer);
    document.body.classList.remove('sp-open');
    $('#spOverlay')?.classList.remove('show');
    $('#sessionPanel')?.classList.remove('show');
  }

  function renderBody(d, scrollBottom) {
    if (!d) return false;
    const sid = sp.sid;
    const body = $('#spBody');
    const nearBottom = scrollBottom || body.scrollHeight - body.scrollTop - body.clientHeight < 80;
    const descEl = body.querySelector('.sp-task-desc');
    const openTask = descEl ? descEl.open : undefined;
    const openSteps = openStepSet(body);

    const baseRounds = d.conversation?.rounds || [];
    const lastRound = baseRounds[baseRounds.length - 1];
    const directOpt = sp.optimistic.find((o) => !o.queued && (
      o.text ? o.text !== (lastRound?.ask || '')
             : !(lastRound && (lastRound.images > 0 || lastRound.image_urls?.length) && lastRound.at >= o.at - 5000)
    ));
    const conv = directOpt
      ? { ...d.conversation, rounds: baseRounds.concat([{ ask: directOpt.text, at: directOpt.at, items: [], image_urls: directOpt.images || [] }]) }
      : d.conversation;

    const live = d.session.state === 'run' || d.send.run?.state === 'running'
      || d.task?.status === 'in_progress' || Boolean(directOpt);
    const optQueue = sp.optimistic.filter((o) => o.queued);
    const qHtml = queueHtml(d.send?.queue, optQueue, live);
    const hasLog = d.log || conv.rounds.length > 0;
    let mainHtml = '';
    if (conv.rounds.length > 0) {
      mainHtml = convHtml(conv, live, sid, Boolean(d.send?.mode));
    } else if (d.task?.status === 'pending') {
      mainHtml = `<div class="cv-empty">${tr('waitingStart')}</div>`;
    } else if (d.task && ['todo', 'review'].includes(d.task.status)) {
      mainHtml = '';
    } else {
      mainHtml = `<div class="cv-empty">${tr(hasLog ? 'empty' : 'noLog')}</div>`;
    }
    body.innerHTML = taskHtml(d, openTask)
      + mainHtml
      + (d.session.summary && !conv.rounds.length ? `<div class="cv-reply md-content">${renderMarkdown(d.session.summary)}</div>` : '')
      + qHtml;
    reopenSteps(body, openSteps);
    if (scrollBottom === 'first' && (d.task?.status === 'review' || !conv.rounds.length) && !sp.optimistic.length && !(d.send?.queue?.length)) {
      body.scrollTop = 0; // the plan or task card comes first
    } else if (nearBottom) {
      body.scrollTop = body.scrollHeight;
    }
    const far = body.scrollHeight - body.scrollTop - body.clientHeight > 220;
    $('#spToBottom')?.classList.toggle('hidden', !far);
    return live;
  }

  async function load(first) {
    const sid = sp.sid;
    if (!sid) return;
    if (sp.loading) {
      sp.reloadPending = sp.reloadPending === 'first' || first ? 'first' : true;
      return;
    }
    sp.loading = true;
    let d;
    const q = sp.taskId ? `?task_id=${sp.taskId}` : '';
    try {
      d = await api(`/sessions/${encodeURIComponent(sid)}${q}`);
    } catch {
      d = null;
    } finally {
      sp.loading = false;
      if (sp.reloadPending && sid === sp.sid) {
        const nextFirst = sp.reloadPending === 'first';
        sp.reloadPending = false;
        setTimeout(() => load(nextFirst), 0);
      }
    }
    if (sid !== sp.sid) return;
    if (!d || d.error) {
      $('#spBody').innerHTML = `<div class="cv-empty">${esc(d?.error || 'Error')}</div>`;
      return;
    }
    if (d.session?.sid && d.session.sid !== sp.sid && sp.sid.startsWith('task:')) {
      sp.sid = d.session.sid;
    }
    const prevOlder = sp.data && sp.data.conversation.start < d.conversation.start
      ? sp.data.conversation : null;
    if (prevOlder) {      // keep rounds the reader already loaded above
      const keep = prevOlder.rounds.slice(0, d.conversation.start - prevOlder.start);
      d.conversation = { ...d.conversation, rounds: keep.concat(d.conversation.rounds), start: prevOlder.start };
    }
    // Synced optimistic items are now reflected in d.conversation or d.send.queue
    sp.optimistic = sp.optimistic.filter((o) => !o.synced);
    sp.data = d;
    renderHead(d);
    const live = renderBody(d, first ? 'first' : false);
    if (first || Date.now() - sp.gitAt > 10000) loadGit(sid);
    renderSend(d);
    clearTimeout(sp.timer);
    const hasQueue = (d.send?.queue?.length || 0) > 0 || sp.optimistic.length > 0;
    sp.timer = setTimeout(() => load(), live || d.send.busy || hasQueue ? 2500 : 15000);
  }

  // The Jarvis task this session ran: same place as the conversation, so a
  // task opened from the board reads exactly like a session opened from home.
  function taskHtml(d, open) {
    const t = d.task;
    if (!t) return '';
    const isOpen = open !== undefined
      ? open
      : (['todo', 'pending', 'review'].includes(t.status) || !(d.conversation?.rounds?.length));
    const imgs = (t.images || []).length
      ? `<div class="cv-imgs sp-task-imgs">${t.images.map((u) => `<img class="cv-img" loading="lazy" alt="" src="${esc(u)}">`).join('')}</div>`
      : '';
    const err = t.error ? `<div class="sp-task-err">${esc(t.error)}</div>` : '';
    const sum = (t.summary && !t.fallback)
      ? `<div class="sp-task-sum md-content">${renderMarkdown(t.summary)}</div>` : '';
    const plan = t.status === 'review' && t.plan
      ? `<div class="sp-task-sum sp-plan md-content">${renderMarkdown(t.plan)}</div>
         <div class="sp-plan-actions">${t.questions
           ? `<button class="btn btn-primary btn-small" data-review>${esc(tr('answer', t.questions))}</button>`
           : `<button class="btn btn-primary btn-small" data-approve>${esc(tr('approve'))}</button>`}</div>`
      : '';
    let actions = '';
    if (t.status === 'todo') {
      const ico = (a) => window.agentIcon ? window.agentIcon(a) : '';
      const hand = ['claude', 'codex', 'antigravity'].map((a) =>
        `<button type="button" class="card-action-btn assign agent-${a}" data-task-assign="${a}">${ico(a)}<span>${esc(agentName(a))}</span></button>`
      ).join('');
      const mine = t.agent === 'me'
        ? `<button type="button" class="card-action-btn assign finish" data-task-finish>✓ ${esc(tr('finish'))}</button>`
        : `<button type="button" class="card-action-btn assign agent-me" data-task-assign="me">${ico('me')}<span>${esc(agentName('me'))}</span></button>`;
      actions = `<div class="sp-task-actions"><div class="card-assign">${hand}${mine}</div>
        <button type="button" class="sp-task-btn danger" data-task-delete>${esc(tr('deleteTask'))}</button></div>`;
    } else if (t.status === 'pending') {
      actions = `<div class="sp-task-actions">
        <button type="button" class="sp-task-btn" data-task-cancel>${esc(tr('cancelTask'))}</button>
        <button type="button" class="sp-task-btn danger" data-task-delete>${esc(tr('deleteTask'))}</button></div>`;
    } else if (t.status === 'in_progress') {
      actions = `<div class="sp-task-actions">
        <button type="button" class="sp-task-btn danger" data-task-cancel>${esc(tr('cancelTask'))}</button></div>`;
    } else if (t.status === 'failed' || t.status === 'cancelled') {
      actions = `<div class="sp-task-actions">
        <button type="button" class="sp-task-btn primary" data-task-retry>${esc(tr('retryTask'))}</button>
        <button type="button" class="sp-task-btn danger" data-task-delete>${esc(tr('deleteTask'))}</button></div>`;
    }
    return `<div class="sp-task">
        <details class="sp-task-desc" ${isOpen ? 'open' : ''}>
          <summary><span class="sp-task-st st-${esc(t.status)}">${esc(tr('st_' + t.status) || t.status)}</span>
            <span class="sp-task-title">${esc(t.description.split('\n')[0].slice(0, 120))}</span></summary>
          <div class="md-content">${renderMarkdown(t.description)}</div>${imgs}
        </details>${err}${plan || sum}${actions}
      </div>`;
  }

  // Polling re-renders the conversation; steps you opened (to look at their images) stay open.
  const openStepSet = (body) => new Set([...body.querySelectorAll('.cv-tools[open]')].map((x) => x.dataset.k));
  function reopenSteps(body, set) {
    body.querySelectorAll('.cv-tools').forEach((x) => { if (set.has(x.dataset.k)) x.open = true; });
  }

  // A thumbnail opens full size over the page; a click anywhere or Esc closes it.
  function showImage(src) {
    let box = $('#cvLightbox');
    if (!box) {
      document.body.insertAdjacentHTML('beforeend', '<div class="cv-lightbox" id="cvLightbox"><img alt=""></div>');
      box = $('#cvLightbox');
      box.addEventListener('click', () => box.classList.remove('show'));
    }
    box.querySelector('img').src = src;
    box.classList.add('show');
  }
  document.addEventListener('click', (e) => {
    const img = e.target.closest('.cv-img');
    if (img) { e.stopPropagation(); showImage(img.src); }
  }, true);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && $('#cvLightbox')?.classList.contains('show')) {
      e.stopImmediatePropagation();
      $('#cvLightbox').classList.remove('show');
    }
  }, true);

  async function loadOlder(before) {
    const d = await api(`/sessions/${encodeURIComponent(sp.sid)}?before=${before}&limit=6`);
    if (!d || d.error || !sp.data) return;
    const body = $('#spBody');
    const h = body.scrollHeight;
    const c = sp.data.conversation;
    sp.data.conversation = { ...c, rounds: d.conversation.rounds.concat(c.rounds), start: d.conversation.start,
                             prev_model: d.conversation.prev_model, prev_effort: d.conversation.prev_effort };
    const openTask = body.querySelector('.sp-task-desc')?.open;
    const openSteps = openStepSet(body);
    body.innerHTML = taskHtml(sp.data, openTask) + convHtml(sp.data.conversation, sp.data.session.state === 'run', sp.sid, Boolean(sp.data.send?.mode));
    reopenSteps(body, openSteps);
    body.scrollTop = body.scrollHeight - h;
  }

  function renderHead(d) {
    const s = d.session;
    const hasOptRun = sp.optimistic.some((o) => !o.queued);
    const effState = (hasOptRun || d.send.run?.state === 'running') ? 'run' : s.state;
    const proj = d.project
      ? `<button class="sp-link" data-project="${d.project.id}">${esc(d.project.name)}</button>` : '';
    const needModalReview = d.task?.status === 'review' && (d.task.questions > 0 || d.task.subtasks > 1);
    const task = d.task && d.project
      ? `<button class="sp-link" data-project="${d.project.id}" data-task="${d.task.id}" title="${esc(tr('details'))}">#${d.task.id}${needModalReview ? ` · ${esc(tr('details'))}` : ''}</button>` : '';
    const mergeBtn = d.merge && d.task
      ? `<button type="button" class="sp-merge" data-merge ${sp.merging ? 'disabled' : ''}>${esc(sp.merging ? tr('merging') : tr('merge', d.merge.commits))}</button>`
      : '';
    if (sp.titleKey !== s.title) {
      sp.titleKey = s.title;
      sp.titleCollapsed = true;
      sp.titleLong = false;
    }
    const foldLabel = sp.titleCollapsed ? `${tr('expandTitle')} ▾` : `${tr('collapseTitle')} ▴`;
    const html = `
      <div class="sp-title-row">
        <button class="sp-back" data-close aria-label="back">←</button>
        <div class="sp-title-wrap">
          <div class="sp-title ${sp.titleCollapsed ? 'is-collapsed' : ''}">${esc(s.title)}</div>
          <button type="button" class="sp-title-fold ${sp.titleLong ? '' : 'hidden'}" data-title-fold aria-expanded="${!sp.titleCollapsed}">${esc(foldLabel)}</button>
        </div>
        ${s.sid && !String(s.sid).startsWith('task:') ? `<button class="sp-icon${s.archived_here ? ' on' : ''}" data-archive="${s.archived_here ? 0 : 1}" title="${esc(tr(s.archived_here ? 'unarchive' : 'archive'))}" aria-label="${esc(tr(s.archived_here ? 'unarchive' : 'archive'))}">${ARCHIVE_ICON}</button>` : ''}
        ${d.project ? `<button class="sp-icon" data-new title="${esc(tr('newSession'))}" aria-label="${esc(tr('newSession'))}">＋</button>` : ''}
        <button class="sp-close" data-close aria-label="close">&times;</button>
      </div>
      <div class="sp-sub">
        ${window.agentBadge ? window.agentBadge(s.agent, 'ov-agent') : `<span class="card-badge agent agent-${esc(s.agent)} ov-agent">${esc(agentName(s.agent))}</span>`}
        <span class="ov-badge st-${esc(effState)}">${tr(effState)}</span>
        ${effortChipHtml(d)}${window.ctxChipHtml ? window.ctxChipHtml(s, d.project ? 'button' : 'span', d.project ? 'type="button" data-ctx-new' : '') : ''}${proj}${task}
        ${branchChipHtml(sp.git, d)}
        ${!d.task && d.project ? `<button class="sp-link" data-hide="${s.hidden ? 0 : 1}">${tr(s.hidden ? 'unhide' : 'hide')}</button>` : ''}
      </div>${sp.effortOpen ? effortMenuHtml(d) : ''}${sp.branchOpen ? branchPopHtml(sp.git, d, mergeBtn) : ''}`;
    if (sp.headSig !== html) {
      sp.headSig = html;
      $('#spHead').innerHTML = html;
      const titleEl = $('#spHead .sp-title');
      if (titleEl) {
        const lh = parseFloat(getComputedStyle(titleEl).lineHeight) || 22;
        const isLong = sp.titleCollapsed
          ? titleEl.scrollHeight > titleEl.clientHeight + 2
          : titleEl.scrollHeight > lh * 2.4;
        if (isLong !== sp.titleLong) {
          sp.titleLong = isLong;
          $('#spHead .sp-title-fold')?.classList.toggle('hidden', !isLong);
        }
      }
    }
  }

  function renderSend(d) {
    const input = $('#spInput'), submit = $('#spSubmit'), stop = $('#spStop'), attach = $('#spAttach');
    const reviewing = d.task?.status === 'review' && d.project;
    const mode = reviewing ? 'revise' : d.send.mode;
    const busy = !reviewing && (d.send.busy || d.session.state === 'run' || d.task?.status === 'in_progress' || sp.optimistic.length > 0);
    const can = !!mode;
    const uploading = sp.uploading > 0;
    input.disabled = !can;
    if (attach) attach.disabled = !can;
    submit.disabled = !can || uploading;
    $('#spQueue').disabled = uploading;
    // Jarvis runs this session: it can be stopped, and a new instruction interrupts
    const ours = !reviewing && Boolean(d.send.ours || d.send.run?.state === 'running');
    input.placeholder = !mode ? tr('phNone') : busy ? tr(ours ? 'phSteer' : 'phQueue')
      : { task: tr('phTask'), revise: tr('phRevise') }[mode] || tr('phDirect');
    stop.classList.toggle('hidden', !ours);
    stop.textContent = tr('stop');
    $('#spQueue').classList.toggle('hidden', !(ours && busy && can));
    $('#spQueue').textContent = tr('queueBtn');
    const runErr = d.send.run?.state === 'error' ? `${tr('failed')}：${d.send.run.error}` : '';
    const err = sp.sendErr || runErr;
    const noteText = err || (uploading ? tr('uploadingImg') : sp.note);
    $('#spNote').textContent = noteText;
    $('#spNote').classList.toggle('err', Boolean(err));
    $('#spNoteX').classList.toggle('hidden', !err);
    // a run the CLI turned away: its instruction goes back into the box (once)
    const failedRun = d.send.run?.state === 'error' && d.send.run.text ? d.send.run : null;
    const key = failedRun && `${sp.sid}:${failedRun.started_at}`;
    if (failedRun && !restoredRuns.has(key)) {
      restoredRuns.add(key);
      if (!input.value.trim()) {
        input.value = failedRun.text;
        input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
      }
      if (!sp.images.length && failedRun.images?.length) {
        sp.images = failedRun.images.slice(0, MAX_SESSION_IMAGES);
      }
    }
    const held = d.send.run?.state === 'error' && (d.send.run.text || d.send.run.images?.length)
      && (d.send.run.held || /active writer|一处写入/.test(d.send.run.error || ''));
    $('#spFork').classList.toggle('hidden', !held);
    $('#spFork').textContent = tr('fork');
    $('#spSend .sp-send-row')?.classList.toggle('hidden', !mode);
    renderComposerImgs('send');
    $('#spSend').classList.toggle('hidden', !mode && !noteText);
  }

  // The context grew too long: open "new session" with a handoff note already written,
  // on the same executor, for you to finish and send.
  function openHandoff(d) {
    const s = d.session;
    const rounds = d.conversation?.rounds || [];
    const lastItems = rounds.length ? rounds[rounds.length - 1].items || [] : [];
    const lastReply = [...lastItems].reverse().find((i) => i.k === 'text')?.t || '';
    const result = (d.task?.summary || s.summary || lastReply || '').trim().slice(0, 1200);
    const line = (s.title || '').trim().split('\n')[0];
    const title = line.length > 40 ? `${line.slice(0, 40)}…` : line;
    const note = state.lang === 'en'
      ? `Continuing "${title}" in a new session (the previous one grew too long).\n\n`
        + (result ? `Where it ended:\n${result}\n\n` : '')
        + 'For details read the code, git log and PROGRESS.md.\n\nNext: '
      : `接着「${title}」继续：上一个会话上下文太长，换成新会话。\n\n`
        + (result ? `上次做到：\n${result}\n\n` : '')
        + '需要细节就读代码、git log 和 PROGRESS.md。\n\n接下来要做：';
    $('#spNew').classList.remove('hidden');
    $('#spNew').querySelectorAll('[data-new-agent]').forEach((x) => x.classList.toggle('on', x.dataset.newAgent === (s.agent || 'claude')));
    const input = $('#spNewInput');
    input.value = note;
    input.style.height = `${Math.min(input.scrollHeight + 2, 260)}px`;
    input.focus();
    input.setSelectionRange(note.length, note.length);
    input.scrollTop = input.scrollHeight;
  }

  // Codex lets one place write to a session at a time: when it is open in a terminal,
  // the reply goes to a copy of the session instead, and the panel follows it there.
  async function forkSession() {
    const run = sp.data?.send?.run, btn = $('#spFork');
    if ((!run?.text && !run?.images?.length) || btn.disabled) return;
    btn.disabled = true;
    btn.textContent = tr('forking');
    let res;
    try {
      res = await api(`/sessions/${encodeURIComponent(sp.sid)}/fork`, {
        method: 'POST',
        body: { text: run.text || '', images: run.images || [] },
      });
    } catch (e) {
      res = { error: e?.message || tr('failed') };
    }
    btn.disabled = false;
    btn.textContent = tr('fork');
    if (!res?.sid) {
      sp.sendErr = res?.error || tr('failed');
      renderSend(sp.data);
      return;
    }
    if (state.activeProjectId) refreshTasks();
    openSessionPanel(res.sid, 'codex', res.task ? { taskId: res.task.id } : {});
  }

  // A brand-new session in this session's project: a task for the chosen executor,
  // run by a worker on its own branch from main; the panel switches to it once it starts.
  async function startNewSession() {
    const d = sp.data, input = $('#spNewInput'), note = $('#spNewNote');
    const text = input.value.trim();
    if ((!text && !sp.newImages.length) || sp.newUploading > 0 || !d?.project || $('#spNewSubmit').disabled) return;
    const agent = $('#spNew [data-new-agent].on')?.dataset.newAgent || 'claude';
    const prevImages = sp.newImages.slice();
    const images = prevImages.map((img) => ({ url: img.url, path: img.path }));
    $('#spNewSubmit').disabled = true;
    input.value = '';
    sp.newImages = [];
    renderComposerImgs('new');
    note.classList.remove('err');
    note.textContent = tr('starting');
    const pid = d.project.id;
    let res;
    try {
      res = await api(`/projects/${pid}/tasks`, { method: 'POST', body: { description: text, images, agent } });
    } catch (e) {
      res = { error: e?.message || tr('failed') };
    }
    const task = res?.task || res;
    if (!task?.id) {
      input.value = text;
      if (!sp.newImages.length && prevImages.length) {
        sp.newImages = prevImages;
        renderComposerImgs('new');
      }
      note.textContent = res?.error || tr('failed');
      note.classList.add('err');
      $('#spNewSubmit').disabled = false;
      return;
    }
    if (state.activeProjectId) refreshTasks();
    for (let i = 0; i < 180; i++) {                      // up to 3 minutes
      await new Promise((r) => setTimeout(r, 1000));
      const t = (await api(`/projects/${pid}/tasks`))?.tasks?.find((x) => x.id === task.id);
      if (t?.session_id) {
        $('#spNew').classList.add('hidden');
        note.textContent = '';
        $('#spNewSubmit').disabled = false;
        openSessionPanel(t.session_id, agent, { taskId: t.id });
        return;
      }
      if (t && ['failed', 'cancelled'].includes(t.status)) {
        note.textContent = `${tr('failed')}：${t.error || t.status}`;
        note.classList.add('err');
        break;
      }
      if (i === 5 && t?.status === 'pending') note.textContent = tr('queued');
    }
    $('#spNewSubmit').disabled = false;
  }

  async function send(opts = {}) {
    const input = $('#spInput');
    const text = input.value.trim();
    const d = sp.data;
    const sid = sp.sid;
    if ((!text && !sp.images.length) || sp.uploading > 0 || input.disabled || !d || !sid) return;

    const prevImages = sp.images.slice();
    const images = prevImages.map((img) => ({ url: img.url, path: img.path }));
    const reviewing = d.task?.status === 'review' && d.project;
    // Clear input immediately for instant feedback
    input.value = '';
    input.style.height = 'auto';
    sp.images = [];
    renderComposerImgs('send');
    sp.sendErr = '';

    if (reviewing) {
      sp.note = tr('revised');
      renderSend(d);
      let r;
      try {
        r = await api(`/projects/${d.project.id}/tasks/${d.task.id}/revise`, {
          method: 'POST',
          body: { feedback: text, images },
        });
      } catch (e) {
        r = { error: e?.message || tr('failed') };
      }
      if (!r?.success) {
        sp.note = '';
        sp.sendErr = r?.error || tr('failed');
        if (sid === sp.sid) {
          input.value = input.value ? `${text}\n${input.value}` : text;
          input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
          if (!sp.images.length && prevImages.length) sp.images = prevImages;
          renderSend(sp.data || d);
        }
        return;
      }
      if (state.activeProjectId) refreshTasks();
      load(true);
      return;
    }

    const ours = Boolean(d.send?.ours || d.send?.run?.state === 'running');
    const steer = ours && !opts.queue;          // interrupt what Jarvis runs and go on with this
    const busyNow = !steer && Boolean(
      d.send?.busy || d.session?.state === 'run' || d.task?.status === 'in_progress'
      || (d.send?.queue?.length || 0) > 0 || sp.optimistic.length > 0
    );
    const opt = {
      id: `opt-${++optSeq}`,
      text,
      images: images.map((x) => x.url),
      at: Date.now(),
      queued: busyNow,
      synced: false,
      serverId: '',
    };
    sp.optimistic.push(opt);
    sp.note = busyNow ? tr('queuedNote') : '';
    renderHead(d);
    renderBody(d, true);
    renderSend(d);

    let res;
    try {
      const body = opts.queue ? { text, images, queue: true } : { text, images };
      res = await api(`/sessions/${encodeURIComponent(sid)}/send`, { method: 'POST', body });
    } catch (e) {
      res = { error: e?.message || tr('failed') };
    }
    if (!res || res.error) {
      sp.optimistic = sp.optimistic.filter((x) => x.id !== opt.id);
      sp.note = '';
      sp.sendErr = res?.error || tr('failed');
      if (sid === sp.sid) {
        input.value = input.value ? `${text}\n${input.value}` : text;
        input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
        if (!sp.images.length && prevImages.length) sp.images = prevImages;
        if (sp.data) {
          renderHead(sp.data);
          renderBody(sp.data, false);
          renderSend(sp.data);
        }
      }
      return;
    }

    opt.synced = true;
    opt.queued = Boolean(res.queued);
    opt.serverId = res.item?.id || (res.task?.id ? `t${res.task.id}` : '');
    if (res.mode === 'task' && res.task?.id) {
      sp.note = res.queued ? tr('queuedTask', res.task.id) : tr('sent', res.task.id);
    } else if (res.queued) {
      sp.note = tr('queuedNote');
    } else {
      sp.note = '';
    }
    if (state.activeProjectId) refreshTasks();
    if (sid === sp.sid) load(true);
  }

  // ── task modal: the task's conversation inline ──
  async function mountTaskConversation(el, sid, agent, taskId) {
    const q = taskId ? `&task_id=${taskId}` : '';
    const d = await api(`/sessions/${encodeURIComponent(sid)}?limit=4${q}`);
    if (!el.isConnected) return;
    if (!d || d.error || !d.log) {
      el.innerHTML = `<div class="cv-empty">${esc(d?.error || tr('noLog'))}</div>`;
      const more = el.parentElement?.querySelector('.modal-more');
      if (more) more.open = true;
      return;
    }
    const h3 = el.previousElementSibling;
    if (h3 && h3.tagName === 'H3' && !h3.querySelector('.sp-model')) {
      h3.insertAdjacentHTML('beforeend', modelChip(d.session.model, d.session.effort));
    }
    el.innerHTML = `<div class="cv-embed">${convHtml(d.conversation, d.session.state === 'run', sid)}</div>
      <button class="btn btn-secondary btn-small cv-continue">${tr('continue')} →</button>`;
    el.querySelector('.cv-older')?.remove();
    el.querySelector('.cv-continue').addEventListener('click', () => openSessionPanel(sid, agent));
  }

  window.openSessionPanel = openSessionPanel;
  window.closeSessionPanel = closePanel;
  window.mountTaskConversation = mountTaskConversation;
  window.convTitle = () => tr('conv');
  window.onSessionChanged = (sid) => { if (sp.sid && (!sid || sid === sp.sid)) load(); };
})();
