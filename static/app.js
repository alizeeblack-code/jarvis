/**
 * Jarvis - Frontend
 *
 * Features:
 * - Project management (select existing dir or create new)
 * - Kanban board with 6 columns, scoped to active project
 * - Real-time WebSocket updates
 * - Voice input via Web Speech API
 * - Plan mode with approval flow
 * - Dark/Light theme, i18n
 * - Mobile-first responsive design
 */

// ─── State ──────────────────────────────────────────────────────────────────

const DEFAULT_COLUMN_LIMITS = { todo: 8, pending: 8, in_progress: 12, wait: 8, done: 8 };

const SAVED_PROJECT_VIEW = localStorage.getItem('projectView');

const state = {
  projects: [],
  activeProjectId: parseInt(localStorage.getItem('activeProject')) || null,
  tasks: [],
  ws: null,
  reconnectTimer: null,
  lang: localStorage.getItem('lang') || 'zh',
  theme: localStorage.getItem('theme') || 'light',
  planAnswers: {},
  uploadedImages: [],  // [{url, path}]
  mobileActiveTab: localStorage.getItem('mobileTab') || 'in_progress',
  activeView: ['list', 'board', 'analytics'].includes(SAVED_PROJECT_VIEW) ? SAVED_PROJECT_VIEW : 'list',
  analyticsRange: 7,    // 7, 30, or 0 (all)
  analyticsData: null,
  columnDisplayLimits: { ...DEFAULT_COLUMN_LIMITS },
  searchQuery: '',
  networkAddresses: null,
  listView: {
    agent: '',
    archivedOpen: false,
    archivedSearchOverride: null,
    recentLimit: 50,
    archivedLimit: 30,
    data: null,
    key: '',
  },
};

// ─── i18n ───────────────────────────────────────────────────────────────────

const i18n = {
  zh: {
    title: 'Jarvis',
    selectProject: '项目',
    todo: 'Todo',
    finish: '完成',
    byAgent: '按执行者',
    agent_todo: '先放 Todo', agent_claude: 'Claude', agent_codex: 'Codex',
    agent_antigravity: 'Antigravity', agent_me: '我自己', agent_unknown: '未记录',
    newProject: '+ 新建项目',
    createProject: '新建项目',
    editProject: '编辑项目',
    projectName: '项目名称',
    projectPath: '项目目录',
    pathHint: '输入本地目录路径，不存在的目录会自动创建',
    create: '创建项目',
    save: '保存',
    cancel: '取消',
    deleteConfirm: '确定删除这个项目吗？任务数据也会一起删除。',
    deleteTaskConfirm: '确定要删除这个任务吗？',
    confirmOk: '确定',
    noProjects: '还没有项目',
    noProjectsHint: '点击上方"新建项目"开始',
    addTask: '添加新任务... (Cmd/Ctrl+Enter 提交)',
    submit: '添加',
    handTo: (name) => `交给 ${name}`, handMe: '我自己做',
    pending: '待开发',
    in_progress: '开发中',
    review: '待 Review',
    done: '已完成',
    failed: '失败',
    cancelled: '已取消',
    col_todo: 'Todo', col_pending: '排队', col_in_progress: '进行中', col_wait: '等你', col_done: '已完成',
    rounds: (n) => `${n} 轮`, archived: '已归档', hideCard: '收起', toMerge: '待合并',
    empty: '无任务',
    approve: '确认并创建任务',
    feedback: '修改反馈',
    exitFull: '退出全屏',
    taskLog: '运行日志',
    description: '任务描述',
    plan: '执行计划',
    error: '错误信息',
    images: '图片',
    totalTasks: (n) => `共 ${n} 个任务`,
    completedAt: '完成',
    failedAt: '失败',
    ago: (s) => `${s}前`,
    dirExists: '目录存在',
    dirMissing: '目录不存在',
    isGit: 'Git',
    tasks_label: '任务',
    cancelLabel: '取消',
    retryLabel: '重试',
    deleteLabel: '删除',
    viewList: '列表',
    viewBoard: '看板',
    viewAnalytics: '分析',
    listAllAgents: '全部',
    listArchiveOld: '归档旧任务',
    listActiveTitle: '进行中与待处理',
    listRecentTitle: '最近会话',
    listArchivedTitle: '已归档',
    listEmptyRecent: '暂无最近会话',
    listEmptyArchived: '暂无已归档记录',
    listLoadMore: (n, total) => `加载更多（${n} / ${total}）`,
    listArchiveAction: '归档',
    listRestoreAction: '恢复',
    listReasonAgent: '客户端已归档',
    listReasonHidden: '已收起',
    listReasonTask: '任务已归档',
    listLoading: '加载中…',
    listDuration: (s) => `耗时 ${s}`,
    bgWait: '等后台',
    analyticsTotal: '总任务',
    analyticsSuccessRate: '成功率',
    analyticsAvgDuration: '平均耗时',
    analyticsTotalCost: '总成本',
    analyticsDailyTrend: '每日趋势',
    analyticsDone: '完成',
    analyticsFailed: '失败',
    analyticsNoData: '暂无数据',
    analyticsRange7: '7天',
    analyticsRange30: '30天',
    analyticsRangeAll: '全部',
    analyticsPending: '待开发',
    analyticsInProgress: '开发中',
    analyticsReview: '待审核',
    analyticsDoneLabel: '已完成',
    analyticsFailedLabel: '失败',
    analyticsCancelled: '已取消',
    analyticsDurationMin: (s) => `最短 ${s}`,
    analyticsDurationMax: (s) => `最长 ${s}`,
    analyticsSampleSize: (n) => `${n} 个样本`,
    runningFor: '已运行',
    urlsTitle: '访问地址',
    urlsTitleWithPort: (p) => `访问地址 (:${p})`,
    urlsPort: '端口',
    urlsAuto: '自动',
    urlsAutoTitle: '自动分配独立端口',
    urlsPortInvalid: '请输入 1–65535 之间的端口号',
    saveFailed: '保存失败',
    urlsLocal: '本地开发',
    urlsLan: '局域网',
    urlsTailscale: 'Tailscale',
    urlsMagicDns: 'Tailscale MagicDNS',
    urlsMissingLan: '未检测到局域网 IP',
    urlsMissingTs: '未检测到 Tailscale IP',
    urlsCopy: '复制',
    urlsCopied: '已复制',
    urlsOpen: '打开',
    urlsLoading: '读取网络地址中…',
  },
  en: {
    title: 'Jarvis',
    selectProject: 'Projects',
    todo: 'Todo',
    finish: 'Done',
    byAgent: 'By executor',
    agent_todo: 'Keep in Todo', agent_claude: 'Claude', agent_codex: 'Codex',
    agent_antigravity: 'Antigravity', agent_me: 'Me', agent_unknown: 'Not recorded',
    newProject: '+ New Project',
    createProject: 'New Project',
    editProject: 'Edit Project',
    projectName: 'Project Name',
    projectPath: 'Project Directory',
    pathHint: 'Enter a local directory path. Non-existing dirs will be created.',
    create: 'Create Project',
    save: 'Save',
    cancel: 'Cancel',
    deleteConfirm: 'Delete this project? Task data will also be removed.',
    deleteTaskConfirm: 'Delete this task?',
    confirmOk: 'OK',
    noProjects: 'No projects yet',
    noProjectsHint: 'Click "New Project" above to start',
    addTask: 'Add new task... (Cmd/Ctrl+Enter to submit)',
    submit: 'Add',
    handTo: (name) => `Hand to ${name}`, handMe: 'Do it myself',
    pending: 'Pending',
    in_progress: 'In Progress',
    review: 'Review',
    done: 'Done',
    failed: 'Failed',
    cancelled: 'Cancelled',
    col_todo: 'Todo', col_pending: 'Queued', col_in_progress: 'Running', col_wait: 'Your turn', col_done: 'Done',
    rounds: (n) => `${n} rounds`, archived: 'Archived', hideCard: 'Put away', toMerge: 'To merge',
    empty: 'No tasks',
    approve: 'Approve & Create Task',
    feedback: 'Edit Feedback',
    exitFull: 'Exit Fullscreen',
    taskLog: 'Logs',
    description: 'Description',
    plan: 'Plan',
    error: 'Error',
    images: 'Images',
    totalTasks: (n) => `${n} tasks`,
    completedAt: 'Done',
    failedAt: 'Failed',
    ago: (s) => `${s} ago`,
    dirExists: 'Dir exists',
    dirMissing: 'Dir missing',
    isGit: 'Git',
    tasks_label: 'tasks',
    cancelLabel: 'Cancel',
    retryLabel: 'Retry',
    deleteLabel: 'Delete',
    viewList: 'List',
    viewBoard: 'Board',
    viewAnalytics: 'Analytics',
    listAllAgents: 'All',
    listArchiveOld: 'Archive old',
    listActiveTitle: 'Active & Pending',
    listRecentTitle: 'Recent Sessions',
    listArchivedTitle: 'Archived',
    listEmptyRecent: 'No recent sessions',
    listEmptyArchived: 'No archived items',
    listLoadMore: (n, total) => `Load more (${n} / ${total})`,
    listArchiveAction: 'Archive',
    listRestoreAction: 'Restore',
    listReasonAgent: 'Archived in app',
    listReasonHidden: 'Put away',
    listReasonTask: 'Task archived',
    listLoading: 'Loading…',
    listDuration: (s) => `Spent ${s}`,
    bgWait: 'Bg wait',
    analyticsTotal: 'Total Tasks',
    analyticsSuccessRate: 'Success Rate',
    analyticsAvgDuration: 'Avg Duration',
    analyticsTotalCost: 'Total Cost',
    analyticsDailyTrend: 'Daily Trend',
    analyticsDone: 'Done',
    analyticsFailed: 'Failed',
    analyticsNoData: 'No data yet',
    analyticsRange7: '7 days',
    analyticsRange30: '30 days',
    analyticsRangeAll: 'All',
    analyticsPending: 'Pending',
    analyticsInProgress: 'In Progress',
    analyticsReview: 'Review',
    analyticsDoneLabel: 'Done',
    analyticsFailedLabel: 'Failed',
    analyticsCancelled: 'Cancelled',
    analyticsDurationMin: (s) => `Min ${s}`,
    analyticsDurationMax: (s) => `Max ${s}`,
    analyticsSampleSize: (n) => `${n} samples`,
    runningFor: 'Running',
    urlsTitle: 'Access URLs',
    urlsTitleWithPort: (p) => `Access URLs (:${p})`,
    urlsPort: 'Port',
    urlsAuto: 'Auto',
    urlsAutoTitle: 'Auto-assign independent port',
    urlsPortInvalid: 'Enter a port between 1 and 65535',
    saveFailed: 'Save failed',
    urlsLocal: 'Local',
    urlsLan: 'LAN',
    urlsTailscale: 'Tailscale',
    urlsMagicDns: 'Tailscale MagicDNS',
    urlsMissingLan: 'No LAN IP detected',
    urlsMissingTs: 'No Tailscale IP detected',
    urlsCopy: 'Copy',
    urlsCopied: 'Copied',
    urlsOpen: 'Open',
    urlsLoading: 'Detecting addresses…',
  },
};

function t(key, ...args) {
  const val = i18n[state.lang]?.[key] || i18n.zh[key];
  return typeof val === 'function' ? val(...args) : val;
}

const AGENTS = ['claude', 'codex', 'antigravity'];
function agentName(a) { return t(`agent_${a || 'unknown'}`) || a; }
function agentIcon(a) {
  if (a === 'codex') {
    return '<svg class="ag-ico" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M2.5 4.2L6.8 8l-4.3 3.8M8.2 11.8h5.3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>';
  }
  if (a === 'antigravity') {
    return '<svg class="ag-ico" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M8 1.5l6.5 6.5L8 14.5 1.5 8 8 1.5zm0 3.3L4.8 8 8 11.2 11.2 8 8 4.8z" fill="currentColor"/></svg>';
  }
  if (a === 'me') {
    return '<svg class="ag-ico" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><circle cx="8" cy="5.2" r="2.5" fill="currentColor"/><path d="M3.2 13.4c0-2.4 2.1-4.1 4.8-4.1s4.8 1.7 4.8 4.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" fill="none"/></svg>';
  }
  return '<svg class="ag-ico" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M8 1.2l1.45 4.45L14 7.1l-4.55 1.45L8 13l-1.45-4.45L2 7.1l4.55-1.45L8 1.2z" fill="currentColor"/></svg>';
}
function agentBadge(a, extraCls = '') {
  const ag = a || 'claude';
  const cls = ['card-badge', 'agent', `agent-${escapeHtml(ag)}`, extraCls].filter(Boolean).join(' ');
  return `<span class="${cls}">${agentIcon(ag)}<span>${escapeHtml(agentName(ag))}</span></span>`;
}
window.agentIcon = agentIcon;
window.agentBadge = agentBadge;

// ─── DOM ────────────────────────────────────────────────────────────────────

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
const _mobileQuery = window.matchMedia('(max-width: 768px)');
function isMobile() { return _mobileQuery.matches; }

// ─── API ────────────────────────────────────────────────────────────────────

async function api(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  return res.json();
}

