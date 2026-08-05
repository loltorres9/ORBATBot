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
from cogs.voicelog import refresh_leaderboard_board as refresh_board
from utils import database
from utils import embeds as embedlib
from web import (
    auth,
    embeds as embed_service,
    invites as invite_service,
    roles as roles_service,
    service,
    voice as voice_service,
)
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
            **await invite_service.overview(guild),
        })

    @app.post('/g/{guild_id}/logs')
    async def save_log_settings(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        channel_id = (form.get('channel_id') or '').strip()
        if channel_id and channel_id not in [str(c.id) for c in postable_channels(context['guild'])]:
            return redirect(request, f"/g/{guild_id}/logs", 'warn',
                            "I can't post in that channel — pick another one.")

        await database.save_log_settings(str(guild_id), {
            'channel_id': channel_id or None,
            **{f'log_{kind}': 1 if form.get(f'log_{kind}') else 0
               for kind in ('join', 'leave', 'kick', 'ban', 'unban')},
            'track_invites': 1 if form.get('track_invites') else 0,
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
