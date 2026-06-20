import copy
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
DEFAULT_ANNOUNCE_CHANNEL_ID = int(os.getenv("BOSS_ANNOUNCE_CHANNEL_ID", "1415879101473882113"))
DEFAULT_STATUS_CHANNEL_ID = int(os.getenv("BOSS_STATUS_CHANNEL_ID", "1517530757898178680"))
TIMEZONE = ZoneInfo(os.getenv("BOSS_TIMEZONE", "Asia/Singapore"))

DATA_FILE = Path(os.getenv("BOSS_DATA_FILE", "bosses.json"))
KILL_LOG_FILE = Path(os.getenv("BOSS_KILL_LOG_FILE", "boss_kills.json"))

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)

data = {"servers": {}}
legacy_state = None

# Reminder keys include Discord server ID and target spawn timestamp, so Santiago
# and Sven can have different timers for the same boss name without collisions.
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


def atomic_write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    tmp_path.replace(path)


def storage_status(path):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        test_path = path.parent / ".boss_timer_write_test"
        test_path.write_text("ok", encoding="utf-8")
        test_path.unlink(missing_ok=True)
        writable = True
    except Exception:
        writable = False

    return {
        "path": str(path.resolve()),
        "exists": path.exists(),
        "writable": writable,
    }


def make_empty_state(guild=None):
    return {
        "name": guild.name if guild else "Unknown Discord Server",
        "announce_channel_id": None,
        "status_channel_id": None,
        "bosses": {},
        "guilds": [],
        "boss_turns": {},
        "boss_current_turn": {},
        "maintenance_mode": False,
        "button_role_id": None,
    }


def migrate_old_payload(payload):
    return {
        "name": "Migrated Timers",
        "announce_channel_id": DEFAULT_ANNOUNCE_CHANNEL_ID,
        "status_channel_id": DEFAULT_STATUS_CHANNEL_ID,
        "bosses": parse_bosses(payload.get("bosses", payload)),
        "guilds": payload.get("guilds", []),
        "boss_turns": payload.get("boss_turns", {}),
        "boss_current_turn": payload.get("boss_current_turn", {}),
        "maintenance_mode": payload.get("maintenance_mode", False),
        "button_role_id": payload.get("button_role_id"),
    }


def get_state(guild):
    global legacy_state
    if guild is None:
        raise commands.CommandError("This command must be used inside a Discord server.")

    guild_id = str(guild.id)
    if guild_id not in data["servers"]:
        if legacy_state and not data["servers"]:
            state = copy.deepcopy(legacy_state)
            state["name"] = guild.name
            legacy_state = None
        else:
            state = make_empty_state(guild)
        data["servers"][guild_id] = state
    else:
        state = data["servers"][guild_id]
        state["name"] = guild.name

    state.setdefault("announce_channel_id", None)
    state.setdefault("status_channel_id", None)
    state.setdefault("bosses", {})
    state.setdefault("guilds", [])
    state.setdefault("boss_turns", {})
    state.setdefault("boss_current_turn", {})
    state.setdefault("maintenance_mode", False)
    state.setdefault("button_role_id", None)
    return state


def get_state_by_id(guild_id):
    return data["servers"].get(str(guild_id))


def reminder_key(guild_id, boss_name, spawn_time, label):
    stamp = ensure_aware(spawn_time).isoformat()
    return str(guild_id), boss_name.lower(), stamp, label


def load_kill_log():
    if not KILL_LOG_FILE.exists():
        return {}
    with KILL_LOG_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_kill_log(log):
    atomic_write_json(KILL_LOG_FILE, log)


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


def parse_bosses(raw_bosses):
    parsed = {}
    for name, info in raw_bosses.items():
        parsed[name] = {
            "spawn_time": parse_datetime(info.get("spawn_time")),
            "death_time": parse_datetime(info.get("death_time")),
            "respawn_time": timedelta(hours=float(info.get("respawn_hours", 0))),
            "killed_by": info.get("killed_by"),
            "schedule": [tuple(item) for item in info.get("schedule", [])],
            "is_scheduled": info.get("is_scheduled", False),
            "is_daily": info.get("is_daily", False),
        }
    return parsed


