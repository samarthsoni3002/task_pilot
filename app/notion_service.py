import os
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import requests
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException

from app import database

load_dotenv(Path(__file__).resolve().parent.parent / '.env')
router = APIRouter(prefix='/api/tasks')


def configuration():
    key = os.getenv('NOTION_API_KEY', '').strip()
    source = os.getenv('NOTION_DATA_SOURCE_ID', '').strip()
    if not key or not source:
        raise HTTPException(503, 'Notion integration is not configured.')
    try:
        source = str(UUID(source))
    except ValueError:
        raise HTTPException(503, 'NOTION_DATA_SOURCE_ID must be a valid data source ID.') from None
    return source, {'Authorization': 'Bearer ' + key, 'Notion-Version': '2025-09-03'}


def rich_text(value):
    value = value or ''
    return [{'type': 'text', 'text': {'content': value[i:i + 2000]}}
            for i in range(0, len(value), 2000)]


def page_payload(task, source, schema):
    required = {'Name': ('title',), 'Status': ('status', 'select'), 'Priority': ('select',),
                'Due': ('date',), 'Category': ('select',), 'Assignee': ('rich_text',)}
    for name, allowed in required.items():
        if schema.get(name, {}).get('type') not in allowed:
            raise HTTPException(400, f'Notion property {name} must have type {" or ".join(allowed)}.')
    properties = {'Name': {'title': rich_text(task['task'])},
                  'Assignee': {'rich_text': rich_text(task['assignee'])}}
    for name, field in [('Status', 'status'), ('Priority', 'priority'), ('Category', 'category')]:
        kind = schema[name]['type']
        options = {option['name'] for option in schema[name][kind].get('options', [])}
        if task[field] not in options:
            raise HTTPException(400, f'Add the option {task[field]} to Notion property {name}.')
        properties[name] = {kind: {'name': task[field]}}
    deadline = task['due_date'] or ''
    valid_date = None
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', deadline):
        try:
            valid_date = date.fromisoformat(deadline).isoformat()
        except ValueError:
            pass
    if valid_date:
        properties['Due'] = {'date': {'start': valid_date}}
    children = []
    if deadline and not valid_date:
        children.append({'object': 'block', 'type': 'paragraph',
                         'paragraph': {'rich_text': rich_text('Original deadline: ' + deadline)}})
    return {'parent': {'type': 'data_source_id', 'data_source_id': source},
            'properties': properties, 'children': children}


@router.post('/{task_id}/notion')
def export_task(task_id: int):
    source, headers = configuration()
    task = database.get_task(task_id)
    if task is None:
        raise HTTPException(404, 'Task not found.')
    if task['notion_page_id']:
        return task
    if task['notion_export_state']:
        raise HTTPException(409, 'Notion export is in progress or unconfirmed. Check Notion before retrying; duplicate export is blocked.')
    try:
        response = requests.get('https://api.notion.com/v1/data_sources/' + source,
                                headers=headers, timeout=30)
        response.raise_for_status()
        schema = response.json()['properties']
    except Exception:
        raise HTTPException(502, 'Could not read Notion data source. Check the key, data source ID, and connection access.') from None
    payload = page_payload(task, source, schema)
    if not database.claim_notion_export(task_id):
        raise HTTPException(409, 'This task has already been exported or an export is in progress.')
    try:
        response = requests.post('https://api.notion.com/v1/pages', headers=headers,
                                 json=payload, timeout=30)
        if 400 <= response.status_code < 500 and response.status_code != 408:
            database.finish_notion_export(task_id, '')
            raise HTTPException(502, f'Notion rejected the export (HTTP {response.status_code}). Check property options and integration permissions.')
        response.raise_for_status()
        page_id = response.json()['id']
        if not page_id:
            raise ValueError('Missing page ID')
        database.finish_notion_export(task_id, 'synced', page_id)
    except HTTPException:
        raise
    except Exception:
        database.finish_notion_export(task_id, 'uncertain')
        raise HTTPException(502, 'Notion export could not be confirmed. Check Notion; another export is blocked to prevent duplicates.') from None
    return database.get_task(task_id)
