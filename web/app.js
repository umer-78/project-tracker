// Board UI. No framework: one state object, one render pass per change.
const $ = (id) => document.getElementById(id);
const STATUSES = [
  ['todo', 'To do'], ['in_progress', 'In progress'], ['review', 'Review'], ['done', 'Done'],
];
const PRIORITIES = ['low', 'medium', 'high', 'urgent'];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const initials = (name) => (name || '?').split(' ').map((p) => p[0]).slice(0, 2).join('').toUpperCase();

const state = {
  token: localStorage.getItem('tracker:token') || '',
  user: null, projects: [], users: [], sprints: [], tasks: [],
  projectId: null, editing: null,
};

async function api(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    ...options,
    headers: {
      'content-type': 'application/json',
      ...(state.token ? { authorization: `Bearer ${state.token}` } : {}),
      ...options.headers,
    },
  });
  if (res.status === 401) { signOut(); throw new Error('Session expired, sign in again.'); }
  if (res.status === 204) return null;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ? JSON.stringify(body.detail) : `Request failed (${res.status})`);
  return body;
}

// ------------------------------------------------------------------ sign in
$('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('loginError').textContent = '';
  try {
    const body = await api('/login', {
      method: 'POST',
      body: JSON.stringify({ email: $('email').value, password: $('password').value }),
    });
    state.token = body.token;
    state.user = body.user;
    localStorage.setItem('tracker:token', body.token);
    await start();
  } catch (err) {
    $('loginError').textContent = err.message;
  }
});

function signOut() {
  state.token = '';
  state.user = null;
  localStorage.removeItem('tracker:token');
  $('app').hidden = true;
  $('login').hidden = false;
}

$('logout').addEventListener('click', async () => {
  try { await api('/logout', { method: 'POST' }); } catch { /* already gone */ }
  signOut();
});

// -------------------------------------------------------------------- load
async function start() {
  state.user = await api('/me');
  $('login').hidden = true;
  $('app').hidden = false;
  $('who').textContent = `${state.user.name} · ${state.user.role}`;
  state.users = await api('/users');
  state.projects = await api('/projects');
  if (!state.projects.length) { $('board').innerHTML = '<p class="muted">No projects yet.</p>'; return; }
  state.projectId = Number(localStorage.getItem('tracker:project')) || state.projects[0].id;
  if (!state.projects.some((p) => p.id === state.projectId)) state.projectId = state.projects[0].id;
  $('projectSelect').innerHTML = state.projects
    .map((p) => `<option value="${p.id}">${esc(p.key)} — ${esc(p.name)}</option>`).join('');
  $('projectSelect').value = String(state.projectId);
  $('assigneeFilter').innerHTML = '<option value="">Everyone</option>' +
    state.users.map((u) => `<option value="${u.id}">${esc(u.name)}</option>`).join('');
  await loadProject();
}

async function loadProject() {
  localStorage.setItem('tracker:project', String(state.projectId));
  state.sprints = await api(`/projects/${state.projectId}/sprints`);
  $('sprintFilter').innerHTML = '<option value="">All sprints</option>' +
    state.sprints.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
  $('sprintSelect').innerHTML = state.sprints.length
    ? state.sprints.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('')
    : '<option value="">No sprints</option>';
  await Promise.all([refreshTasks(), refreshStats(), refreshSide()]);
  drawBurndown();
}

async function refreshTasks() {
  const params = new URLSearchParams();
  if ($('search').value.trim()) params.set('q', $('search').value.trim());
  if ($('assigneeFilter').value) params.set('assignee_id', $('assigneeFilter').value);
  if ($('sprintFilter').value) params.set('sprint_id', $('sprintFilter').value);
  state.tasks = await api(`/projects/${state.projectId}/tasks?${params}`);
  renderBoard();
}

async function refreshStats() {
  const summary = await api(`/projects/${state.projectId}/summary`);
  const t = summary.tasks;
  $('stats').innerHTML = `
    <div class="stat"><span class="k">Tasks</span><span class="v">${t.total}</span>
      <div class="bar"><i style="width:${summary.completion_percent}%"></i></div>
      <span class="fine muted">${t.done} done · ${summary.completion_percent}%</span></div>
    <div class="stat"><span class="k">In flight</span><span class="v">${t.in_progress + t.review}</span>
      <span class="fine muted">${t.in_progress} in progress, ${t.review} in review</span></div>
    <div class="stat"><span class="k">Estimated</span><span class="v">${summary.estimated_hours}h</span>
      <span class="fine muted">${summary.spent_hours}h spent</span></div>
    <div class="stat"><span class="k">Overdue</span><span class="v" style="color:${summary.overdue.length ? 'var(--bad)' : 'inherit'}">${summary.overdue.length}</span>
      <span class="fine muted">${summary.overdue.slice(0, 2).map((o) => esc(o.title)).join(', ') || 'nothing late'}</span></div>`;
}