async def save_data():
    payload = {"servers": {}}
    for guild_id, state in data["servers"].items():
        payload["servers"][guild_id] = {
            "name": state.get("name", "Unknown Discord Server"),
            "announce_channel_id": state.get("announce_channel_id"),
            "status_channel_id": state.get("status_channel_id"),
            "bosses": {name: serialize_boss(info) for name, info in state.get("bosses", {}).items()},
            "guilds": state.get("guilds", []),
            "boss_turns": state.get("boss_turns", {}),
            "boss_current_turn": state.get("boss_current_turn", {}),
            "maintenance_mode": state.get("maintenance_mode", False),
            "button_role_id": state.get("button_role_id"),
        }
    atomic_write_json(DATA_FILE, payload)


def load_data():
    global data, legacy_state
    source_file = DATA_FILE
    if not source_file.exists():
        repo_file = Path("bosses.json")
        if DATA_FILE != repo_file and repo_file.exists():
            source_file = repo_file
            print(f"{DATA_FILE} not found. Importing existing {repo_file} for first persistent save.")
        else:
            return

    with source_file.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if "servers" not in payload:
        legacy_state = migrate_old_payload(payload)
        data = {"servers": {}}
        return

    data = {"servers": {}}
    for guild_id, state in payload.get("servers", {}).items():
        data["servers"][str(guild_id)] = {
            "name": state.get("name", "Unknown Discord Server"),
            "announce_channel_id": state.get("announce_channel_id", DEFAULT_ANNOUNCE_CHANNEL_ID),
            "status_channel_id": state.get("status_channel_id", DEFAULT_STATUS_CHANNEL_ID),
            "bosses": parse_bosses(state.get("bosses", {})),
            "guilds": state.get("guilds", []),
            "boss_turns": state.get("boss_turns", {}),
            "boss_current_turn": state.get("boss_current_turn", {}),
            "maintenance_mode": state.get("maintenance_mode", False),
            "button_role_id": state.get("button_role_id"),
        }


def next_scheduled_spawn(info, after=None):
    after = ensure_aware(after or now_sg())
    upcoming = []

    for day, time_text in info.get("schedule", []):
        spawn_time = parse_time_text(time_text)
        if info.get("is_daily"):
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


def get_current_turn(state, boss_name):
    turns = state["boss_turns"].get(boss_name, [])
    if not turns:
        return None
    index = state["boss_current_turn"].get(boss_name, 0) % len(turns)
    state["boss_current_turn"][boss_name] = index
    return turns[index]


def advance_turn(state, boss_name):
    turns = state["boss_turns"].get(boss_name, [])
    if not turns:
        return None, None
    current_index = state["boss_current_turn"].get(boss_name, 0) % len(turns)
    next_index = (current_index + 1) % len(turns)
    state["boss_current_turn"][boss_name] = next_index
    return turns[current_index], turns[next_index]


def can_use_boss_button(state, member):
    role_id = state.get("button_role_id")
    if not role_id:
        return True
    if getattr(member.guild_permissions, "administrator", False):
        return True
    return any(role.id == role_id for role in getattr(member, "roles", []))


def button_role_text(state, guild):
    role_id = state.get("button_role_id")
    if not role_id:
        return "Everyone"
    role = guild.get_role(role_id) if guild else None
    return role.mention if role else f"Role ID {role_id}"