// ─── WebSocket ──────────────────────────────────────────────────────────────

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  state.ws = new WebSocket(`${proto}://${location.host}/ws`);

  state.ws.onopen = () => {
    $('#connStatus').classList.add('connected');
    clearInterval(state.reconnectTimer);
    setInterval(() => {
      if (state.ws?.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, 30000);
  };

  state.ws.onmessage = (event) => {
    try {
      handleWSMessage(JSON.parse(event.data));
    } catch (e) {
      console.warn('WS parse error:', e);
    }
  };

  state.ws.onclose = () => {
    $('#connStatus').classList.remove('connected');
    state.reconnectTimer = setTimeout(connectWS, 3000);
  };

  state.ws.onerror = () => { state.ws?.close(); };
}

function handleWSMessage(msg) {
  switch (msg.type) {
    case 'project_created':
      refreshProjects();
      break;
    case 'task_created':
    case 'task_update':
    case 'worker_started':
    case 'tasks_archived':
      if (!state.activeProjectId || msg.project_id === state.activeProjectId) {
        refreshTasks();
      }
      refreshProjects();
      break;
    case 'overview_changed':
      if (!state.activeProjectId && window.refreshOverview) window.refreshOverview();
      if (state.activeProjectId && state.activeView === 'list') refreshProjectListView();
      if (state.activeProjectId && window.refreshProjectLive) window.refreshProjectLive();
      if (window.onSessionChanged) window.onSessionChanged();
      break;
    case 'session_changed':
      if (window.onSessionChanged) window.onSessionChanged(msg.sid);
      break;
    case 'task_log':
      if ($('#taskModal').dataset.taskId == msg.task_id) {
        appendModalLog(msg.message, msg.timestamp);
      }
      break;
    case 'task_event':
      if ($('#taskModal').dataset.taskId == msg.task_id) {
        appendModalEvent(msg.event, msg.data, msg.timestamp);
      }
      break;
  }
}

// ─── Project Management ─────────────────────────────────────────────────────

async function refreshProjects() {
  const data = await api('/projects');
  state.projects = data.projects || [];
  if (!state.activeProjectId) {
    renderProjectList();
    if (window.refreshOverview) window.refreshOverview();
  } else {
    updateProjectUrlsButtonTitle();
  }
}

function renderProjectList() {
  // The global overview (overview.js) owns the home page once its data is in.
  if (window.renderOverview && window.renderOverview()) return;
  const grid = $('#projectGrid');
  if (state.projects.length === 0) {
    grid.innerHTML = `
      <div class="project-empty">
        <div class="project-empty-icon">📁</div>
        <p><b>${t('noProjects')}</b></p>
        <p>${t('noProjectsHint')}</p>
      </div>`;
    return;
  }

  grid.innerHTML = state.projects.map(p => {
    const stats = p.task_stats || {};
    const statBadges = [];
    if (stats.pending > 0)
      statBadges.push(`<span class="project-stat pending">${stats.pending} ${t('pending')}</span>`);
    if (stats.in_progress > 0)
      statBadges.push(`<span class="project-stat active">${stats.in_progress} ${t('in_progress')}</span>`);
    if (stats.done > 0)
      statBadges.push(`<span class="project-stat done">${stats.done} ${t('done')}</span>`);
    if (stats.failed > 0)
      statBadges.push(`<span class="project-stat failed">${stats.failed} ${t('failed')}</span>`);
    if (statBadges.length === 0)
      statBadges.push(`<span class="project-stat pending">${stats.total} ${t('tasks_label')}</span>`);

    const indicator = p.path_exists
      ? `<span class="project-card-indicator exists" title="${t('dirExists')}"></span>`
      : `<span class="project-card-indicator missing" title="${t('dirMissing')}"></span>`;

    const gitBadge = p.is_git
      ? `<span class="project-stat active">${t('isGit')}</span>` : '';

    return `
      <div class="project-card" data-project-id="${p.id}">
        <button class="project-card-delete" data-project-id="${p.id}" title="Delete">&times;</button>
        <div class="project-card-name">${indicator} ${escapeHtml(p.name)}</div>
        <div class="project-card-path">${escapeHtml(p.path)}</div>
        <div class="project-card-stats">${gitBadge}${statBadges.join('')}</div>
      </div>`;
  }).join('');

  // Click events handled by delegated listener on #projectGrid (see init)
}

function enterProject(projectId) {
  const project = state.projects.find(p => p.id === projectId);
  if (!project) return;

  resetTaskViewSwipe();
  state.activeProjectId = projectId;
  state.columnDisplayLimits = { ...DEFAULT_COLUMN_LIMITS };
  Object.assign(state.listView, {
    agent: '',
    archivedOpen: false,
    archivedSearchOverride: null,
    recentLimit: 50,
    archivedLimit: 30,
    data: null,
    key: '',
  });
  localStorage.setItem('activeProject', projectId);

  // Update UI
  $('#projectView').classList.add('hidden');
  $('#taskView').classList.remove('hidden');
  $('#currentProjectName').textContent = project.name;
  $('#currentProjectPath').textContent = project.path;
  $('#agentSelect').value = 'claude';     // a task you send runs right away; Todo is a choice
  syncAgyModel();
  updateProjectUrlsButtonTitle();
  document.title = `${project.name} - ${t('title')}`;

  switchView(state.activeView === 'analytics' ? 'list' : state.activeView);
  refreshTasks();
}

function exitProject() {
  resetTaskViewSwipe();
  closeProjectUrlsPopover();
  state.activeProjectId = null;
  localStorage.removeItem('activeProject');
  state.tasks = [];
  state.analyticsData = null;
  state.listView.data = null;
  if (state.activeView === 'analytics') switchView('list');
  $('#pvStrip').classList.add('hidden');

  $('#projectView').classList.remove('hidden');
  $('#taskView').classList.add('hidden');
  $('#headerTitle').textContent = t('title');
  document.title = t('title');

  refreshProjects();
}

function showProjectModalError(msg) {
  const el = $('#projectModalError');
  if (!el) return;
  if (!msg) {
    el.textContent = '';
    el.classList.add('hidden');
  } else {
    el.textContent = msg;
    el.classList.remove('hidden');
  }
}

function openNewProjectModal() {
  showProjectModalError('');
  $('#projectNameInput').value = '';
  $('#projectPathInput').value = '';
  $('#settingSkipPermissions').checked = false;
  $('#settingAutoCommit').checked = true;
  $('#projectSettingsGroup').classList.add('hidden');
  $('#projectModalTitle').textContent = t('createProject');
  $('#projectCreateBtn').textContent = t('create');
  $('#projectCreateBtn').dataset.mode = 'create';
  $('#projectModalOverlay').classList.add('active');
  $('#projectNameInput').focus();
}

function closeProjectModal() {
  showProjectModalError('');
  $('#dirBrowser').classList.add('hidden');
  $('#projectModalOverlay').classList.remove('active');
}

async function handleProjectCreate() {
  if ($('#projectCreateBtn').dataset.mode === 'edit') return;
  const name = $('#projectNameInput').value.trim();
  const path = $('#projectPathInput').value.trim();
  if (!name || !path) return;

  const res = await api('/projects', { method: 'POST', body: { name, path, lang: state.lang } });
  if (res && res.error) {
    showProjectModalError(res.error);
    return;
  }
  closeProjectModal();
  await refreshProjects();
}

// ─── Task Rendering ─────────────────────────────────────────────────────────

async function refreshTasks() {
  if (!state.activeProjectId) return;
  const data = await api(`/projects/${state.activeProjectId}/tasks`);
  state.tasks = data.tasks || [];
  renderBoard();
  updateStats(data.stats);
  if (state.activeView === 'list') refreshProjectListView();
  if (window.refreshProjectLive) window.refreshProjectLive();
}

const BOARD_COLUMNS = ['todo', 'pending', 'in_progress', 'wait', 'done'];
const FAILED_WAIT_MS = 3 * 86400000;

function taskAt(task) {
  return Math.max(...['completed_at', 'started_at', 'created_at']
    .map((k) => (task[k] ? new Date(task[k]).getTime() || 0 : 0)));
}

// Column of a task the board (from /live) does not know yet — same rules as board.py.
function statusColumn(task) {
  const s = task.status;
  if (s === 'review') return 'wait';
  if (s === 'failed') return Date.now() - taskAt(task) <= FAILED_WAIT_MS ? 'wait' : 'done';
  if (s === 'cancelled') return 'done';
  return BOARD_COLUMNS.includes(s) ? s : 'done';
}

// One card per line of work (board.py): a session with every task that ran in it,
// a task not started yet, or a session opened outside Jarvis.
function boardColumns() {
  const byId = new Map(state.tasks.map((t) => [t.id, t]));
  const cols = Object.fromEntries(BOARD_COLUMNS.map((c) => [c, []]));
  const seen = new Set();
  const bySid = new Map();
  const board = window.projectBoard ? window.projectBoard() : null;
  for (const c of BOARD_COLUMNS) {
    for (const card of (board && board[c]) || []) {
      const tasks = card.tasks.filter((id) => byId.has(id));
      if (card.head != null && !tasks.length) continue;   // deleted since
      const head = tasks.includes(card.head) ? card.head : tasks[tasks.length - 1] ?? null;
      const cc = { ...card, tasks, head };
      tasks.forEach((id) => seen.add(id));
      if (cc.sid) bySid.set(cc.sid, cc);
      cols[c].push(cc);
    }
  }
  // tasks newer than the board snapshot: join their session's card, or stand alone
  for (const task of state.tasks) {
    if (seen.has(task.id)) continue;
    const own = task.session_id && !task.session_linked ? bySid.get(task.session_id) : null;
    const col = task.status === 'in_progress' ? 'in_progress' : statusColumn(task);
    if (own) {
      own.tasks.push(task.id);
      const curHead = own.head != null ? byId.get(own.head) : null;
      if (curHead?.status !== 'in_progress' && task.id > (own.head ?? -1)) own.head = task.id;
      if (col === 'in_progress') {
        own.head = task.id;
        for (const c of BOARD_COLUMNS) cols[c] = cols[c].filter((x) => x !== own);
        cols.in_progress.push(own);
      } else if (col === 'pending' && !cols.in_progress.includes(own) && !cols.pending.includes(own)) {
        for (const c of BOARD_COLUMNS) cols[c] = cols[c].filter((x) => x !== own);
        cols.pending.push(own);
      }
      continue;
    }
    cols[col].push({ head: task.id, tasks: [task.id], sid: task.session_id || '', agent: task.agent || '',
                     state: '', at: taskAt(task), title: '', step: '' });
  }
  for (const c of BOARD_COLUMNS) {
    if (c === 'todo' || c === 'pending') cols[c].sort((x, y) => (x.head ?? 0) - (y.head ?? 0));
    else cols[c].sort((x, y) => y.at - x.at);
  }
  return { cols, byId };
}

function cardMatches(card, byId, query) {
  if (!query) return true;
  const texts = [card.title, ...card.tasks.map((id) => byId.get(id)?.description), ...card.tasks.map(String)];
  return texts.some((x) => (x || '').toLowerCase().includes(query));
}

function renderBoard() {
  const query = state.searchQuery.toLowerCase().trim();
  const { cols, byId } = boardColumns();
  const columns = {};
  for (const c of BOARD_COLUMNS) columns[c] = cols[c].filter((card) => cardMatches(card, byId, query));

  for (const [status, cards] of Object.entries(columns)) {
    const list = $(`#list-${status}`);
    const count = $(`#count-${status}`);
    if (!list || !count) continue;

    count.textContent = cards.length;

    if (cards.length === 0) {
      list.innerHTML = `<div class="empty-state">${t('empty')}</div>`;
      continue;
    }

    const limit = state.columnDisplayLimits[status];
    const visible = limit ? cards.slice(0, limit) : cards;
    const remaining = cards.length - visible.length;

    let html = '';

    // Stats summary card for done column
    if (status === 'done' && !query) {
      const allDone = state.tasks.filter(t => t.status === 'done');
      const weekAgo = new Date(Date.now() - 7 * 86400000).toISOString();
      const thisWeek = allDone.filter(t => (t.completed_at || '') >= weekAgo).length;
      let avgDur = 0;
      const withDur = allDone.filter(t => t.started_at && t.completed_at);
      if (withDur.length > 0) {
        const totalMs = withDur.reduce((s, t) => s + (new Date(t.completed_at) - new Date(t.started_at)), 0);
        avgDur = totalMs / withDur.length;
      }
      const fmtDur = avgDur < 60000 ? '<1m'
        : avgDur < 3600000 ? `${Math.round(avgDur / 60000)}m`
        : `${(avgDur / 3600000).toFixed(1)}h`;
      const archivedCount = state._stats?.archived || 0;
      const totalDone = allDone.length + archivedCount;
      html += `<div class="done-stats-card">
        <div class="done-stat"><span class="done-stat-val">${totalDone}</span><span class="done-stat-label">总完成</span></div>
        <div class="done-stat"><span class="done-stat-val">${thisWeek}</span><span class="done-stat-label">本周</span></div>
        <div class="done-stat"><span class="done-stat-val">${fmtDur}</span><span class="done-stat-label">平均耗时</span></div>
      </div>`;
    }

    html += visible.map((card) => (card.head != null
      ? renderCard(byId.get(card.head), card) : renderSessionCard(card))).join('');
    if (remaining > 0) {
      html += `<button class="show-more-btn" data-status="${status}">${state.lang === 'en' ? `Load more (${ visible.length} / ${cards.length})` : `下拉或点击加载更多（${visible.length} / ${cards.length}）`}</button>`;
    }
    const prevScroll = list.scrollTop;
    list.innerHTML = html;
    if (prevScroll) list.scrollTop = prevScroll;
    list.dataset.remaining = String(remaining);
    if (!list._scrollBound) {
      list._scrollBound = true;
      list.addEventListener('scroll', () => {
        const rem = parseInt(list.dataset.remaining || '0', 10);
        if (rem > 0 && list.scrollHeight - list.scrollTop - list.clientHeight < 80) {
          state.columnDisplayLimits[status] = (state.columnDisplayLimits[status] || 8) + 8;
          renderBoard();
        }
      }, { passive: true });
    }

    list.querySelectorAll('.card').forEach(card => {
      const taskId = parseInt(card.dataset.taskId);
      card.addEventListener('click', (e) => {
        if (Date.now() - _lastSwipeEnd < 260) return;
        if (e.target.closest('.card-action-btn') || e.target.closest('.card-toggle') || e.target.closest('[data-stop]')) return;
        // If this card is swiped open, close it instead of opening modal
        const wrapper = card.closest('.card-swipe-wrapper');
        if (wrapper && wrapper === _openSwipeCard) {
          closeSwipeCard(wrapper);
          return;
        }
        // Open the unified session panel for every card on the board.
        const task = byId.get(taskId);
        const sid = card.dataset.sid || (task ? `task:${state.activeProjectId}:${task.id}` : '');
        if (sid && window.openSessionPanel) {
          window.openSessionPanel(
            sid,
            card.dataset.agent || task?.agent || 'claude',
            task ? { projectId: state.activeProjectId, taskId: task.id } : {}
          );
          return;
        }
        if (task) openTaskModal(taskId);
      });
    });

    list.querySelectorAll('.card-toggle').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const card = btn.closest('.card');
        card.querySelector('.card-desc').classList.toggle('expanded');
        btn.classList.toggle('expanded');
      });
    });

    list.querySelectorAll('.card-action-btn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const taskId = parseInt(btn.dataset.taskId);
        const action = btn.dataset.action;
        const pid = state.activeProjectId;
        if (action === 'hide') {
          btn.closest('.card-swipe-wrapper')?.remove();
          await api(`/sessions/${encodeURIComponent(btn.dataset.sid)}/hide`, { method: 'POST', body: {} });
          refreshTasks();
          return;
        }
        if (action === 'retry') await api(`/projects/${pid}/tasks/${taskId}/retry`, { method: 'POST' });
        else if (action === 'cancel') await api(`/projects/${pid}/tasks/${taskId}/cancel`, { method: 'POST' });
        else if (action === 'delete') await api(`/projects/${pid}/tasks/${taskId}`, { method: 'DELETE' });
        else if (action === 'assign') await api(`/projects/${pid}/tasks/${taskId}/assign`, { method: 'POST', body: { agent: btn.dataset.agent } });
        else if (action === 'finish') await api(`/projects/${pid}/tasks/${taskId}/complete`, { method: 'POST', body: {} });
        refreshTasks();
      });
    });

    // Swipe action buttons (mobile)
    list.querySelectorAll('.swipe-action-btn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const taskId = parseInt(btn.dataset.taskId);
        const action = btn.dataset.action;
        const pid = state.activeProjectId;
        haptic();
        if (action === 'hide' && btn.dataset.sid) {
          btn.closest('.card-swipe-wrapper')?.remove();
          await api(`/sessions/${encodeURIComponent(btn.dataset.sid)}/hide`, { method: 'POST', body: {} });
          refreshTasks();
          return;
        }
        if (action === 'retry') await api(`/projects/${pid}/tasks/${taskId}/retry`, { method: 'POST' });
        else if (action === 'cancel') await api(`/projects/${pid}/tasks/${taskId}/cancel`, { method: 'POST' });
        else if (action === 'delete') await api(`/projects/${pid}/tasks/${taskId}`, { method: 'DELETE' });
        refreshTasks();
      });
    });

    list.querySelectorAll('.show-more-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const s = btn.dataset.status;
        state.columnDisplayLimits[s] = (state.columnDisplayLimits[s] || 8) + 8;
        renderBoard();
      });
    });
  }

  // Mobile: update navigation and show active column
  if (isMobile()) {
    if (!BOARD_COLUMNS.includes(state.mobileActiveTab)) state.mobileActiveTab = 'in_progress';
    // Only auto-select a non-empty tab when first entering a project, never yank
    // the user away from a tab they explicitly tapped or swiped into.
    if (_mobileTabProject !== state.activeProjectId) {
      const totalCards = BOARD_COLUMNS.reduce((n, s) => n + (columns[s]?.length || 0), 0);
      if (totalCards > 0) {
        _mobileTabProject = state.activeProjectId;
        const activeCount = columns[state.mobileActiveTab]?.length || 0;
        if (activeCount === 0) {
          const firstNonEmpty = ['in_progress', 'wait', 'todo', 'pending', 'done']
            .find(s => columns[s].length > 0);
          if (firstNonEmpty) state.mobileActiveTab = firstNonEmpty;
        }
      }
    }
    updateMobileTabs(columns);
    updateStatusOverview(columns);
    document.querySelectorAll('.board .column').forEach(col => {
      col.classList.toggle('mobile-active', col.dataset.status === state.mobileActiveTab);
    });
  }

  // Initialize card swipe gestures (mobile)
  bindCardSwipe();
}

// A session opened outside Jarvis (desktop app, terminal).
function renderSessionCard(card) {
  const ag = card.agent || 'claude';
  // running / waiting / failed: the same progress pill as the home page
  const pill = ['run', 'wait', 'error', 'limit'].includes(card.state) && window.pillHtml
    ? window.pillHtml({ agent: ag }, card) : '';
  const swipeHtml = card.state !== 'run'
    ? `<div class="card-swipe-actions">
        <button class="swipe-action-btn hide-action" data-action="hide" data-sid="${escapeHtml(card.sid)}">
          <svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round"/></svg>${t('hideCard')}
        </button></div>`
    : '';
  return `
    <div class="card-swipe-wrapper">
      ${swipeHtml}
      <div class="card card-session agent-${escapeHtml(ag)}" data-sid="${escapeHtml(card.sid)}" data-agent="${escapeHtml(ag)}">
        <div class="card-badges">${agentBadge(ag)}
          ${card.turns ? `<span class="card-badge rounds">${t('rounds', card.turns)}</span>` : ''}
          ${card.archived ? `<span class="card-badge st-cancelled">${t('archived')}</span>` : ''}
          ${window.ctxChipHtml ? window.ctxChipHtml(card) : ''}
          ${card.state !== 'run' ? `<button class="card-action-btn card-hide" data-action="hide" data-sid="${escapeHtml(card.sid)}" title="${t('hideCard')}" aria-label="${t('hideCard')}">✕</button>` : ''}</div>
        <div class="card-desc">${escapeHtml(card.title || card.sid.slice(0, 8))}</div>
        ${pill}
        <div class="card-time">${card.at ? timeAgo(new Date(card.at).toISOString()) : ''}
</div>
      </div>
    </div>`;
}

function renderCard(task, card = null) {
  const badges = [];
  const n = card ? card.tasks.length : 1;
  const effAgent = task.agent || (card && card.agent) || (task.status !== 'todo' ? 'claude' : '');
  if (effAgent) badges.push(agentBadge(effAgent));
  if (task.plan_mode) badges.push('<span class="card-badge plan">Plan</span>');
  if (task.status === 'review') badges.push(`<span class="card-badge review">${t('review')}</span>`);
  if (task.status === 'failed' || task.status === 'cancelled') badges.push(`<span class="card-badge st-${task.status}">${t(task.status)}</span>`);
  if (n > 1) badges.push(`<span class="card-badge rounds">${t('rounds', n)}</span>`);
  const hasPendingMerge = task.merge_state === 'pending' || (card && (card.tasks || []).some(x => x.merge_state === 'pending'));
  if (hasPendingMerge) badges.push(`<span class="card-badge review">${t('toMerge')}</span>`);
  if (card && card.ctx_level && window.ctxChipHtml) badges.push(window.ctxChipHtml(card));
  if ((task.images || []).length > 0) badges.push(`<span class="card-badge attachment"><svg viewBox="0 0 24 24" width="11" height="11" style="vertical-align:-1px;margin-right:2px;"><rect x="3" y="3" width="18" height="18" rx="2" stroke="currentColor" stroke-width="2" fill="none"/><circle cx="8.5" cy="8.5" r="1.5" fill="currentColor"/><path d="M21 15l-5-5L5 21" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>${task.images.length}</span>`);

  // A reply from the session panel reads under its session's title.
  const desc = card && card.title && task.source === 'session'
    ? `${escapeHtml(card.title)}<div class="card-ask">› ${escapeHtml(task.description)}</div>`
    : escapeHtml(task.description);
  const timeStr = formatTime(task);

  let actions = '';
  if (task.status === 'todo') {
    const hand = AGENTS.map(a => `<button class="card-action-btn assign agent-${a}" data-task-id="${task.id}"
        data-action="assign" data-agent="${a}">${agentIcon(a)}<span>${agentName(a)}</span></button>`).join('');
    const mine = task.agent === 'me'
      ? `<button class="card-action-btn assign finish" data-task-id="${task.id}" data-action="finish">✓ ${t('finish')}</button>`
      : `<button class="card-action-btn assign agent-me" data-task-id="${task.id}" data-action="assign" data-agent="me">${agentIcon('me')}<span>${agentName('me')}</span></button>`;
    actions = `<div class="card-assign">${hand}${mine}</div>`;
  } else if (task.status === 'failed' || task.status === 'cancelled') {
    actions = `
      <div class="card-actions">
        <button class="card-action-btn" data-task-id="${task.id}" data-action="retry" title="重试">↻</button>
        <button class="card-action-btn danger" data-task-id="${task.id}" data-action="delete" title="删除">🗑</button>
      </div>`;
  } else if (task.status === 'in_progress') {
    actions = `
      <div class="card-actions">
        <button class="card-action-btn danger" data-task-id="${task.id}" data-action="cancel" title="取消">✕</button>
      </div>`;
  }

  const spinner = task.status === 'in_progress' ? '<span class="card-spinner"></span>' : '';

  // Duration display for in_progress tasks
  let durationHtml = '';
  if (task.status === 'in_progress' && task.started_at) {
    const elapsed = (Date.now() - new Date(task.started_at).getTime()) / 1000;
    const durStr = elapsed < 60 ? `${Math.floor(elapsed)}s`
      : elapsed < 3600 ? `${Math.floor(elapsed / 60)}m`
      : `${(elapsed / 3600).toFixed(1)}h`;
    const isWarning = elapsed > 900; // 15 minutes
    durationHtml = `<div class="card-duration${isWarning ? ' warning' : ''}">
      <svg viewBox="0 0 24 24" width="12" height="12"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" fill="none"/><path d="M12 6v6l4 2" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>
      ${t('runningFor') || '已运行'} ${durStr}</div>`;
  }

  // Latest log preview for in_progress tasks
  let logPreviewHtml = '';
  if (task.status === 'in_progress' && task.logs && task.logs.length > 0) {
    const lastLog = task.logs[task.logs.length - 1];
    const logMsg = typeof lastLog === 'string' ? lastLog : (lastLog.message || lastLog.msg || '');
    if (logMsg) {
      const truncLog = escapeHtml(logMsg.length > 80 ? logMsg.slice(0, 80) + '...' : logMsg);
      logPreviewHtml = `<div class="card-log-preview">${truncLog}</div>`;
    }
  }

  // Error preview for failed tasks
  let errorPreviewHtml = '';
  if (task.status === 'failed') {
    const errMsg = task.error || (task.summary ? task.summary.split('\n')[0] : '');
    if (errMsg) {
      const truncErr = escapeHtml(errMsg.length > 80 ? errMsg.slice(0, 80) + '...' : errMsg);
      errorPreviewHtml = `<div class="card-error-preview">${truncErr}</div>`;
    }
  }

  // Live session line / "open in desktop" (overview.js, from /projects/:id/live)
  const liveHtml = window.taskLiveHtml ? window.taskLiveHtml(task) : '';

  // Pulse bar for in_progress
  const pulseHtml = task.status === 'in_progress' ? '<div class="card-pulse"></div>' : '';

  // Swipe action buttons (revealed on mobile swipe)
  let swipeHtml = '';
  if (task.status === 'pending' || task.status === 'in_progress') {
    swipeHtml = `<div class="card-swipe-actions">
      <button class="swipe-action-btn cancel-action" data-task-id="${task.id}" data-action="cancel">
        <svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round"/></svg>${t('cancelLabel')}
      </button></div>`;
  } else if (task.status === 'failed' || task.status === 'cancelled') {
    swipeHtml = `<div class="card-swipe-actions">
      <button class="swipe-action-btn retry" data-task-id="${task.id}" data-action="retry">
        <svg viewBox="0 0 24 24"><path d="M1 4v6h6M23 20v-6h-6" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M20.49 9A9 9 0 005.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 013.51 15" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>${t('retryLabel')}
      </button>
      <button class="swipe-action-btn delete-action" data-task-id="${task.id}" data-action="delete">
        <svg viewBox="0 0 24 24"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>${t('deleteLabel')}
      </button></div>`;
  }

  return `
    <div class="card-swipe-wrapper">
      ${swipeHtml}
      <div class="card ${effAgent ? `agent-${escapeHtml(effAgent)}` : ''}" data-task-id="${task.id}" data-sid="${escapeHtml(task.session_id || (card && card.sid) || '')}" data-agent="${escapeHtml(effAgent || 'claude')}">
        <div class="card-top">
          ${badges.length ? `<div class="card-badges">${badges.join('')}</div>` : ''}
          <span class="card-id">${spinner}#${task.id}</span>
          <button class="card-toggle">▾</button>
        </div>
        <div class="card-desc">${desc}</div>
        ${durationHtml}
        ${liveHtml}
        ${logPreviewHtml}
        ${errorPreviewHtml}
        ${timeStr ? `<div class="card-time">${timeStr}</div>` : ''}
        ${actions}
        ${pulseHtml}
      </div>
    </div>`;
}

