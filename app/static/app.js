const form = document.querySelector('#extract-form');
const message = document.querySelector('#message');
const extractButton = document.querySelector('#extract-button');
const loading = document.querySelector('#loading');
const feedback = document.querySelector('#feedback');
const rows = document.querySelector('#task-rows');
let tasks = [];
let pendingExtraction = null;

function notify(text, error = false) {
  feedback.textContent = text;
  feedback.classList.toggle('error', error);
  feedback.hidden = !text;
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Request failed. Please try again.');
  }
  return response.status === 204 ? null : response.json();
}

function render() {
  rows.replaceChildren();
  document.querySelector('#empty-state').hidden = tasks.length > 0;
  document.querySelector('#table-container').hidden = tasks.length === 0;
  for (const task of tasks) {
    const row = document.createElement('tr');
    for (const field of ['task', 'category', 'assignee', 'due_date', 'priority', 'status']) {
      const cell = document.createElement('td');
      if (field === 'priority' || field === 'status') {
        const badge = document.createElement('span');
        badge.className = `badge ${task[field].toLowerCase()}`;
        badge.textContent = task[field];
        cell.append(badge);
      } else {
        cell.textContent = task[field] || '—';
      }
      row.append(cell);
    }
    const cell = document.createElement('td');
    const actions = document.createElement('div');
    actions.className = 'actions';
    for (const action of ['toggle', 'delete']) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = action === 'delete' ? 'Delete' : task.status === 'Pending' ? 'Complete' : 'Reopen';
      button.className = action;
      button.addEventListener('click', async () => {
        const buttons = actions.querySelectorAll('button');
        buttons.forEach(item => { item.disabled = true; });
        try {
          const updated = await request(`/api/tasks/${task.id}`, { method: action === 'delete' ? 'DELETE' : 'PATCH' });
          tasks = action === 'delete' ? tasks.filter(item => item.id !== task.id) : tasks.map(item => item.id === task.id ? updated : item);
          notify('');
          render();
        } catch (error) {
          notify(error.message, true);
        } finally {
          render();
        }
      });
      actions.append(button);
    }
    cell.append(actions);
    row.append(cell);
    rows.append(row);
  }
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (extractButton.disabled) return;
  const text = message.value.trim();
  if (!text) { notify('Please paste a message first.', true); return; }
  if (!pendingExtraction || pendingExtraction.text !== text) {
    pendingExtraction = { text, request_id: crypto.randomUUID() };
  }
  extractButton.disabled = true;
  loading.hidden = false;
  form.setAttribute('aria-busy', 'true');
  notify('');
  try {
    const payload = await request('/api/extract', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(pendingExtraction),
    });
    const extractedTasks = Array.isArray(payload)
      ? payload
      : Array.isArray(payload?.tasks) ? payload.tasks : [];
    const returnedIds = new Set(extractedTasks.map(task => task.id));
    tasks = [...[...extractedTasks].reverse(), ...tasks.filter(task => !returnedIds.has(task.id))];
    render();
    const sync = payload?.notion_sync;
    if (typeof sync?.configured === 'boolean') showNotionStatus(sync.configured);
    else if (sync?.synced > 0) showNotionStatus(true);
    if (!extractedTasks.length) notify('No action items found in this message.');
    else if (!sync) notify(`${extractedTasks.length} task${extractedTasks.length === 1 ? '' : 's'} extracted.`);
    else if (sync.configured === false) notify('Tasks extracted, but Notion is not configured.');
    else if (sync.synced === extractedTasks.length) notify('Your tasks have been updated in Notion ✓');
    else if (sync.synced) notify(`Tasks extracted. ${sync.synced} of ${extractedTasks.length} tasks were added to Notion.`);
    else notify('Tasks extracted, but Notion could not be updated.');
  } catch (error) {
    notify(error.message, true);
  } finally {
    extractButton.disabled = false;
    loading.hidden = true;
    form.setAttribute('aria-busy', 'false');
  }
});

async function initialize() {
  extractButton.disabled = true;
  try {
    tasks = await request('/api/tasks');
    render();
  } catch (error) {
    notify(error.message, true);
  } finally {
    extractButton.disabled = false;
  }
}
initialize();

function showNotionStatus(configured) {
  const badge = document.querySelector('#notion-status');
  if (!badge) return;
  badge.textContent = configured ? 'Connected' : 'Not configured';
  badge.classList.toggle('completed', configured);
}

request('/api/integrations', { cache: 'no-store' }).then(status => {
  showNotionStatus(status.notion === true);
}).catch(() => {
  const badge = document.querySelector('#notion-status');
  if (badge?.textContent === 'Checking…') badge.textContent = 'Status unavailable';
});