async def record_kill(guild_id, boss_name, user, channel):
    state = get_state_by_id(guild_id)
    if not state or boss_name not in state["bosses"]:
        await channel.send(f"Boss **{boss_name}** was not found for this Discord server.")
        return

    boss = state["bosses"][boss_name]
    killed_at = now_sg()
    boss["death_time"] = killed_at
    boss["killed_by"] = user.id

    log = load_kill_log()
    server_log = log.setdefault(str(guild_id), {})
    server_log.setdefault(boss_name, []).append(killed_at.isoformat())
    save_kill_log(log)

    respawn_at = killed_at + boss.get("respawn_time", timedelta())
    for label, _, _ in REMINDERS:
        reminder_sent.discard(reminder_key(guild_id, boss_name, respawn_at, label))
    reminder_sent.discard(reminder_key(guild_id, boss_name, respawn_at, "respawn"))

    turn_line = ""
    if state["maintenance_mode"]:
        turn_line = "\nMaintenance mode is ON. Turn tracking was not advanced."
    else:
        current_turn, next_turn = advance_turn(state, boss_name)
        if current_turn:
            turn_line = f"\nCurrent turn: **{current_turn}**\nNext turn: **{next_turn}**"

    await save_data()
    await channel.send(
        f"Boss **{boss_name}** marked dead by {user.mention} at "
        f"{killed_at.strftime('%m-%d-%Y %I:%M %p')}.\n"
        f"Respawns at: **{respawn_at.strftime('%m-%d-%Y %I:%M %p')}**{turn_line}"
    )
    await refresh_status_message(guild_id, state)


def make_death_view(guild_id, boss_name):
    view = View(timeout=None)
    button = Button(label=f"Time of Death {boss_name}", style=discord.ButtonStyle.danger)

    async def callback(interaction):
        state = get_state_by_id(guild_id)
        if not state:
            await interaction.response.send_message("This Discord server is not configured yet.", ephemeral=True)
            return
        if not can_use_boss_button(state, interaction.user):
            await interaction.response.send_message(
                f"Only {button_role_text(state, interaction.guild)} can use boss buttons.",
                ephemeral=True,
            )
            return
        await record_kill(guild_id, boss_name, interaction.user, interaction.channel)
        await interaction.response.send_message("Time of death recorded.", ephemeral=True)
        try:
            await interaction.message.edit(view=None)
        except discord.HTTPException:
            pass

    button.callback = callback
    view.add_item(button)
    return view


def make_next_turn_view(guild_id, boss_name):
    view = View(timeout=None)
    button = Button(label=f"Next Turn {boss_name}", style=discord.ButtonStyle.primary)

    async def callback(interaction):
        state = get_state_by_id(guild_id)
        if not state:
            await interaction.response.send_message("This Discord server is not configured yet.", ephemeral=True)
            return
        if not can_use_boss_button(state, interaction.user):
            await interaction.response.send_message(
                f"Only {button_role_text(state, interaction.guild)} can use boss buttons.",
                ephemeral=True,
            )
            return
        if state["maintenance_mode"]:
            await interaction.response.send_message("Maintenance mode is ON. Turn not advanced.", ephemeral=True)
            return
        current_turn, next_turn = advance_turn(state, boss_name)
        if not current_turn:
            await interaction.response.send_message("No turn order is configured for this boss.", ephemeral=True)
            return
        await save_data()
        await interaction.channel.send(
            f"**{boss_name}** turn advanced by {interaction.user.mention}.\n"
            f"Previous turn: **{current_turn}**\nCurrent turn: **{next_turn}**"
        )
        await interaction.response.send_message("Turn advanced.", ephemeral=True)
        try:
            await interaction.message.edit(view=None)
        except discord.HTTPException:
            pass
        await refresh_status_message(guild_id, state)

    button.callback = callback
    view.add_item(button)
    return view