function formatTime(task) {
  if (task.status === 'done' && task.completed_at) return `${t('completedAt')}: ${timeAgo(task.completed_at)}`;
  if (task.status === 'failed' && task.started_at) return `${t('failedAt')}: ${timeAgo(task.started_at)}`;
  if (task.started_at) return timeAgo(task.started_at);
  return '';
}

function timeAgo(isoStr) {
  const diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
  if (diff < 60) return t('ago', `${Math.floor(diff)}s`);
  if (diff < 3600) return t('ago', `${Math.floor(diff / 60)}m`);
  if (diff < 86400) return t('ago', `${Math.floor(diff / 3600)}h`);
  return t('ago', `${Math.floor(diff / 86400)}d`);
}

function updateStats(stats) {
  if (!stats) return;
  state._stats = stats;
}

// ─── Task Modal ─────────────────────────────────────────────────────────────

function openTaskModal(taskId, taskObj = null) {
  const task = taskObj || state.tasks.find(t => t.id === taskId);
  if (!task) return;

  const modal = $('#taskModal');
  modal.dataset.taskId = taskId;

  // Mobile viewport height fix
  if (isMobile() && window.visualViewport) {
    const setModalHeight = () => {
      modal.style.maxHeight = `${window.visualViewport.height}px`;
    };
    setModalHeight();
    window.visualViewport.addEventListener('resize', setModalHeight);
    // Store cleanup function
    modal._cleanupViewport = () => {
      window.visualViewport.removeEventListener('resize', setModalHeight);
      modal.style.maxHeight = '';
    };
  }

  const statusLabels = {
    pending: [t('pending'), ''],
    in_progress: [t('in_progress'), 'badge-blue'],
    review: [t('review'), 'badge-orange'],
    done: [t('done'), 'badge-green'],
    failed: [t('failed'), 'badge-red'],
    cancelled: [t('cancelled'), 'badge-gray'],
    archived: [t('archived'), 'badge-gray'],
  };
  const [label, cls] = statusLabels[task.status] || ['', ''];
  const badge = $('#modalStatusBadge');
  badge.textContent = label;
  badge.className = `modal-status-badge ${cls}`;

  $('#modalTitle').textContent = task.description.slice(0, 100);

  let bodyHTML = task.status === 'in_progress' && window.taskChecklistHtml ? window.taskChecklistHtml(task) : '';

  // For completed/failed tasks, show summary at top
  if (task.status === 'done' || task.status === 'failed') {
    if (task.plan_content) {
      // Plan task: show decisions and implementation status
      const questions = task.plan_questions || [];
      const answeredQs = questions.filter(q => q.answer);
      bodyHTML += `<div class="task-summary plan-summary">`;
      bodyHTML += `<h3>Plan 决策摘要</h3>`;

      // Find all successor implementation tasks
      // Prefer stored IDs; fallback: find tasks whose description references this plan
      const createdIds = task.plan_created_task_ids || [];
      const successors = createdIds.length > 0
        ? state.tasks.filter(t => createdIds.includes(t.id))
        : state.tasks.filter(t =>
            !t.plan_mode && (
              t.predecessor_id === task.id ||
              (t.description || '').includes(`[来自 Plan #${task.id}`)
            ));
      if (successors.length > 0) {
        const sLabels = {pending:'⏳ 待开发', in_progress:'🔄 开发中', done:'✅ 已完成', failed:'❌ 失败', cancelled:'🚫 已取消'};
        bodyHTML += `<div class="plan-successors">`;
        successors.forEach(s => {
          bodyHTML += `<div class="plan-successor" data-task-id="${s.id}">实现任务 <strong>#${s.id}</strong> ${sLabels[s.status] || s.status}</div>`;
        });
        bodyHTML += `</div>`;
      }

      // Show adopted vs deleted sub-tasks summary
      const originalSubtasks = task.plan_subtasks || [];
      let adoptedSubtasks = task.plan_adopted_subtasks;
      // Fallback for old tasks: infer adopted indices from successor task descriptions
      if (!adoptedSubtasks && successors.length > 0) {
        const inferredIndices = new Set();
        successors.forEach(s => {
          const m = (s.description || '').match(/\[来自 Plan #\d+ 子任务 (\d+)\]/);
          if (m) inferredIndices.add(parseInt(m[1]));
        });
        if (inferredIndices.size > 0) {
          adoptedSubtasks = originalSubtasks.filter(st => inferredIndices.has(st.index));
        }
      }
      if (!adoptedSubtasks) adoptedSubtasks = [];
      if (originalSubtasks.length > 0 && (adoptedSubtasks.length > 0 || task.plan_adopted_subtasks)) {
        const adoptedIndices = new Set(adoptedSubtasks.map(st => st.index));
        bodyHTML += `<div class="plan-subtask-summary">`;
        bodyHTML += `<div class="plan-subtask-summary-title">子任务决策</div>`;
        originalSubtasks.forEach(st => {
          const adopted = adoptedIndices.has(st.index);
          bodyHTML += `<div class="plan-subtask-status ${adopted ? 'adopted' : 'deleted'}">`;
          bodyHTML += `<span>${adopted ? '✅' : '❌'}</span> `;
          bodyHTML += `<span>${st.index}. ${escapeHtml(st.title)}</span>`;
          bodyHTML += `<span class="plan-subtask-label">${adopted ? '已采纳' : '已删除'}</span>`;
          bodyHTML += `</div>`;
        });
        bodyHTML += `</div>`;
      }

      // Show user's answers to questions
      if (answeredQs.length > 0) {
        bodyHTML += `<div class="plan-answers">`;
        answeredQs.forEach(q => {
          bodyHTML += `<div class="plan-answer-item">`;
          bodyHTML += `<span class="plan-q">${escapeHtml(q.question)}</span>`;
          bodyHTML += `<span class="plan-a">${escapeHtml(q.answer)}</span>`;
          bodyHTML += `</div>`;
        });
        bodyHTML += `</div>`;
      } else if (questions.length > 0) {
        bodyHTML += `<div class="plan-no-answers">${questions.length} 个问题（未记录回答）</div>`;
      }
      bodyHTML += `</div>`;
    } else {
      // Normal task: show output summary (prefer task.summary field, fallback to [output] log)
      let summaryText = task.summary || '';
      if (!summaryText) {
        const logs = task.logs || [];
        const outputLog = [...logs].reverse().find(l => (l.message || '').startsWith('[output] ') || l.type === 'result');
        if (outputLog) summaryText = outputLog.message ? outputLog.message.slice(9) : (outputLog.content || '');
      }
      if (summaryText) {
        bodyHTML += `<div class="task-summary"><h3>完成总结</h3><div class="md-content">${renderMarkdown(summaryText)}</div></div>`;
      }
    }
  }

  bodyHTML += `<h3>${t('description')}</h3><div class="md-content">${renderMarkdown(task.description)}</div>`;

  // Show attached images
  const images = task.images || [];
  if (images.length > 0) {
    bodyHTML += `<div class="modal-images">`;
    for (const img of images) {
      bodyHTML += `<a class="modal-image-link" href="javascript:void(0)" onclick="openLightbox('${escapeHtml(img.url)}')"><img src="${escapeHtml(img.url)}" alt="attachment"></a>`;
    }
    bodyHTML += `</div>`;
  }

  if (task.plan_content) bodyHTML += `<h3>${t('plan')}</h3><div class="md-content">${renderMarkdown(task.plan_content)}</div>`;

  // Sub-tasks extracted from plan (editable in review mode)
  const subtasks = task.plan_subtasks || [];
  if (subtasks.length > 0) {
    const isReviewMode = task.status === 'review';
    bodyHTML += `<h3>子任务列表 (${subtasks.length})</h3>`;
    bodyHTML += `<div class="subtask-list" id="subtaskList">`;
    subtasks.forEach((st, i) => {
      const deps = (st.depends_on || []).map(d => `#${d}`).join(', ') || '无';
      const files = (st.files || []).join(', ') || '-';
      bodyHTML += `<div class="subtask-card" data-index="${i}">
        <div class="subtask-header">
          <span class="subtask-idx">${st.index}</span>
          ${isReviewMode
            ? `<input class="subtask-title-input" value="${escapeHtml(st.title)}" data-field="title" data-i="${i}">`
            : `<span class="subtask-title">${escapeHtml(st.title)}</span>`}
          ${isReviewMode ? `<button class="subtask-del" data-i="${i}" title="删除">✕</button>` : ''}
        </div>
        <div class="subtask-body">
          ${isReviewMode
            ? `<textarea class="subtask-desc-input" data-field="description" data-i="${i}" rows="2">${escapeHtml(st.description)}</textarea>`
            : `<div class="subtask-desc">${escapeHtml(st.description)}</div>`}
          <div class="subtask-meta">
            <span>📁 ${escapeHtml(files)}</span>
            <span>🔗 依赖: ${escapeHtml(deps)}</span>
          </div>
        </div>
      </div>`;
    });
    bodyHTML += `</div>`;
    if (isReviewMode) {
      bodyHTML += `<div class="subtask-actions">
        <button class="btn btn-small" id="addSubtaskBtn">+ 添加子任务</button>
      </div>`;
    }
  }

  if (task.error) bodyHTML += `<h3>${t('error')}</h3><pre style="color:var(--red);">${escapeHtml(task.error)}</pre>`;

  // Interactive questions from Plan Mode (Step 9)
  const isReview = task.status === 'review';
  if (task.plan_questions && task.plan_questions.length > 0) {
    const headerText = isReview ? 'Claude 的问题（请回答后再确认）' : 'Claude 的问题';
    bodyHTML += `<div class="plan-questions-header">
      <svg viewBox="0 0 24 24" width="18" height="18" style="vertical-align:middle;margin-right:6px;">
        <circle cx="12" cy="12" r="10" stroke="var(--primary)" stroke-width="2" fill="none"/>
        <path d="M9 9a3 3 0 115.12 2.12c-.58.59-1.12 1.04-1.12 1.88v.5" stroke="var(--primary)" stroke-width="2" fill="none" stroke-linecap="round"/>
        <circle cx="12" cy="17" r="0.5" fill="var(--primary)"/>
      </svg>
      ${headerText}
    </div>`;
    const disabledAttr = isReview ? '' : ' disabled';
    task.plan_questions.forEach((q, qi) => {
      bodyHTML += `<div class="question-card" data-question-index="${qi}">`;
      bodyHTML += `<div class="q-label">${escapeHtml(q.question)}</div>`;
      if (q.type === 'text') {
        bodyHTML += `<input type="text" class="form-input q-text-input" data-qi="${qi}" placeholder="输入你的回答..." value="${escapeHtml(q.answer || '')}"${disabledAttr}>`;
      } else if (q.choices && q.choices.length === 1) {
        // Single suggestion — show it with accept + custom option
        const suggestion = q.choices[0];
        const answered = q.answer || '';
        const isAccepted = answered === suggestion;
        bodyHTML += `<div class="q-suggestion">${escapeHtml(suggestion)}</div>`;
        bodyHTML += `<div class="choice-grid">`;
        bodyHTML += `<button class="choice-btn${isAccepted ? ' selected' : ''}" data-qi="${qi}" data-choice="${escapeHtml(suggestion)}"${disabledAttr}>同意建议</button>`;
        bodyHTML += `</div>`;
        bodyHTML += `<input type="text" class="form-input q-text-input q-custom" data-qi="${qi}" placeholder="或输入自定义回答..." value="${!isAccepted ? escapeHtml(answered) : ''}"${disabledAttr}>`;
      } else {
        bodyHTML += `<div class="choice-grid">`;
        (q.choices || []).forEach(choice => {
          const sel = q.answer === choice ? ' selected' : '';
          bodyHTML += `<button class="choice-btn${sel}" data-qi="${qi}" data-choice="${escapeHtml(choice)}"${disabledAttr}>${escapeHtml(choice)}</button>`;
        });
        bodyHTML += `</div>`;
        bodyHTML += `<input type="text" class="form-input q-text-input q-custom" data-qi="${qi}" placeholder="或输入自定义回答..."${disabledAttr}>`;
      }
      bodyHTML += `</div>`;
    });
  }

  const diagStart = bodyHTML.length;
  // Diagnostics (parsed from [DIAG] log entries)
  const diagLog = (task.logs || []).find(l => (l.message || '').startsWith('[DIAG] ') || l.type === 'diag');
  if (diagLog) {
    try {
      const diagStr = diagLog.message ? diagLog.message.slice(7) : (diagLog.content || '{}');
      const diag = JSON.parse(diagStr);
      bodyHTML += `<h3>Diagnostics</h3><div class="diag-grid">`;
      bodyHTML += `<div class="diag-item"><span class="diag-label">Model</span><span class="diag-value">${escapeHtml(diag.model || '-')}</span></div>`;
      bodyHTML += `<div class="diag-item"><span class="diag-label">Turns</span><span class="diag-value">${diag.turns || 0}</span></div>`;
      bodyHTML += `<div class="diag-item"><span class="diag-label">Cost</span><span class="diag-value">$${(diag.total_cost_usd || 0).toFixed(4)}</span></div>`;
      bodyHTML += `<div class="diag-item"><span class="diag-label">Tool Calls</span><span class="diag-value">${diag.total_tool_calls || 0}</span></div>`;
      bodyHTML += `</div>`;
      // Tool usage breakdown
      if (diag.tool_summary && Object.keys(diag.tool_summary).length > 0) {
        bodyHTML += `<h3>Tool Usage</h3><div class="tool-summary">`;
        for (const [name, stats] of Object.entries(diag.tool_summary)) {
          const errClass = stats.errors > 0 ? ' has-errors' : '';
          bodyHTML += `<div class="tool-stat${errClass}"><span class="tool-name">${escapeHtml(name)}</span>`;
          bodyHTML += `<span class="tool-count">${stats.count}x</span>`;
          if (stats.errors > 0) bodyHTML += `<span class="tool-errors">${stats.errors} err</span>`;
          bodyHTML += `</div>`;
        }
        bodyHTML += `</div>`;
      }
      // Problems detected
      if (diag.problems && diag.problems.length > 0) {
        bodyHTML += `<h3>Problems Detected</h3><div class="diag-problems">`;
        for (const p of diag.problems) {
          bodyHTML += `<div class="diag-problem">${escapeHtml(p)}</div>`;
        }
        bodyHTML += `</div>`;
      }
    } catch (e) { /* ignore parse errors */ }
  }

  // Logs — structured rendering with filter
  const allLogs = (task.logs || []).filter(l => (l.type || '') !== 'diag');
  if (allLogs.length > 0) {
    bodyHTML += `<h3>${t('taskLog')}</h3>`;
    bodyHTML += `<div class="log-filters">
      <button class="log-filter active" data-filter="all">全部</button>
      <button class="log-filter" data-filter="tool">工具</button>
      <button class="log-filter" data-filter="message">消息</button>
      <button class="log-filter" data-filter="error">错误</button>
    </div>`;
    bodyHTML += `<div id="modalLogList">`;
    for (const log of allLogs.slice(-50)) {
      bodyHTML += renderLogEntry(log);
    }
    bodyHTML += '</div>';
  }
  // With a session on disk, the conversation replaces the raw log; the log and
  // diagnostics stay available, folded.
  if (task.session_id && window.mountTaskConversation) {
    const rest = bodyHTML.slice(diagStart);
    bodyHTML = bodyHTML.slice(0, diagStart) + `<h3>${window.convTitle()}</h3><div id="modalConv"></div>` +
      (rest ? `<details class="modal-more"><summary>${t('taskLog')}</summary>${rest}</details>` : '');
  }
  $('#modalBody').innerHTML = bodyHTML;
  if (task.session_id && window.mountTaskConversation) {
    window.mountTaskConversation($('#modalConv'), task.session_id, task.agent || 'claude',
                                 task.session_linked ? task.id : null);
  }

  // Log filter buttons
  document.querySelectorAll('.log-filter').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.log-filter').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const filter = btn.dataset.filter;
      document.querySelectorAll('#modalLogList .log-entry').forEach(entry => {
        const group = entry.dataset.logGroup;
        entry.style.display = (filter === 'all' || group === filter) ? '' : 'none';
      });
    });
  });

  // Sub-task editing (review mode)
  state.editedSubtasks = task.plan_subtasks ? JSON.parse(JSON.stringify(task.plan_subtasks)) : [];
  document.querySelectorAll('.subtask-title-input, .subtask-desc-input').forEach(input => {
    input.addEventListener('input', () => {
      const i = parseInt(input.dataset.i);
      const field = input.dataset.field;
      if (state.editedSubtasks[i]) {
        state.editedSubtasks[i][field] = input.value;
        // Sync back to task data
        const taskData = state.tasks.find(t => t.id === task.id);
        if (taskData) taskData.plan_subtasks = state.editedSubtasks;
      }
    });
  });
  document.querySelectorAll('.subtask-del').forEach(btn => {
    btn.addEventListener('click', () => {
      const i = parseInt(btn.dataset.i);
      state.editedSubtasks.splice(i, 1);
      // Re-index remaining subtasks
      state.editedSubtasks.forEach((st, j) => st.index = j + 1);
      // Sync back to task data so re-render picks it up
      const taskData = state.tasks.find(t => t.id === task.id);
      if (taskData) taskData.plan_subtasks = state.editedSubtasks;
      openTaskModal(task.id);
    });
  });
  const addBtn = document.getElementById('addSubtaskBtn');
  if (addBtn) {
    addBtn.addEventListener('click', () => {
      const nextIdx = state.editedSubtasks.length + 1;
      state.editedSubtasks.push({
        index: nextIdx, title: '新子任务', description: '',
        files: [], depends_on: []
      });
      // Update task data so re-render sees it
      const t = state.tasks.find(t => t.id === task.id);
      if (t) t.plan_subtasks = state.editedSubtasks;
      openTaskModal(task.id);
    });
  }

  const hasPlanQuestions = task.plan_questions && task.plan_questions.length > 0;
  if (task.status === 'review') {
    $('#modalApprove').classList.remove('hidden');
    $('#modalFeedback').classList.remove('hidden');
    $('#modalReopen').classList.add('hidden');
    $('#modalDelete').classList.remove('hidden');
  } else if (task.status === 'done') {
    // Done task — allow re-opening
    $('#modalApprove').classList.add('hidden');
    $('#modalFeedback').classList.add('hidden');
    $('#modalReopen').classList.remove('hidden');
    $('#modalReopen').textContent = hasPlanQuestions ? '重新回答' : '重新打开';
    $('#modalDelete').classList.add('hidden');
  } else {
    $('#modalApprove').classList.add('hidden');
    $('#modalFeedback').classList.add('hidden');
    $('#modalReopen').classList.add('hidden');
    $('#modalDelete').classList.add('hidden');
  }

  $('#modalOverlay').classList.add('active');

  // Bind successor task link (click to open that task)
  document.querySelectorAll('#modalBody .plan-successor[data-task-id]').forEach(el => {
    el.addEventListener('click', () => {
      const sid = parseInt(el.dataset.taskId);
      if (sid) { closeModal(); setTimeout(() => openTaskModal(sid), 200); }
    });
  });

  // Bind choice buttons for plan questions
  state.planAnswers = {};

  // Choice button answers
  document.querySelectorAll('#modalBody .choice-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const qi = btn.dataset.qi;
      const choice = btn.dataset.choice;
      btn.closest('.choice-grid').querySelectorAll('.choice-btn').forEach(b => {
        b.classList.remove('selected');
      });
      btn.classList.add('selected');
      state.planAnswers[qi] = choice;
    });
  });

  // Free text input answers
  document.querySelectorAll('#modalBody .q-text-input').forEach(input => {
    input.addEventListener('input', () => {
      state.planAnswers[input.dataset.qi] = input.value;
    });
  });
}

