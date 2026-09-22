"""The FastAPI application behind the web UI.

It runs inside the bot's own process and event loop, so a page can post a
Discord message, register a persistent view or read a member's roles directly —
no polling, no second deployment, no outbox table. `web/server.py` starts it.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from cogs.events import _RECURRENCE_LABELS, _recurrence_text
from cogs.memberlog import DEFAULT_WELCOME_TEMPLATE, WELCOME_PLACEHOLDERS
from cogs.redditfeed import POLL_MINUTES
from cogs.voicelog import refresh_leaderboard_board as refresh_board
from utils import database
from utils import embeds as embedlib
from utils import help as help_lib
from utils import reddit as reddit_lib
from utils import tacmap as tacmap_lib
from web import (
    auth,
    nav as nav_service,
    embeds as embed_service,
    invites as invite_service,
    operations as operation_service,
    orbat as orbat_service,
    reddit as reddit_service,
    roles as roles_service,
    service,
    slots as slots_service,
    tacmap as tacmap_service,
    terrain as terrain_service,
    voice as voice_service,
)
from web.auth import Forbidden, NotAuthenticated
from web.config import LOGO_NAMES, WebConfig
from web.guilds import (
    can_action_slots,
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


def _help_for(page: str) -> str:
    """The how-to URL for one web page, or '' when there is none."""
    topics = help_lib.for_page(page)
    return f"/help/{topics[0].key}" if topics else ''


def create_app(bot, config: WebConfig) -> FastAPI:
    app = FastAPI(title='ORBAT', docs_url=None, redoc_url=None, openapi_url=None)
    app.mount('/static', StaticFiles(directory=_HERE / 'static'), name='static')

    templates = Jinja2Templates(directory=str(_HERE / 'templates'))
    templates.env.globals.update(
        brand=config.brand,
        logo_url=_logo_url(),
        # Absolute origin, needed for the link-preview image: og:image is fetched
        # by other sites, so a relative path is useless there.
        site_url=config.base_url,
        fmt_dt=fmt_dt,
        fmt_date=fmt_date,
        fmt_input=fmt_input,
        relative=relative,
        message_link=message_link,
        recurrence_text=_recurrence_text,
        recurrence_labels=_RECURRENCE_LABELS,
        reminder_choices=service.REMINDER_CHOICES,
        repeat_choices=service.REPEAT_CHOICES,
        # The `?` beside a page heading. A page names itself with the key
        # `web/nav.py` gave it and gets the URL of its how-to, or '' when it
        # has none — which `tests/test_help.py` is there to prevent.
        help_for=_help_for,
    )
    # The little Markdown the how-tos are written in. It escapes first, so the
    # result is safe to mark safe — see `utils/help.inline_html`.
    templates.env.filters['inline_md'] = lambda text: Markup(
        help_lib.inline_html(text or ''))

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
            auth.set_flash(response, config, kind or 'ok', text, request)
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
            'may_action_slots': can_action_slots(member),
            'is_admin': is_admin(member),
            # The tab bar, built from what this member may open — see web/nav.py.
            'nav': nav_service.build(
                guild.id,
                is_admin=is_admin(member),
                may_action_slots=can_action_slots(member),
            ),
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
            samesite='lax', secure=config.cookie_secure_for(request), path='/',
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
        auth.write_session(response, config, auth.new_session(profile), request)
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
            'values': {'reminder': 30, 'repeat': 'none', 'repeat_delay': '', 'mention_ids': []},
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
                'repeat_delay': event['recurrence_delay_hours'] if event['recurrence_delay_hours'] is not None else '',
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

    # -- slot approvals -----------------------------------------------------

    async def slots_context(request: Request, guild_id: str) -> dict:
        context = await guild_context(request, guild_id)
        if not context['may_action_slots']:
            raise Forbidden(
                'Only a Unit Leader or a server admin can action slot requests.'
            )
        return context

    async def slots_page(request: Request, context: dict, error: str = None,
                         status: int = 200):
        return render(request, 'slots.html', {
            **context,
            **await slots_service.queue(context['guild'], context['member']),
            'error': error,
        }, status=status)

    @app.get('/g/{guild_id}/slots', response_class=HTMLResponse)
    async def slot_queue(request: Request, guild_id: str):
        return await slots_page(request, await slots_context(request, guild_id))

    @app.post('/g/{guild_id}/slots/{request_id}/approve', response_class=HTMLResponse)
    async def slot_approve(request: Request, guild_id: str, request_id: int):
        context = await slots_context(request, guild_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            note = await slots_service.approve(
                bot, context['guild'], context['member'], request_id
            )
        except ValueError as e:
            return await slots_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/slots", 'ok', note)

    @app.post('/g/{guild_id}/slots/{request_id}/deny', response_class=HTMLResponse)
    async def slot_deny(request: Request, guild_id: str, request_id: int):
        context = await slots_context(request, guild_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            note = await slots_service.deny(
                bot, context['guild'], context['member'], request_id,
                form.get('reason'),
            )
        except ValueError as e:
            return await slots_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/slots", 'ok', note)

    @app.post('/g/{guild_id}/slots/assign', response_class=HTMLResponse)
    async def slot_assign(request: Request, guild_id: str):
        context = await slots_context(request, guild_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            note = await slots_service.assign(
                bot, context['guild'], context['member'], form
            )
        except ValueError as e:
            return await slots_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/slots", 'ok', note)

    @app.post('/g/{guild_id}/slots/{request_id}/clear', response_class=HTMLResponse)
    async def slot_clear(request: Request, guild_id: str, request_id: int):
        context = await slots_context(request, guild_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            note = await slots_service.clear(
                bot, context['guild'], context['member'], request_id
            )
        except ValueError as e:
            return await slots_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/slots", 'ok', note)

    # -- the operation ------------------------------------------------------

    async def operation_context(request: Request, guild_id: str) -> dict:
        context = await guild_context(request, guild_id)
        if not context['is_admin']:
            raise Forbidden('Only a server admin can run an operation.')
        return context

    async def operation_page(request: Request, context: dict, error: str = None,
                             status: int = 200, debug: dict = None,
                             panel: str = None):
        """The operation page. *panel* names a collapsed section to re-open,
        so a form that comes back with an error is not folded away with what
        the person typed still in it."""
        return render(request, 'operation.html', {
            **context,
            **await operation_service.overview(context['guild'], context['tz']),
            'channels_choices': postable_channels(context['guild']),
            'debug': debug,
            'panel': panel,
            'error': error,
        }, status=status)

    async def operation_settings_page(request: Request, context: dict,
                                      error: str = None, status: int = 200):
        return render(request, 'operation_settings.html', {
            **context,
            'channels': await operation_service.channel_settings(context['guild']),
            'channels_choices': postable_channels(context['guild']),
            'timezone': context['tz'],
            'timezone_choices': operation_service.TIMEZONE_CHOICES,
            'error': error,
        }, status=status)

    async def operation_action(request: Request, guild_id: str, run,
                               panel: str = None, settings: bool = False):
        """Every form on both pages: check CSRF, run one service call, flash it.

        `run(context, form)` returns the message, or raises ValueError with one
        for the person who submitted — the convention the rest of `web/` uses.
        *panel* re-opens the section the error came from; *settings* sends the
        answer back to the settings page instead of the operation page.
        """
        context = await operation_context(request, guild_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        page = f"/g/{guild_id}/operation" + ('/settings' if settings else '')
        try:
            note = await run(context, form)
        except ValueError as e:
            if settings:
                return await operation_settings_page(
                    request, context, error=str(e), status=400
                )
            return await operation_page(request, context, error=str(e),
                                        status=400, panel=panel)
        return redirect(request, page, 'ok', note)

    @app.get('/g/{guild_id}/operation/settings', response_class=HTMLResponse)
    async def operation_settings(request: Request, guild_id: str):
        return await operation_settings_page(
            request, await operation_context(request, guild_id)
        )

    @app.get('/g/{guild_id}/operation', response_class=HTMLResponse)
    async def operation_overview(request: Request, guild_id: str):
        return await operation_page(request, await operation_context(request, guild_id))

    @app.post('/g/{guild_id}/operation/start', response_class=HTMLResponse)
    async def operation_start(request: Request, guild_id: str):
        return await operation_action(request, guild_id, lambda context, form:
            operation_service.start(bot, context['guild'], form, context['tz']),
            panel='start')

    @app.post('/g/{guild_id}/operation/time', response_class=HTMLResponse)
    async def operation_time(request: Request, guild_id: str):
        async def run(context, form):
            op = await database.get_active_operation(str(context['guild'].id))
            return await operation_service.set_time(
                bot, context['guild'], op, form, context['tz']
            )
        return await operation_action(request, guild_id, run)

    @app.post('/g/{guild_id}/operation/timezone', response_class=HTMLResponse)
    async def operation_timezone(request: Request, guild_id: str):
        return await operation_action(request, guild_id, lambda context, form:
            operation_service.set_timezone(context['guild'], form), settings=True)

    @app.post('/g/{guild_id}/operation/channels', response_class=HTMLResponse)
    async def operation_channels(request: Request, guild_id: str):
        return await operation_action(request, guild_id, lambda context, form:
            operation_service.save_channels(context['guild'], form), settings=True)

    @app.post('/g/{guild_id}/operation/board', response_class=HTMLResponse)
    async def operation_board(request: Request, guild_id: str):
        async def run(context, form):
            op = await database.get_active_operation(str(context['guild'].id))
            return await operation_service.post_board(bot, context['guild'], op, form)
        return await operation_action(request, guild_id, run, panel='board')

    @app.post('/g/{guild_id}/operation/announce', response_class=HTMLResponse)
    async def operation_announce(request: Request, guild_id: str):
        async def run(context, form):
            op = await database.get_active_operation(str(context['guild'].id))
            return await operation_service.post_announcement(
                bot, context['guild'], context['member'], op, form, context['tz']
            )
        return await operation_action(request, guild_id, run, panel='announce')

    @app.post('/g/{guild_id}/operation/clear-requests', response_class=HTMLResponse)
    async def operation_clear_requests(request: Request, guild_id: str):
        async def run(context, form):
            op = await database.get_active_operation(str(context['guild'].id))
            return await operation_service.clear_pending(bot, context['guild'], op)
        return await operation_action(request, guild_id, run)

    @app.post('/g/{guild_id}/operation/slots', response_class=HTMLResponse)
    async def operation_debug_slots(request: Request, guild_id: str):
        """The raw roster — `/debug-slots`. Rendered in place, not redirected to,
        because a flash message is the wrong shape for forty lines of output."""
        context = await operation_context(request, guild_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        op = await database.get_active_operation(str(context['guild'].id))
        try:
            debug = await operation_service.raw_slots(op, form.get('squad'))
        except ValueError as e:
            return await operation_page(request, context, error=str(e), status=400)
        debug['squad'] = (form.get('squad') or '').strip()
        return await operation_page(request, context, debug=debug)

    # -- ORBATs -------------------------------------------------------------

    def require_orbat_admin(context: dict):
        if not context['is_admin']:
            raise Forbidden("Only a server admin can build ORBATs.")

    async def orbat_context(request: Request, guild_id: str, orbat_id: int) -> dict:
        context = await guild_context(request, guild_id)
        require_orbat_admin(context)
        record = await database.get_orbat(orbat_id)
        if record is None or record['guild_id'] != str(context['guild'].id):
            raise Forbidden("No such ORBAT on this server.")
        context['record'] = record
        return context

    async def orbat_list_page(request: Request, context: dict, error: str = None,
                              status: int = 200):
        return render(request, 'orbats.html', {
            **context,
            'orbats': await database.get_guild_orbats(str(context['guild'].id)),
            'error': error,
        }, status=status)

    async def orbat_editor(request: Request, context: dict, text: str, nets_text: str,
                           checked: dict = None, pending: bool = False,
                           error: str = None, status: int = 200):
        return render(request, 'orbat_form.html', {
            **context,
            'text': text,
            'nets_text': nets_text,
            'result': (checked or {}).get('result'),
            'nets': (checked or {}).get('nets'),
            'diff': (checked or {}).get('diff'),
            'board': (checked or {}).get('board'),
            'summary': (checked or {}).get('summary'),
            'pending': pending,
            'stored': await orbat_service.stored_board(context['record']['id']),
            'live_operation': await database.orbat_live_operation(context['record']['id']),
            'error': error,
        }, status=status)

    @app.get('/g/{guild_id}/orbats', response_class=HTMLResponse)
    async def orbat_list(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_orbat_admin(context)
        return await orbat_list_page(request, context)

    @app.post('/g/{guild_id}/orbats')
    async def new_orbat(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_orbat_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            orbat_id = await orbat_service.create(
                context['guild'], context['member'],
                form.get('name'), form.get('description'),
            )
        except ValueError as e:
            return await orbat_list_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/orbats/{orbat_id}", 'ok',
                        'ORBAT created — now write the roster.')

    @app.get('/g/{guild_id}/orbats/{orbat_id}', response_class=HTMLResponse)
    async def orbat_edit(request: Request, guild_id: str, orbat_id: int):
        context = await orbat_context(request, guild_id, orbat_id)
        return await orbat_editor(
            request, context,
            await orbat_service.editor_text(context['record']),
            await orbat_service.editor_nets_text(context['record']),
        )

    @app.post('/g/{guild_id}/orbats/{orbat_id}', response_class=HTMLResponse)
    async def orbat_save(request: Request, guild_id: str, orbat_id: int):
        context = await orbat_context(request, guild_id, orbat_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        text = form.get('text') or ''
        nets_text = form.get('nets') or ''
        action = form.get('action') or 'preview'
        checked = await orbat_service.review(orbat_id, text, nets_text)

        if not checked['ok'] or action == 'preview':
            return await orbat_editor(request, context, text, nets_text, checked)

        # An edit that would unseat somebody, or move them onto a differently
        # named role, always stops for a confirmation. Everything else saves
        # straight away: asking on every edit would train people to click
        # through the one that matters.
        if action != 'confirm' and checked['diff'].needs_confirmation:
            return await orbat_editor(request, context, text, nets_text, checked,
                                      pending=True)

        note = await orbat_service.apply(orbat_id, text, nets_text, checked)
        return redirect(request, f"/g/{guild_id}/orbats/{orbat_id}", 'ok', note)

    @app.post('/g/{guild_id}/orbats/{orbat_id}/rename', response_class=HTMLResponse)
    async def orbat_rename(request: Request, guild_id: str, orbat_id: int):
        context = await orbat_context(request, guild_id, orbat_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            await orbat_service.rename(orbat_id, form.get('name'), form.get('description'))
        except ValueError as e:
            return await orbat_editor(
                request, context,
                await orbat_service.editor_text(context['record']),
                await orbat_service.editor_nets_text(context['record']),
                error=str(e), status=400,
            )
        return redirect(request, f"/g/{guild_id}/orbats/{orbat_id}", 'ok', 'Renamed.')

    @app.post('/g/{guild_id}/orbats/{orbat_id}/export', response_class=HTMLResponse)
    async def orbat_export(request: Request, guild_id: str, orbat_id: int):
        context = await orbat_context(request, guild_id, orbat_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            tab = await orbat_service.export(
                context['guild'], context['record'], form.get('sheet_url'),
                bool(form.get('with_bookings')),
            )
        except ValueError as e:
            return await orbat_editor(
                request, context,
                await orbat_service.editor_text(context['record']),
                await orbat_service.editor_nets_text(context['record']),
                error=str(e), status=400,
            )
        return redirect(request, f"/g/{guild_id}/orbats/{orbat_id}", 'ok',
                        f'Exported into a new tab named "{tab}".')

    @app.post('/g/{guild_id}/orbats/{orbat_id}/duplicate')
    async def orbat_duplicate(request: Request, guild_id: str, orbat_id: int):
        context = await orbat_context(request, guild_id, orbat_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            new_id = await orbat_service.duplicate(
                orbat_id, context['member'], form.get('name')
            )
        except ValueError as e:
            return await orbat_editor(
                request, context,
                await orbat_service.editor_text(context['record']),
                await orbat_service.editor_nets_text(context['record']),
                error=str(e), status=400,
            )
        return redirect(request, f"/g/{guild_id}/orbats/{new_id}", 'ok',
                        'Copied — the structure, not the bookings.')

    @app.post('/g/{guild_id}/orbats/{orbat_id}/delete')
    async def orbat_delete(request: Request, guild_id: str, orbat_id: int):
        context = await orbat_context(request, guild_id, orbat_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            note = await orbat_service.delete(context['record'])
        except ValueError as e:
            return await orbat_editor(
                request, context,
                await orbat_service.editor_text(context['record']),
                await orbat_service.editor_nets_text(context['record']),
                error=str(e), status=400,
            )
        return redirect(request, f"/g/{guild_id}/orbats", 'ok', note)

    # -- tactical maps ------------------------------------------------------

    # The palette never changes between requests, so it is built once.
    _map_catalog = tacmap_lib.json_payload(tacmap_lib.catalog())

    async def map_context(request: Request, guild_id: str, map_id: int) -> dict:
        context = await guild_context(request, guild_id)
        record = await database.get_tac_map(map_id)
        if record is None or record['guild_id'] != str(context['guild'].id):
            raise Forbidden('No such map on this server.')
        context['record'] = record
        # Everyone in the guild may read the plan; drawing on it is the same
        # audience that may create an event — a Unit Leader or an admin.
        context['may_draw'] = context['may_create']
        return context

    def require_draw(context: dict):
        if not context['may_create']:
            raise Forbidden('Only a Unit Leader or a server admin can change a map.')

    async def map_list_page(request: Request, context: dict, error: str = None,
                            status: int = 200):
        rows = await database.get_guild_tac_maps(str(context['guild'].id))
        return render(request, 'tacmaps.html', {
            **context,
            'maps': [
                {'record': row,
                 'summary': tacmap_lib.summarise(tacmap_service.load(row))}
                for row in rows
            ],
            'may_draw': context['may_create'],
            'error': error,
        }, status=status)

    async def map_editor(request: Request, context: dict, error: str = None,
                         status: int = 200, panel: str = None,
                         channel: int = tacmap_lib.DEFAULT_CHANNEL,
                         ocap_maps: list = None, ocap_base: str = None):
        record = context['record']
        doc = tacmap_service.load(record)
        # The terrain's own town names, drawn under the plan. They belong to
        # the terrain, so a map on an OCAP server has none — the page says so.
        places = await tacmap_service.places_for(context['guild'].id, doc)
        terrains = await database.get_guild_tac_terrains(str(context['guild'].id))
        # The directory is remembered per guild, so it is typed once and the
        # terrains are a dropdown from then on. `ocap_maps` is only filled by
        # the listing route — opening the page must never read somebody
        # else's server.
        if ocap_base is None:
            ocap_base = (await database.get_ocap_base_url(str(context['guild'].id))
                         or tacmap_service.DEFAULT_OCAP_BASE)
        return render(request, 'tacmap_edit.html', {
            **context,
            'doc_json': tacmap_lib.json_payload(doc),
            'catalog_json': _map_catalog,
            'svg': tacmap_lib.render(doc, places=places),
            'places_json': tacmap_lib.json_payload(places),
            'places_sqf': tacmap_lib.places_sqf(),
            'summary': tacmap_lib.summarise(doc),
            'editable': context['may_draw'],
            'sqf': tacmap_lib.to_sqf(doc, prefix=tacmap_service.arma_prefix(record),
                                     title=record['name']),
            'sqf_editable': tacmap_lib.to_sqf(
                doc, prefix=tacmap_service.arma_prefix(record),
                title=record['name'], editable=True, channel=channel),
            'arma_channel': channel,
            'arma_channels': tacmap_lib.ARMA_CHANNELS,
            'save_url': f"/g/{context['guild'].id}/maps/{record['id']}/save",
            'share_modes': tacmap_service.SHARE_MODES,
            # The canonical origin, not the one this request came in on: a
            # share link is made to be copied somewhere else, and it has to
            # carry the unit's own domain even when it was made from a
            # different one.
            'share_url': config.public_url(tacmap_service.share_path(record),
                                           request),
            'channels': postable_channels(context['guild']),
            'terrains': terrains,
            'ocap_base': ocap_base,
            'ocap_maps': ocap_maps or [],
            'panel': panel,
            'error': error,
        }, status=status)

    async def map_json(request: Request):
        """The body of a save — a ValueError the editor shows as it is."""
        try:
            payload = await request.json()
        except Exception:
            payload = None
        if not isinstance(payload, dict):
            raise ValueError('That was not a map document.')
        return payload

    @app.get('/g/{guild_id}/maps', response_class=HTMLResponse)
    async def map_list(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        return await map_list_page(request, context)

    @app.post('/g/{guild_id}/maps')
    async def new_map(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            map_id = await tacmap_service.create(
                context['guild'], context['member'], form.get('name'),
                form.get('description'), form.get('background'),
            )
        except ValueError as e:
            return await map_list_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/maps/{map_id}", 'ok',
                        'Map created — now draw the plan.')

    @app.get('/g/{guild_id}/maps/{map_id}', response_class=HTMLResponse)
    async def map_edit(request: Request, guild_id: str, map_id: int,
                       channel: int = tacmap_lib.DEFAULT_CHANNEL):
        context = await map_context(request, guild_id, map_id)
        return await map_editor(request, context, channel=channel)

    @app.post('/g/{guild_id}/maps/{map_id}/save')
    async def map_save(request: Request, guild_id: str, map_id: int):
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        try:
            payload = await map_json(request)
            auth.check_csrf(context['session'], payload.get('csrf'))
            notes = await tacmap_service.save(
                context['record'], payload.get('doc'),
                context['member'].display_name,
            )
        except ValueError as e:
            return JSONResponse({'ok': False, 'error': str(e)}, status_code=400)
        return JSONResponse({'ok': True, 'notes': notes})

    @app.get('/g/{guild_id}/maps/{map_id}/arma.sqf', response_class=PlainTextResponse)
    async def map_sqf(request: Request, guild_id: str, map_id: int,
                      editable: int = 0,
                      channel: int = tacmap_lib.DEFAULT_CHANNEL):
        """The markers as a script, for pasting into a running mission.

        Served as a file as well as shown on the page, because a plan being
        briefed off a second screen is easier to keep somewhere than to
        re-copy out of the browser each time it changes. `editable=1` is the
        variant whose markers can be moved and deleted in game.
        """
        context = await map_context(request, guild_id, map_id)
        return PlainTextResponse(
            tacmap_service.sqf(context['record'], editable=bool(editable),
                               channel=channel))

    # -- terrains, uploaded and served from here ----------------------------

    async def terrain_page(request: Request, context: dict, error: str = None,
                           status: int = 200):
        terrains = await database.get_guild_tac_terrains(str(context['guild'].id))
        return render(request, 'terrains.html', {
            **context,
            'terrains': [
                {'record': row,
                 'size': terrain_service.megabytes(row['bytes']),
                 'places': terrain_service.load_places(row),
                 'used_by': await database.tac_terrain_usage(row['id'])}
                for row in terrains
            ],
            'may_upload': context['is_admin'],
            'zoom_choices': terrain_service.ZOOM_CHOICES,
            'default_zoom': terrain_service.DEFAULT_ZOOM,
            # The script that copies a terrain's names out of a running
            # mission — the general way in, since OCAP archives carry none.
            'places_sqf': tacmap_lib.places_sqf(),
            'place_groups': tacmap_lib.PLACE_GROUPS,
            'error': error,
        }, status=status)

    @app.get('/g/{guild_id}/terrains', response_class=HTMLResponse)
    async def terrain_list(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_draw(context)
        return await terrain_page(request, context)

    @app.post('/g/{guild_id}/terrains')
    async def terrain_upload(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        archive = form.get('archive')
        if archive is None or not getattr(archive, 'filename', ''):
            return await terrain_page(request, context, status=400,
                                      error='Choose a zip or 7z of the tile folder.')
        try:
            note = await terrain_service.upload(
                context['guild'], context['member'], archive.file, archive.filename,
                form.get('name'), form.get('world_size'), form.get('max_zoom'),
                form.get('places'),
            )
        except ValueError as e:
            return await terrain_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/terrains", 'ok', note)

    @app.post('/g/{guild_id}/terrains/{terrain_id}/places')
    async def terrain_places(request: Request, guild_id: str, terrain_id: int):
        """Replace a terrain's place names — the paste box on the list page."""
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        record = await database.get_tac_terrain(terrain_id)
        if record is None or record['guild_id'] != str(context['guild'].id):
            raise Forbidden('No such terrain on this server.')
        try:
            note = await terrain_service.set_places(
                record, form.get('places'), context['member'].display_name)
        except ValueError as e:
            return await terrain_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/terrains", 'ok', note)

    @app.post('/g/{guild_id}/terrains/{terrain_id}/delete')
    async def terrain_delete(request: Request, guild_id: str, terrain_id: int):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        record = await database.get_tac_terrain(terrain_id)
        if record is None or record['guild_id'] != str(context['guild'].id):
            raise Forbidden('No such terrain on this server.')
        try:
            note = await terrain_service.delete(record)
        except ValueError as e:
            return await terrain_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/terrains", 'ok', note)

    # One tile. No session: a map's background has to load for everybody the
    # share link was sent to, and a terrain render is not a secret. The cache
    # headers are what keep a page of 256 tiles to one round of requests.
    @app.get('/t/{terrain_id}/{zoom}/{column}/{row}.png')
    async def terrain_tile(terrain_id: int, zoom: int, column: int, row: int):
        image = await database.get_tac_terrain_tile(terrain_id, zoom, column, row)
        if image is None:
            return Response(status_code=404)
        return Response(content=bytes(image), media_type='image/png', headers={
            'Cache-Control': 'public, max-age=31536000, immutable',
        })

    @app.post('/g/{guild_id}/maps/{map_id}/ocap-places', response_class=HTMLResponse)
    async def map_ocap_places(request: Request, guild_id: str, map_id: int):
        """Read the terrain's town names off the OCAP server the map sits on."""
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        doc = tacmap_service.load(context['record'])
        try:
            note = await tacmap_service.import_ocap_places(
                context['guild'].id, doc, context['member'].display_name)
        except ValueError as e:
            return await map_editor(request, context, error=str(e), status=400,
                                    panel='terrain')
        return redirect(request, f"/g/{guild_id}/maps/{map_id}", 'ok', note)

    @app.post('/g/{guild_id}/maps/{map_id}/places', response_class=HTMLResponse)
    async def map_places(request: Request, guild_id: str, map_id: int):
        """Paste in this terrain's place names.

        On the map rather than only on the Terrains page, because a map backed
        by an OCAP server has no terrain row there to paste into — which made
        the one remaining path unreachable for exactly the deployments that
        needed it.
        """
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        doc = tacmap_service.load(context['record'])
        try:
            note = await tacmap_service.set_places(
                context['guild'].id, doc, form.get('places'),
                context['member'].display_name)
        except ValueError as e:
            return await map_editor(request, context, error=str(e), status=400,
                                    panel='terrain')
        return redirect(request, f"/g/{guild_id}/maps/{map_id}", 'ok', note)

    @app.post('/g/{guild_id}/maps/{map_id}/terrain')
    async def map_terrain(request: Request, guild_id: str, map_id: int):
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        terrain = await database.get_tac_terrain(int(form.get('terrain_id') or 0))
        if terrain is None or terrain['guild_id'] != str(context['guild'].id):
            return await map_editor(request, context, panel='terrain', status=400,
                              error='Pick one of this server\'s terrains.')
        note = await terrain_service.apply_to_map(
            context['record'], terrain, context['member'].display_name
        )
        return redirect(request, f"/g/{guild_id}/maps/{map_id}", 'ok', note)

    @app.post('/g/{guild_id}/maps/{map_id}/ocap-list')
    async def map_ocap_list(request: Request, guild_id: str, map_id: int):
        """Read the OCAP directory and come back with its terrains as a list.

        Its own route rather than part of opening the page: reading it is a
        request to somebody else's server, and that must happen because a
        person asked for it, not because they looked at a map.
        """
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        base = form.get('base') or ''
        try:
            names = await tacmap_service.list_ocap_maps(base)
        except ValueError as e:
            return await map_editor(request, context, error=str(e), status=400,
                                    panel='ocap', ocap_base=base)
        # Only a directory that answered is worth remembering.
        await database.set_ocap_base_url(
            str(context['guild'].id), tacmap_service.clean_base_url(base)
        )
        return await map_editor(request, context, panel='ocap', ocap_maps=names,
                                ocap_base=tacmap_service.clean_base_url(base))

    @app.post('/g/{guild_id}/maps/{map_id}/ocap')
    async def map_ocap(request: Request, guild_id: str, map_id: int):
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        url = (form.get('url') or '').strip()
        chosen = (form.get('name') or '').strip()
        if chosen:
            base = tacmap_service.clean_base_url(form.get('base') or '')
            url = f'{base}/{chosen}' if base else chosen
        try:
            note = await tacmap_service.import_ocap(
                context['record'], url, context['member'].display_name
            )
        except ValueError as e:
            return await map_editor(request, context, error=str(e), status=400,
                                    panel='ocap')
        return redirect(request, f"/g/{guild_id}/maps/{map_id}", 'ok', note)

    @app.post('/g/{guild_id}/maps/{map_id}/rename')
    async def map_rename(request: Request, guild_id: str, map_id: int):
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            await tacmap_service.rename(
                context['record'], form.get('name'), form.get('description')
            )
        except ValueError as e:
            return await map_editor(request, context, error=str(e), status=400, panel='about')
        return redirect(request, f"/g/{guild_id}/maps/{map_id}", 'ok', 'Renamed.')

    @app.post('/g/{guild_id}/maps/{map_id}/duplicate')
    async def map_duplicate(request: Request, guild_id: str, map_id: int):
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            new_id = await tacmap_service.duplicate(
                context['record'], context['member'], form.get('name')
            )
        except ValueError as e:
            return await map_editor(request, context, error=str(e), status=400, panel='about')
        return redirect(request, f"/g/{guild_id}/maps/{new_id}", 'ok',
                        'Copied — the plan, not the share link.')

    @app.post('/g/{guild_id}/maps/{map_id}/share')
    async def map_share(request: Request, guild_id: str, map_id: int):
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            note = (await tacmap_service.regenerate(context['record'])
                    if form.get('action') == 'new'
                    else await tacmap_service.share(context['record'], form.get('mode')))
        except ValueError as e:
            return await map_editor(request, context, error=str(e), status=400, panel='share')
        return redirect(request, f"/g/{guild_id}/maps/{map_id}", 'ok', note)

    @app.post('/g/{guild_id}/maps/{map_id}/post')
    async def map_post(request: Request, guild_id: str, map_id: int):
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            note = await tacmap_service.post(
                context['guild'], context['record'], form.get('channel_id'),
                tacmap_service.map_url(config.public_origin(request), guild_id,
                                       context['record']),
                context['member'],
            )
        except ValueError as e:
            return await map_editor(request, context, error=str(e), status=400, panel='post')
        return redirect(request, f"/g/{guild_id}/maps/{map_id}", 'ok', note)

    @app.post('/g/{guild_id}/maps/{map_id}/delete')
    async def map_delete(request: Request, guild_id: str, map_id: int):
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        note = await tacmap_service.delete(context['record'])
        return redirect(request, f"/g/{guild_id}/maps", 'ok', note)

    # The share link. No session, no Discord — the token in the URL is the whole
    # credential, which is what makes it forwardable to people who only ever see
    # the plan, and why taking it away has to be one click on the editor page.
    @app.get('/m/{token}', response_class=HTMLResponse)
    async def shared_map_page(request: Request, token: str):
        record = await database.get_tac_map_by_token(token)
        if record is None:
            return render(request, 'error.html', {
                'title': 'No such map',
                'message': "That link doesn't open a map any more. Ask whoever "
                           "sent it to you for the current one.",
            }, status=404)
        doc = tacmap_service.load(record)
        places = await tacmap_service.places_for(record['guild_id'], doc)
        return render(request, 'tacmap_view.html', {
            'record': record,
            'doc_json': tacmap_lib.json_payload(doc),
            'catalog_json': _map_catalog,
            'places_json': tacmap_lib.json_payload(places),
            'svg': tacmap_lib.render(doc, places=places),
            'summary': tacmap_lib.summarise(doc),
            'editable': record['share_mode'] == 'edit',
            'save_url': f'/m/{token}/save',
        })

    @app.post('/m/{token}/save')
    async def shared_map_save(request: Request, token: str):
        record = await database.get_tac_map_by_token(token)
        if record is None or record['share_mode'] != 'edit':
            return JSONResponse(
                {'ok': False, 'error': 'This link may look at the map, not change it.'},
                status_code=403,
            )
        try:
            payload = await map_json(request)
            notes = await tacmap_service.save(
                record, payload.get('doc'), 'someone with the link'
            )
        except ValueError as e:
            return JSONResponse({'ok': False, 'error': str(e)}, status_code=400)
        return JSONResponse({'ok': True, 'notes': notes})

    # -- embeds -------------------------------------------------------------

    async def embed_context(request: Request, guild_id: str, embed_id: int) -> dict:
        context = await guild_context(request, guild_id)
        require_admin(context)
        record = await database.get_embed(embed_id)
        if record is None or record['guild_id'] != str(context['guild'].id):
            raise Forbidden("No such embed on this server.")
        context['record'] = record
        context['fields'] = await database.get_embed_fields(embed_id)
        return context

    def embed_form(request: Request, context: dict, mode: str, values: dict,
                   error: str = None, status: int = 200):
        return render(request, 'embed_form.html', {
            **context,
            'mode': mode,
            'values': values,
            'channels': postable_channels(context['guild']),
            'slots': range(embed_service.FIELD_SLOTS),
            'error': error,
        }, status=status)

    @app.get('/g/{guild_id}/embeds', response_class=HTMLResponse)
    async def embed_list(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        return render(request, 'embeds.html', {
            **context, 'embeds': await database.get_guild_embeds(str(guild_id)),
        })

    @app.get('/g/{guild_id}/embeds/new', response_class=HTMLResponse)
    async def new_embed_form(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        return embed_form(request, context, 'create', {'color': embedlib.DEFAULT_COLOR})

    @app.post('/g/{guild_id}/embeds/new')
    async def create_embed(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = form_values(await request.form())
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            embed_id = await embed_service.create(context['guild'], context['member'], form)
        except ValueError as e:
            return embed_form(request, context, 'create', form, str(e), status=400)
        return redirect(request, f"/g/{guild_id}/embeds/{embed_id}", 'ok',
                        "Saved. Pick a channel below to post it.")

    @app.get('/g/{guild_id}/embeds/{embed_id}', response_class=HTMLResponse)
    async def embed_detail(request: Request, guild_id: str, embed_id: int):
        context = await embed_context(request, guild_id, embed_id)
        return render(request, 'embed_detail.html', {
            **context,
            'channels': postable_channels(context['guild']),
            'posted_in': context['guild'].get_channel(int(context['record']['channel_id']))
                         if context['record']['channel_id'] else None,
        })

    @app.get('/g/{guild_id}/embeds/{embed_id}/edit', response_class=HTMLResponse)
    async def edit_embed_form(request: Request, guild_id: str, embed_id: int):
        context = await embed_context(request, guild_id, embed_id)
        return embed_form(request, context, 'edit',
                          embed_service.form_values(context['record'], context['fields']))

    @app.post('/g/{guild_id}/embeds/{embed_id}/edit')
    async def edit_embed(request: Request, guild_id: str, embed_id: int):
        context = await embed_context(request, guild_id, embed_id)
        form = form_values(await request.form())
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            notes = await embed_service.save(bot, context['record'], form)
        except ValueError as e:
            return embed_form(request, context, 'edit', form, str(e), status=400)
        return redirect(request, f"/g/{guild_id}/embeds/{embed_id}", 'ok',
                        ' '.join(['Saved.'] + notes))

    @app.post('/g/{guild_id}/embeds/{embed_id}/send')
    async def send_embed(request: Request, guild_id: str, embed_id: int):
        context = await embed_context(request, guild_id, embed_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            notes = await embed_service.send(
                bot, context['guild'], context['record'], form.get('channel_id')
            )
        except ValueError as e:
            return redirect(request, f"/g/{guild_id}/embeds/{embed_id}", 'warn', str(e))
        return redirect(request, f"/g/{guild_id}/embeds/{embed_id}", 'ok', ' '.join(notes))

    @app.post('/g/{guild_id}/embeds/{embed_id}/delete')
    async def delete_embed(request: Request, guild_id: str, embed_id: int):
        context = await embed_context(request, guild_id, embed_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        notes = await embed_service.delete(bot, context['record'], bool(form.get('delete_message')))
        return redirect(request, f"/g/{guild_id}/embeds", 'ok',
                        ' '.join(['Embed deleted.'] + notes))

    # -- voice time ---------------------------------------------------------

    async def voice_page(request: Request, context: dict, period: str,
                         error: str = None, status: int = 200):
        guild = context['guild']
        settings = await database.get_voice_settings(str(guild.id))
        return render(request, 'voice.html', {
            **context,
            **await voice_service.overview(guild, context['member'], period),
            'settings': settings,
            'excluded': voice_service.excluded_set(settings),
            'periods': voice_service.PERIODS,
            # `channels` is already the busiest-channels list from overview().
            'post_channels': postable_channels(guild) if context['is_admin'] else [],
            'voice_channels': guild.voice_channels if context['is_admin'] else [],
            'afk_channel': guild.afk_channel,
            'board_channel': (guild.get_channel(int(settings['board_channel_id']))
                              if settings and settings['board_channel_id'] else None),
            'error': error,
        }, status=status)

    @app.get('/g/{guild_id}/voice', response_class=HTMLResponse)
    async def voice_stats(request: Request, guild_id: str, period: str = None):
        context = await guild_context(request, guild_id)
        return await voice_page(request, context, voice_service.clean_period(period))

    @app.post('/g/{guild_id}/voice')
    async def save_voice_settings(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            values = voice_service.read_settings_form(context['guild'], form)
        except ValueError as e:
            return await voice_page(request, context, voice_service.DEFAULT_PERIOD,
                                    error=str(e), status=400)

        previous = await database.get_voice_settings(str(guild_id))
        await database.save_voice_settings(str(guild_id), values)
        # The cog caches the settings for a few seconds; drop that so a change
        # here takes effect on the very next voice event.
        cog = bot.get_cog('VoiceLogCog')
        if cog is not None:
            cog.forget_settings(str(guild_id))

        notes = ['Voice tracking is on.' if values['enabled']
                 else 'Voice tracking is off — nothing is recorded.']

        moved = previous and previous['board_channel_id'] != values['board_channel_id']
        if moved:
            # The old message stays where it was; the next refresh posts a new
            # one in the new channel rather than trying to edit across channels.
            await database.set_voice_board_state(str(guild_id), None)

        if values['board_enabled']:
            try:
                what = await refresh_board(bot, context['guild'])
                notes.append(f"Daily board {what}.")
            except ValueError as e:
                notes.append(f"The daily board couldn't be updated: {e}")
        return redirect(request, f"/g/{guild_id}/voice", 'ok', ' '.join(notes))

    @app.post('/g/{guild_id}/voice/post')
    async def post_voice_leaderboard(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            note = await voice_service.post_leaderboard(
                bot, context['guild'], form.get('channel_id'), form.get('period'),
            )
        except ValueError as e:
            return redirect(request, f"/g/{guild_id}/voice", 'warn', str(e))
        return redirect(request, f"/g/{guild_id}/voice", 'ok', note)

    # -- member logging -----------------------------------------------------

    @app.get('/g/{guild_id}/logs', response_class=HTMLResponse)
    async def log_settings(request: Request, guild_id: str, saved: bool = False):
        context = await guild_context(request, guild_id)
        require_admin(context)
        guild = context['guild']
        perms = guild.me.guild_permissions if guild.me else None
        return render(request, 'logs.html', {
            **context,
            'settings': await database.get_log_settings(str(guild_id)),
            'channels': postable_channels(guild),
            'member_events': bool(bot.intents.members),
            'can_read_audit': bool(perms and perms.view_audit_log),
            'welcome_placeholders': WELCOME_PLACEHOLDERS,
            'welcome_default_template': DEFAULT_WELCOME_TEMPLATE,
            **await invite_service.overview(guild),
        })

    @app.post('/g/{guild_id}/logs')
    async def save_log_settings(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        channel_id = (form.get('channel_id') or '').strip()
        postable_ids = [str(c.id) for c in postable_channels(context['guild'])]
        if channel_id and channel_id not in postable_ids:
            return redirect(request, f"/g/{guild_id}/logs", 'warn',
                            "I can't post in that channel — pick another one.")

        welcome_channel_id = (form.get('welcome_channel_id') or '').strip()
        if welcome_channel_id and welcome_channel_id not in postable_ids:
            return redirect(request, f"/g/{guild_id}/logs", 'warn',
                            "I can't post in that welcome channel — pick another one.")

        await database.save_log_settings(str(guild_id), {
            'channel_id': channel_id or None,
            **{f'log_{kind}': 1 if form.get(f'log_{kind}') else 0
               for kind in ('join', 'leave', 'kick', 'ban', 'unban')},
            'track_invites': 1 if form.get('track_invites') else 0,
            'welcome_channel_id': welcome_channel_id or None,
            'welcome_message': (form.get('welcome_message') or '').strip()[:1000] or None,
            'welcome_dm': 1 if form.get('welcome_dm') else 0,
        })
        return redirect(request, f"/g/{guild_id}/logs", 'ok',
                        'Logging settings saved.' if channel_id else
                        'Logging is off — no channel is selected.')

    @app.post('/g/{guild_id}/logs/invites')
    async def save_invite_labels(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        labels, remove = invite_service.read_form(form)
        await database.save_invite_labels(str(guild_id), labels, remove)
        return redirect(request, f"/g/{guild_id}/logs", 'ok',
                        f"Saved {len(labels)} invite label(s).")

    # -- reddit feeds -------------------------------------------------------

    async def feed_context(request: Request, guild_id: str, feed_id: int) -> dict:
        context = await guild_context(request, guild_id)
        require_admin(context)
        feed = await database.get_reddit_feed(feed_id)
        if feed is None or feed['guild_id'] != str(context['guild'].id):
            raise Forbidden("No such Reddit watch on this server.")
        context['feed'] = feed
        return context

    def feed_form_values(feed) -> dict:
        return {
            'kind': feed['kind'],
            'source': feed['source'],
            'channel_id': feed['channel_id'] or '',
            'template': feed['template'] or '',
            'authors': ' '.join(
                (feed['author_filter'] or '').split(',')
            ).strip(),
            'mention_ids': (feed['mention_role_id'] or '').split(','),
            'mention_users': ' '.join(
                (feed['mention_user_id'] or '').split(',')
            ).strip(),
            'enabled': feed['enabled'],
        }

    def feed_form(request: Request, context: dict, mode: str, values: dict,
                  error: str = None, preview: dict = None, status: int = 200):
        return render(request, 'reddit_form.html', {
            **context,
            'mode': mode,
            'values': values,
            'error': error,
            'preview': preview,
            'channels': postable_channels(context['guild']),
            'roles': mentionable_roles(context['guild']),
            'kinds': reddit_lib.FEED_KINDS,
            'placeholders': reddit_lib.PLACEHOLDERS,
            'examples': reddit_service.TEMPLATE_EXAMPLES,
            'default_template': reddit_lib.DEFAULT_TEMPLATE,
        }, status=status)

    @app.get('/g/{guild_id}/reddit', response_class=HTMLResponse)
    async def reddit_feeds(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        feeds = await database.get_reddit_feeds(str(guild_id))
        return render(request, 'reddit.html', {
            **context,
            'feeds': reddit_service.view_models(context['guild'], feeds),
            'poll_minutes': POLL_MINUTES,
        })

    @app.get('/g/{guild_id}/reddit/new', response_class=HTMLResponse)
    async def new_reddit_feed_form(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        return feed_form(request, context, 'create', {
            'kind': reddit_lib.DEFAULT_KIND,
            'source': '',
            'channel_id': '',
            'template': reddit_lib.DEFAULT_TEMPLATE,
            'authors': '',
            'mention_ids': [],
            'mention_users': '',
            'enabled': 1,
        })

    @app.post('/g/{guild_id}/reddit/new')
    async def create_reddit_feed(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = form_values(await request.form())
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            feed_id, warnings = await reddit_service.create(
                context['guild'], context['member'], form
            )
        except ValueError as e:
            return feed_form(request, context, 'create', form, str(e), status=400)

        text = "Watch added. The first check notes what is already there and " \
               "announces only what comes after it."
        if warnings:
            text += ' ' + ' '.join(warnings)
        return redirect(request, f"/g/{guild_id}/reddit/{feed_id}", 'ok', text)

    @app.get('/g/{guild_id}/reddit/{feed_id}', response_class=HTMLResponse)
    async def edit_reddit_feed_form(request: Request, guild_id: str, feed_id: int):
        context = await feed_context(request, guild_id, feed_id)
        return feed_form(request, context, 'edit', feed_form_values(context['feed']))

    @app.post('/g/{guild_id}/reddit/{feed_id}')
    async def edit_reddit_feed(request: Request, guild_id: str, feed_id: int):
        context = await feed_context(request, guild_id, feed_id)
        form = form_values(await request.form())
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            warnings = await reddit_service.save(context['guild'], context['feed'], form)
        except ValueError as e:
            return feed_form(request, context, 'edit', form, str(e), status=400)
        return redirect(request, f"/g/{guild_id}/reddit", 'ok',
                        ' '.join(['Watch saved.'] + warnings))

    @app.post('/g/{guild_id}/reddit/{feed_id}/preview', response_class=HTMLResponse)
    async def preview_reddit_feed(request: Request, guild_id: str, feed_id: int):
        """Show the newest post as this watch would announce it.

        Rendered in place rather than flashed, and it posts nothing and marks
        nothing as seen — it is meant to be pressed while working on the text.
        """
        context = await feed_context(request, guild_id, feed_id)
        form = form_values(await request.form())
        auth.check_csrf(context['session'], form.get('csrf'))

        # Previewed against what is in the form, not what is stored, so an
        # unsaved edit is what you see.
        try:
            values, _ = await reddit_service.read_form(context['guild'], form)
            preview = await reddit_service.preview(
                {**dict(context['feed']), **values}
            )
        except (ValueError, reddit_lib.FeedError) as e:
            return feed_form(request, context, 'edit', form, str(e), status=400)
        return feed_form(request, context, 'edit', form, preview=preview)

    @app.post('/g/{guild_id}/reddit/{feed_id}/check')
    async def check_reddit_feed(request: Request, guild_id: str, feed_id: int):
        context = await feed_context(request, guild_id, feed_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            note = await reddit_service.check_now(bot, context['feed'])
        except ValueError as e:
            return redirect(request, f"/g/{guild_id}/reddit", 'warn', str(e))
        return redirect(request, f"/g/{guild_id}/reddit", 'ok', note)

    @app.get('/g/{guild_id}/reddit/{feed_id}/posts', response_class=HTMLResponse)
    async def reddit_feed_posts(request: Request, guild_id: str, feed_id: int):
        """The feed's recent posts, each with an Announce button.

        Its own page rather than a panel on the watch form, because opening it
        reads the feed — the form has to open without touching Reddit, not
        least when Reddit is refusing us.
        """
        context = await feed_context(request, guild_id, feed_id)
        feed = context['feed']
        read, error = {'url': reddit_lib.feed_url(feed['kind'], feed['source']),
                       'posts': [], 'on_feed': 0,
                       'authors': reddit_service.feed_authors(feed)}, None
        try:
            read = await reddit_service.recent(feed)
        except (ValueError, reddit_lib.FeedError) as e:
            error = str(e)
        return render(request, 'reddit_posts.html', {
            **context,
            'label': reddit_lib.kind_prefix(feed['kind']) + feed['source'],
            'posts': read['posts'],
            'source_url': read['url'],
            'on_feed': read['on_feed'],
            'authors': read['authors'],
            'error': error,
        })

    @app.post('/g/{guild_id}/reddit/{feed_id}/announce')
    async def announce_reddit_post(request: Request, guild_id: str, feed_id: int):
        context = await feed_context(request, guild_id, feed_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            note = await reddit_service.catch_up(
                bot, context['feed'], form.get('post_id')
            )
        except ValueError as e:
            return redirect(request, f"/g/{guild_id}/reddit/{feed_id}/posts",
                            'warn', str(e))
        return redirect(request, f"/g/{guild_id}/reddit/{feed_id}/posts", 'ok', note)

    @app.post('/g/{guild_id}/reddit/{feed_id}/mark')
    async def mark_reddit_post(request: Request, guild_id: str, feed_id: int):
        """Take posts out of the queue without announcing them.

        `scope=all` marks everything the page listed, which is the one press
        that stops a backlog going out three at a time; otherwise it is the
        ticked rows.
        """
        context = await feed_context(request, guild_id, feed_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        # The ids come from the page, which has already read the feed. Reading
        # it again to write down a decision would fail exactly when Reddit is
        # refusing us — which is when a backlog most needs marking.
        listed = [i for i in (form.get('all_ids') or '').split(',') if i]
        chosen = listed if form.get('scope') == 'all' else form.getlist('post_id')

        try:
            note = await reddit_service.mark(context['feed'], chosen, listed)
        except ValueError as e:
            return redirect(request, f"/g/{guild_id}/reddit/{feed_id}/posts",
                            'warn', str(e))
        return redirect(request, f"/g/{guild_id}/reddit/{feed_id}/posts", 'ok', note)

    @app.post('/g/{guild_id}/reddit/{feed_id}/delete')
    async def delete_reddit_feed(request: Request, guild_id: str, feed_id: int):
        context = await feed_context(request, guild_id, feed_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        await database.delete_reddit_feed(feed_id)
        label = (reddit_lib.kind_prefix(context['feed']['kind'])
                 + context['feed']['source'])
        return redirect(request, f"/g/{guild_id}/reddit", 'ok',
                        f"Stopped watching {label}.")

    @app.post('/g/{guild_id}/refresh')
    async def refresh_permissions(request: Request, guild_id: str):
        """Drop the cached member so a role change shows up straight away."""
        session = require_session(request)
        auth.check_csrf(session, (await request.form()).get('csrf'))
        forget_member(guild_id, session['id'])
        return redirect(request, f"/g/{guild_id}", 'ok', 'Permissions re-read from Discord.')

    # -- help ---------------------------------------------------------------
    #
    # Deliberately outside the guild: the how-tos are the same on every server,
    # they name tabs rather than linking at them, and somebody who cannot get
    # past the sign-in page is exactly the person who needs to read one. There
    # is no `web/help.py` to go with these two routes because there would be
    # nothing in it — `utils/help.py` holds the content and there are no
    # permissions to check and no form to translate.

    @app.get('/help', response_class=HTMLResponse)
    async def help_index(request: Request, q: str = ''):
        query = (q or '').strip()[:100]
        return render(request, 'help.html', {
            'catalog': help_lib.catalog(),
            'query': query,
            'results': help_lib.search(query) if query else [],
            'total': len(help_lib.TOPICS),
        })

    @app.get('/help/{key}', response_class=HTMLResponse)
    async def help_topic(request: Request, key: str):
        topic = help_lib.topic(key)
        if topic is None:
            return render(request, 'error.html', {
                'title': 'No such how-to',
                'message': "That help page doesn't exist. Try the index.",
            }, status=404)
        return render(request, 'help_topic.html', {
            'topic': topic,
            'shelf': help_lib.group(topic.group),
            'related': help_lib.related(topic),
        })

    @app.get('/healthz', response_class=PlainTextResponse)
    async def healthz():
        return 'ok' if bot.is_ready() else 'starting'

    return app