def boss_rows(state):
    rows = []
    current_time = now_sg()
    for name, info in state["bosses"].items():
        if info.get("is_scheduled"):
            spawn_at = info.get("spawn_time")
            if not spawn_at or spawn_at <= current_time - timedelta(minutes=30):
                spawn_at = next_scheduled_spawn(info, current_time)
                info["spawn_time"] = spawn_at
            rows.append((name, "Scheduled", spawn_at, schedule_text(info)))
        else:
            death_time = info.get("death_time")
            respawn_time = info.get("respawn_time", timedelta())
            spawn_at = death_time + respawn_time if death_time else info.get("spawn_time")
            rows.append((name, "Regular", spawn_at, ""))
    rows.sort(key=lambda row: row[2] or datetime.max.replace(tzinfo=TIMEZONE))
    return rows


def boss_status_embeds(state, title=None):
    title = title or f"LordNine Boss Timers - {state.get('name', 'Server')}"
    rows = boss_rows(state)
    if not rows:
        return [discord.Embed(title=title, description="No bosses added yet.", color=discord.Color.blue())]

    embeds = []
    for index in range(0, len(rows), 25):
        embed = discord.Embed(title=title, color=discord.Color.blue())
        for name, boss_type, spawn_at, sched_text in rows[index : index + 25]:
            turn = get_current_turn(state, name)
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


async def refresh_status_message(guild_id, state=None):
    state = state or get_state_by_id(guild_id)
    if not state:
        return
    channel_id = state.get("status_channel_id")
    channel = bot.get_channel(channel_id) if channel_id else None
    if not channel:
        return
    async for message in channel.history(limit=20):
        if message.author == bot.user and message.embeds:
            await message.delete()
    for embed in boss_status_embeds(state):
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


def todays_bosses(state, remaining_only=False):
    current_time = now_sg()
    today = current_time.date()
    rows = []
    for name, info in state["bosses"].items():
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


async def send_today_announcement(channel, state, title, remaining_only=False, ping=True):
    rows = todays_bosses(state, remaining_only=remaining_only)
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
            f"**Server:** {state.get('name', 'Unknown')}\n"
            f"**Date:** {date_text}\n"
            f"**Total:** {len(rows)} spawn(s) | Scheduled: {scheduled_count} | Regular: {regular_count}"
        ),
        color=discord.Color.gold(),
    )
    groups = {}
    for name, boss_type, spawn_at in rows:
        turn = get_current_turn(state, name)
        turn_text = f" - Turn: {turn}" if turn else ""
        groups.setdefault(spawn_at.strftime("%I:%M %p"), []).append(f"**{name}** ({boss_type}){turn_text}")
    for time_text, names in groups.items():
        embed.add_field(name=time_text, value="\n".join(names), inline=False)
    await channel.send("@everyone" if ping else None, embed=embed)


@bot.command(name="boss_setup")
@commands.has_permissions(administrator=True)
async def boss_setup(ctx, announce_channel: discord.TextChannel = None, status_channel: discord.TextChannel = None):
    state = get_state(ctx.guild)
    state["announce_channel_id"] = (announce_channel or ctx.channel).id
    state["status_channel_id"] = (status_channel or announce_channel or ctx.channel).id
    await save_data()
    await refresh_status_message(ctx.guild.id, state)
    await ctx.send(
        f"Boss timer setup saved for **{ctx.guild.name}**.\n"
        f"Announcements: <#{state['announce_channel_id']}>\n"
        f"Status panel: <#{state['status_channel_id']}>"
    )


@bot.command(name="boss_button_role")
@commands.has_permissions(administrator=True)
async def boss_button_role(ctx, role: discord.Role = None):
    state = get_state(ctx.guild)
    state["button_role_id"] = role.id if role else None
    await save_data()
    if role:
        await ctx.send(f"Boss buttons are now limited to {role.mention} and administrators in **{ctx.guild.name}**.")
    else:
        await ctx.send(f"Boss buttons can now be clicked by everyone in **{ctx.guild.name}**.")