function appendModalLog(message, timestamp) {
  const logList = document.getElementById('modalLogList');
  if (!logList) return;
  const time = new Date(timestamp).toLocaleTimeString();
  const div = document.createElement('div');
  div.className = 'log-entry';
  div.innerHTML = `<span class="log-time">${time}</span>${escapeHtml(message)}`;
  logList.appendChild(div);
  logList.scrollTop = logList.scrollHeight;
}

function closeModal() {
  const modal = $('#taskModal');
  if (modal._cleanupViewport) {
    modal._cleanupViewport();
    delete modal._cleanupViewport;
  }
  $('#modalOverlay').classList.remove('active');
  $('#taskModal').dataset.taskId = '';
  $('#taskModal').style.transform = '';
}

function showConfirm(message, opts = {}) {
  return new Promise((resolve) => {
    const overlay = $('#confirmModalOverlay');
    const okBtn = $('#confirmModalOk');
    $('#confirmModalMessage').textContent = message;
    okBtn.textContent = opts.okText || t('confirmOk');
    okBtn.classList.toggle('btn-primary', Boolean(opts.primary));
    okBtn.classList.toggle('btn-danger', !opts.primary);
    $('#confirmModalCancel').textContent = t('cancel');
    overlay.classList.add('active');

    function cleanup(result) {
      overlay.classList.remove('active');
      okBtn.removeEventListener('click', onOk);
      $('#confirmModalCancel').removeEventListener('click', onCancel);
      overlay.removeEventListener('click', onOverlay);
      resolve(result);
    }
    function onOk() { cleanup(true); }
    function onCancel() { cleanup(false); }
    function onOverlay(e) { if (e.target === overlay) cleanup(false); }

    okBtn.addEventListener('click', onOk);
    $('#confirmModalCancel').addEventListener('click', onCancel);
    overlay.addEventListener('click', onOverlay);
  });
}
window.showConfirm = showConfirm;

// ─── Theme & Language ───────────────────────────────────────────────────────

function toggleTheme() {
  state.theme = state.theme === 'light' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', state.theme);
  localStorage.setItem('theme', state.theme);
}

function toggleLang() {
  state.lang = state.lang === 'zh' ? 'en' : 'zh';
  localStorage.setItem('lang', state.lang);
  $('#langLabel').textContent = state.lang === 'zh' ? '中' : 'EN';
  applyLanguage();
  if (state.activeProjectId) refreshTasks();
  else renderProjectList();
}

function applyLanguage() {
  $('#taskInput').placeholder = t('addTask');
  // submitBtn now uses SVG icon, no text to update
  $('#modalApprove').textContent = t('approve');
  $('#modalFeedback').textContent = t('feedback');
  $('#modalFullscreen').textContent = t('exitFull');
  $('#projectViewTitle').textContent = t('selectProject');
  $('#newProjectBtn').textContent = t('newProject');
  $('#labelProjectName').textContent = t('projectName');
  $('#labelProjectPath').textContent = t('projectPath');
  $('#pathHint').textContent = t('pathHint');
  $('#projectCancelBtn').textContent = t('cancel');


  $('#headerTitle').textContent = t('title');
  if (!state.activeProjectId) {
    document.title = t('title');
  } else {
    document.title = `${$('#currentProjectName').textContent} - ${t('title')}`;
  }

  document.querySelectorAll('.column').forEach((col) => {
    const title = col.querySelector('.column-title');
    if (title && col.dataset.status) title.textContent = t(`col_${col.dataset.status}`);
  });
  document.querySelectorAll('#agentSelect option').forEach((o) => {
    o.textContent = o.value === 'todo' ? agentName('todo') : o.value === 'me' ? t('handMe') : t('handTo', agentName(o.value));
  });

  // Analytics view labels
  applyAnalyticsLanguage();
  if (state.activeView === 'analytics' && state.analyticsData) {
    renderAnalytics(state.analyticsData);
  }
  applyProjectUrlsLanguage();
}

// ─── Image Upload ────────────────────────────────────────────────────────────

const MAX_TASK_IMAGES = 5;

async function uploadImageFile(file) {
  if (!state.activeProjectId) return null;
  if (state.uploadedImages.length >= MAX_TASK_IMAGES) return null;

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch(`/api/projects/${state.activeProjectId}/uploads`, {
      method: 'POST',
      body: formData,
    });
    const data = await res.json();
    if (data.error) { console.warn('Upload error:', data.error); return null; }
    return { url: data.url, path: data.path };
  } catch (e) {
    console.error('Upload failed:', e);
    return null;
  }
}

async function handleImageFiles(files) {
  for (const file of files) {
    if (state.uploadedImages.length >= MAX_TASK_IMAGES) break;
    if (!file.type.startsWith('image/')) continue;
    const result = await uploadImageFile(file);
    if (result) {
      state.uploadedImages.push(result);
      renderImagePreviews();
    }
  }
}

function renderImagePreviews() {
  const bar = $('#imagePreviewBar');
  if (state.uploadedImages.length === 0) {
    bar.classList.add('hidden');
    bar.innerHTML = '';
    return;
  }
  bar.classList.remove('hidden');
  bar.innerHTML = state.uploadedImages.map((img, i) => `
    <div class="image-thumb">
      <img src="${escapeHtml(img.url)}" alt="upload" onclick="openLightbox('${escapeHtml(img.url)}')">
      <button class="image-thumb-remove" data-index="${i}">&times;</button>
    </div>
  `).join('');

  bar.querySelectorAll('.image-thumb-remove').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const idx = parseInt(btn.dataset.index);
      state.uploadedImages.splice(idx, 1);
      renderImagePreviews();
    });
  });
}

// ─── Auto-resize textarea ───────────────────────────────────────────────────

function autoResizeTaskInput() {
  const el = $('#taskInput');
  el.style.height = 'auto';
  el.style.height = el.scrollHeight + 'px';
}

// ─── Task Submission ────────────────────────────────────────────────────────

// Antigravity runs on Hydrogen; the model picker shows only when it is chosen
function syncAgyModel() {
  $('#agyModelSelect').classList.toggle('hidden', $('#agentSelect').value !== 'antigravity');
}

async function submitTask() {
  if (!state.activeProjectId) return;
  const input = $('#taskInput');
  const description = input.value.trim();
  if (!description) return;

  const agent = $('#agentSelect').value || 'todo';
  const agy_model = agent === 'antigravity' ? $('#agyModelSelect').value : '';
  $('#agyModelSelect').value = '';        // Argon is picked for one task at a time
  const prevImages = [...state.uploadedImages];
  const images = prevImages.map(img => ({ url: img.url, path: img.path }));

  // Optimistically clear input immediately
  input.value = '';
  input.style.height = 'auto';
  input.rows = 1;
  state.uploadedImages = [];
  renderImagePreviews();
  if (isMobile()) closeMobileInput();

  try {
    const res = await api(`/projects/${state.activeProjectId}/tasks`, {
      method: 'POST',
      body: { description, images, agent, agy_model },
    });
    if (res?.error) throw new Error(res.error);
    refreshTasks();
  } catch (err) {
    input.value = input.value ? `${description}\n${input.value}` : description;
    autoResizeTaskInput();
    if (!state.uploadedImages.length && prevImages.length) {
      state.uploadedImages = prevImages;
      renderImagePreviews();
    }
    showToast(err?.message || '提交失败', 'error');
  }
}

// ─── Event Bindings ─────────────────────────────────────────────────────────

function bindEvents() {
  $('#agentSelect').addEventListener('change', syncAgyModel);
  // Task submit
  $('#submitBtn').addEventListener('click', submitTask);
  $('#taskInput').addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); submitTask(); }
  });

  // Auto-resize textarea (content-driven, no focus/blur expansion)
  $('#taskInput').addEventListener('input', autoResizeTaskInput);

  // Image upload
  $('#imageUploadBtn').addEventListener('click', () => {
    if (state.uploadedImages.length >= MAX_TASK_IMAGES) return;
    $('#imageFileInput').click();
  });
  $('#imageFileInput').addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
      handleImageFiles(Array.from(e.target.files));
      e.target.value = '';
    }
  });

  // Paste images into textarea
  $('#taskInput').addEventListener('paste', (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const imageFiles = [];
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile();
        if (file) imageFiles.push(file);
      }
    }
    if (imageFiles.length > 0) {
      e.preventDefault();
      handleImageFiles(imageFiles);
    }
  });

  // Drag & drop images onto input section
  const inputSection = $('.input-section');
  inputSection.addEventListener('dragover', (e) => {
    e.preventDefault();
    inputSection.classList.add('drag-over');
  });
  inputSection.addEventListener('dragleave', () => {
    inputSection.classList.remove('drag-over');
  });
  inputSection.addEventListener('drop', (e) => {
    e.preventDefault();
    inputSection.classList.remove('drag-over');
    const files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/'));
    if (files.length > 0) handleImageFiles(files);
  });

  // Archive
  $('#archiveBtn').addEventListener('click', promptArchiveOldTasks);

  // Search
  let searchTimer;
  $('#searchInput').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    const val = e.target.value;
    $('#searchClear').classList.toggle('hidden', !val);
    searchTimer = setTimeout(() => {
      state.searchQuery = val;
      state.columnDisplayLimits = { ...DEFAULT_COLUMN_LIMITS };
      state.listView.recentLimit = 50;
      state.listView.archivedLimit = 30;
      state.listView.archivedSearchOverride = null;
      if (state.activeView === 'list') refreshProjectListView();
      else renderBoard();
    }, 200);
  });
  $('#searchClear').addEventListener('click', () => {
    $('#searchInput').value = '';
    $('#searchClear').classList.add('hidden');
    state.searchQuery = '';
    state.columnDisplayLimits = { ...DEFAULT_COLUMN_LIMITS };
    state.listView.recentLimit = 50;
    state.listView.archivedLimit = 30;
    state.listView.archivedSearchOverride = null;
    if (state.activeView === 'list') refreshProjectListView();
    else renderBoard();
  });

  window.addEventListener('scroll', () => {
    if (!isMobile() || !state.activeProjectId || state.activeView !== 'board') return;
    const s = state.mobileActiveTab;
    const list = $(`#list-${s}`);
    if (!list) return;
    const rem = parseInt(list.dataset.remaining || '0', 10);
    if (rem > 0 && document.documentElement.scrollHeight - window.scrollY - window.innerHeight < 120) {
      state.columnDisplayLimits[s] = (state.columnDisplayLimits[s] || 8) + 8;
      renderBoard();
    }
  }, { passive: true });

  // Theme & Language
  $('#themeToggle').addEventListener('click', toggleTheme);
  $('#langToggle').addEventListener('click', toggleLang);

  // Task Modal
  $('#modalClose').addEventListener('click', closeModal);
  $('#modalOverlay').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeModal();
  });
  $('#modalFullscreen').addEventListener('click', closeModal);

  $('#modalApprove').addEventListener('click', async () => {
    const taskId = parseInt($('#taskModal').dataset.taskId);
    if (taskId && state.activeProjectId) {
      await api(`/projects/${state.activeProjectId}/tasks/${taskId}/approve`, {
        method: 'POST',
        body: {
          answers: state.planAnswers || {},
          subtasks: state.editedSubtasks.length > 0 ? state.editedSubtasks : undefined,
        },
      });
      state.planAnswers = {};
      closeModal();
      refreshTasks();
    }
  });

  $('#modalFeedback').addEventListener('click', () => {
    const existing = $('#modalBody .plan-feedback-area');
    if (existing) {
      existing.querySelector('textarea').focus();
      return;
    }
    const area = document.createElement('div');
    area.className = 'plan-feedback-area';
    area.innerHTML = `
      <textarea class="plan-feedback-input" placeholder="输入你的反馈，Claude 会在原方案基础上修改..." rows="3"></textarea>
      <div class="plan-feedback-actions">
        <button class="btn btn-primary plan-feedback-submit">提交反馈</button>
        <button class="btn btn-ghost plan-feedback-cancel">取消</button>
      </div>
    `;
    $('#modalBody').appendChild(area);
    const textarea = area.querySelector('textarea');
    textarea.focus();

    area.querySelector('.plan-feedback-cancel').addEventListener('click', () => area.remove());
    area.querySelector('.plan-feedback-submit').addEventListener('click', async () => {
      const feedback = textarea.value.trim();
      if (!feedback) return;
      const taskId = parseInt($('#taskModal').dataset.taskId);
      const res = await api(`/projects/${state.activeProjectId}/tasks/${taskId}/revise`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ feedback }),
      });
      if (res && res.success) {
        closeModal();
        refreshTasks();
      }
    });
  });

  // Reopen done plan task for re-answering
  $('#modalReopen').addEventListener('click', async () => {
    const taskId = parseInt($('#taskModal').dataset.taskId);
    if (taskId && state.activeProjectId) {
      // Reset task status back to review
      await api(`/projects/${state.activeProjectId}/tasks/${taskId}/reopen`, { method: 'POST' });
      closeModal();
      refreshTasks();
    }
  });

  // Delete task from modal
  $('#modalDelete').addEventListener('click', async () => {
    const taskId = parseInt($('#taskModal').dataset.taskId);
    if (taskId && state.activeProjectId && await showConfirm(t('deleteTaskConfirm'))) {
      await api(`/projects/${state.activeProjectId}/tasks/${taskId}`, { method: 'DELETE' });
      closeModal();
      refreshTasks();
    }
  });

  // Project navigation
  const goHome = () => {
    if (window.closeSessionPanel && document.body.classList.contains('sp-open')) {
      window.closeSessionPanel();
    }
    if (state.activeProjectId) {
      exitProject();
    } else {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  };
  $('#homeLogo')?.addEventListener('click', goHome);
  $('#homeLogo')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      goHome();
    }
  });
  $('#backToProjects').addEventListener('click', exitProject);

  // Project card clicks — delegated so they survive DOM re-renders
  $('#projectGrid').addEventListener('click', async (e) => {
    const deleteBtn = e.target.closest('.project-card-delete');
    if (deleteBtn) {
      e.stopPropagation();
      if (!await showConfirm(t('deleteConfirm'))) return;
      const id = parseInt(deleteBtn.dataset.projectId);
      await api(`/projects/${id}`, { method: 'DELETE' });
      refreshProjects();
      return;
    }
    const card = e.target.closest('.project-card');
    if (card) {
      const id = parseInt(card.dataset.projectId);
      enterProject(id);
    }
  });

  // Discover projects
  $('#discoverBtn').addEventListener('click', openDiscoverModal);
  $('#discoverModalClose').addEventListener('click', closeDiscoverModal);
  $('#discoverCancelBtn').addEventListener('click', closeDiscoverModal);
  $('#discoverModalOverlay').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeDiscoverModal();
  });
  $('#discoverImportBtn').addEventListener('click', handleDiscoverImport);

  // Project modal
  $('#newProjectBtn').addEventListener('click', openNewProjectModal);
  $('#projectModalClose').addEventListener('click', closeProjectModal);
  $('#projectCancelBtn').addEventListener('click', closeProjectModal);
  $('#projectModalOverlay').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeProjectModal();
  });
  $('#projectCreateBtn').addEventListener('click', handleProjectCreate);

  // Enter key in project modal inputs
  $('#projectNameInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); $('#projectPathInput').focus(); }
  });
  $('#projectPathInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); handleProjectCreate(); }
  });

  // Directory browser
  $('#browseBtn').addEventListener('click', toggleDirBrowser);
  $('#dirSelectBtn').addEventListener('click', selectCurrentDir);

  // Project settings (edit)
  $('#projectSettingsBtn').addEventListener('click', () => {
    const project = state.projects.find(p => p.id === state.activeProjectId);
    if (!project) return;
    showProjectModalError('');
    $('#projectNameInput').value = project.name;
    $('#projectPathInput').value = project.path;
    // Show settings in edit mode
    const settings = project.settings || {};
    $('#settingSkipPermissions').checked = !!settings.skip_permissions;
    $('#settingAutoCommit').checked = settings.auto_commit !== false;
    $('#settingTestCommand').value = settings.test_command || '';
    $('#settingWorktreeSetup').value = settings.worktree_setup || '';
    $('#settingMaxParallel').value = settings.max_parallel || '';
    $('#settingDevPort').value = settings.dev_port || '';
    $('#projectSettingsGroup').classList.remove('hidden');
    $('#projectModalTitle').textContent = t('editProject');
    $('#projectCreateBtn').textContent = t('save');
    $('#projectCreateBtn').dataset.mode = 'edit';
    $('#projectModalOverlay').classList.add('active');
    $('#projectNameInput').focus();
  });

  // Override create button for edit mode
  $('#projectCreateBtn').addEventListener('click', async () => {
    if ($('#projectCreateBtn').dataset.mode === 'edit') {
      const name = $('#projectNameInput').value.trim();
      const path = $('#projectPathInput').value.trim();
      if (!name || !path || !state.activeProjectId) return;
      const maxPar = parseInt($('#settingMaxParallel').value, 10);
      const rawDevPortStr = $('#settingDevPort').value.trim();
      let devPort = null;
      if (rawDevPortStr) {
        if (!/^\d+$/.test(rawDevPortStr)) {
          showProjectModalError(t('urlsPortInvalid'));
          $('#settingDevPort').focus();
          return;
        }
        devPort = parseInt(rawDevPortStr, 10);
        if (devPort < 1 || devPort > 65535) {
          showProjectModalError(t('urlsPortInvalid'));
          $('#settingDevPort').focus();
          return;
        }
      }
      const settings = {
        skip_permissions: $('#settingSkipPermissions').checked,
        auto_commit: $('#settingAutoCommit').checked,
        test_command: $('#settingTestCommand').value.trim(),
        worktree_setup: $('#settingWorktreeSetup').value.trim(),
        max_parallel: Number.isFinite(maxPar) ? Math.max(1, Math.min(5, maxPar)) : 3,
        dev_port: devPort,
      };
      const res = await api(`/projects/${state.activeProjectId}`, {
        method: 'PUT', body: { name, path, settings },
      });
      if (!res || res.error) {
        showProjectModalError(res?.error || t('saveFailed'));
        return;
      }
      showProjectModalError('');
      closeProjectModal();
      await refreshProjects();
      const updated = state.projects.find(p => p.id === state.activeProjectId);
      if (updated) {
        $('#currentProjectName').textContent = updated.name;
        $('#currentProjectPath').textContent = updated.path;
        document.title = `${updated.name} - ${t('title')}`;
        updateProjectUrlsButtonTitle();
      }
      if (!$('#projectUrlsPopover')?.classList.contains('hidden')) {
        renderProjectUrls();
      }
    }
  });

  // Project access URLs popover
  $('#projectUrlsBtn').addEventListener('click', (e) => {
    e.stopPropagation();
    toggleProjectUrlsPopover();
  });
  $('#projectDevPortSave').addEventListener('click', saveProjectDevPortFromPopover);
  $('#projectDevPortAuto').addEventListener('click', () => saveProjectDevPort(null));
  $('#projectDevPortInput').addEventListener('input', () => showProjectUrlsError(''));
  $('#projectDevPortInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      saveProjectDevPortFromPopover();
    }
  });
  $('#projectUrlsList').addEventListener('click', async (e) => {
    const copyBtn = e.target.closest('[data-copy-url]');
    if (!copyBtn) return;
    const url = copyBtn.dataset.copyUrl;
    if (!url) return;
    const ok = await copyText(url);
    if (ok) {
      copyBtn.textContent = t('urlsCopied');
      copyBtn.classList.add('copied');
      setTimeout(() => {
        copyBtn.textContent = t('urlsCopy');
        copyBtn.classList.remove('copied');
      }, 1400);
    }
  });
  document.addEventListener('click', (e) => {
    const pop = $('#projectUrlsPopover');
    if (!pop || pop.classList.contains('hidden')) return;
    if (!e.target.closest('#projectUrlsWrap')) {
      closeProjectUrlsPopover();
    }
  });

  // Markdown file editor modal (kept for programmatic / escape handling)
  $('#mdEditorClose').addEventListener('click', closeMdEditor);
  $('#mdEditorCancel').addEventListener('click', closeMdEditor);
  $('#mdEditorOverlay').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeMdEditor();
  });
  $('#mdEditorSave').addEventListener('click', saveMdEditor);

  // Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeModal();
      closeProjectModal();
      closeMdEditor();
      closeProjectUrlsPopover();
      if (isMobile()) closeMobileInput();
    }
  });

  // Mobile FAB & Input
  $('#mobileFab').addEventListener('click', openMobileInput);
  $('#mobileInputOverlay').addEventListener('click', closeMobileInput);
}