async function refreshSide() {
  const [workload, activity] = await Promise.all([
    api(`/workload?project_id=${state.projectId}`), api('/activity?limit=12'),
  ]);
  const peak = Math.max(1, ...workload.map((w) => w.open_hours));
  $('workload').innerHTML = workload.map((w) => `
    <div class="wl"><span class="name">${esc(w.name)}</span>
      <span class="track"><i style="width:${(w.open_hours / peak) * 100}%"></i></span>
      <span class="muted">${w.open_hours}h${w.overdue ? ` · <span style="color:var(--bad)">${w.overdue} late</span>` : ''}</span>
    </div>`).join('') || '<p class="muted">Nobody has open tasks.</p>';
  $('activity').innerHTML = activity.map((a) => {
    // the detail is often just the title again, which reads like a stutter
    const detail = a.detail && a.detail !== a.task_title ? esc(a.detail) : '';
    const when = String(a.created_at || '').slice(5, 16);
    return `<li>${esc(a.actor || 'someone')} ${esc(a.action.replace('.', ' '))}
      ${a.task_title ? `<b>${esc(a.task_title)}</b>` : ''} ${detail}
      <span class="fine">${esc(when)}</span></li>`;
  }).join('');
}

// ------------------------------------------------------------------- board
function renderBoard() {
  $('board').innerHTML = STATUSES.map(([key, label]) => {
    const tasks = state.tasks.filter((t) => t.status === key);
    return `<section class="column" data-status="${key}">
      <h2>${label} <span class="count">${tasks.length}</span></h2>
      ${tasks.map(taskCard).join('')}
    </section>`;
  }).join('');
  document.querySelectorAll('.task').forEach((el) => {
    el.addEventListener('dragstart', onDragStart);
    el.addEventListener('dragend', () => el.classList.remove('dragging'));
    el.addEventListener('click', () => openTask(Number(el.dataset.id)));
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') openTask(Number(el.dataset.id));
      // keyboard alternative to dragging
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        const order = STATUSES.map(([s]) => s);
        const task = state.tasks.find((t) => t.id === Number(el.dataset.id));
        const next = order[order.indexOf(task.status) + (e.key === 'ArrowRight' ? 1 : -1)];
        if (next) move(task.id, next);
      }
    });
  });
  document.querySelectorAll('.column').forEach((col) => {
    col.addEventListener('dragover', (e) => { e.preventDefault(); col.classList.add('drop'); });
    col.addEventListener('dragleave', () => col.classList.remove('drop'));
    col.addEventListener('drop', (e) => {
      e.preventDefault();
      col.classList.remove('drop');
      move(Number(e.dataTransfer.getData('text/plain')), col.dataset.status);
    });
  });
}

function taskCard(t) {
  const late = t.due_date && t.status !== 'done' && t.due_date < new Date().toISOString().slice(0, 10);
  return `<article class="task" draggable="true" tabindex="0" data-id="${t.id}"
      aria-label="${esc(t.title)}, ${t.status}">
    <div class="title">${esc(t.title)}</div>
    <div class="meta">
      <span class="pill ${t.priority}">${t.priority}</span>
      ${t.estimate_hours ? `<span>${t.estimate_hours}h</span>` : ''}
      ${t.due_date ? `<span class="${late ? 'pill overdue' : ''}">${late ? 'due ' : ''}${t.due_date}</span>` : ''}
      ${t.comment_count ? `<span>💬 ${t.comment_count}</span>` : ''}
      <span style="margin-left:auto" title="${esc(t.assignee_name || 'unassigned')}">
        ${t.assignee_name ? `<span class="avatar">${esc(initials(t.assignee_name))}</span>` : ''}</span>
    </div>
  </article>`;
}

function onDragStart(e) {
  e.dataTransfer.setData('text/plain', e.currentTarget.dataset.id);
  e.currentTarget.classList.add('dragging');
}

async function move(taskId, status) {
  const task = state.tasks.find((t) => t.id === taskId);
  if (!task || task.status === status) return;
  task.status = status;           // optimistic: the card moves immediately
  renderBoard();
  try {
    await api(`/tasks/${taskId}`, { method: 'PATCH', body: JSON.stringify({ status }) });
  } catch (err) {
    alert(err.message);
  }
  await Promise.all([refreshTasks(), refreshStats(), refreshSide()]);
  drawBurndown();
}

// ------------------------------------------------------------------ dialog
function fillSelect(el, options, selected) {
  el.innerHTML = options.map(([value, label]) =>
    `<option value="${value}"${String(value) === String(selected ?? '') ? ' selected' : ''}>${esc(label)}</option>`).join('');
}