@bot.command(name="boss_add")
@commands.has_permissions(administrator=True)
async def boss_add(ctx, name: str, respawn_hours: float):
    state = get_state(ctx.guild)
    state["bosses"][name] = {
        "spawn_time": now_sg(),
        "death_time": None,
        "respawn_time": timedelta(hours=respawn_hours),
        "killed_by": None,
        "schedule": [],
        "is_scheduled": False,
        "is_daily": False,
    }
    await save_data()
    await refresh_status_message(ctx.guild.id, state)
    await ctx.send(f"Boss **{name}** added to **{ctx.guild.name}** with {respawn_hours:g} hour respawn.")


@bot.command(name="boss_delete")
@commands.has_permissions(administrator=True)
async def boss_delete(ctx, name: str):
    state = get_state(ctx.guild)
    if state["bosses"].pop(name, None) is None:
        await ctx.send(f"Boss **{name}** was not found in **{ctx.guild.name}**.")
        return
    state["boss_turns"].pop(name, None)
    state["boss_current_turn"].pop(name, None)
    await save_data()
    await refresh_status_message(ctx.guild.id, state)
    await ctx.send(f"Boss **{name}** deleted from **{ctx.guild.name}**.")


@bot.command(name="boss_tod_edit")
@commands.has_permissions(administrator=True)
async def boss_tod_edit(ctx, name: str = None, *, new_time: str = None):
    state = get_state(ctx.guild)
    if not name or not new_time:
        await ctx.send("Usage: `!boss_tod_edit <boss> <MM-DD-YYYY HH:MM AM/PM>`")
        return
    if name not in state["bosses"]:
        await ctx.send(f"Boss **{name}** was not found in **{ctx.guild.name}**.")
        return
    if state["bosses"][name].get("is_scheduled"):
        await ctx.send("Scheduled bosses use their schedule. Time of death only applies to regular bosses.")
        return
    death_time = parse_datetime(new_time)
    if not death_time:
        await ctx.send("Invalid date. Use `MM-DD-YYYY HH:MM AM/PM`, for example `06-20-2026 08:30 PM`.")
        return
    state["bosses"][name]["death_time"] = death_time
    state["bosses"][name]["killed_by"] = ctx.author.id
    await save_data()
    await refresh_status_message(ctx.guild.id, state)
    respawn_at = death_time + state["bosses"][name].get("respawn_time", timedelta())
    await ctx.send(f"Updated **{name}** TOD for **{ctx.guild.name}**. Respawn: **{respawn_at.strftime('%m-%d-%Y %I:%M %p')}**")


@bot.command(name="boss_add_schedule")
@commands.has_permissions(administrator=True)
async def boss_add_scheduled(ctx, *, args: str):
    state = get_state(ctx.guild)
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
    state["bosses"][name] = info
    await save_data()
    await refresh_status_message(ctx.guild.id, state)
    await ctx.send(
        f"Scheduled boss **{name}** added to **{ctx.guild.name}**.\n"
        f"Schedule: {schedule_text(info)}\n"
        f"Next spawn: **{info['spawn_time'].strftime('%m-%d-%Y %I:%M %p')}**"
    )


@bot.command(name="boss_status")
async def boss_status(ctx):
    state = get_state(ctx.guild)
    for embed in boss_status_embeds(state):
        await ctx.send(embed=embed)


@bot.command(name="boss_today")
async def boss_today(ctx):
    state = get_state(ctx.guild)
    await send_today_announcement(ctx.channel, state, "Today's Boss Schedule", ping=False)


@bot.command(name="boss_alive")
async def boss_alive(ctx):
    state = get_state(ctx.guild)
    current_time = now_sg()
    alive = []
    for name, info in state["bosses"].items():
        if info.get("is_scheduled"):
            spawn_at = info.get("spawn_time")
            if spawn_at and spawn_at <= current_time <= spawn_at + timedelta(minutes=30):
                alive.append(name)
        else:
            death_time = info.get("death_time")
            if not death_time or current_time >= death_time + info.get("respawn_time", timedelta()):
                alive.append(name)
    await ctx.send("Alive bosses: " + (", ".join(alive) if alive else "None"))


