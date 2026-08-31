"""The lab web server -- an ORBAT editor with no bot, no Discord and no Sheets.

Run it with:

    uvicorn lab.devserver:app --reload --port 8081

Nothing in this package is imported by bot.py or web/, and nothing here imports
cogs/ or web/ in return. The only thing it borrows from the real site is
web/static/style.css, mounted read-only so the prototype looks like the place it
would eventually live. Turning this into Stufe B means moving these routes into
web/app.py behind a flag; until then the production code cannot even see it.

Deliberately missing, because Stufe A is about the editor and nothing else:
OAuth (there is no sign-in), permission checks, and any contact with Discord.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from lab import diff as diffing
from lab import parser, render, store
from lab.seed import DEMO_TEXT, seed_if_empty

_HERE = Path(__file__).parent
_STATIC = _HERE.parent / 'web' / 'static'

app = FastAPI(title='ORBAT Lab', docs_url=None, redoc_url=None, openapi_url=None)
app.mount('/static', StaticFiles(directory=str(_STATIC)), name='static')
templates = Jinja2Templates(directory=str(_HERE / 'templates'))
templates.env.globals.update(demo_text=DEMO_TEXT)


@app.on_event('startup')
def _startup():
    store.init()
    seed_if_empty()


def _page(request: Request, name: str, context: dict, status: int = 200):
    context = {'msg': request.query_params.get('msg'), **context}
    return templates.TemplateResponse(request=request, name=name, context=context,
                                      status_code=status)


def _back(path: str, msg: str = None):
    suffix = f'?msg={msg}' if msg else ''
    return RedirectResponse(f'{path}{suffix}', status_code=303)


# -- ORBAT templates --------------------------------------------------------

@app.get('/', response_class=HTMLResponse)
def index(request: Request):
    return _page(request, 'index.html', {'orbats': store.list_orbats()})


@app.post('/orbats')
def create_orbat(name: str = Form(...), description: str = Form(None)):
    name = (name or '').strip()
    if not name:
        return _back('/', 'Name fehlt.')
    orbat_id = store.create_orbat(name, (description or '').strip() or None)
    return _back(f'/orbats/{orbat_id}')


@app.post('/orbats/{orbat_id}/duplicate')
def duplicate_orbat(orbat_id: int, name: str = Form(...)):
    new_id = store.duplicate_orbat(orbat_id, (name or '').strip() or 'Kopie')
    return _back(f'/orbats/{new_id}', 'Kopiert — ohne Belegungen.')


@app.post('/orbats/{orbat_id}/delete')
def delete_orbat(orbat_id: int):
    store.delete_orbat(orbat_id)
    return _back('/', 'ORBAT gelöscht.')


# -- the editor -------------------------------------------------------------

def _editor_context(orbat_id: int, text: str = None, result=None, changes=None,
                    pending_text: str = None) -> dict:
    orbat = store.get_orbat(orbat_id)
    squads = store.load_squads(orbat_id)
    if text is None:
        text = orbat.get('source_text') or (parser.to_text(squads) if squads else '')
    preview = None
    if result is not None and result.ok:
        preview = render.build_board([
            {'id': None, 'name': s.name, 'column_side': s.column,
             'exclude_from_count': s.exclude_from_count,
             'slots': [{'role_name': slot.role_name, 'reserved_unit': slot.reserved_unit,
                        'booking': None, 'pending': False} for slot in s.slots]}
            for s in result.squads
        ])
    return {
        'orbat': orbat,
        'text': text,
        'result': result,
        'diff': changes,
        'summary': diffing.summarise(changes) if changes else None,
        'preview': preview,
        'pending_text': pending_text,
        'ops': store.list_ops(orbat_id),
        'stored_slots': sum(len(s['slots']) for s in squads),
    }


@app.get('/orbats/{orbat_id}', response_class=HTMLResponse)
def editor(request: Request, orbat_id: int):
    if store.get_orbat(orbat_id) is None:
        return _page(request, 'missing.html', {}, status=404)
    return _page(request, 'editor.html', _editor_context(orbat_id))


@app.post('/orbats/{orbat_id}', response_class=HTMLResponse)
def save(request: Request, orbat_id: int, text: str = Form(''), action: str = Form('preview')):
    if store.get_orbat(orbat_id) is None:
        return _page(request, 'missing.html', {}, status=404)

    result = parser.parse(text)
    stored = store.load_squads(orbat_id)
    changes = diffing.build_diff(stored, result.squads) if result.ok else None

    if not result.ok:
        return _page(request, 'editor.html',
                     _editor_context(orbat_id, text, result, None))

    if action == 'preview':
        return _page(request, 'editor.html',
                     _editor_context(orbat_id, text, result, changes))

    # A save that would drop somebody's booking always stops for a confirmation.
    # Everything else applies straight away -- asking on every edit would train
    # people to click through the one that matters.
    if action == 'save' and changes.needs_confirmation:
        return _page(request, 'editor.html',
                     _editor_context(orbat_id, text, result, changes, pending_text=text))

    store.apply_structure(orbat_id, result.squads, changes, source_text=text)
    return _back(f'/orbats/{orbat_id}', f'Gespeichert — {diffing.summarise(changes)}.')


# -- operations and the board ----------------------------------------------

@app.post('/orbats/{orbat_id}/ops')
def create_op(orbat_id: int, name: str = Form(...)):
    op_id = store.create_op(orbat_id, (name or '').strip() or 'Operation')
    return _back(f'/orbats/{orbat_id}/ops/{op_id}')


@app.get('/orbats/{orbat_id}/ops/{op_id}', response_class=HTMLResponse)
def board(request: Request, orbat_id: int, op_id: int):
    orbat, op = store.get_orbat(orbat_id), store.get_op(op_id)
    if orbat is None or op is None or op['orbat_id'] != orbat_id:
        return _page(request, 'missing.html', {}, status=404)
    squads = store.load_squads(orbat_id, op_id=op_id)
    open_slots = [
        (slot, squad) for squad in squads for slot in squad['slots'] if not slot['booking']
    ]
    return _page(request, 'board.html', {
        'orbat': orbat, 'op': op,
        'board': render.build_board(squads),
        'open_slots': open_slots,
    })


@app.post('/orbats/{orbat_id}/ops/{op_id}/book')
def book(orbat_id: int, op_id: int, slot_id: int = Form(...), member_name: str = Form(...),
         unit: str = Form(None), status: str = Form('approved')):
    member_name = (member_name or '').strip()
    if not member_name:
        return _back(f'/orbats/{orbat_id}/ops/{op_id}', 'Name fehlt.')
    store.book(op_id, slot_id, member_name, (unit or '').strip() or None, status)
    return _back(f'/orbats/{orbat_id}/ops/{op_id}')


@app.post('/orbats/{orbat_id}/ops/{op_id}/free')
def free(orbat_id: int, op_id: int, slot_id: int = Form(...)):
    store.unbook(op_id, slot_id)
    return _back(f'/orbats/{orbat_id}/ops/{op_id}')


@app.post('/orbats/{orbat_id}/ops/{op_id}/delete')
def delete_op(orbat_id: int, op_id: int):
    store.delete_op(op_id)
    return _back(f'/orbats/{orbat_id}', 'Einsatz gelöscht.')


@app.get('/healthz', response_class=PlainTextResponse)
def healthz():
    return 'ok'