function openDialog(task) {
  state.editing = task;
  $('taskDialogTitle').textContent = task ? `Task #${task.id}` : 'New task';
  $('taskTitle').value = task?.title || '';
  $('taskDescription').value = task?.description || '';
  fillSelect($('taskStatus'), STATUSES, task?.status || 'todo');
  fillSelect($('taskPriority'), PRIORITIES.map((p) => [p, p]), task?.priority || 'medium');
  fillSelect($('taskAssignee'), [['', 'Unassigned'], ...state.users.map((u) => [u.id, u.name])], task?.assignee_id || '');
  fillSelect($('taskSprint'), [['', 'Backlog'], ...state.sprints.map((s) => [s.id, s.name])], task?.sprint_id || '');
  $('taskEstimate').value = task?.estimate_hours ?? 0;
  $('taskDue').value = task?.due_date || '';
  $('taskDelete').hidden = !task;
  $('taskError').textContent = '';
  $('taskComments').innerHTML = task?.comments?.length
    ? `<h3 style="font-size:13px;margin:14px 0 0">Comments</h3>` + task.comments.map((c) =>
      `<div class="comment"><b>${esc(c.author)} <span class="fine muted">${esc(c.created_at)}</span></b>${esc(c.body)}</div>`).join('')
    : '';
  $('taskDialog').showModal();
}

async function openTask(id) {
  openDialog(await api(`/tasks/${id}`));
}

$('newTask').addEventListener('click', () => openDialog(null));
$('taskCancel').addEventListener('click', () => $('taskDialog').close());

$('taskSave').addEventListener('click', async () => {
  const payload = {
    title: $('taskTitle').value.trim(),
    description: $('taskDescription').value,
    status: $('taskStatus').value,
    priority: $('taskPriority').value,
    assignee_id: $('taskAssignee').value ? Number($('taskAssignee').value) : null,
    sprint_id: $('taskSprint').value ? Number($('taskSprint').value) : null,
    estimate_hours: Number($('taskEstimate').value || 0),
    due_date: $('taskDue').value || null,
  };
  if (!payload.title) { $('taskError').textContent = 'A title is required.'; return; }
  try {
    if (state.editing) {
      await api(`/tasks/${state.editing.id}`, { method: 'PATCH', body: JSON.stringify(payload) });
    } else {
      await api(`/projects/${state.projectId}/tasks`, { method: 'POST', body: JSON.stringify(payload) });
    }
    $('taskDialog').close();
    await loadProject();
  } catch (err) {
    $('taskError').textContent = err.message;
  }
});

$('taskDelete').addEventListener('click', async () => {
  if (!state.editing || !confirm('Delete this task?')) return;
  await api(`/tasks/${state.editing.id}`, { method: 'DELETE' });
  $('taskDialog').close();
  await loadProject();
});

// --------------------------------------------------------------- burndown
async function drawBurndown() {
  const sprintId = $('sprintSelect').value;
  const canvas = $('burndown');
  const ctx = canvas.getContext('2d');
  const ratio = window.devicePixelRatio || 1;
  canvas.width = canvas.clientWidth * ratio;
  canvas.height = 220 * ratio;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, canvas.clientWidth, 220);
  if (!sprintId) { $('burndownNote').textContent = 'No sprints in this project yet.'; return; }

  const data = await api(`/sprints/${sprintId}/burndown`);
  const css = (name) => getComputedStyle(document.body).getPropertyValue(name).trim();
  const w = canvas.clientWidth;
  const h = 220;
  const pad = { l: 38, r: 10, t: 12, b: 24 };
  const max = Math.max(1, data.committed_hours);
  const x = (i) => pad.l + (i * (w - pad.l - pad.r)) / Math.max(1, data.days.length - 1);
  const y = (v) => pad.t + (1 - v / max) * (h - pad.t - pad.b);

  ctx.strokeStyle = css('--border');
  ctx.fillStyle = css('--muted');
  ctx.font = '11px system-ui';
  ctx.beginPath();
  for (let i = 0; i <= 4; i++) {
    const value = (max / 4) * i;
    const yy = Math.round(y(value)) + 0.5;
    ctx.moveTo(pad.l, yy);
    ctx.lineTo(w - pad.r, yy);
    ctx.fillText(value.toFixed(0), 6, yy + 3);
  }
  ctx.stroke();

  const line = (values, color, dashed) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.setLineDash(dashed ? [5, 4] : []);
    ctx.beginPath();
    let started = false;
    values.forEach((v, i) => {
      if (v === null) return;
      if (!started) { ctx.moveTo(x(i), y(v)); started = true; } else ctx.lineTo(x(i), y(v));
    });
    ctx.stroke();
    ctx.setLineDash([]);
  };
  line(data.ideal, css('--muted'), true);
  line(data.actual, css('--accent'), false);
  ctx.fillText(data.days[0].slice(5), pad.l, h - 8);
  ctx.fillText(data.days.at(-1).slice(5), w - pad.r - 26, h - 8);
  $('burndownNote').textContent =
    `${data.tasks_done}/${data.tasks} tasks done · ${data.completed_hours}h of ${data.committed_hours}h ` +
    `· ${data.remaining_hours}h left. Dashed line is the ideal pace.`;
}

// ------------------------------------------------------------------ events
$('projectSelect').addEventListener('change', (e) => { state.projectId = Number(e.target.value); loadProject(); });
$('sprintSelect').addEventListener('change', drawBurndown);
$('assigneeFilter').addEventListener('change', refreshTasks);
$('sprintFilter').addEventListener('change', refreshTasks);
let searchTimer;
$('search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(refreshTasks, 250); });

if (state.token) start().catch(signOut);
