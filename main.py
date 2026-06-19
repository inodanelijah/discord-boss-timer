import json
import os
import shlex
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks
from discord.ui import Button, View
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip().strip('"').strip("'")
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")
ANNOUNCE_CHANNEL_ID = int(os.getenv("BOSS_ANNOUNCE_CHANNEL_ID", "1415879101473882113"))
STATUS_CHANNEL_ID = int(os.getenv("BOSS_STATUS_CHANNEL_ID", "1517530757898178680"))
TIMEZONE = ZoneInfo(os.getenv("BOSS_TIMEZONE", "Asia/Singapore"))

DATA_FILE = Path(os.getenv("BOSS_DATA_FILE", "bosses.json"))
KILL_LOG_FILE = Path(os.getenv("BOSS_KILL_LOG_FILE", "boss_kills.json"))

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)

bosses = {}
guilds = []
boss_turns = {}
boss_current_turn = {}
maintenance_mode = False

# Reminder keys include the target spawn timestamp. This fixes scheduled bosses:
# when the next scheduled spawn is saved, old reminders cannot block new ones.
reminder_sent = set()
daily_announcements_sent = set()

DAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
REMINDERS = (
    ("1h", 3600, "1 hour"),
    ("15m", 900, "15 minutes"),
    ("5m", 300, "5 minutes"),
)


def now_sg():
    return datetime.now(TIMEZONE)


def ensure_aware(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TIMEZONE)
    return dt.astimezone(TIMEZONE)


def parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return ensure_aware(value)
    try:
        return ensure_aware(datetime.fromisoformat(value))
    except ValueError:
        pass
    for fmt in ("%m-%d-%Y %I:%M %p", "%Y-%m-%d %H:%M", "%m/%d/%Y %I:%M %p"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=TIMEZONE)
        except ValueError:
            continue
    return None