@bot.command(name="guild_add")
@commands.has_permissions(administrator=True)
async def guild_add(ctx, *, guild_name: str):
    state = get_state(ctx.guild)
    if guild_name not in state["guilds"]:
        state["guilds"].append(guild_name)
        await save_data()
    await ctx.send(f"Guild **{guild_name}** added to **{ctx.guild.name}**.")


@bot.command(name="guild_list")
async def guild_list(ctx):
    state = get_state(ctx.guild)
    await ctx.send("Guilds: " + (", ".join(state["guilds"]) if state["guilds"] else "None"))


@bot.command(name="guild_delete")
@commands.has_permissions(administrator=True)
async def guild_delete(ctx, *, guild_name: str):
    state = get_state(ctx.guild)
    if guild_name in state["guilds"]:
        state["guilds"].remove(guild_name)
    for boss_name, turns in list(state["boss_turns"].items()):
        state["boss_turns"][boss_name] = [turn for turn in turns if turn != guild_name]
    await save_data()
    await ctx.send(f"Guild **{guild_name}** deleted from **{ctx.guild.name}**.")


@bot.command(name="set_boss_turns")
@commands.has_permissions(administrator=True)
async def set_boss_turns(ctx, boss_name: str, *guild_order):
    state = get_state(ctx.guild)
    if boss_name not in state["bosses"]:
        await ctx.send(f"Boss **{boss_name}** was not found in **{ctx.guild.name}**.")
        return
    if not guild_order:
        await ctx.send("Add at least one guild name.")
        return
    state["boss_turns"][boss_name] = list(guild_order)
    state["boss_current_turn"][boss_name] = 0
    await save_data()
    await refresh_status_message(ctx.guild.id, state)
    await ctx.send(f"Turn order for **{boss_name}** in **{ctx.guild.name}**: " + " -> ".join(guild_order))


@bot.command(name="check_turn")
async def check_turn(ctx, boss_name: str):
    state = get_state(ctx.guild)
    turn = get_current_turn(state, boss_name)
    await ctx.send(f"Current turn for **{boss_name}** in **{ctx.guild.name}**: **{turn or 'Not configured'}**")


@bot.command(name="clear_boss_turns")
@commands.has_permissions(administrator=True)
async def clear_boss_turns(ctx, boss_name: str):
    state = get_state(ctx.guild)
    state["boss_turns"].pop(boss_name, None)
    state["boss_current_turn"].pop(boss_name, None)
    await save_data()
    await refresh_status_message(ctx.guild.id, state)
    await ctx.send(f"Turn order cleared for **{boss_name}** in **{ctx.guild.name}**.")


@bot.command(name="maintenance_on")
@commands.has_permissions(administrator=True)
async def maintenance_on(ctx):
    state = get_state(ctx.guild)
    state["maintenance_mode"] = True
    await save_data()
    await ctx.send(f"Maintenance mode is ON for **{ctx.guild.name}**. Boss timers continue, but turns will not advance.")


@bot.command(name="maintenance_off")
@commands.has_permissions(administrator=True)
async def maintenance_off(ctx):
    state = get_state(ctx.guild)
    state["maintenance_mode"] = False
    await save_data()
    await ctx.send(f"Maintenance mode is OFF for **{ctx.guild.name}**. Turn tracking will advance normally.")


@bot.command(name="maintenance_status")
async def maintenance_status(ctx):
    state = get_state(ctx.guild)
    await ctx.send(f"Maintenance mode for **{ctx.guild.name}**: **{'ON' if state['maintenance_mode'] else 'OFF'}**")