// ─── Log Helpers ────────────────────────────────────────────────────────────

const LOG_TYPE_CONFIG = {
  init:       { icon: '⚡', cls: 'log-init',   group: 'all' },
  message:    { icon: '💬', cls: 'log-msg',    group: 'message' },
  tool_use:   { icon: '🔧', cls: 'log-tool',   group: 'tool' },
  tool_ok:    { icon: '✅', cls: 'log-tool-ok', group: 'tool' },
  tool_error: { icon: '❌', cls: 'log-error',  group: 'error' },
  result:     { icon: '🏁', cls: 'log-result', group: 'all' },
  git:        { icon: '🔀', cls: 'log-git',    group: 'all' },
  error:      { icon: '🚨', cls: 'log-error',  group: 'error' },
  rate_limit: { icon: '⏳', cls: 'log-warn',   group: 'error' },
  info:       { icon: 'ℹ️', cls: 'log-info',   group: 'all' },
  diag:       { icon: '📊', cls: 'log-diag',   group: 'all' },
};

function renderLogEntry(log) {
  const time = new Date(log.time).toLocaleTimeString();
  // Support both new structured format and legacy {time, message} format
  const logType = log.type || _inferType(log.message || '');
  const content = log.content || log.message || '';
  const cfg = LOG_TYPE_CONFIG[logType] || { icon: '', cls: '', group: 'all' };
  const toolName = log.meta?.tool_name || '';
  const status = log.meta?.status || '';

  // Collapsible for long content
  const isLong = content.length > 200;
  const displayContent = isLong ? content.slice(0, 200) + '…' : content;
  const expandBtn = isLong ? ` <span class="log-expand" onclick="this.parentElement.querySelector('.log-full').style.display='block';this.style.display='none'">展开</span>` : '';
  const fullContent = isLong ? `<div class="log-full" style="display:none">${escapeHtml(content)}</div>` : '';

  // Status badge for tool entries
  let statusBadge = '';
  if (status === 'success') statusBadge = '<span class="log-status log-status-ok">OK</span>';
  else if (status === 'failed') statusBadge = '<span class="log-status log-status-fail">FAIL</span>';
  else if (status === 'created') statusBadge = '<span class="log-status log-status-run">RUN</span>';

  return `<div class="log-entry ${cfg.cls}" data-log-group="${cfg.group}">
    <span class="log-time">${time}</span>
    <span class="log-icon">${cfg.icon}</span>
    ${toolName ? `<span class="log-tool-name">${escapeHtml(toolName)}</span>` : ''}
    ${statusBadge}
    <span class="log-content">${escapeHtml(displayContent)}${expandBtn}</span>
    ${fullContent}
  </div>`;
}

function _inferType(message) {
  if (message.startsWith('[tool_use]')) return 'tool_use';
  if (message.startsWith('[tool_ok]')) return 'tool_ok';
  if (message.startsWith('[tool_error]')) return 'tool_error';
  if (message.startsWith('[init]')) return 'init';
  if (message.startsWith('[result]')) return 'result';
  if (message.startsWith('[output]')) return 'result';
  if (message.startsWith('[stderr]')) return 'error';
  if (message.startsWith('[git]')) return 'git';
  if (message.startsWith('[rate_limit]')) return 'rate_limit';
  if (message.startsWith('[DIAG]')) return 'diag';
  return 'message';
}

function getLogClass(message) {
  // Legacy compat — used by appendModalEvent
  const cfg = LOG_TYPE_CONFIG[_inferType(message)];
  return cfg ? cfg.cls : '';
}

function appendModalEvent(eventName, data, timestamp) {
  const logList = document.getElementById('modalLogList');
  if (!logList) return;
  const time = new Date(timestamp).toLocaleTimeString();
  const div = document.createElement('div');
  let cls = '';
  let text = '';
  switch (eventName) {
    case 'tool_use':
      cls = 'log-tool';
      text = `[tool_use] ${data.tool}: ${data.input || ''}`;
      break;
    case 'tool_error':
      cls = 'log-error';
      text = `[tool_error] ${data.error || ''}`;
      break;
    case 'init':
      cls = 'log-init';
      text = `[init] model=${data.model} tools=${data.tool_count}`;
      break;
    case 'result':
      cls = 'log-result';
      text = `[result] ${data.subtype} | turns=${data.turns} cost=$${(data.cost||0).toFixed(4)} ${data.stop_reason}`;
      break;
    case 'rate_limit':
      cls = 'log-warn';
      text = `[rate_limit] ${data.status}`;
      break;
    default:
      text = `[${eventName}] ${JSON.stringify(data)}`;
  }
  div.className = `log-entry ${cls}`;
  div.innerHTML = `<span class="log-time">${time}</span>${escapeHtml(text)}`;
  logList.appendChild(div);
  logList.scrollTop = logList.scrollHeight;
}

// ─── Project Discovery ──────────────────────────────────────────────────────

async function openDiscoverModal() {
  $('#discoverModalOverlay').classList.add('active');
  $('#discoverList').innerHTML = '<div class="loading">扫描 ~/.claude/projects/ ...</div>';

  const resp = await api('/discover-projects');
  if (!resp || !resp.projects) {
    $('#discoverList').innerHTML = '<p>扫描失败</p>';
    return;
  }

  if (resp.projects.length === 0) {
    $('#discoverList').innerHTML = '<p>未发现本地 Claude Code 项目。请先在某个项目目录中使用过 Claude Code。</p>';
    return;
  }

  let html = '';
  for (const p of resp.projects) {
    const imported = p.already_imported;
    const lastActive = p.last_active ? new Date(p.last_active).toLocaleDateString() : '';
    html += `
      <label class="discover-item ${imported ? 'imported' : ''}" data-path="${escapeHtml(p.path)}">
        <input type="checkbox" class="discover-check" value="${escapeHtml(p.path)}"
               data-name="${escapeHtml(p.name)}" ${imported ? 'disabled checked' : ''}>
        <div class="discover-info">
          <div class="discover-name">${escapeHtml(p.name)}
            ${imported ? '<span class="discover-badge">已导入</span>' : ''}
          </div>
          <div class="discover-path">${escapeHtml(p.path)}</div>
          <div class="discover-meta">
            ${p.session_count} 个会话 · 最近活跃 ${lastActive}
            ${p.first_prompt ? ` · "${escapeHtml(p.first_prompt.slice(0, 40))}"` : ''}
          </div>
        </div>
      </label>`;
  }
  $('#discoverList').innerHTML = html;
}

function closeDiscoverModal() {
  $('#discoverModalOverlay').classList.remove('active');
}

async function handleDiscoverImport() {
  const checks = document.querySelectorAll('.discover-check:checked:not(:disabled)');
  if (checks.length === 0) {
    closeDiscoverModal();
    return;
  }

  let imported = 0;
  for (const cb of checks) {
    const path = cb.value;
    const name = cb.dataset.name;
    const resp = await api('/projects', {
      method: 'POST',
      body: { name, path, lang: state.lang },
    });
    if (resp && resp.project) imported++;
  }

  closeDiscoverModal();
  if (imported > 0) {
    await loadProjects();
    renderProjectList();
  }
}

// ─── Directory Browser ──────────────────────────────────────────────────────

let _dirBrowserCurrent = '';

async function toggleDirBrowser() {
  const browser = $('#dirBrowser');
  if (browser.classList.contains('hidden')) {
    const inputPath = $('#projectPathInput').value.trim() || '~';
    await loadDir(inputPath);
    browser.classList.remove('hidden');
  } else {
    browser.classList.add('hidden');
  }
}

async function loadDir(path) {
  const data = await api(`/browse?path=${encodeURIComponent(path)}`);
  _dirBrowserCurrent = data.current;
  $('#projectPathInput').value = data.current;

  // Render breadcrumb
  renderBreadcrumb(data.current, data.parent);

  // Render directory list
  const listEl = $('#dirList');
  if (data.dirs.length === 0) {
    listEl.innerHTML = '<div class="dir-empty">无子目录</div>';
  } else {
    listEl.innerHTML = data.dirs.map(d => `
      <div class="dir-item" data-path="${escapeHtml(d.path)}">
        <span class="dir-item-icon">${d.is_git ? '📂' : '📁'}</span>
        <span class="dir-item-name">${escapeHtml(d.name)}</span>
        ${d.is_git ? '<span class="dir-item-badge">Git</span>' : ''}
      </div>
    `).join('');

    listEl.querySelectorAll('.dir-item').forEach(item => {
      item.addEventListener('click', () => {
        loadDir(item.dataset.path);
      });
    });
  }
}

function renderBreadcrumb(currentPath, parentPath) {
  const parts = currentPath.split('/').filter(Boolean);
  const crumb = $('#dirBreadcrumb');
  let html = '';

  // Up button
  if (parentPath) {
    html += `<span class="dir-breadcrumb-seg" data-path="${escapeHtml(parentPath)}">⬆</span>`;
    html += `<span class="dir-breadcrumb-sep"> / </span>`;
  }

  // Path segments (show last 4)
  const shown = parts.length > 4 ? parts.slice(-4) : parts;
  if (parts.length > 4) html += `<span class="dir-breadcrumb-sep">…/</span>`;

  shown.forEach((seg, i) => {
    const segPath = '/' + parts.slice(0, parts.length - shown.length + i + 1).join('/');
    html += `<span class="dir-breadcrumb-seg" data-path="${escapeHtml(segPath)}">${escapeHtml(seg)}</span>`;
    if (i < shown.length - 1) html += `<span class="dir-breadcrumb-sep">/</span>`;
  });

  crumb.innerHTML = html;

  crumb.querySelectorAll('.dir-breadcrumb-seg').forEach(seg => {
    seg.addEventListener('click', () => {
      loadDir(seg.dataset.path);
    });
  });
}

function selectCurrentDir() {
  $('#projectPathInput').value = _dirBrowserCurrent;
  $('#dirBrowser').classList.add('hidden');
  // Auto-fill project name from directory name if empty
  if (!$('#projectNameInput').value.trim()) {
    const dirName = _dirBrowserCurrent.split('/').filter(Boolean).pop() || '';
    $('#projectNameInput').value = dirName;
  }
}

// ─── Project Access URLs ─────────────────────────────────────────────────────

function showProjectUrlsError(msg) {
  const el = $('#projectUrlsError');
  if (!el) return;
  if (!msg) {
    el.textContent = '';
    el.classList.add('hidden');
  } else {
    el.textContent = msg;
    el.classList.remove('hidden');
  }
}

function updateProjectUrlsButtonTitle() {
  const btn = $('#projectUrlsBtn');
  if (!btn) return;
  const project = state.projects.find(p => p.id === state.activeProjectId);
  const port = project?.settings?.dev_port;
  btn.title = port ? t('urlsTitleWithPort', port) : t('urlsTitle');
}

function applyProjectUrlsLanguage() {
  updateProjectUrlsButtonTitle();
  const title = $('#projectUrlsTitle');
  if (title) title.textContent = t('urlsTitle');
  const portLabel = $('#projectDevPortLabel');
  if (portLabel) portLabel.textContent = t('urlsPort');
  const saveBtn = $('#projectDevPortSave');
  if (saveBtn) saveBtn.textContent = t('save');
  const autoBtn = $('#projectDevPortAuto');
  if (autoBtn) {
    autoBtn.textContent = t('urlsAuto');
    autoBtn.title = t('urlsAutoTitle');
  }
  if (!$('#projectUrlsPopover')?.classList.contains('hidden')) {
    renderProjectUrls();
  }
}

function closeProjectUrlsPopover() {
  const pop = $('#projectUrlsPopover');
  const btn = $('#projectUrlsBtn');
  if (pop) pop.classList.add('hidden');
  showProjectUrlsError('');
  if (btn) {
    btn.classList.remove('active');
    btn.setAttribute('aria-expanded', 'false');
  }
}

async function toggleProjectUrlsPopover() {
  const pop = $('#projectUrlsPopover');
  const btn = $('#projectUrlsBtn');
  if (!pop || !btn) return;
  const opening = pop.classList.contains('hidden');
  if (!opening) {
    closeProjectUrlsPopover();
    return;
  }
  showProjectUrlsError('');
  pop.classList.remove('hidden');
  btn.classList.add('active');
  btn.setAttribute('aria-expanded', 'true');
  const project = state.projects.find(p => p.id === state.activeProjectId);
  if (!project?.settings?.dev_port) {
    await refreshProjects();
  }
  renderProjectUrls();
  try {
    const data = await api('/network-addresses');
    if (data && !data.error) {
      state.networkAddresses = data;
      if (!pop.classList.contains('hidden')) renderProjectUrls();
    }
  } catch (e) {
    console.warn('Failed to fetch network addresses:', e);
  }
}

function _buildAccessUrl(host, port) {
  const proto = window.location.protocol === 'https:' ? 'https:' : 'http:';
  const omitPort = (proto === 'http:' && port === 80) || (proto === 'https:' && port === 443);
  return omitPort ? `${proto}//${host}` : `${proto}//${host}:${port}`;
}

