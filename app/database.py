import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / 'tasks.db'


@contextmanager
def connection():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize():
    with connection() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT NOT NULL,
            assignee TEXT,
            due_date TEXT,
            priority TEXT NOT NULL CHECK(priority IN ('Low', 'Medium', 'High')),
            category TEXT NOT NULL CHECK(category IN
                ('Meeting', 'Follow-up', 'Document', 'Communication', 'Payment', 'Research', 'Other')),
            status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending', 'Completed')),
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )''')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(tasks)')}
        if 'notion_page_id' not in columns:
            db.execute('ALTER TABLE tasks ADD COLUMN notion_page_id TEXT')
        if 'notion_export_state' not in columns:
            db.execute("ALTER TABLE tasks ADD COLUMN notion_export_state TEXT NOT NULL DEFAULT ''")
        # Remove retired integration fields while preserving tasks and Notion state.
        for column in ('todoist_task_id', 'todoist_export_state'):
            if column in columns:
                db.execute(f'ALTER TABLE tasks DROP COLUMN {column}')
        db.execute('''CREATE TABLE IF NOT EXISTS extraction_requests (
            request_id TEXT PRIMARY KEY, text_hash TEXT NOT NULL, task_ids TEXT NOT NULL
        )''')


def saved_extraction(db, request_id, text_hash):
    row = db.execute('SELECT * FROM extraction_requests WHERE request_id = ?', (request_id,)).fetchone()
    if row is None:
        return None
    if row['text_hash'] != text_hash:
        raise ValueError('Request ID was already used for a different message.')
    tasks = [db.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
             for task_id in json.loads(row['task_ids'])]
    return [dict(task) for task in tasks if task is not None]


def get_extraction(request_id, text_hash):
    with connection() as db:
        return saved_extraction(db, request_id, text_hash)


def save_tasks(tasks, request_id=None, text_hash=None):
    saved = []
    with connection() as db:
        if request_id:
            db.execute('BEGIN IMMEDIATE')
            existing = saved_extraction(db, request_id, text_hash)
            if existing is not None:
                return existing
        for task in tasks:
            cursor = db.execute(
                '''INSERT INTO tasks (task, assignee, due_date, priority, category)
                   VALUES (:task, :assignee, :due_date, :priority, :category)''',
                task.model_dump(),
            )
            saved.append(dict(db.execute('SELECT * FROM tasks WHERE id = ?', (cursor.lastrowid,)).fetchone()))
        if request_id:
            db.execute('INSERT INTO extraction_requests VALUES (?, ?, ?)',
                       (request_id, text_hash, json.dumps([task['id'] for task in saved])))
    return saved


def list_tasks():
    with connection() as db:
        return [dict(row) for row in db.execute('SELECT * FROM tasks ORDER BY id DESC')]


def toggle_task(task_id):
    with connection() as db:
        db.execute('''UPDATE tasks SET status = CASE status
            WHEN 'Pending' THEN 'Completed' ELSE 'Pending' END WHERE id = ?''', (task_id,))
        row = db.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
        return dict(row) if row else None


def delete_task(task_id):
    with connection() as db:
        return db.execute('DELETE FROM tasks WHERE id = ?', (task_id,)).rowcount > 0


def get_task(task_id):
    with connection() as db:
        row = db.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
        return dict(row) if row else None


def claim_notion_export(task_id):
    with connection() as db:
        return db.execute("""UPDATE tasks SET notion_export_state = 'sending'
            WHERE id = ? AND notion_page_id IS NULL AND notion_export_state = ''""",
            (task_id,)).rowcount > 0


def finish_notion_export(task_id, state, page_id=None):
    with connection() as db:
        db.execute('UPDATE tasks SET notion_export_state = ?, notion_page_id = ? WHERE id = ?',
                   (state, page_id, task_id))