@bot.command(name="boss_storage")
@commands.has_permissions(administrator=True)
async def boss_storage(ctx):
    boss_file = storage_status(DATA_FILE)
    kill_file = storage_status(KILL_LOG_FILE)
    await ctx.send(
        "**Boss Timer Storage**\n"
        f"Boss data: `{boss_file['path']}`\n"
        f"Boss data exists: **{boss_file['exists']}** | Writable: **{boss_file['writable']}**\n"
        f"Kill log: `{kill_file['path']}`\n"
        f"Kill log exists: **{kill_file['exists']}** | Writable: **{kill_file['writable']}**"
    )


@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(title="Boss Timer Commands", color=discord.Color.blue())
    embed.add_field(
        name="Setup",
        value=(
            "`!boss_setup #announce-channel #status-channel`\n"
            "`!boss_button_role @role` - restrict TOD/Next Turn buttons\n"
            "`!boss_button_role` - allow everyone to click buttons\n"
            "`!boss_storage`"
        ),
        inline=False,
    )
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
    embed.set_footer(text="Each Discord server has its own separate boss timers.")
    await ctx.send(embed=embed)


@tasks.loop(seconds=10)
async def boss_respawn_notifications():
    current_time = now_sg()

    for guild_id, state in list(data["servers"].items()):
        channel_id = state.get("announce_channel_id")
        channel = bot.get_channel(channel_id) if channel_id else None
        if not channel:
            continue

        changed = False
        for boss_name, info in list(state["bosses"].items()):
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
            turn = get_current_turn(state, boss_name)
            turn_line = f"\nCurrent turn: **{turn}**" if turn else ""

            for label, seconds, label_text in REMINDERS:
                key = reminder_key(guild_id, boss_name, spawn_at, label)
                if 0 < seconds_until <= seconds and key not in reminder_sent:
                    await channel.send(
                        f"@everyone **{boss_name}** will respawn in **{label_text}**!{turn_line}"
                    )
                    reminder_sent.add(key)

            respawn_key = reminder_key(guild_id, boss_name, spawn_at, "respawn")
            if seconds_until <= 0 and respawn_key not in reminder_sent:
                view = make_next_turn_view(guild_id, boss_name) if info.get("is_scheduled") else make_death_view(guild_id, boss_name)
                await channel.send(
                    f"@everyone **{boss_name}** has respawned! Time to hunt!{turn_line}",
                    view=view,
                )
                reminder_sent.add(respawn_key)

        if changed:
            await save_data()
            await refresh_status_message(guild_id, state)


@boss_respawn_notifications.before_loop
async def before_boss_respawn_notifications():
    await bot.wait_until_ready()


@tasks.loop(minutes=1)
async def daily_announcement():
    current_time = now_sg()
    if not ((current_time.hour == 0 and current_time.minute == 0) or (current_time.hour == 8 and current_time.minute == 0)):
        return

    for guild_id, state in list(data["servers"].items()):
        key = (str(guild_id), current_time.date().isoformat(), current_time.hour)
        if key in daily_announcements_sent:
            continue
        daily_announcements_sent.add(key)

        channel_id = state.get("announce_channel_id")
        channel = bot.get_channel(channel_id) if channel_id else None
        if not channel:
            continue
        if current_time.hour == 0:
            await send_today_announcement(channel, state, "Today's Full Boss Schedule", remaining_only=False)
        else:
            await send_today_announcement(channel, state, "Remaining Bosses Today", remaining_only=True)


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
        original_content = message.content
        try:
            for line in lines:
                message.content = line
                await bot.process_commands(message)
        finally:
            message.content = original_content
        return

    await bot.process_commands(message)


@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    load_data()

    # Create a server state for every Discord server where the bot is installed.
    for guild in bot.guilds:
        get_state(guild)
    await save_data()

    if not boss_respawn_notifications.is_running():
        boss_respawn_notifications.start()
    if not daily_announcement.is_running():
        daily_announcement.start()

    for guild in bot.guilds:
        await refresh_status_message(guild.id, get_state(guild))


if not TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable not set")

bot.run(TOKEN)
