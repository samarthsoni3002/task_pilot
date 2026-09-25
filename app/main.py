import logging
import os
import traceback
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app import database, hf_service
from app import notion_service

STATIC_DIR = Path(__file__).resolve().parent / 'static'
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.initialize()
    yield


app = FastAPI(title='TaskPilot', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')
app.include_router(notion_service.router)


class ExtractRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=50000)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


@app.get('/')
def index():
    return FileResponse(STATIC_DIR / 'index.html')


@app.get('/app')
def workspace():
    return FileResponse(STATIC_DIR / 'app.html')


@app.get('/api/integrations')
def integrations():
    return {
        'notion': all(os.getenv(name, '').strip() for name in ('NOTION_API_KEY', 'NOTION_DATA_SOURCE_ID')),
    }


@app.post('/api/extract')
def extract(payload: ExtractRequest):
    text_hash = sha256(payload.text.encode()).hexdigest()
    try:
        saved = database.get_extraction(payload.request_id, text_hash) if payload.request_id else None
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    try:
        tasks = hf_service.extract_tasks(payload.text) if saved is None else []
    except hf_service.ConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        trace = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        model = os.getenv('HF_MODEL', '')
        key = os.getenv('HF_TOKEN', '').strip()
        if key:
            trace = trace.replace(key, '[REDACTED]')
            model = model.replace(key, '[REDACTED]')
        # Log the full traceback after redaction, without appending the raw exception.
        logger.exception('AI extraction failed (HF_MODEL=%s)\n%s', model, trace, exc_info=False)
        raise HTTPException(status_code=502, detail='Could not extract tasks using the AI model.') from exc
    if saved is None:
        try:
            saved = database.save_tasks(tasks, payload.request_id, text_hash)
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
    configured = all(os.getenv(name, '').strip() for name in ('NOTION_API_KEY', 'NOTION_DATA_SOURCE_ID'))
    synced = 0
    for task in saved:
        if configured:
            try:
                notion_service.export_task(task['id'])
            except Exception:
                # Notion failures must never undo locally saved extraction results.
                pass
        current = database.get_task(task['id'])
        if current:
            task.update(current)
        synced += bool(task.get('notion_page_id'))
    return {
        'tasks': [{key: value for key, value in task.items() if key != 'notion_page_id'} for task in saved],
        'notion_sync': {'configured': configured, 'attempted': len(saved) if configured else 0,
                        'synced': synced, 'failed': len(saved) - synced},
    }


@app.get('/api/tasks')
def tasks():
    return database.list_tasks()


@app.patch('/api/tasks/{task_id}')
def toggle(task_id: int):
    task = database.toggle_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail='Task not found.')
    return task


@app.delete('/api/tasks/{task_id}', status_code=204)
def delete(task_id: int):
    if not database.delete_task(task_id):
        raise HTTPException(status_code=404, detail='Task not found.')
    return Response(status_code=204)


@app.get('/api/health')
def health():
    return {'status': 'ok'}