function renderProjectUrls() {
  const listEl = $('#projectUrlsList');
  const portInput = $('#projectDevPortInput');
  if (!listEl || !portInput) return;

  const project = state.projects.find(p => p.id === state.activeProjectId);
  const activePort = project?.settings?.dev_port || null;
  const net = state.networkAddresses;

  if (document.activeElement !== portInput) {
    portInput.value = activePort ? String(activePort) : '';
  }
  updateProjectUrlsButtonTitle();

  if (!net || !activePort) {
    listEl.innerHTML = `<div class="project-url-row is-empty"><div class="project-url-meta"><span class="project-url-value">${escapeHtml(t('urlsLoading'))}</span></div></div>`;
    return;
  }

  const rows = [
    { label: t('urlsLocal'), host: net.localhost || 'localhost', emptyText: '' },
    { label: t('urlsLan'), host: net.lan_ip, emptyText: t('urlsMissingLan') },
    { label: t('urlsTailscale'), host: net.tailscale_ip, emptyText: t('urlsMissingTs') },
  ];
  if (net.tailscale_dns) {
    rows.push({ label: t('urlsMagicDns'), host: net.tailscale_dns, emptyText: '' });
  }

  listEl.innerHTML = rows.map((row) => {
    if (!row.host) {
      return `<div class="project-url-row is-empty">
        <div class="project-url-meta">
          <span class="project-url-label">${escapeHtml(row.label)}</span>
          <span class="project-url-value">${escapeHtml(row.emptyText)}</span>
        </div>
      </div>`;
    }
    const url = _buildAccessUrl(row.host, activePort);
    const safeUrl = escapeHtml(url);
    return `<div class="project-url-row">
      <div class="project-url-meta">
        <span class="project-url-label">${escapeHtml(row.label)}</span>
        <span class="project-url-value" title="${safeUrl}">${safeUrl}</span>
      </div>
      <div class="project-url-actions">
        <button type="button" class="project-url-act" data-copy-url="${safeUrl}">${escapeHtml(t('urlsCopy'))}</button>
        <a class="project-url-act open" href="${safeUrl}" target="_blank" rel="noopener noreferrer">${escapeHtml(t('urlsOpen'))}</a>
      </div>
    </div>`;
  }).join('');
}

async function saveProjectDevPortFromPopover() {
  const input = $('#projectDevPortInput');
  if (!input) return;
  const raw = input.value.trim();
  if (!raw) {
    await saveProjectDevPort(null);
    return;
  }
  if (!/^\d+$/.test(raw)) {
    showProjectUrlsError(t('urlsPortInvalid'));
    input.focus();
    return;
  }
  const port = parseInt(raw, 10);
  if (port < 1 || port > 65535) {
    showProjectUrlsError(t('urlsPortInvalid'));
    input.focus();
    return;
  }
  await saveProjectDevPort(port);
}

async function saveProjectDevPort(port) {
  if (!state.activeProjectId) return;
  const res = await api(`/projects/${state.activeProjectId}`, {
    method: 'PUT',
    body: { settings: { dev_port: port } },
  });
  if (!res || res.error) {
    showProjectUrlsError(res?.error || t('saveFailed'));
    return;
  }
  showProjectUrlsError('');
  if (res.project) {
    const idx = state.projects.findIndex(p => p.id === state.activeProjectId);
    if (idx !== -1) state.projects[idx] = res.project;
    const input = $('#projectDevPortInput');
    if (input && res.project.settings?.dev_port) {
      input.value = String(res.project.settings.dev_port);
    }
  } else {
    await refreshProjects();
  }
  renderProjectUrls();
}

async function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) { /* fallback below for non-HTTPS LAN/Tailscale origins */ }
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch (_) {
    ok = false;
  }
  document.body.removeChild(ta);
  return ok;
}

// ─── Markdown File Editor (CLAUDE.md / PROGRESS.md) ─────────────────────────

let _mdEditorEndpoint = ''; // tracks which file we're editing

async function openMdEditor(endpoint, title) {
  if (!state.activeProjectId) return;
  _mdEditorEndpoint = endpoint;
  const data = await api(`/projects/${state.activeProjectId}/${endpoint}`);
  $('#mdEditorTextarea').value = data.content || '';
  $('#mdEditorPath').textContent = data.path || '';
  $('#mdEditorTitle').textContent = title;
  $('#mdEditorOverlay').classList.add('active');
  $('#mdEditorTextarea').focus();
}

function closeMdEditor() {
  $('#mdEditorOverlay').classList.remove('active');
  _mdEditorEndpoint = '';
}

async function saveMdEditor() {
  if (!state.activeProjectId || !_mdEditorEndpoint) return;
  const content = $('#mdEditorTextarea').value;
  await api(`/projects/${state.activeProjectId}/${_mdEditorEndpoint}`, {
    method: 'PUT',
    body: { content },
  });
  closeMdEditor();
}

// ─── Utilities ──────────────────────────────────────────────────────────────

function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function renderMarkdown(str) {
  if (!str) return '';
  if (typeof marked !== 'undefined' && marked.parse) {
    try {
      return marked.parse(str, { breaks: true });
    } catch (e) {
      return `<pre>${escapeHtml(str)}</pre>`;
    }
  }
  // Fallback if marked.js not loaded
  return `<pre>${escapeHtml(str)}</pre>`;
}

// ─── Mobile Native Enhancements ─────────────────────────────────────────────

function updateMobileTabs(columns) {
  const track = $('#mobileTabsTrack');
  if (!track) return;
  const statuses = BOARD_COLUMNS;
  const badgeColors = {
    todo: '', pending: '', in_progress: 'badge-blue', wait: 'badge-orange', done: 'badge-green',
  };

  track.innerHTML = statuses.map(s => {
    const count = columns[s].length;
    const active = s === state.mobileActiveTab ? ' active' : '';
    const badge = count > 0
      ? ` <span class="tab-badge ${badgeColors[s]}">${count}</span>`
      : '';
    return `<button class="mobile-tab${active}" data-status="${s}">${t(`col_${s}`)}${badge}</button>`;
  }).join('');

  track.querySelectorAll('.mobile-tab').forEach(tab => {
    tab.addEventListener('click', () => switchMobileTab(tab.dataset.status));
  });

  // Scroll active tab into view
  const activeTab = track.querySelector('.mobile-tab.active');
  if (activeTab) {
    activeTab.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
  }
}

function updateStatusOverview(columns) {
  const overview = $('#statusOverview');
  if (!overview) return;
  const items = [
    { status: 'pending', color: 'var(--gray)', count: columns.pending.length },
    { status: 'in_progress', color: 'var(--blue)', count: columns.in_progress.length },
    { status: 'wait', color: 'var(--orange)', count: columns.wait.length },
    { status: 'done', color: 'var(--green)', count: columns.done.length },
  ];

  overview.innerHTML = items
    .filter(i => i.count > 0)
    .map(i => `<span class="status-dot" data-status="${i.status}" style="--dot-color:${i.color}">
      <span class="dot-circle"></span>${i.count}
    </span>`)
    .join('');

  overview.querySelectorAll('.status-dot').forEach(dot => {
    dot.addEventListener('click', () => switchMobileTab(dot.dataset.status));
  });
}

function switchMobileTab(status) {
  state.mobileActiveTab = status;
  localStorage.setItem('mobileTab', status);

  // Update tab active state
  document.querySelectorAll('.mobile-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.status === status);
  });

  // Show/hide columns
  document.querySelectorAll('.board .column').forEach(col => {
    col.classList.toggle('mobile-active', col.dataset.status === status);
  });

  // Scroll active tab into view
  const activeTab = document.querySelector('.mobile-tab.active');
  if (activeTab) {
    activeTab.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
  }
}

function openMobileInput() {
  const inputSection = $('.input-section');
  inputSection.classList.add('mobile-open');
  $('#mobileInputOverlay').classList.add('active');
  $('#mobileFab').classList.add('hidden');
  setTimeout(() => $('#taskInput').focus(), 300);

  // Adjust for virtual keyboard: move input above keyboard
  // Capture height BEFORE keyboard opens — on iOS 16+, window.innerHeight
  // shrinks with the keyboard, making the old delta-based calc return 0.
  if (window.visualViewport) {
    const initialHeight = window.innerHeight;
    const onViewportResize = () => {
      const vv = window.visualViewport;
      const keyboardHeight = initialHeight - (vv.offsetTop + vv.height);
      inputSection.style.bottom = Math.max(0, keyboardHeight) + 'px';
      // Limit height to visible viewport so content doesn't hide behind browser chrome
      inputSection.style.maxHeight = vv.height + 'px';
    };
    window.visualViewport.addEventListener('resize', onViewportResize);
    window.visualViewport.addEventListener('scroll', onViewportResize);
    // Store cleanup ref
    inputSection._vpCleanup = () => {
      window.visualViewport.removeEventListener('resize', onViewportResize);
      window.visualViewport.removeEventListener('scroll', onViewportResize);
      inputSection.style.bottom = '';
      inputSection.style.maxHeight = '';
    };
  }
}

function closeMobileInput() {
  const inputSection = $('.input-section');
  inputSection.classList.remove('mobile-open');
  $('#mobileInputOverlay').classList.remove('active');
  $('#mobileFab').classList.remove('hidden');
  $('#taskInput').blur();
  // Cleanup viewport listeners
  if (inputSection._vpCleanup) {
    inputSection._vpCleanup();
    inputSection._vpCleanup = null;
  }
}

function initModalDrag() {
  const modal = $('#taskModal');
  if (!modal) return;
  let startY = 0, currentY = 0, isDragging = false;

  modal.addEventListener('touchstart', (e) => {
    const handle = e.target.closest('.modal-drag-handle');
    const header = e.target.closest('.modal-header');
    if (!handle && !header) return;
    startY = e.touches[0].clientY;
    currentY = startY;
    isDragging = true;
    modal.style.transition = 'none';
  }, { passive: true });

  modal.addEventListener('touchmove', (e) => {
    if (!isDragging) return;
    currentY = e.touches[0].clientY;
    const dy = currentY - startY;
    if (dy < 0) return;
    // Damping effect: drag gets harder the further you pull
    const dampened = dy * 0.6;
    modal.style.transform = `translateY(${dampened}px)`;
  }, { passive: true });

  modal.addEventListener('touchend', () => {
    if (!isDragging) return;
    isDragging = false;
    const dy = currentY - startY;
    modal.style.transition = 'transform 0.3s cubic-bezier(0.32, 0.72, 0, 1)';
    if (dy > 120) {
      // Dismiss modal
      modal.style.transform = 'translateY(100%)';
      setTimeout(() => {
        closeModal();
      }, 300);
    } else {
      // Snap back
      modal.style.transform = '';
    }
    startY = 0;
    currentY = 0;
  });
}

// ─── Mobile Gestures ────────────────────────────────────────────────────────

let _openSwipeCard = null;
let _cardSwipeActive = false;
let _lastSwipeEnd = 0;
let _mobileTabProject = null;

function haptic(ms) {
  if (navigator.vibrate) navigator.vibrate(ms || 10);
}

function bindCardSwipe() {
  if (!isMobile()) return;
  _openSwipeCard = null;

  document.querySelectorAll('.card-swipe-wrapper').forEach(wrapper => {
    const card = wrapper.querySelector('.card');
    const actionsEl = wrapper.querySelector('.card-swipe-actions');
    if (!card || !actionsEl || actionsEl.children.length === 0) return;

    let startX = 0, startY = 0, dx = 0, locked = false, isCardSwipe = false;
    const aw = () => actionsEl.offsetWidth || 76;

    card.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) return;
      if (_openSwipeCard && _openSwipeCard !== wrapper) closeSwipeCard(_openSwipeCard);
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      dx = 0;
      locked = false;
      isCardSwipe = false;
    }, { passive: true });

    card.addEventListener('touchmove', (e) => {
      if (locked && !isCardSwipe) return;
      const cx = e.touches[0].clientX - startX;
      const cy = e.touches[0].clientY - startY;

      if (!locked && (Math.abs(cx) > 8 || Math.abs(cy) > 8)) {
        locked = true;
        const isH = Math.abs(cx) > Math.abs(cy) * 1.2;
        const isOpen = wrapper === _openSwipeCard;
        // Only capture as a card swipe when swiping left to open actions,
        // or when this card is already open and the user is swiping to adjust/close it.
        // Swiping right on a closed card bubbles to the board tab switcher!
        isCardSwipe = isH && (isOpen || cx < 0);
        if (isCardSwipe) {
          _cardSwipeActive = true;
          card.classList.add('swiping');
        }
      }
      if (!locked || !isCardSwipe) return;

      e.preventDefault();
      e.stopPropagation();
      const w = aw();
      const isOpen = wrapper === _openSwipeCard;
      let tx;
      if (isOpen) {
        tx = Math.min(0, Math.max(-w * 1.25, -w + cx));
      } else {
        tx = Math.min(0, cx);
        if (tx < -w) tx = -w - (-tx - w) * 0.22;
      }
      dx = tx;
      card.style.transform = `translateX(${tx}px)`;
    }, { passive: false });

    const finishCardSwipe = () => {
      card.classList.remove('swiping');
      if (!locked || !isCardSwipe) {
        _cardSwipeActive = false;
        return;
      }
      _cardSwipeActive = false;
      _lastSwipeEnd = Date.now();

      const w = aw();
      const threshold = Math.min(w * 0.35, 36);
      const isOpen = wrapper === _openSwipeCard;

      if (isOpen) {
        if (dx > -w + threshold) closeSwipeCard(wrapper);
        else openSwipeCard(wrapper, w);
      } else {
        if (dx < -threshold) { openSwipeCard(wrapper, w); haptic(); }
        else closeSwipeCard(wrapper);
      }
    };

    card.addEventListener('touchend', finishCardSwipe);
    card.addEventListener('touchcancel', () => {
      card.classList.remove('swiping');
      _cardSwipeActive = false;
      locked = false;
      isCardSwipe = false;
      closeSwipeCard(wrapper);
    });
  });
}

function openSwipeCard(wrapper, w) {
  if (_openSwipeCard && _openSwipeCard !== wrapper) closeSwipeCard(_openSwipeCard);
  const card = wrapper.querySelector('.card');
  if (card) card.style.transform = `translateX(-${w}px)`;
  _openSwipeCard = wrapper;
}

function closeSwipeCard(wrapper) {
  if (!wrapper) return;
  const card = wrapper.querySelector('.card');
  if (card) card.style.transform = '';
  if (_openSwipeCard === wrapper) _openSwipeCard = null;
}

function initCardSwipeGlobal() {
  // Close swiped card when tapping outside
  document.addEventListener('touchstart', (e) => {
    if (_openSwipeCard && !_openSwipeCard.contains(e.target)) {
      closeSwipeCard(_openSwipeCard);
    }
  }, { passive: true });
}

function initBoardGestures() {
  const board = $('#board');
  if (!board) return;

  // Pull-to-refresh indicator
  const ptr = document.createElement('div');
  ptr.className = 'pull-refresh-indicator';
  ptr.innerHTML = '<span class="refresh-arrow">↓</span>';
  board.insertBefore(ptr, board.firstChild);

  let startX = 0, startY = 0, startTime = 0, lastX = 0, locked = false, gesture = null;
  let refreshing = false;

  const getActiveCol = () => board.querySelector('.column.mobile-active');
  const resetColTransform = () => {
    const col = getActiveCol();
    if (!col) return;
    col.style.transition = 'transform 0.22s cubic-bezier(0.2, 0.8, 0.2, 1)';
    col.style.transform = '';
    setTimeout(() => { if (col) col.style.transition = ''; }, 240);
  };

  board.addEventListener('touchstart', (e) => {
    if (_cardSwipeActive || refreshing || e.touches.length !== 1) return;
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
    startTime = Date.now();
    lastX = startX;
    locked = false;
    gesture = null;
  }, { passive: true });

  board.addEventListener('touchmove', (e) => {
    if (refreshing || gesture === 'none') return;
    if (_cardSwipeActive) {
      resetColTransform();
      gesture = 'none';
      return;
    }
    if (!isMobile()) return;

    const cx = e.touches[0].clientX;
    const cy = e.touches[0].clientY;
    lastX = cx;
    const dx = cx - startX;
    const dy = cy - startY;

    if (!locked && (Math.abs(dx) > 10 || Math.abs(dy) > 10)) {
      locked = true;
      if (Math.abs(dx) > Math.abs(dy) * 1.2) {
        gesture = (startX <= 48 && dx > 0) ? 'none' : 'swipe';
      } else if (dy > 0 && (document.scrollingElement || document.body).scrollTop <= 0) {
        gesture = 'pull';
      } else {
        gesture = 'none';
      }
    }

    if (gesture === 'swipe') {
      e.preventDefault();
      if (_openSwipeCard) closeSwipeCard(_openSwipeCard);
      const col = getActiveCol();
      if (col) {
        const statuses = BOARD_COLUMNS;
        const idx = statuses.indexOf(state.mobileActiveTab);
        const atEdge = (idx === 0 && dx > 0) || (idx === statuses.length - 1 && dx < 0);
        const damp = atEdge ? 0.12 : 0.32;
        col.style.transition = 'none';
        col.style.transform = `translateX(${Math.round(dx * damp)}px)`;
      }
    } else if (gesture === 'pull') {
      e.preventDefault();
      const dist = Math.min((cy - startY) * 0.4, 70);
      ptr.style.height = dist + 'px';
      ptr.classList.add('pulling');
      ptr.classList.toggle('ready', dist > 45);
    }
  }, { passive: false });

  const endBoardGesture = () => {
    if (_cardSwipeActive || refreshing) {
      resetColTransform();
      gesture = null;
      locked = false;
      return;
    }

    if (gesture === 'swipe' && isMobile()) {
      resetColTransform();
      const dx = lastX - startX;
      const dt = Math.max(1, Date.now() - startTime);
      const velocity = Math.abs(dx) / dt; // px per ms
      if (Math.abs(dx) > 45 || (Math.abs(dx) > 24 && velocity > 0.32)) {
        const statuses = BOARD_COLUMNS;
        const idx = statuses.indexOf(state.mobileActiveTab);
        const newIdx = dx > 0 ? Math.max(0, idx - 1) : Math.min(statuses.length - 1, idx + 1);
        if (newIdx !== idx) {
          _lastSwipeEnd = Date.now();
          switchMobileTab(statuses[newIdx]);
          haptic();
        }
      }
    }

    if (gesture === 'pull' && isMobile()) {
      ptr.classList.remove('pulling');
      const h = parseInt(ptr.style.height) || 0;
      if (h > 45) {
        refreshing = true;
        ptr.classList.add('refreshing');
        ptr.innerHTML = '<span class="refresh-spinner"></span>';
        ptr.style.height = '';
        haptic(20);
        refreshTasks().then(() => {
          setTimeout(() => {
            ptr.classList.remove('refreshing');
            ptr.innerHTML = '<span class="refresh-arrow">↓</span>';
            refreshing = false;
          }, 400);
        });
      } else {
        ptr.style.height = '';
      }
    }

    gesture = null;
    locked = false;
  };

  board.addEventListener('touchend', endBoardGesture);
  board.addEventListener('touchcancel', () => {
    resetColTransform();
    ptr.classList.remove('pulling');
    ptr.style.height = '';
    gesture = null;
    locked = false;
  });
}

