"""Announcing new Reddit posts in a Discord channel.

One row of `reddit_feeds` is one watch: a Reddit user or a subreddit, the
channel it announces in, the text it announces with, and who gets pinged. The
loop below reads each watch's public Atom feed every few minutes and posts
whatever it has not posted before.

**It announces, and that is all it does.** A message that asks the channel to go
and upvote is vote manipulation under Reddit's content policy, and it is the
group of accounts answering the call — not just the poster — that gets banned
for it; the pattern is trivially visible in the voting timeline whatever the
message says. So there is no vote wording anywhere in here, and the template
help on the web page says the same thing. Commenting and discussion are fine,
and count for more in Reddit's own ranking than a handful of early votes.

`check_feed()` is the whole implementation: the loop calls it, and so does the
**Check now** button on the web page, so a manual check does exactly what the
scheduled one does.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from utils import database, reddit

# How often every watch is read. Reddit's feeds are cached for a minute or two
# anyway, so anything shorter buys nothing and only costs rate limit.
POLL_MINUTES = 5

# A courtesy pause between feeds, so a guild with a handful of watches doesn't
# hit Reddit with them all at once.
BETWEEN_FEEDS = 1.0

# How many post ids are remembered per watch. A feed page holds 25 entries, so
# this is comfortably more than one read can ever contain.
MAX_SEEN = 60

# How many posts one watch may announce per check. Somebody who posts six times
# in ten minutes gets spread over the next few checks instead of flooding the
# channel — nothing is dropped, the rest simply go next time.
MAX_PER_CHECK = 3

# Discord's message limit.
MAX_MESSAGE = 2000


def mention_ids(feed, column: str) -> list:
    """The ids stored in one of the two comma-separated mention columns."""
    return [part for part in (feed[column] or '').split(',') if part]


def mention_prefix(feed) -> str:
    """The ping line — roles first, then people."""
    roles = ' '.join(f'<@&{role_id}>' for role_id in mention_ids(feed, 'mention_role_id'))
    users = ' '.join(f'<@{user_id}>' for user_id in mention_ids(feed, 'mention_user_id'))
    return ' '.join(part for part in (roles, users) if part)


def allowed_mentions(feed) -> discord.AllowedMentions:
    """Exactly the roles and people this watch names, and nothing else.

    The post title goes into the message verbatim, so a title containing
    `@everyone` would otherwise ping the whole server — the announcement is
    quoting Reddit, not speaking for the admin who set the watch up.
    """
    return discord.AllowedMentions(
        everyone=False,
        roles=[discord.Object(id=int(i)) for i in mention_ids(feed, 'mention_role_id')],
        users=[discord.Object(id=int(i)) for i in mention_ids(feed, 'mention_user_id')],
    )


def build_message(feed, post: dict) -> str:
    """The announcement, ping line included."""
    text = reddit.render(feed['template'], post)
    prefix = mention_prefix(feed)
    body = f"{prefix}\n{text}" if prefix else text
    return body[:MAX_MESSAGE]


def _cooldown_until(seconds: int) -> datetime:
    """Naive UTC, like every stored time here."""
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(tzinfo=None)


def _seen_list(feed) -> list:
    """The ids already announced, or None when the watch has never been read."""
    if feed['seen_ids'] is None:
        return None
    return [part for part in feed['seen_ids'].split(',') if part]


async def check_feed(bot: commands.Bot, feed) -> dict:
    """Read one watch and post what is new. Returns what happened.

    Raises `reddit.FeedError` when the feed itself couldn't be read, and
    `ValueError` when the watch has nowhere to post. Both carry a message meant
    for a person; the loop prints them, the web page flashes them.
    """
    guild = bot.get_guild(int(feed['guild_id']))
    if guild is None:
        raise ValueError("I'm not in that server any more.")

    if not feed['channel_id']:
        raise ValueError("This watch has no channel to announce in yet.")
    channel = guild.get_channel(int(feed['channel_id']))
    if channel is None:
        raise ValueError("The channel this watch posts in is gone — pick another one.")
    if not channel.permissions_for(guild.me).send_messages:
        raise ValueError(f"I'm not allowed to post in #{channel.name}.")

    try:
        posts = await reddit.fetch(feed['kind'], feed['source'])
    except reddit.RateLimited as e:
        # Reddit refused us rather than the feed. Asking again on the next tick
        # is what turns a passing throttle into a standing one, so the watch
        # stands down until `retry_at` — `get_due_reddit_feeds()` skips it, and
        # the next read that gets through clears it.
        await database.record_reddit_read(
            feed['id'], error=str(e), retry_at=_cooldown_until(e.retry_after)
        )
        raise
    except reddit.FeedError as e:
        await database.record_reddit_read(feed['id'], error=str(e))
        raise

    newest = next((p['published'] for p in posts if p['published']), None)
    if newest is not None:
        newest = newest.replace(tzinfo=None)

    seen = _seen_list(feed)
    if seen is None:
        # First read: remember what is already there and say nothing. Otherwise
        # switching a watch on would announce the author's last 25 posts.
        await database.record_reddit_read(
            feed['id'],
            seen_ids=','.join(p['id'] for p in posts[:MAX_SEEN]),
            last_post_at=newest,
        )
        return {'seeded': len(posts), 'posted': 0, 'waiting': 0}

    # `posts` is newest first; announcing walks it the other way, so a burst
    # arrives in the order it was written.
    fresh = [p for p in reversed(posts) if p['id'] not in seen]
    batch = fresh[:MAX_PER_CHECK]

    posted, error = [], None
    for post in batch:
        try:
            await channel.send(
                build_message(feed, post), allowed_mentions=allowed_mentions(feed)
            )
            posted.append(post)
        except discord.Forbidden as e:
            # A permission problem is fixable, so the post stays unannounced and
            # goes out on the next check once it is.
            error = f"I couldn't post in #{channel.name}: {e.text or e}"
            break
        except discord.HTTPException as e:
            # Discord refused this particular message. Marking it announced
            # anyway is deliberate: retrying it every five minutes for ever
            # would wedge the watch behind one bad post.
            error = f"Discord rejected the message for {post['url']}: {e}"
            posted.append(post)
            break

    seen_now = [p['id'] for p in reversed(posted)] + seen
    await database.record_reddit_read(
        feed['id'],
        seen_ids=','.join(seen_now[:MAX_SEEN]),
        last_post_at=newest,
        error=error,
    )
    return {
        'seeded': 0,
        'posted': len(posted),
        'waiting': max(0, len(fresh) - len(posted)),
        'error': error,
        'newest': posts[0] if posts else None,
    }


class RedditFeedCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.poll.start()

    def cog_unload(self):
        self.poll.cancel()

    @tasks.loop(minutes=POLL_MINUTES)
    async def poll(self):
        """Read every enabled watch.

        `discord.ext.tasks` kills a loop for good on an unhandled exception, so
        the query and each watch are caught separately — one suspended Reddit
        account must not stop every other guild's announcements.
        """
        try:
            feeds = await database.get_due_reddit_feeds()
        except Exception as e:
            print(f"❌ redditfeed: could not load the watches: {e!r}")
            return

        for index, feed in enumerate(feeds):
            if index:
                await asyncio.sleep(BETWEEN_FEEDS)
            try:
                result = await check_feed(self.bot, feed)
                if result.get('error'):
                    print(f"⚠️ redditfeed {feed['id']}: {result['error']}")
            except reddit.RateLimited as e:
                print(f"⚠️ redditfeed {feed['id']} ({feed['source']}): {e} "
                      f"Standing down for {e.retry_after // 60} minute(s).")
            except (reddit.FeedError, ValueError) as e:
                # Already recorded on the row, where the web page shows it.
                print(f"⚠️ redditfeed {feed['id']} ({feed['source']}): {e}")
            except Exception as e:
                print(f"❌ redditfeed {feed['id']} ({feed['source']}) failed: {e!r}")

    @poll.before_loop
    async def _before_poll(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(RedditFeedCog(bot))