def parse_time_text(value):
    value = value.strip().upper().replace(" ", "")
    for fmt in ("%I:%M%p", "%I%p", "%H:%M"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Invalid time: {value}")


def reminder_key(boss_name, spawn_time, label):
    stamp = ensure_aware(spawn_time).isoformat()
    return boss_name.lower(), stamp, label


def load_kill_log():
    if not KILL_LOG_FILE.exists():
        return {}
    with KILL_LOG_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_kill_log(log):
    with KILL_LOG_FILE.open("w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)


def serialize_boss(info):
    return {
        "spawn_time": ensure_aware(info.get("spawn_time")).isoformat()
        if info.get("spawn_time")
        else None,
        "death_time": ensure_aware(info.get("death_time")).isoformat()
        if info.get("death_time")
        else None,
        "respawn_hours": info.get("respawn_time", timedelta()).total_seconds() / 3600,
        "killed_by": info.get("killed_by"),
        "schedule": info.get("schedule", []),
        "is_scheduled": info.get("is_scheduled", False),
        "is_daily": info.get("is_daily", False),
    }


async def save_bosses():
    payload = {
        "bosses": {name: serialize_boss(info) for name, info in bosses.items()},
        "guilds": guilds,
        "boss_turns": boss_turns,
        "boss_current_turn": boss_current_turn,
        "maintenance_mode": maintenance_mode,
    }
    with DATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def load_bosses():
    global guilds, boss_turns, boss_current_turn, maintenance_mode
    if not DATA_FILE.exists():
        return
    with DATA_FILE.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    raw_bosses = payload.get("bosses", payload)
    bosses.clear()
    for name, info in raw_bosses.items():
        bosses[name] = {
            "spawn_time": parse_datetime(info.get("spawn_time")),
            "death_time": parse_datetime(info.get("death_time")),
            "respawn_time": timedelta(hours=float(info.get("respawn_hours", 0))),
            "killed_by": info.get("killed_by"),
            "schedule": [tuple(item) for item in info.get("schedule", [])],
            "is_scheduled": info.get("is_scheduled", False),
            "is_daily": info.get("is_daily", False),
        }

    guilds = payload.get("guilds", [])
    boss_turns = payload.get("boss_turns", {})
    boss_current_turn = payload.get("boss_current_turn", {})
    maintenance_mode = payload.get("maintenance_mode", False)


def next_scheduled_spawn(info, after=None):
    after = ensure_aware(after or now_sg())
    schedule = info.get("schedule", [])
    is_daily = info.get("is_daily", False)
    upcoming = []

    for day, time_text in schedule:
        spawn_time = parse_time_text(time_text)
        if is_daily:
            for offset in range(0, 8):
                candidate_date = (after + timedelta(days=offset)).date()
                candidate = datetime.combine(candidate_date, spawn_time, TIMEZONE)
                if candidate > after:
                    upcoming.append(candidate)
        else:
            target_day = DAYS[day.lower()]
            days_ahead = (target_day - after.weekday()) % 7
            for extra_week in (0, 1):
                candidate_date = (after + timedelta(days=days_ahead + (extra_week * 7))).date()
                candidate = datetime.combine(candidate_date, spawn_time, TIMEZONE)
                if candidate > after:
                    upcoming.append(candidate)

    return min(upcoming) if upcoming else None


def schedule_text(info):
    if info.get("is_daily"):
        return ", ".join(time_text for _, time_text in info.get("schedule", []))
    return ", ".join(f"{day} {time_text}" for day, time_text in info.get("schedule", []))


def get_current_turn(boss_name):
    turns = boss_turns.get(boss_name, [])
    if not turns:
        return None
    index = boss_current_turn.get(boss_name, 0) % len(turns)
    boss_current_turn[boss_name] = index
    return turns[index]


def advance_turn(boss_name):
    turns = boss_turns.get(boss_name, [])
    if not turns:
        return None, None
    current_index = boss_current_turn.get(boss_name, 0) % len(turns)
    next_index = (current_index + 1) % len(turns)
    boss_current_turn[boss_name] = next_index
    return turns[current_index], turns[next_index]


async def record_kill(boss_name, user, channel):
    boss = bosses[boss_name]
    killed_at = now_sg()
    boss["death_time"] = killed_at
    boss["killed_by"] = user.id

    log = load_kill_log()
    log.setdefault(boss_name, []).append(killed_at.isoformat())
    save_kill_log(log)

    respawn_at = killed_at + boss.get("respawn_time", timedelta())
    for label, _, _ in REMINDERS:
        reminder_sent.discard(reminder_key(boss_name, respawn_at, label))
    reminder_sent.discard(reminder_key(boss_name, respawn_at, "respawn"))

    turn_line = ""
    if maintenance_mode:
        turn_line = "\nMaintenance mode is ON. Turn tracking was not advanced."
    else:
        current_turn, next_turn = advance_turn(boss_name)
        if current_turn:
            turn_line = f"\nCurrent turn: **{current_turn}**\nNext turn: **{next_turn}**"

    await save_bosses()
    await channel.send(
        f"Boss **{boss_name}** marked dead by {user.mention} at "
        f"{killed_at.strftime('%m-%d-%Y %I:%M %p')}.\n"
        f"Respawns at: **{respawn_at.strftime('%m-%d-%Y %I:%M %p')}**{turn_line}"
    )
    await refresh_status_message()


def make_death_view(boss_name):
    view = View(timeout=None)
    button = Button(label=f"Time of Death {boss_name}", style=discord.ButtonStyle.danger)

    async def callback(interaction):
        await record_kill(boss_name, interaction.user, interaction.channel)
        await interaction.response.send_message("Time of death recorded.", ephemeral=True)
        try:
            await interaction.message.edit(view=None)
        except discord.HTTPException:
            pass

    button.callback = callback
    view.add_item(button)
    return view


def make_next_turn_view(boss_name):
    view = View(timeout=None)
    button = Button(label=f"Next Turn {boss_name}", style=discord.ButtonStyle.primary)

    async def callback(interaction):
        if maintenance_mode:
            await interaction.response.send_message("Maintenance mode is ON. Turn not advanced.", ephemeral=True)
            return
        current_turn, next_turn = advance_turn(boss_name)
        if not current_turn:
            await interaction.response.send_message("No turn order is configured for this boss.", ephemeral=True)
            return
        await save_bosses()
        await interaction.channel.send(
            f"**{boss_name}** turn advanced by {interaction.user.mention}.\n"
            f"Previous turn: **{current_turn}**\nCurrent turn: **{next_turn}**"
        )
        await interaction.response.send_message("Turn advanced.", ephemeral=True)
        try:
            await interaction.message.edit(view=None)
        except discord.HTTPException:
            pass
        await refresh_status_message()

    button.callback = callback
    view.add_item(button)
    return view


def boss_rows():
    rows = []
    now = now_sg()
    for name, info in bosses.items():
        if info.get("is_scheduled"):
            spawn_at = info.get("spawn_time")
            if not spawn_at or spawn_at <= now - timedelta(minutes=30):
                spawn_at = next_scheduled_spawn(info, now)
                info["spawn_time"] = spawn_at
            rows.append((name, "Scheduled", spawn_at, schedule_text(info)))
        else:
            death_time = info.get("death_time")
            respawn_time = info.get("respawn_time", timedelta())
            spawn_at = death_time + respawn_time if death_time else info.get("spawn_time")
            rows.append((name, "Regular", spawn_at, ""))
    rows.sort(key=lambda row: row[2] or datetime.max.replace(tzinfo=TIMEZONE))
    return rows


def boss_status_embeds(title="LordNine Boss Timers"):
    rows = boss_rows()
    if not rows:
        return [discord.Embed(title=title, description="No bosses added yet.", color=discord.Color.blue())]

    embeds = []
    for index in range(0, len(rows), 25):
        embed = discord.Embed(title=title, color=discord.Color.blue())
        for name, boss_type, spawn_at, sched_text in rows[index : index + 25]:
            turn = get_current_turn(name)
            turn_line = f"\nTurn: **{turn}**" if turn else ""
            if spawn_at:
                delta = spawn_at - now_sg()
                status = "Alive" if delta.total_seconds() <= 0 else "Respawning"
                value = f"Type: **{boss_type}**\nStatus: **{status}**\nNext: **{spawn_at.strftime('%m-%d-%Y %I:%M %p')}**"
            else:
                value = f"Type: **{boss_type}**\nNext: **Not set**"
            if sched_text:
                value += f"\nSchedule: {sched_text}"
            value += turn_line
            embed.add_field(name=name, value=value, inline=False)
        embeds.append(embed)
    return embeds


async def refresh_status_message():
    channel = bot.get_channel(STATUS_CHANNEL_ID)
    if not channel:
        return
    async for message in channel.history(limit=20):
        if message.author == bot.user and message.embeds:
            await message.delete()
    for embed in boss_status_embeds():
        await channel.send(embed=embed)


def scheduled_spawns_on_date(info, target_date):
    spawns = []
    for day, time_text in info.get("schedule", []):
        spawn_time = parse_time_text(time_text)
        if info.get("is_daily"):
            spawns.append(datetime.combine(target_date, spawn_time, TIMEZONE))
            continue
        if DAYS[day.lower()] == target_date.weekday():
            spawns.append(datetime.combine(target_date, spawn_time, TIMEZONE))
    return spawns


def todays_bosses(remaining_only=False):
    current_time = now_sg()
    today = current_time.date()
    rows = []
    for name, info in bosses.items():
        if info.get("is_scheduled"):
            for spawn_at in scheduled_spawns_on_date(info, today):
                if not remaining_only or spawn_at >= current_time:
                    rows.append((name, "Scheduled", spawn_at))
        else:
            death_time = info.get("death_time")
            if death_time:
                spawn_at = death_time + info.get("respawn_time", timedelta())
                if spawn_at.date() == today and (not remaining_only or spawn_at >= current_time):
                    rows.append((name, "Regular", spawn_at))
    rows.sort(key=lambda row: row[2])
    return rows


async def send_today_announcement(channel, title, remaining_only=False):
    rows = todays_bosses(remaining_only=remaining_only)
    date_text = now_sg().strftime("%A, %B %d, %Y")
    if not rows:
        embed = discord.Embed(
            title=title,
            description=f"No {'remaining ' if remaining_only else ''}bosses scheduled for today ({date_text}).",
            color=discord.Color.blue(),
        )
        await channel.send(embed=embed)
        return

    scheduled_count = sum(1 for _, boss_type, _ in rows if boss_type == "Scheduled")
    regular_count = len(rows) - scheduled_count
    embed = discord.Embed(
        title=title,
        description=(
            f"**Date:** {date_text}\n"
            f"**Total:** {len(rows)} spawn(s) | Scheduled: {scheduled_count} | Regular: {regular_count}"
        ),
        color=discord.Color.gold(),
    )
    groups = {}
    for name, boss_type, spawn_at in rows:
        turn = get_current_turn(name)
        turn_text = f" - Turn: {turn}" if turn else ""
        groups.setdefault(spawn_at.strftime("%I:%M %p"), []).append(f"**{name}** ({boss_type}){turn_text}")
    for time_text, names in groups.items():
        embed.add_field(name=time_text, value="\n".join(names), inline=False)
    await channel.send("@everyone", embed=embed)


@bot.command(name="boss_add")
@commands.has_permissions(administrator=True)
async def boss_add(ctx, name: str, respawn_hours: float):
    bosses[name] = {
        "spawn_time": now_sg(),
        "death_time": None,
        "respawn_time": timedelta(hours=respawn_hours),
        "killed_by": None,
        "schedule": [],
        "is_scheduled": False,
        "is_daily": False,
    }
    await save_bosses()
    await refresh_status_message()
    await ctx.send(f"Boss **{name}** added with {respawn_hours:g} hour respawn.")


@bot.command(name="boss_delete")
@commands.has_permissions(administrator=True)
async def boss_delete(ctx, name: str):
    if bosses.pop(name, None) is None:
        await ctx.send(f"Boss **{name}** was not found.")
        return
    boss_turns.pop(name, None)
    boss_current_turn.pop(name, None)
    await save_bosses()
    await refresh_status_message()
    await ctx.send(f"Boss **{name}** deleted.")


@bot.command(name="boss_tod_edit")
@commands.has_permissions(administrator=True)
async def boss_tod_edit(ctx, name: str = None, *, new_time: str = None):
    if not name or not new_time:
        await ctx.send("Usage: `!boss_tod_edit <boss> <MM-DD-YYYY HH:MM AM/PM>`")
        return
    if name not in bosses:
        await ctx.send(f"Boss **{name}** was not found.")
        return
    if bosses[name].get("is_scheduled"):
        await ctx.send("Scheduled bosses use their schedule. Time of death only applies to regular bosses.")
        return
    death_time = parse_datetime(new_time)
    if not death_time:
        await ctx.send("Invalid date. Use `MM-DD-YYYY HH:MM AM/PM`, for example `06-20-2026 08:30 PM`.")
        return
    bosses[name]["death_time"] = death_time
    bosses[name]["killed_by"] = ctx.author.id
    await save_bosses()
    await refresh_status_message()
    respawn_at = death_time + bosses[name].get("respawn_time", timedelta())
    await ctx.send(f"Updated **{name}** TOD. Respawn: **{respawn_at.strftime('%m-%d-%Y %I:%M %p')}**")


@bot.command(name="boss_add_schedule")
@commands.has_permissions(administrator=True)
async def boss_add_scheduled(ctx, *, args: str):
    try:
        parts = shlex.split(args)
    except ValueError as exc:
        await ctx.send(f"Invalid command: {exc}")
        return

    if len(parts) < 2:
        await ctx.send(
            "Usage: `!boss_add_schedule <boss> <time> [time...]` or "
            "`!boss_add_schedule <boss> <day> <time> [day time...]`"
        )
        return

    name = parts[0]
    schedule_parts = parts[1:]
    is_weekly = schedule_parts[0].lower() in DAYS
    schedule = []

    try:
        if is_weekly:
            if len(schedule_parts) % 2 != 0:
                await ctx.send("Weekly schedule must use day/time pairs, for example `Monday 2:30PM Wednesday 7:45PM`.")
                return
            for day, time_text in zip(schedule_parts[0::2], schedule_parts[1::2]):
                if day.lower() not in DAYS:
                    await ctx.send(f"Invalid day: `{day}`")
                    return
                parse_time_text(time_text)
                schedule.append((day.capitalize(), time_text.upper()))
        else:
            for time_text in schedule_parts:
                parse_time_text(time_text)
                schedule.append(("Daily", time_text.upper()))
    except ValueError as exc:
        await ctx.send(str(exc))
        return

    info = {
        "spawn_time": None,
        "death_time": None,
        "respawn_time": timedelta(days=1 if not is_weekly else 7),
        "killed_by": None,
        "schedule": schedule,
        "is_scheduled": True,
        "is_daily": not is_weekly,
    }
    info["spawn_time"] = next_scheduled_spawn(info, now_sg())
    bosses[name] = info
    await save_bosses()
    await refresh_status_message()
    await ctx.send(
        f"Scheduled boss **{name}** added.\n"
        f"Schedule: {schedule_text(info)}\n"
        f"Next spawn: **{info['spawn_time'].strftime('%m-%d-%Y %I:%M %p')}**"
    )


@bot.command(name="boss_status")
async def boss_status(ctx):
    for embed in boss_status_embeds():
        await ctx.send(embed=embed)


@bot.command(name="boss_today")
async def boss_today(ctx):
    await send_today_announcement(ctx.channel, "Today's Boss Schedule")


@bot.command(name="boss_alive")
async def boss_alive(ctx):
    now = now_sg()
    alive = []
    for name, info in bosses.items():
        if info.get("is_scheduled"):
            spawn_at = info.get("spawn_time")
            if spawn_at and spawn_at <= now <= spawn_at + timedelta(minutes=30):
                alive.append(name)
        else:
            death_time = info.get("death_time")
            if not death_time or now >= death_time + info.get("respawn_time", timedelta()):
                alive.append(name)
    await ctx.send("Alive bosses: " + (", ".join(alive) if alive else "None"))


@bot.command(name="guild_add")
@commands.has_permissions(administrator=True)
async def guild_add(ctx, *, guild_name: str):
    if guild_name not in guilds:
        guilds.append(guild_name)
        await save_bosses()
    await ctx.send(f"Guild **{guild_name}** added.")


@bot.command(name="guild_list")
async def guild_list(ctx):
    await ctx.send("Guilds: " + (", ".join(guilds) if guilds else "None"))


@bot.command(name="guild_delete")
@commands.has_permissions(administrator=True)
async def guild_delete(ctx, *, guild_name: str):
    if guild_name in guilds:
        guilds.remove(guild_name)
    for boss_name, turns in list(boss_turns.items()):
        boss_turns[boss_name] = [turn for turn in turns if turn != guild_name]
    await save_bosses()
    await ctx.send(f"Guild **{guild_name}** deleted.")


@bot.command(name="set_boss_turns")
@commands.has_permissions(administrator=True)
async def set_boss_turns(ctx, boss_name: str, *guild_order):
    if boss_name not in bosses:
        await ctx.send(f"Boss **{boss_name}** was not found.")
        return
    if not guild_order:
        await ctx.send("Add at least one guild name.")
        return
    boss_turns[boss_name] = list(guild_order)
    boss_current_turn[boss_name] = 0
    await save_bosses()
    await refresh_status_message()
    await ctx.send(f"Turn order for **{boss_name}**: " + " -> ".join(guild_order))


@bot.command(name="check_turn")
async def check_turn(ctx, boss_name: str):
    turn = get_current_turn(boss_name)
    await ctx.send(f"Current turn for **{boss_name}**: **{turn or 'Not configured'}**")


@bot.command(name="clear_boss_turns")
@commands.has_permissions(administrator=True)
async def clear_boss_turns(ctx, boss_name: str):
    boss_turns.pop(boss_name, None)
    boss_current_turn.pop(boss_name, None)
    await save_bosses()
    await refresh_status_message()
    await ctx.send(f"Turn order cleared for **{boss_name}**.")


@bot.command(name="maintenance_on")
@commands.has_permissions(administrator=True)
async def maintenance_on(ctx):
    global maintenance_mode
    maintenance_mode = True
    await save_bosses()
    await ctx.send("Maintenance mode is ON. Boss timers continue, but turns will not advance.")


@bot.command(name="maintenance_off")
@commands.has_permissions(administrator=True)
async def maintenance_off(ctx):
    global maintenance_mode
    maintenance_mode = False
    await save_bosses()
    await ctx.send("Maintenance mode is OFF. Turn tracking will advance normally.")


@bot.command(name="maintenance_status")
async def maintenance_status(ctx):
    await ctx.send(f"Maintenance mode: **{'ON' if maintenance_mode else 'OFF'}**")


@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(title="Boss Timer Commands", color=discord.Color.blue())
    embed.add_field(
        name="Bosses",
        value=(
            "`!boss_add <name> <respawn_hours>`\n"
            "`!boss_delete <name>`\n"
            "`!boss_tod_edit <name> <MM-DD-YYYY HH:MM AM/PM>`\n"
            "`!boss_add_schedule <name> <time...>`\n"
            "`!boss_add_schedule <name> <day time...>`\n"
            "`!boss_status`, `!boss_today`, `!boss_alive`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Turns",
        value=(
            "`!guild_add <name>`, `!guild_list`, `!guild_delete <name>`\n"
            "`!set_boss_turns <boss> <guild1> <guild2> ...`\n"
            "`!check_turn <boss>`, `!clear_boss_turns <boss>`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Maintenance",
        value="`!maintenance_on`, `!maintenance_off`, `!maintenance_status`",
        inline=False,
    )
    await ctx.send(embed=embed)


@tasks.loop(seconds=10)
async def boss_respawn_notifications():
    channel = bot.get_channel(ANNOUNCE_CHANNEL_ID)
    if not channel:
        return

    current_time = now_sg()
    changed = False

    for boss_name, info in list(bosses.items()):
        if info.get("is_scheduled"):
            spawn_at = info.get("spawn_time")
            if not spawn_at or spawn_at <= current_time - timedelta(minutes=30):
                info["spawn_time"] = next_scheduled_spawn(info, current_time)
                spawn_at = info["spawn_time"]
                changed = True
            if not spawn_at:
                continue
        else:
            death_time = info.get("death_time")
            if not death_time:
                continue
            spawn_at = death_time + info.get("respawn_time", timedelta())

        seconds_until = (spawn_at - current_time).total_seconds()
        turn = get_current_turn(boss_name)
        turn_line = f"\nCurrent turn: **{turn}**" if turn else ""

        for label, seconds, label_text in REMINDERS:
            key = reminder_key(boss_name, spawn_at, label)
            if 0 < seconds_until <= seconds and key not in reminder_sent:
                await channel.send(
                    f"@everyone **{boss_name}** will respawn in **{label_text}**!{turn_line}"
                )
                reminder_sent.add(key)

        respawn_key = reminder_key(boss_name, spawn_at, "respawn")
        if seconds_until <= 0 and respawn_key not in reminder_sent:
            if info.get("is_scheduled"):
                view = make_next_turn_view(boss_name)
            else:
                view = make_death_view(boss_name)
            await channel.send(
                f"@everyone **{boss_name}** has respawned! Time to hunt!{turn_line}",
                view=view,
            )
            reminder_sent.add(respawn_key)

    if changed:
        await save_bosses()
        await refresh_status_message()


@boss_respawn_notifications.before_loop
async def before_boss_respawn_notifications():
    await bot.wait_until_ready()


@tasks.loop(minutes=1)
async def daily_announcement():
    current_time = now_sg()
    if not ((current_time.hour == 0 and current_time.minute == 0) or (current_time.hour == 8 and current_time.minute == 0)):
        return

    key = (current_time.date().isoformat(), current_time.hour)
    if key in daily_announcements_sent:
        return
    daily_announcements_sent.add(key)

    channel = bot.get_channel(ANNOUNCE_CHANNEL_ID)
    if not channel:
        return
    if current_time.hour == 0:
        await send_today_announcement(channel, "Today's Full Boss Schedule", remaining_only=False)
    else:
        await send_today_announcement(channel, "Remaining Bosses Today", remaining_only=True)


@daily_announcement.before_loop
async def before_daily_announcement():
    await bot.wait_until_ready()


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    lines = [line.strip() for line in message.content.splitlines() if line.strip()]
    if len(lines) <= 1:
        await bot.process_commands(message)
        return

    prefixes = (COMMAND_PREFIX,) if isinstance(COMMAND_PREFIX, str) else tuple(COMMAND_PREFIX)
    if all(line.startswith(prefixes) for line in lines):
        for line in lines:
            line_message = message
            line_message.content = line
            await bot.process_commands(line_message)
        return

    await bot.process_commands(message)


@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    load_bosses()

    # Discord can fire on_ready more than once after reconnects.
    if not boss_respawn_notifications.is_running():
        boss_respawn_notifications.start()
    if not daily_announcement.is_running():
        daily_announcement.start()

    await refresh_status_message()


if not TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable not set")

bot.run(TOKEN)