function resetTaskViewSwipe(el) {
  const tv = el || $('#taskView');
  if (!tv) return;
  tv.classList.remove('is-swiping');
  tv.style.transition = '';
  tv.style.transform = '';
  tv.style.opacity = '';
}

function hasScrollableX(target, stopAt) {
  let node = target instanceof Element ? target : target?.parentElement;
  while (node && node !== stopAt) {
    if (node.matches('.mobile-tabs, .status-overview')) return true;
    const ox = getComputedStyle(node).overflowX;
    if ((ox === 'auto' || ox === 'scroll') && node.scrollWidth > node.clientWidth + 2) {
      return true;
    }
    node = node.parentElement;
  }
  return false;
}

function initProjectGestures() {
  const taskView = $('#taskView');
  if (!taskView) return;

  let startX = 0, startY = 0, startTime = 0, dx = 0, locked = false, dragging = false;

  const cancelSwipe = () => {
    startTime = 0;
    locked = false;
    dx = 0;
    if (dragging) {
      dragging = false;
      resetTaskViewSwipe(taskView);
    }
  };

  taskView.addEventListener('touchstart', (e) => {
    if (e.touches.length !== 1 || !state.activeProjectId || taskView.classList.contains('hidden')) return;
    if (_cardSwipeActive) return;
    if (document.body.classList.contains('sp-open')) return;
    if (document.querySelector('.modal-overlay.active')) return;
    if ($('#mobileInputOverlay')?.classList.contains('active')) return;
    if (!$('#lightboxOverlay')?.classList.contains('hidden')) return;
    if (!$('#landscapeGuard')?.classList.contains('hidden')) return;
    if (e.target.closest('input, textarea, select')) return;

    const x = e.touches[0].clientX;
    const inBar = Boolean(e.target.closest('.project-bar, #pvStrip'));
    const scrollStrip = e.target.closest('.mobile-tabs, .status-overview');
    if (scrollStrip && scrollStrip.scrollLeft > 0) return;

    if (!inBar && x > 48) {
      if (state.activeView === 'board' && e.target.closest('#board, #mobileNav')) return;
      if (hasScrollableX(e.target, taskView)) return;
    }

    startX = x;
    startY = e.touches[0].clientY;
    startTime = Date.now();
    dx = 0;
    locked = false;
    dragging = false;
  }, { passive: true });

  taskView.addEventListener('touchmove', (e) => {
    if (!startTime) return;
    if (_cardSwipeActive) {
      cancelSwipe();
      return;
    }
    if (locked && !dragging) return;

    const cx = e.touches[0].clientX - startX;
    const cy = e.touches[0].clientY - startY;
    if (!locked && (Math.abs(cx) > 10 || Math.abs(cy) > 10)) {
      locked = true;
      dragging = cx > 0 && cx > Math.abs(cy) * 1.25;
      if (dragging) {
        taskView.classList.add('is-swiping');
        taskView.style.transition = 'none';
      }
    }
    if (!dragging) return;

    e.preventDefault();
    dx = Math.max(0, cx);
    taskView.style.transform = `translateX(${dx}px)`;
    taskView.style.opacity = String(Math.max(0.6, 1 - dx / 520));
  }, { passive: false });

  const endProjectSwipe = () => {
    if (!startTime) return;
    const dt = Math.max(1, Date.now() - startTime);
    const velocity = dx / dt;
    startTime = 0;
    if (!dragging) return;

    dragging = false;
    _lastSwipeEnd = Date.now();
    if (dx > 85 || (dx > 36 && velocity > 0.42)) {
      resetTaskViewSwipe(taskView);
      haptic();
      exitProject();
      return;
    }
    taskView.classList.remove('is-swiping');
    taskView.style.transition = 'transform 0.22s cubic-bezier(0.2, 0.8, 0.2, 1), opacity 0.22s ease';
    taskView.style.transform = '';
    taskView.style.opacity = '';
    setTimeout(() => {
      if (!dragging && taskView) taskView.style.transition = '';
    }, 240);
  };

  taskView.addEventListener('touchend', endProjectSwipe);
  taskView.addEventListener('touchcancel', cancelSwipe);
}

// ─── Project List View ──────────────────────────────────────────────────────

async function promptArchiveOldTasks() {
  if (!state.activeProjectId) return;
  const days = prompt(
    state.lang === 'en' ? 'Archive done/failed/cancelled tasks older than N days?' : '归档多少天前的已完成/失败/已取消任务？',
    '7'
  );
  if (days === null) return;
  const n = parseInt(days, 10);
  if (isNaN(n) || n < 0) return;
  const res = await api(`/projects/${state.activeProjectId}/tasks/archive`, {
    method: 'POST', body: { days: n },
  });
  if (res && res.archived_count > 0) {
    refreshTasks();
  } else if (res && res.archived_count === 0) {
    alert(state.lang === 'en' ? 'No matching tasks to archive' : '没有符合条件的任务需要归档');
  }
}

function formatListTokens(n) {
  if (!n) return '';
  if (n < 1000) return `${n} tok`;
  if (n < 1e6) return `${(n / 1000).toFixed(n < 10000 ? 1 : 0)}k tok`;
  return `${(n / 1e6).toFixed(1)}M tok`;
}

