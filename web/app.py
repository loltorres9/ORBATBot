"""The FastAPI application behind the web UI.

It runs inside the bot's own process and event loop, so a page can post a
Discord message, register a persistent view or read a member's roles directly —
no polling, no second deployment, no outbox table. `web/server.py` starts it.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cogs.events import _RECURRENCE_LABELS, _recurrence_text
from utils import database
from web import auth, roles as roles_service, service
from web.auth import Forbidden, NotAuthenticated
from web.config import LOGO_NAMES, WebConfig
from web.guilds import (
    can_create_events,
    can_manage_event,
    forget_member,
    is_admin,
    mentionable_roles,
    postable_channels,
    resolve_member,
    user_guilds,
)
from web.helpers import fmt_date, fmt_dt, fmt_input, message_link, relative

_HERE = Path(__file__).parent


def _logo_url() -> str:
    """The site logo, if one was dropped into web/static — '' when there is none.

    The file's modification time rides along as a query string so replacing the
    logo isn't hidden behind a cached copy in someone's browser.
    """
    for name in LOGO_NAMES:
        path = _HERE / 'static' / name
        if path.exists():
            return f"/static/{name}?v={int(path.stat().st_mtime)}"
    return ''


def create_app(bot, config: WebConfig) -> FastAPI:
    app = FastAPI(title='ORBAT', docs_url=None, redoc_url=None, openapi_url=None)
    app.mount('/static', StaticFiles(directory=_HERE / 'static'), name='static')

    templates = Jinja2Templates(directory=str(_HERE / 'templates'))
    templates.env.globals.update(
        brand=config.brand,
        logo_url=_logo_url(),
        fmt_dt=fmt_dt,
        fmt_date=fmt_date,
        fmt_input=fmt_input,
        relative=relative,
        message_link=message_link,
        recurrence_text=_recurrence_text,
        recurrence_labels=_RECURRENCE_LABELS,
        reminder_choices=service.REMINDER_CHOICES,
        repeat_choices=service.REPEAT_CHOICES,
    )

    # -- request plumbing ---------------------------------------------------

    def session_of(request: Request):
        return auth.read_session(request, config)

    def require_session(request: Request) -> dict:
        session = session_of(request)
        if session is None:
            raise NotAuthenticated(request.url.path)
        return session

    def render(request: Request, name: str, context: dict, status: int = 200):
        session = session_of(request)
        flash = auth.read_flash(request, config)
        context = {
            'session': session,
            'avatar': auth.avatar_url(session) if session else None,
            'flash': flash,
            'csrf': (session or {}).get('csrf', ''),
            **context,
        }
        response = templates.TemplateResponse(
            request=request, name=name, context=context, status_code=status
        )
        if flash:
            response.delete_cookie(auth.FLASH_COOKIE, path='/')
        return response

    def redirect(request: Request, path: str, kind: str = None, text: str = None):
        response = RedirectResponse(path, status_code=303)
        if text:
            auth.set_flash(response, config, kind or 'ok', text)
        return response

    def form_values(form) -> dict:
        """Flatten a submitted form, keeping the ping roles as a list."""
        data = {key: form.get(key) for key in form.keys()}
        data['mention_ids'] = form.getlist('mention_ids')
        return data

    async def guild_context(request: Request, guild_id: str) -> dict:
        """The signed-in user seen as a member of one guild, or an error."""
        session = require_session(request)
        guild = bot.get_guild(int(guild_id)) if str(guild_id).isdigit() else None
        if guild is None:
            raise Forbidden("I'm not in that server.")
        member = await resolve_member(bot, guild, session['id'])
        if member is None:
            raise Forbidden(f"You're not a member of {guild.name}.")
        return {
            'session': session,
            'guild': guild,
            'member': member,
            'tz': await database.get_guild_timezone(str(guild.id)),
            'may_create': can_create_events(member),
            'is_admin': is_admin(member),
        }

    async def event_context(request: Request, guild_id: str, event_id: int) -> dict:
        context = await guild_context(request, guild_id)
        event = await database.get_event(event_id)
        if event is None or event['guild_id'] != str(context['guild'].id):
            raise Forbidden("No such event on this server.")
        context['event'] = event
        context['may_manage'] = can_manage_event(context['member'], event)
        return context

    def require_manage(context: dict):
        if not context['may_manage']:
            raise Forbidden("Only the organiser or a server admin can change this event.")

    # -- error handling -----------------------------------------------------

    @app.exception_handler(NotAuthenticated)
    async def _not_authenticated(request: Request, exc: NotAuthenticated):
        return RedirectResponse(f"/login?next={exc.next_path}", status_code=303)

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden):
        return render(request, 'error.html',
                      {'title': 'Not allowed', 'message': exc.message}, status=403)

    # -- auth ---------------------------------------------------------------

    @app.get('/login')
    async def login(request: Request, next: str = '/'):
        if session_of(request):
            return RedirectResponse(auth.safe_next(next), status_code=303)
        url, state = auth.authorize_url(config, request, next)
        response = RedirectResponse(url, status_code=303)
        # The nonce inside the signed state is compared against this cookie on
        # the way back, so a callback nobody here started is rejected.
        response.set_cookie(
            auth.STATE_COOKIE, state, max_age=auth.STATE_MAX_AGE, httponly=True,
            samesite='lax', secure=config.cookie_secure, path='/',
        )
        return response

    @app.get('/auth/callback')
    async def callback(request: Request, code: str = None, state: str = None,
                       error: str = None, error_description: str = None):
        if error:
            raise Forbidden(f"Discord cancelled the login: {error_description or error}")
        if not code or not state:
            raise Forbidden("That callback was missing its code — start again from the front page.")
        if state != request.cookies.get(auth.STATE_COOKIE):
            raise Forbidden("That login didn't start here. Try again from the front page.")

        payload = auth.read_state(config, state)
        profile = await auth.exchange_code(config, request, code)

        response = RedirectResponse(auth.safe_next(payload.get('n')), status_code=303)
        auth.write_session(response, config, auth.new_session(profile))
        response.delete_cookie(auth.STATE_COOKIE, path='/')
        return response

    @app.post('/logout')
    async def logout(request: Request):
        session = session_of(request)
        if session:
            auth.check_csrf(session, (await request.form()).get('csrf'))
        response = RedirectResponse('/', status_code=303)
        auth.clear_session(response)
        return response

    # -- pages --------------------------------------------------------------

    @app.get('/', response_class=HTMLResponse)
    async def index(request: Request):
        session = session_of(request)
        if session is None:
            return render(request, 'login.html', {})
        guilds = await user_guilds(bot, session['id'])
        if len(guilds) == 1:
            return RedirectResponse(f"/g/{guilds[0]['guild'].id}", status_code=303)
        return render(request, 'guilds.html', {'guilds': guilds})

    @app.get('/g/{guild_id}', response_class=HTMLResponse)
    async def guild_events(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        tz = context['tz']

        upcoming = []
        for event in await database.get_upcoming_events(str(guild_id), limit=25):
            upcoming.append(await service.event_view_model(event, tz))
        past = [
            event for event in await database.get_guild_events(str(guild_id), limit=25)
            if event['status'] != 'scheduled'
        ][:10]

        return render(request, 'events.html', {
            **context, 'upcoming': upcoming, 'past': past,
        })

    @app.get('/g/{guild_id}/events/new', response_class=HTMLResponse)
    async def new_event_form(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        if not context['may_create']:
            raise Forbidden(
                "You need the Unit Leader role or Manage Server permission to create events."
            )
        return render(request, 'event_form.html', {
            **context,
            'mode': 'create',
            'channels': postable_channels(context['guild']),
            'roles': mentionable_roles(context['guild']),
            'values': {'reminder': 30, 'repeat': 'none', 'mention_ids': []},
            'error': None,
        })

    @app.post('/g/{guild_id}/events/new')
    async def create_event(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        if not context['may_create']:
            raise Forbidden(
                "You need the Unit Leader role or Manage Server permission to create events."
            )
        form = form_values(await request.form())
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            event_id, warnings = await service.create_event(
                bot, context['guild'], context['member'], context['tz'], form
            )
        except ValueError as e:
            return render(request, 'event_form.html', {
                **context,
                'mode': 'create',
                'channels': postable_channels(context['guild']),
                'roles': mentionable_roles(context['guild']),
                'values': form,
                'error': str(e),
            }, status=400)

        text = f"Event #{event_id} created and posted."
        if warnings:
            text += ' ' + ' '.join(warnings)
        return redirect(request, f"/g/{guild_id}/events/{event_id}", 'ok', text)

    @app.get('/g/{guild_id}/events/{event_id}', response_class=HTMLResponse)
    async def event_detail(request: Request, guild_id: str, event_id: int):
        context = await event_context(request, guild_id, event_id)
        view = await service.event_view_model(context['event'], context['tz'])
        mine = await database.get_event_signup(event_id, str(context['session']['id']))
        return render(request, 'event_detail.html', {
            **context, **view, 'my_response': mine['response'] if mine else None,
        })

    @app.get('/g/{guild_id}/events/{event_id}/edit', response_class=HTMLResponse)
    async def edit_event_form(request: Request, guild_id: str, event_id: int):
        context = await event_context(request, guild_id, event_id)
        require_manage(context)
        event = context['event']
        if event['status'] != 'scheduled':
            raise Forbidden(f"Event #{event_id} is {event['status']} and can't be changed.")

        custom = await database.get_event_responses(event_id)
        return render(request, 'event_form.html', {
            **context,
            'mode': 'edit',
            'channels': postable_channels(context['guild']),
            'roles': mentionable_roles(context['guild']),
            'values': {
                'title': event['title'],
                'start_time': fmt_input(event['event_time'], context['tz']),
                'duration': event['duration_minutes'] or '',
                'description': event['description'] or '',
                'location': event['location'] or '',
                'image_url': event['image_url'] or '',
                'reminder': event['reminder_minutes'] or 0,
                'repeat': event['recurrence'] or 'none',
                'repeat_until': fmt_input(event['recurrence_until'], context['tz']),
                'mention_ids': (event['mention_role_id'] or '').split(','),
                'responses': ' | '.join(
                    ('-' if row['is_decline'] else '')
                    + (f"{row['emoji']} " if row['emoji'] else '')
                    + row['label']
                    for row in custom
                ),
            },
            'error': None,
        })

    @app.post('/g/{guild_id}/events/{event_id}/edit')
    async def edit_event(request: Request, guild_id: str, event_id: int):
        context = await event_context(request, guild_id, event_id)
        require_manage(context)
        if context['event']['status'] != 'scheduled':
            raise Forbidden(f"Event #{event_id} is {context['event']['status']}.")
        form = form_values(await request.form())
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            notes = await service.edit_event(
                bot, context['guild'], context['event'], context['tz'], form
            )
        except ValueError as e:
            return render(request, 'event_form.html', {
                **context,
                'mode': 'edit',
                'channels': postable_channels(context['guild']),
                'roles': mentionable_roles(context['guild']),
                'values': form,
                'error': str(e),
            }, status=400)

        return redirect(request, f"/g/{guild_id}/events/{event_id}", 'ok',
                        ' '.join(['Event updated.'] + notes))

    @app.post('/g/{guild_id}/events/{event_id}/cancel')
    async def cancel_event(request: Request, guild_id: str, event_id: int):
        context = await event_context(request, guild_id, event_id)
        require_manage(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        if context['event']['status'] != 'scheduled':
            return redirect(request, f"/g/{guild_id}/events/{event_id}", 'warn',
                            f"Event #{event_id} is already {context['event']['status']}.")

        notes = await service.cancel_event(
            bot, context['guild'], context['event'], context['member'],
            (form.get('reason') or '').strip(), bool(form.get('stop_series')),
            context['tz'],
        )
        return redirect(request, f"/g/{guild_id}/events/{event_id}", 'ok',
                        ' '.join([f"Cancelled #{event_id}."] + notes))

    @app.get('/g/{guild_id}/events/{event_id}/delete', response_class=HTMLResponse)
    async def delete_event_form(request: Request, guild_id: str, event_id: int):
        context = await event_context(request, guild_id, event_id)
        require_manage(context)
        view = await service.event_view_model(context['event'], context['tz'])
        return render(request, 'event_delete.html', {**context, **view})

    @app.post('/g/{guild_id}/events/{event_id}/delete')
    async def delete_event(request: Request, guild_id: str, event_id: int):
        context = await event_context(request, guild_id, event_id)
        require_manage(context)
        auth.check_csrf(context['session'], (await request.form()).get('csrf'))

        notes = await service.delete_event(bot, context['event'])
        return redirect(request, f"/g/{guild_id}", 'ok',
                        ' '.join([f"Deleted event #{event_id}."] + notes))

    @app.post('/g/{guild_id}/events/{event_id}/rsvp')
    async def rsvp(request: Request, guild_id: str, event_id: int):
        context = await event_context(request, guild_id, event_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            note = await service.rsvp(
                bot, context['event'], context['member'], form.get('response')
            )
        except ValueError as e:
            return redirect(request, f"/g/{guild_id}/events/{event_id}", 'warn', str(e))
        return redirect(request, f"/g/{guild_id}/events/{event_id}", 'ok', note)

    # -- game roles ---------------------------------------------------------

    async def roles_page(request: Request, guild_id: str, context: dict,
                         error: str = None, status: int = 200):
        guild = context['guild']
        return render(request, 'roles.html', {
            **context,
            'entries': await roles_service.role_entries(guild, context['member']),
            'panel_in': await roles_service.panel_location(guild),
            'channels': postable_channels(guild) if context['is_admin'] else [],
            'can_assign': roles_service.can_assign(guild),
            'max_roles': roles_service.MAX_GAME_ROLES,
            'error': error,
        }, status=status)

    def require_admin(context: dict):
        if not context['is_admin']:
            raise Forbidden("Only a server admin can manage the game roles themselves.")

    @app.get('/g/{guild_id}/roles', response_class=HTMLResponse)
    async def game_roles(request: Request, guild_id: str):
        return await roles_page(request, guild_id, await guild_context(request, guild_id))

    @app.post('/g/{guild_id}/roles')
    async def save_game_roles(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            note = await roles_service.set_member_roles(
                context['guild'], context['member'], form.getlist('role_ids')
            )
        except ValueError as e:
            return await roles_page(request, guild_id, context, error=str(e), status=400)

        # The member object is cached; drop it so the page that follows shows the
        # roles as they now are rather than as they were up to a minute ago.
        forget_member(guild_id, context['session']['id'])
        return redirect(request, f"/g/{guild_id}/roles", 'ok', note)

    @app.post('/g/{guild_id}/roles/add')
    async def add_game_role(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            notes = await roles_service.add_role(
                bot, context['guild'], context['member'],
                form.get('name'), form.get('emoji'), form.get('description'),
            )
        except ValueError as e:
            return await roles_page(request, guild_id, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/roles", 'ok', ' '.join(notes))

    @app.post('/g/{guild_id}/roles/remove')
    async def remove_game_role(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            notes = await roles_service.remove_role(
                bot, context['guild'], context['member'],
                form.get('role_id'), bool(form.get('delete_role')),
            )
        except ValueError as e:
            return await roles_page(request, guild_id, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/roles", 'ok', ' '.join(notes))

    @app.post('/g/{guild_id}/roles/panel')
    async def post_game_role_panel(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            note = await roles_service.post_panel(bot, context['guild'], form.get('channel_id'))
        except ValueError as e:
            return await roles_page(request, guild_id, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/roles", 'ok', note)

    @app.post('/g/{guild_id}/refresh')
    async def refresh_permissions(request: Request, guild_id: str):
        """Drop the cached member so a role change shows up straight away."""
        session = require_session(request)
        auth.check_csrf(session, (await request.form()).get('csrf'))
        forget_member(guild_id, session['id'])
        return redirect(request, f"/g/{guild_id}", 'ok', 'Permissions re-read from Discord.')

    @app.get('/healthz', response_class=PlainTextResponse)
    async def healthz():
        return 'ok' if bot.is_ready() else 'starting'

    return app