function formatListMs(ms) {
  if (!ms || ms <= 0) return '';
  const sec = Math.round(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
}

function formatListAgo(ms) {
  if (!ms) return '';
  return timeAgo(new Date(ms).toISOString());
}

async function refreshProjectListView() {
  const pid = state.activeProjectId;
  if (!pid) return;
  const q = new URLSearchParams({
    agent: state.listView.agent || '',
    q: state.searchQuery.trim(),
    recent_limit: state.listView.recentLimit,
    archived_limit: state.listView.archivedLimit,
  });
  const key = `${pid}:${q.toString()}`;
  state.listView.key = key;
  let data;
  try {
    data = await api(`/projects/${pid}/list?${q.toString()}`);
  } catch {
    return;
  }
  if (!data || data.error || state.activeProjectId !== pid || state.listView.key !== key) return;
  state.listView.data = data;
  if (state.activeView === 'list') renderListView();
}

function truncateText(text, max = 120) {
  const norm = String(text || '').replace(/\s+/g, ' ').trim();
  if (norm.length <= max) return { text: norm, full: norm };
  return { text: norm.slice(0, max - 1).trimEnd() + '…', full: norm };
}

function listRowTitle(item) {
  const task = item.task;
  if (item.title && item.ask) {
    const main = truncateText(item.title, 120);
    const ask = truncateText(item.ask, 100);
    return `<span class="lv-title-main" title="${escapeHtml(main.full)}">${escapeHtml(main.text)}</span><div class="lv-ask" title="${escapeHtml(ask.full)}">› ${escapeHtml(ask.text)}</div>`;
  }
  const raw = item.title || task?.description || (item.sid ? item.sid.slice(0, 8) : `#${item.head}`);
  const main = truncateText(raw, 120);
  return `<span class="lv-title-main" title="${escapeHtml(main.full)}">${escapeHtml(main.text)}</span>`;
}

function listRowSub(item) {
  const bits = [];
  if ((item.state === 'error' || item.state === 'limit') && item.err) {
    bits.push(`<span class="lv-err">${escapeHtml(item.err)}</span>`);
  } else if (item.todo && item.todo.total) {
    bits.push(`${item.todo.done}/${item.todo.total}${item.todo.cur ? ' · ' + escapeHtml(item.todo.cur) : ''}`);
  } else if (item.step) {
    bits.push(`<span class="ov-mono">${escapeHtml(item.step)}</span>`);
  }
  if (item.turns) bits.push(escapeHtml(t('rounds', item.turns)));
  const dur = item.active_ms ? formatListMs(item.active_ms) : '';
  if (dur) bits.push(`<span class="lv-dur">${escapeHtml(t('listDuration', dur))}</span>`);
  if (item.at) bits.push(escapeHtml(formatListAgo(item.at)));
  if (item.out) bits.push(formatListTokens(item.out));
  const ctxHtml = item.ctx_level && window.ctxChipHtml ? ` ${window.ctxChipHtml(item)}` : '';
  return bits.join(' · ') + ctxHtml;
}

function listStatusBadge(item) {
  const task = item.task;
  if (item.archived) {
    const map = {
      agent: t('listReasonAgent'),
      hidden: t('listReasonHidden'),
      task: t('listReasonTask'),
      panel: t('archived'),
    };
    const label = map[item.archive_reason] || t('archived');
    return `<span class="card-badge st-cancelled">${escapeHtml(label)}</span>`;
  }
  if (task?.status === 'todo') return `<span class="card-badge">${escapeHtml(t('col_todo'))}</span>`;
  if (task?.status === 'pending') return `<span class="card-badge">${escapeHtml(t('col_pending'))}</span>`;
  if (task?.status === 'review') return `<span class="card-badge review">${escapeHtml(t('review'))}</span>`;
  if (task?.status === 'failed' || task?.status === 'cancelled') {
    return `<span class="card-badge st-${task.status}">${escapeHtml(t(task.status))}</span>`;
  }
  if (item.bucket === 'active') {
    if (item.column === 'in_progress' && item.state === 'run' && item.bg_wait) {
      return `<span class="card-badge st-bg">${escapeHtml(t('bgWait'))}</span>`;
    }
    const map = { in_progress: 'col_in_progress', wait: 'col_wait', pending: 'col_pending', todo: 'col_todo' };
    const cls = item.column === 'in_progress' ? 'badge-blue' : item.column === 'wait' ? 'review' : '';
    return `<span class="card-badge ${cls}">${escapeHtml(t(map[item.column] || 'col_in_progress'))}</span>`;
  }
  return '';
}

function listRowActions(item) {
  const task = item.task;
  if (item.archived) {
    if (item.archive_reason === 'task' && item.head != null) {
      return `<button type="button" class="lv-act-btn" data-list-action="unarchive" data-tid="${item.head}">${escapeHtml(t('listRestoreAction'))}</button>`;
    }
    if (item.archive_reason === 'panel' && item.sid) {
      return `<button type="button" class="lv-act-btn" data-list-action="unarchive-session" data-sid="${escapeHtml(item.sid)}">${escapeHtml(t('listRestoreAction'))}</button>`;
    }
    if (item.archive_reason === 'hidden' && item.sid) {
      return `<button type="button" class="lv-act-btn" data-list-action="unhide" data-sid="${escapeHtml(item.sid)}">${escapeHtml(t('listRestoreAction'))}</button>`;
    }
    return '';
  }
  if (task?.status === 'todo') {
    const btns = AGENTS.map(a =>
      `<button type="button" class="card-action-btn assign agent-${a}" data-list-action="assign" data-tid="${task.id}" data-agent="${a}">${agentIcon(a)}<span>${agentName(a)}</span></button>`
    ).join('');
    const mine = task.agent === 'me'
      ? `<button type="button" class="card-action-btn assign finish" data-list-action="finish" data-tid="${task.id}">✓ ${escapeHtml(t('finish'))}</button>`
      : `<button type="button" class="card-action-btn assign agent-me" data-list-action="assign" data-tid="${task.id}" data-agent="me">${agentIcon('me')}<span>${agentName('me')}</span></button>`;
    return `<div class="lv-assign">${btns}${mine}</div>`;
  }
  const acts = [];
  if (task?.status === 'in_progress' || task?.status === 'pending') {
    acts.push(`<button type="button" class="lv-act-btn danger" data-list-action="cancel" data-tid="${task.id}">${escapeHtml(t('cancelLabel'))}</button>`);
  } else {
    if (task?.status === 'failed' || task?.status === 'cancelled') {
      acts.push(`<button type="button" class="lv-act-btn" data-list-action="retry" data-tid="${task.id}">${escapeHtml(t('retryLabel'))}</button>`);
    }
    if (item.head != null && task && !['todo', 'pending', 'in_progress', 'review'].includes(task.status)) {
      acts.push(`<button type="button" class="lv-act-btn" data-list-action="archive-item" data-tid="${item.head}">${escapeHtml(t('listArchiveAction'))}</button>`);
    } else if (item.sid && item.state !== 'run') {
      acts.push(`<button type="button" class="lv-act-btn" data-list-action="hide-item" data-sid="${escapeHtml(item.sid)}">${escapeHtml(t('listArchiveAction'))}</button>`);
    }
  }
  return acts.length ? `<div class="lv-actions">${acts.join('')}</div>` : '';
}

function renderListItem(item) {
  const ag = item.agent || 'claude';
  const task = item.task;
  const baseState = item.state || (task?.status === 'in_progress' ? 'run' : task?.status === 'review' ? 'wait' : 'idle');
  const dotState = item.archived ? 'archived' : (baseState === 'run' && item.bg_wait ? 'bg' : baseState);
  const showPill = !item.archived && ['run', 'wait', 'error', 'limit'].includes(item.state) && window.pillHtml;
  const pill = showPill ? `<div class="lv-pill">${window.pillHtml({ agent: ag }, item)}</div>` : '';
  const mergeBadge = item.has_pending_merge ? `<span class="card-badge review">${escapeHtml(t('toMerge'))}</span>` : '';
  const planBadge = task?.plan_mode ? '<span class="card-badge plan">Plan</span>' : '';
  const taskTag = item.head != null ? `<span class="lv-task-tag">#${item.head}</span>` : '';
  return `
    <div class="lv-row st-${escapeHtml(dotState)} agent-${escapeHtml(ag)}${item.archived ? ' is-archived' : ''}"
         data-sid="${escapeHtml(item.sid || '')}"
         data-agent="${escapeHtml(ag)}"
         data-tid="${item.head != null ? item.head : ''}"
         data-archived="${item.archived ? '1' : '0'}">
      <div class="lv-row-top">
        <span class="ov-state-dot st-${escapeHtml(dotState)}"></span>
        <div class="lv-row-main">
          <div class="lv-row-title">
            ${ag ? agentBadge(ag) : ''}
            ${taskTag}
            ${planBadge}
            ${listStatusBadge(item)}
            ${mergeBadge}
            ${listRowTitle(item)}
          </div>
          <div class="lv-row-sub">${listRowSub(item)}</div>
        </div>
        ${listRowActions(item)}
      </div>
      ${pill}
    </div>`;
}

function renderListView() {
  const el = $('#listView');
  if (!el) return;
  const d = state.listView.data;
  if (!d) {
    el.innerHTML = `<div class="lv-loading">${escapeHtml(t('listLoading'))}</div>`;
    return;
  }

  const agents = ['', ...AGENTS];
  const filterChips = agents.map((a) => {
    const on = state.listView.agent === a;
    const label = a ? agentName(a) : t('listAllAgents');
    return `<button type="button" class="lv-filter-btn${on ? ' active' : ''}" data-list-agent="${a}">${a ? agentIcon(a) : ''}<span>${escapeHtml(label)}</span></button>`;
  }).join('');

  const toolbar = `
    <div class="lv-toolbar">
      <div class="lv-filters">${filterChips}</div>
      <button type="button" class="lv-archive-old-btn" id="lvArchiveOldBtn">
        <svg viewBox="0 0 24 24" width="14" height="14"><path d="M21 8v13H3V8M1 3h22v5H1zM10 12h4" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <span>${escapeHtml(t('listArchiveOld'))}</span>
      </button>
    </div>`;

  const activeHtml = d.active?.length ? `
    <div class="lv-section">
      <div class="lv-section-head">
        <span class="lv-section-title">${escapeHtml(t('listActiveTitle'))}</span>
        <span class="lv-section-count">${d.active.length}</span>
      </div>
      <div class="lv-card">${d.active.map(renderListItem).join('')}</div>
    </div>` : '';

  const recentMore = d.recent.length < (d.recent_total || 0)
    ? `<div class="lv-more-wrap"><button type="button" class="lv-more-btn" data-list-more="recent">${escapeHtml(t('listLoadMore', d.recent.length, d.recent_total))}</button></div>`
    : '';
  const recentHtml = `
    <div class="lv-section">
      <div class="lv-section-head">
        <span class="lv-section-title">${escapeHtml(t('listRecentTitle'))}</span>
        <span class="lv-section-count">${d.recent_total ?? d.recent.length}</span>
      </div>
      <div class="lv-card">
        ${d.recent.length ? d.recent.map(renderListItem).join('') : `<div class="lv-empty">${escapeHtml(t('listEmptyRecent'))}</div>`}
        ${recentMore}
      </div>
    </div>`;

  const hasSearch = Boolean(state.searchQuery.trim());
  const archCount = d.archived_total ?? d.counts?.archived ?? d.archived.length;
  const isArchOpen = state.listView.archivedSearchOverride !== null
    ? state.listView.archivedSearchOverride
    : (state.listView.archivedOpen || (hasSearch && d.archived.length > 0));
  const archMore = d.archived.length < (d.archived_total || 0)
    ? `<div class="lv-more-wrap"><button type="button" class="lv-more-btn" data-list-more="archived">${escapeHtml(t('listLoadMore', d.archived.length, d.archived_total))}</button></div>`
    : '';
  const archivedHtml = `
    <div class="lv-section lv-archived-section">
      <button type="button" class="lv-collapse-head${isArchOpen ? ' is-open' : ''}" id="lvArchivedToggle" aria-expanded="${isArchOpen}">
        <span class="lv-caret">${isArchOpen ? '▾' : '▸'}</span>
        <span class="lv-section-title">${escapeHtml(t('listArchivedTitle'))}</span>
        <span class="lv-section-count">${archCount}</span>
      </button>
      ${isArchOpen ? `<div class="lv-card">${d.archived.length ? d.archived.map(renderListItem).join('') : `<div class="lv-empty">${escapeHtml(t('listEmptyArchived'))}</div>`}${archMore}</div>` : ''}
    </div>`;

  el.innerHTML = toolbar + activeHtml + recentHtml + archivedHtml;
}

function initListView() {
  const el = $('#listView');
  if (!el) return;
  el.addEventListener('click', async (e) => {
    if (Date.now() - _lastSwipeEnd < 260) return;
    const filterBtn = e.target.closest('[data-list-agent]');
    if (filterBtn) {
      state.listView.agent = filterBtn.dataset.listAgent || '';
      state.listView.recentLimit = 50;
      state.listView.archivedLimit = 30;
      refreshProjectListView();
      return;
    }
    if (e.target.closest('#lvArchiveOldBtn')) {
      promptArchiveOldTasks();
      return;
    }
    if (e.target.closest('#lvArchivedToggle')) {
      const hasSearch = Boolean(state.searchQuery.trim());
      if (hasSearch) {
        const cur = state.listView.archivedSearchOverride !== null
          ? state.listView.archivedSearchOverride
          : (state.listView.archivedOpen || (state.listView.data?.archived?.length > 0));
        state.listView.archivedSearchOverride = !cur;
      } else {
        state.listView.archivedOpen = !state.listView.archivedOpen;
      }
      renderListView();
      return;
    }
    const moreBtn = e.target.closest('[data-list-more]');
    if (moreBtn) {
      if (moreBtn.dataset.listMore === 'recent') state.listView.recentLimit += 50;
      else state.listView.archivedLimit += 30;
      refreshProjectListView();
      return;
    }
    const actBtn = e.target.closest('[data-list-action]');
    if (actBtn) {
      e.stopPropagation();
      const pid = state.activeProjectId;
      const action = actBtn.dataset.listAction;
      const tid = actBtn.dataset.tid ? parseInt(actBtn.dataset.tid, 10) : null;
      const sid = actBtn.dataset.sid || '';
      actBtn.disabled = true;
      if (action === 'assign' && tid) {
        await api(`/projects/${pid}/tasks/${tid}/assign`, { method: 'POST', body: { agent: actBtn.dataset.agent } });
      } else if (action === 'finish' && tid) {
        await api(`/projects/${pid}/tasks/${tid}/complete`, { method: 'POST', body: {} });
      } else if (action === 'retry' && tid) {
        await api(`/projects/${pid}/tasks/${tid}/retry`, { method: 'POST' });
      } else if (action === 'cancel' && tid) {
        await api(`/projects/${pid}/tasks/${tid}/cancel`, { method: 'POST' });
      } else if (action === 'archive-item' && tid) {
        await api(`/projects/${pid}/tasks/${tid}/archive`, { method: 'POST' });
      } else if (action === 'unarchive' && tid) {
        await api(`/projects/${pid}/tasks/${tid}/unarchive`, { method: 'POST' });
      } else if (action === 'hide-item' && sid) {
        await api(`/sessions/${encodeURIComponent(sid)}/archive`, { method: 'POST', body: { archived: true } });
      } else if (action === 'unarchive-session' && sid) {
        await api(`/sessions/${encodeURIComponent(sid)}/archive`, { method: 'POST', body: { archived: false } });
      } else if (action === 'unhide' && sid) {
        await api(`/sessions/${encodeURIComponent(sid)}/hide`, { method: 'POST', body: { hidden: false } });
      }
      refreshTasks();
      return;
    }
    const row = e.target.closest('.lv-row');
    if (!row) return;
    const pid = state.activeProjectId;
    const sid = row.dataset.sid;
    const tid = row.dataset.tid ? parseInt(row.dataset.tid, 10) : null;
    const agent = row.dataset.agent || 'claude';
    const isArchived = row.dataset.archived === '1';
    if (sid && window.openSessionPanel) {
      window.openSessionPanel(sid, agent, tid ? { projectId: pid, taskId: tid } : {});
      return;
    }
    if (tid && !isArchived && window.openSessionPanel) {
      window.openSessionPanel(`task:${pid}:${tid}`, agent, { projectId: pid, taskId: tid });
      return;
    }
    if (tid) {
      const existing = state.tasks.find(t => t.id === tid);
      if (existing) {
        openTaskModal(tid, existing);
      } else {
        const res = await api(`/projects/${pid}/tasks/${tid}`);
        if (res && res.task) openTaskModal(tid, res.task);
      }
    }
  });
}

// ─── Analytics ──────────────────────────────────────────────────────────────

function switchView(view) {
  if (!['list', 'board', 'analytics'].includes(view)) view = 'list';
  state.activeView = view;
  if (view !== 'analytics') {
    try { localStorage.setItem('projectView', view); } catch { /* private mode */ }
  }
  const isList = view === 'list';
  const isBoard = view === 'board';
  const isAnalytics = view === 'analytics';

  // Toggle view buttons
  $('#viewListBtn')?.classList.toggle('active', isList);
  $('#viewBoardBtn')?.classList.toggle('active', isBoard);
  $('#viewAnalyticsBtn')?.classList.toggle('active', isAnalytics);

  // Toggle sections
  $('#listView')?.classList.toggle('hidden', !isList);
  $('#board').classList.toggle('hidden', !isBoard);
  $('#mobileNav').classList.toggle('hidden', !isBoard);
  $('#analyticsView').classList.toggle('hidden', !isAnalytics);
  $('.input-section').classList.toggle('hidden', isAnalytics);
  $('#mobileFab').classList.toggle('hidden', isAnalytics);

  if (isList) {
    if (state.listView.data) renderListView();
    refreshProjectListView();
  } else if (isBoard) {
    renderBoard();
  } else if (isAnalytics) {
    fetchAnalytics();
  }
}

async function fetchAnalytics() {
  if (!state.activeProjectId) return;
  const days = state.analyticsRange || 0;
  const url = `/projects/${state.activeProjectId}/analytics` + (days > 0 ? `?days=${days}` : '');
  const data = await api(url);
  state.analyticsData = data;
  renderAnalytics(data);
}

function formatDuration(seconds) {
  if (!seconds || seconds <= 0) return '-';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

function renderAnalytics(data) {
  if (!data) return;
  renderAnalyticsCards(data);
  renderAnalyticsChart(data.daily_trend || []);
  applyAnalyticsLanguage();
}

function renderAnalyticsCards(data) {
  const container = $('#analyticsCards');
  const sc = data.status_counts || {};
  const dur = data.duration || {};
  const rate = data.success_rate;
  const rateStr = rate != null ? `${(rate * 100).toFixed(1)}%` : '-';
  const rateColor = rate >= 0.9 ? 'var(--green)' : rate >= 0.7 ? 'var(--orange)' : 'var(--red)';

  const costStr = data.total_cost_usd != null
    ? `$${data.total_cost_usd.toFixed(2)}`
    : '-';

  const avgDurStr = formatDuration(dur.avg_seconds);
  const minDurStr = formatDuration(dur.min_seconds);
  const maxDurStr = formatDuration(dur.max_seconds);

  container.innerHTML = `
    <div class="analytics-card">
      <div class="analytics-card-label">${t('analyticsTotal')}</div>
      <div class="analytics-card-value">${data.total_tasks || 0}</div>
      <div class="analytics-card-breakdown">
        <span class="ac-dot" style="--c:var(--gray)">${sc.todo || 0} ${t('todo')}</span>
        <span class="ac-dot" style="--c:var(--gray)">${sc.pending || 0} ${t('analyticsPending')}</span>
        <span class="ac-dot" style="--c:var(--blue)">${sc.in_progress || 0} ${t('analyticsInProgress')}</span>
        <span class="ac-dot" style="--c:var(--orange)">${sc.review || 0} ${t('analyticsReview')}</span>
        <span class="ac-dot" style="--c:var(--green)">${sc.done || 0} ${t('analyticsDoneLabel')}</span>
        <span class="ac-dot" style="--c:var(--red)">${sc.failed || 0} ${t('analyticsFailedLabel')}</span>
        <span class="ac-dot" style="--c:var(--gray)">${sc.cancelled || 0} ${t('analyticsCancelled')}</span>
      </div>
    </div>
    <div class="analytics-card">
      <div class="analytics-card-label">${t('analyticsSuccessRate')}</div>
      <div class="analytics-card-value" style="color:${rateColor}">${rateStr}</div>
      <div class="analytics-card-sub">${sc.done || 0} ${t('analyticsDone')} / ${(sc.done || 0) + (sc.failed || 0)} ${t('analyticsTotal').toLowerCase()}</div>
    </div>
    <div class="analytics-card">
      <div class="analytics-card-label">${t('analyticsAvgDuration')}</div>
      <div class="analytics-card-value">${avgDurStr}</div>
      <div class="analytics-card-sub">
        ${dur.sample_size ? t('analyticsSampleSize', dur.sample_size) : ''}
        ${dur.min_seconds ? `<br>${t('analyticsDurationMin', minDurStr)} / ${t('analyticsDurationMax', maxDurStr)}` : ''}
      </div>
    </div>
    <div class="analytics-card">
      <div class="analytics-card-label">${t('analyticsTotalCost')}</div>
      <div class="analytics-card-value">${costStr}</div>
    </div>
    ${renderAgentTable(data.by_agent || {})}
  `;
}

function renderAgentTable(byAgent) {
  const order = [...AGENTS, 'me', 'unknown']
    .filter(a => byAgent[a] && (byAgent[a].done || byAgent[a].failed));
  if (!order.length) return '';
  const rows = order.map(a => {
    const r = byAgent[a];
    const rate = r.success_rate != null ? `${(r.success_rate * 100).toFixed(0)}%` : '-';
    return `<tr>
      <td><span class="card-badge agent agent-${a}">${escapeHtml(agentName(a))}</span></td>
      <td>${r.done}</td><td>${r.failed}</td><td>${rate}</td><td>${formatDuration(r.avg_seconds)}</td>
    </tr>`;
  }).join('');
  return `<div class="analytics-card agent-table-card">
      <div class="analytics-card-label">${t('byAgent')}</div>
      <table class="agent-table">
        <thead><tr><th></th><th>${t('analyticsDoneLabel')}</th><th>${t('analyticsFailedLabel')}</th>
          <th>${t('analyticsSuccessRate')}</th><th>${t('analyticsAvgDuration')}</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function renderAnalyticsChart(trend) {
  const container = $('#analyticsChart');
  if (!trend || trend.length === 0) {
    container.innerHTML = `<div class="analytics-empty">${t('analyticsNoData')}</div>`;
    return;
  }

  const maxVal = Math.max(...trend.map(d => (d.done || 0) + (d.failed || 0)), 1);

  const bars = trend.map(d => {
    const done = d.done || 0;
    const failed = d.failed || 0;
    const total = done + failed;
    const donePct = (done / maxVal) * 100;
    const failedPct = (failed / maxVal) * 100;
    const dateLabel = d.date.slice(5); // MM-DD

    return `
      <div class="chart-bar-group">
        <div class="chart-bar-stack" title="${done} ${t('analyticsDone')}, ${failed} ${t('analyticsFailed')}">
          <div class="chart-bar done" style="height:${donePct}%"></div>
          <div class="chart-bar failed" style="height:${failedPct}%"></div>
        </div>
        <div class="chart-bar-label">${dateLabel}</div>
        ${total > 0 ? `<div class="chart-bar-value">${total}</div>` : ''}
      </div>`;
  }).join('');

  container.innerHTML = `
    <div class="chart-legend">
      <span class="legend-item"><span class="legend-dot" style="background:var(--green)"></span>${t('analyticsDone')}</span>
      <span class="legend-item"><span class="legend-dot" style="background:var(--red)"></span>${t('analyticsFailed')}</span>
    </div>
    <div class="chart-bars">${bars}</div>
  `;
}

function applyAnalyticsLanguage() {
  if ($('#viewListLabel')) $('#viewListLabel').textContent = t('viewList');
  $('#viewBoardLabel').textContent = t('viewBoard');
  $('#viewAnalyticsLabel').textContent = t('viewAnalytics');
  $('#chartTitle').textContent = t('analyticsDailyTrend');
  if (state.activeProjectId && state.activeView === 'list' && state.listView.data) {
    renderListView();
  }

  const rangeLabels = { '7': 'analyticsRange7', '30': 'analyticsRange30', '0': 'analyticsRangeAll' };
  document.querySelectorAll('.time-range-btn').forEach(btn => {
    const key = rangeLabels[btn.dataset.range];
    if (key) btn.textContent = t(key);
  });
}

function initAnalytics() {
  // View toggle
  $('#viewListBtn')?.addEventListener('click', () => switchView('list'));
  $('#viewBoardBtn').addEventListener('click', () => switchView('board'));
  $('#viewAnalyticsBtn').addEventListener('click', () => switchView('analytics'));

  // Time range picker
  document.querySelectorAll('.time-range-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.time-range-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.analyticsRange = parseInt(btn.dataset.range);
      fetchAnalytics();
    });
  });
}

// ─── Init ───────────────────────────────────────────────────────────────────

// ─── Phone access (token links; only offered on the machine running Jarvis) ───

async function initModels() {
  let data;
  try { data = await api('/models'); } catch (_) { return; }
  const sel = $('#agyModelSelect');
  (data?.antigravity || []).forEach(({ value, label }) => sel.add(new Option(label, value)));
}

async function initAccess() {
  let data;
  try { data = await api('/access'); } catch (_) { return; }
  if (!data || data.error) return;
  const wrap = $('#accessWrap'), btn = $('#accessBtn'), pop = $('#accessPopover'), list = $('#accessList');
  wrap.classList.remove('hidden');
  if (data.local_only) {
    list.innerHTML = `<div class="project-url-row is-empty"><div class="project-url-meta">
      <span class="project-url-value">只在本机监听。用 <code>JARVIS_HOST=0.0.0.0 ./start.sh</code> 启动后，其他设备凭令牌访问。</span>
    </div></div>`;
  } else if (!data.urls.length) {
    list.innerHTML = `<div class="project-url-row is-empty"><div class="project-url-meta"><span class="project-url-value">没有找到局域网或 Tailscale 地址</span></div></div>`;
  } else {
    list.innerHTML = data.urls.map(u => {
      const safe = escapeHtml(u.url);
      return `<div class="project-url-row">
        <div class="project-url-meta">
          <span class="project-url-label">${escapeHtml(u.label)}</span>
          <span class="project-url-value" title="${safe}">${safe}</span>
        </div>
        <div class="project-url-actions">
          <button type="button" class="project-url-act" data-copy-url="${safe}">${escapeHtml(t('urlsCopy'))}</button>
        </div>
      </div>`;
    }).join('');
  }
  const close = () => { pop.classList.add('hidden'); btn.classList.remove('active'); btn.setAttribute('aria-expanded', 'false'); };
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (!pop.classList.contains('hidden')) return close();
    pop.classList.remove('hidden'); btn.classList.add('active'); btn.setAttribute('aria-expanded', 'true');
  });
  document.addEventListener('click', (e) => { if (!e.target.closest('#accessWrap')) close(); });
  list.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-copy-url]');
    if (!b || !(await copyText(b.dataset.copyUrl))) return;
    b.textContent = t('urlsCopied'); b.classList.add('copied');
    setTimeout(() => { b.textContent = t('urlsCopy'); b.classList.remove('copied'); }, 1400);
  });
}

document.addEventListener('DOMContentLoaded', async () => {
  document.documentElement.setAttribute('data-theme', state.theme);
  $('#langLabel').textContent = state.lang === 'zh' ? '中' : 'EN';

  bindEvents();
  applyLanguage();
  connectWS();
  initModalDrag();
  initCardSwipeGlobal();
  initProjectGestures();
  initBoardGestures();
  initListView();
  initAnalytics();
  initAccess();
  initModels();

  // Mobile responsive handler
  _mobileQuery.addEventListener('change', () => {
    if (state.activeProjectId) renderBoard();
    if (!isMobile()) {
      document.querySelectorAll('.board .column').forEach(col => col.classList.remove('mobile-active'));
      closeMobileInput();
    }
  });

  // Load projects
  await refreshProjects();

  // If we had an active project saved, try to re-enter it
  if (state.activeProjectId) {
    const project = state.projects.find(p => p.id === state.activeProjectId);
    if (project) {
      enterProject(state.activeProjectId);
    } else {
      // Project was deleted, go to project list
      state.activeProjectId = null;
      localStorage.removeItem('activeProject');
    }
  }

  // ─── Image Lightbox ──────────────────────────────────────────────────────
  const lightboxOverlay = $('#lightboxOverlay');
  const lightboxImg = $('#lightboxImg');
  const lightboxClose = $('#lightboxClose');

  window.openLightbox = function(url) {
    lightboxImg.src = url;
    lightboxOverlay.classList.remove('hidden');
  };

  function closeLightbox() {
    lightboxOverlay.classList.add('hidden');
    lightboxImg.src = '';
  }

  lightboxOverlay.addEventListener('click', closeLightbox);
  lightboxClose.addEventListener('click', (e) => { e.stopPropagation(); closeLightbox(); });
  lightboxImg.addEventListener('click', (e) => e.stopPropagation());
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !lightboxOverlay.classList.contains('hidden')) closeLightbox();
  });

  // Auto-refresh
  setInterval(() => {
    if (state.activeProjectId) refreshTasks();
    else refreshProjects();
  }, 10000);

  // A page left open (phones keep tabs for days) reloads itself after an update,
  // unless you are in the middle of typing something.
  const myVersion = document.querySelector('meta[name="pv-version"]')?.content;
  async function checkVersion() {
    if (!myVersion || document.hidden) return;
    let v;
    try { v = (await api('/version')).version; } catch { return; }
    if (!v || v === myVersion) return;
    const typing = ['#taskInput', '#spInput', '#ovTodoInput'].some((sel) => $(sel)?.value?.trim())
      || document.querySelector('.modal-overlay.active');
    if (!typing) location.reload();
  }
  setInterval(checkVersion, 60000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkVersion(); });

  // Lock to portrait where supported (installed PWA / Android), and show a
  // portrait-only guard on phones rotated to landscape (ignoring keyboard resize).
  try { screen.orientation?.lock?.('portrait')?.catch(() => {}); } catch {}
  let landscapeDismissed = false;
  const coarsePointer = window.matchMedia('(pointer: coarse)');
  function isPhoneLandscape() {
    if (!coarsePointer.matches || Math.min(screen.width, screen.height) > 520) return false;
    const type = screen.orientation?.type;
    if (type) return type.startsWith('landscape');
    return screen.width > screen.height && window.innerWidth > window.innerHeight;
  }
  function updateLandscapeGuard() {
    const el = $('#landscapeGuard');
    if (!el) return;
    const show = !landscapeDismissed && isPhoneLandscape();
    el.classList.toggle('hidden', !show);
    if (show) {
      const en = state.lang === 'en';
      $('#landscapeGuardTitle').textContent = en ? 'Rotate to portrait' : '请竖屏使用 Jarvis';
      $('#landscapeGuardSub').textContent = en ? 'Portrait mode works best on mobile' : '竖屏浏览与操作体验更佳';
      $('#landscapeGuardDismiss').textContent = en ? 'Continue anyway' : '仍要横屏查看';
    }
  }
  $('#landscapeGuardDismiss')?.addEventListener('click', () => {
    landscapeDismissed = true;
    updateLandscapeGuard();
  });
  window.addEventListener('resize', updateLandscapeGuard);
  window.addEventListener('orientationchange', () => { landscapeDismissed = false; updateLandscapeGuard(); });
  screen.orientation?.addEventListener?.('change', () => { landscapeDismissed = false; updateLandscapeGuard(); });
  updateLandscapeGuard();
});
