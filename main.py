import os
from flask import Flask
from threading import Thread

# --- Flask server to satisfy Render port requirement ---
app = Flask("")

@app.route("/")
def home():
    return "Bot is running!"

def run_server():
    port = int(os.environ.get("PORT", 5000))  # Render provides PORT
    app.run(host="0.0.0.0", port=port)

# Start Flask in a separate thread
Thread(target=run_server).start()

import os
import json
import asyncio
from datetime import datetime, timedelta, time
import pytz
from threading import Thread
import discord
from discord.ext import commands, tasks
from discord.ui import View, Button
import re
# --- CONFIG ---
TOKEN = "MTQxMzI0MTAwNTExNjg4MzA5OA.GpyhkL.uaSYogKFGZlqoIhC1ufRfOMMWskFxivUuNrhfw"
CHANNEL_ID = 1413785757990260836
DATA_FILE = "bosses.json"
status_channel_id = 1416452770017317034
sg_timezone = pytz.timezone("Asia/Singapore")

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

# --- DATA ---
bosses = {}
reminder_sent = set()
json_lock = asyncio.Lock()
status_messages = []  # Store message objects for editing


# --- FIXED HELPERS (with null handling) ---
def parse_datetime(dt_str):
    if not dt_str or dt_str == "null" or dt_str is None:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = sg_timezone.localize(dt)
        return dt
    except (ValueError, TypeError):
        return None


async def save_bosses():
    async with json_lock:
        data = {
            name: {
                "spawn_time": info["spawn_time"].isoformat() if info.get("spawn_time") else None,
                "death_time": info["death_time"].isoformat() if info.get("death_time") else None,
                "respawn_hours": info["respawn_time"].total_seconds() / 3600,
                "killed_by": info.get("killed_by"),
                "schedule": info.get("schedule", []),
                "is_scheduled": info.get("is_scheduled", False),
                "is_daily": info.get("is_daily", False)
            }
            for name, info in bosses.items()
        }
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=4)


def load_bosses():
    if not os.path.exists(DATA_FILE):
        return
    with open(DATA_FILE, "r") as f:
        data = json.load(f)
    for name, info in data.items():
        bosses[name] = {
            "spawn_time": parse_datetime(info.get("spawn_time")),
            "death_time": parse_datetime(info.get("death_time")),
            "respawn_time": timedelta(hours=info.get("respawn_hours", 0)),
            "killed_by": info.get("killed_by"),
            "schedule": info.get("schedule", []),
            "is_scheduled": info.get("is_scheduled", False),
            "is_daily": info.get("is_daily", False)
        }

# --- FIXED EMBED REFRESH ---
# --- FIXED EMBED REFRESH ---
async def refresh_status_message():
    global status_messages

    ch = bot.get_channel(status_channel_id)
    if ch is None:
        print("⚠️ Status channel not found!")
        return

    embeds = boss_status_embeds()

    # If we have no existing messages, create new ones
    if not status_messages:
        # Clean up old bot messages first
        try:
            async for msg in ch.history(limit=50):
                if msg.author == bot.user:
                    try:
                        await msg.delete()
                    except:
                        pass
        except:
            pass

        # Send new messages with delays to avoid rate limits
        status_messages = []
        for i, em in enumerate(embeds):
            try:
                msg = await ch.send(embed=em)
                status_messages.append(msg)
                # Add a small delay between message creations
                if i < len(embeds) - 1:
                    await asyncio.sleep(1)
            except Exception as e:
                print(f"Error sending message: {e}")
        print("✅ Boss timer embeds created.")
        return

    # If we have existing messages, edit them with rate limit handling
    if len(status_messages) == len(embeds):
        for i, msg in enumerate(status_messages):
            try:
                await msg.edit(embed=embeds[i])
                # Add a small delay between edits to avoid rate limits
                if i < len(status_messages) - 1:
                    await asyncio.sleep(1)
            except discord.NotFound:
                # Message was deleted, need to recreate
                status_messages = []
                await refresh_status_message()
                return
            except discord.HTTPException as e:
                if e.status == 429:  # Rate limited
                    retry_after = e.retry_after
                    print(f"Rate limited. Retrying in {retry_after} seconds.")
                    await asyncio.sleep(retry_after)
                    await msg.edit(embed=embeds[i])
                else:
                    print(f"Error editing message: {e}")
            except Exception as e:
                print(f"Error editing message: {e}")
    else:
        # Number of embeds changed, recreate all messages
        try:
            # Delete old messages
            for msg in status_messages:
                try:
                    await msg.delete()
                except:
                    pass
        except:
            pass

        status_messages = []
        for i, em in enumerate(embeds):
            try:
                msg = await ch.send(embed=em)
                status_messages.append(msg)
                # Add a small delay between message creations
                if i < len(embeds) - 1:
                    await asyncio.sleep(1)
            except Exception as e:
                print(f"Error sending message: {e}")

    print("✅ Boss timer embeds refreshed.")

# --- EMBED BUILDER WITH SORTING ---
# --- EMBED BUILDER WITH SORTING ---
def boss_status_embeds():
    now = datetime.now(sg_timezone)

    # Create lists to store bosses for each category
    today_bosses = []
    other_bosses = []
    alive_bosses = []
    scheduled_bosses = []  # For scheduled bosses that are not spawning soon

    # Categorize and calculate respawn times
    for boss_name, info in bosses.items():
        death_time = info.get("death_time")
        respawn_time = info.get("respawn_time", timedelta())
        killed_by = f"<@{info.get('killed_by')}>" if info.get('killed_by') else "N/A"
        is_scheduled = info.get("is_scheduled", False)
        schedule = info.get("schedule", [])

        # Handle scheduled bosses differently
        if is_scheduled:
            spawn_time = info.get("spawn_time")
            if spawn_time:
                status = "✅ Alive" if now >= spawn_time else "⏰ Scheduled"

                # Format the schedule for display
                schedule_text = ""
                for day, time_str in schedule:
                    schedule_text += f"{day} {time_str}, "
                schedule_text = schedule_text.rstrip(", ")  # Remove trailing comma

                boss_data = {
                    "name": boss_name,
                    "status": status,
                    "schedule_text": schedule_text,
                    "respawn_str": spawn_time.strftime("%m-%d-%Y %I:%M %p"),
                    "respawn_at": spawn_time,
                    "death_time": death_time
                }

                if now >= spawn_time:
                    alive_bosses.append(boss_data)
                elif spawn_time.date() == now.date():
                    today_bosses.append(boss_data)
                elif (spawn_time.date() - now.date()).days <= 7:  # Spawning within a week
                    other_bosses.append(boss_data)
                else:
                    scheduled_bosses.append(boss_data)
            continue

        # Regular boss logic (unchanged)
        if death_time:
            respawn_at = death_time + respawn_time
            status = "✅ Alive" if now >= respawn_at else "❌ Dead"
            respawn_str = respawn_at.strftime("%m-%d-%Y %I:%M %p")

            boss_data = {
                "name": boss_name,
                "status": status,
                "respawn_str": respawn_str,
                "killed_by": killed_by,
                "respawn_at": respawn_at,
                "death_time": death_time
            }

            if now >= respawn_at:
                alive_bosses.append(boss_data)
            elif respawn_at.date() == now.date():
                today_bosses.append(boss_data)
            else:
                other_bosses.append(boss_data)
        else:
            # Boss is alive (never died)
            boss_data = {
                "name": boss_name,
                "status": "✅ Alive",
                "respawn_str": "N/A",
                "killed_by": killed_by,
                "respawn_at": now + timedelta(days=365),  # Far future for sorting
                "death_time": None
            }
            alive_bosses.append(boss_data)

    # Sort bosses by respawn time (soonest first)
    today_bosses.sort(key=lambda x: x["respawn_at"])
    other_bosses.sort(key=lambda x: x["respawn_at"])
    alive_bosses.sort(key=lambda x: x["name"])  # Sort alive bosses alphabetically
    scheduled_bosses.sort(key=lambda x: x["respawn_at"])  # Sort scheduled bosses by spawn time

    # Function to split a list into chunks of max 25 items
    def chunk_list(lst, chunk_size):
        for i in range(0, len(lst), chunk_size):
            yield lst[i:i + chunk_size]

    # Create embeds
    embeds = []

    # Today's bosses (split into multiple pages if needed)
    today_chunks = list(chunk_list(today_bosses, 25))
    for i, chunk in enumerate(today_chunks):
        embed = discord.Embed(
            title=f"📅 LordNine Boss Timers - Today's Spawns (Page {i + 1})",
            description=f"🔥🐉 **__⚔️ BOSS SPAWNS FOR TODAY ⚔️__** 🐉🔥\n\n"
                        f"_Timezone: Asia/Singapore_\n"
                        f"Last updated: {now.strftime('%m-%d-%Y %I:%M %p')}",
            color=discord.Color.green()
        )

        for boss in chunk:
            if "schedule_text" in boss:  # Scheduled boss
                embed.add_field(
                    name=f"⚔️ {boss['name']}",
                    value=f"**Status:** {boss['status']}\n**Schedule:** {boss['schedule_text']}\n**Next Spawn:** {boss['respawn_str']}",
                    inline=False
                )
            else:  # Regular boss
                embed.add_field(
                    name=f"⚔️ {boss['name']}",
                    value=f"**Status:** {boss['status']}\n**Respawn At:** {boss['respawn_str']}\n**Marked By:** {boss['killed_by']}",
                    inline=False
                )

        embeds.append(embed)

    # Other bosses within the next week (split into multiple pages if needed)
    other_chunks = list(chunk_list(other_bosses, 25))
    for i, chunk in enumerate(other_chunks):
        embed = discord.Embed(
            title=f"📅 LordNine Boss Timers - Next Week Spawns (Page {len(embeds) + 1})",
            description=f"**__📌 BOSS spawns within the next week__**\n\n"
                        f"_Timezone: Asia/Singapore_\n"
                        f"Last updated: {now.strftime('%m-%d-%Y %I:%M %p')}",
            color=discord.Color.blue()
        )

        for boss in chunk:
            if "schedule_text" in boss:  # Scheduled boss
                embed.add_field(
                    name=f"⚔️ {boss['name']}",
                    value=f"**Status:** {boss['status']}\n**Schedule:** {boss['schedule_text']}\n**Next Spawn:** {boss['respawn_str']}",
                    inline=False
                )
            else:  # Regular boss
                embed.add_field(
                    name=f"⚔️ {boss['name']}",
                    value=f"**Status:** {boss['status']}\n**Respawn At:** {boss['respawn_str']}\n**Marked By:** {boss['killed_by']}",
                    inline=False
                )

        embeds.append(embed)

    # Alive bosses (split into multiple pages if needed)
    alive_chunks = list(chunk_list(alive_bosses, 25))
    for i, chunk in enumerate(alive_chunks):
        embed = discord.Embed(
            title=f"📅 LordNine Boss Timers - Alive Bosses (Page {len(embeds) + 1})",
            description=f"**__✅ Currently Alive BOSSES __**\n\n"
                        f"_Timezone: Asia/Singapore_\n"
                        f"Last updated: {now.strftime('%m-%d-%Y %I:%M %p')}",
            color=discord.Color.gold()
        )

        for boss in chunk:
            if "schedule_text" in boss:  # Scheduled boss
                embed.add_field(
                    name=f"⚔️ {boss['name']}",
                    value=f"**Status:** {boss['status']}\n**Schedule:** {boss['schedule_text']}\n**Next Spawn:** {boss['respawn_str']}",
                    inline=False
                )
            else:  # Regular boss
                embed.add_field(
                    name=f"⚔️ {boss['name']}",
                    value=f"**Status:** {boss['status']}\n**Respawn At:** {boss['respawn_str']}\n**Marked By:** {boss['killed_by']}",
                    inline=False
                )

        embeds.append(embed)

    # Future scheduled bosses (split into multiple pages if needed)
    scheduled_chunks = list(chunk_list(scheduled_bosses, 25))
    for i, chunk in enumerate(scheduled_chunks):
        embed = discord.Embed(
            title=f"📅 LordNine Boss Timers - Future Scheduled (Page {len(embeds) + 1})",
            description=f"**__⏰ Future Scheduled BOSSES __**\n\n"
                        f"_Timezone: Asia/Singapore_\n"
                        f"Last updated: {now.strftime('%m-%d-%Y %I:%M %p')}",
            color=discord.Color.purple()
        )

        for boss in chunk:
            embed.add_field(
                name=f"⚔️ {boss['name']}",
                value=f"**Status:** {boss['status']}\n**Schedule:** {boss['schedule_text']}\n**Next Spawn:** {boss['respawn_str']}",
                inline=False
            )

        embeds.append(embed)

    # If all embeds are empty, return at least one
    if not embeds:
        embed = discord.Embed(
            title="📅 LordNine Boss Timers",
            description="No bosses are currently being tracked.",
            color=discord.Color.green()
        )
        embeds.append(embed)

    return embeds

# --- COMMANDS ---
@bot.command(name="boss_add")
async def boss_add(ctx, name: str, respawn_hours: float):
    spawn_time = datetime.now(sg_timezone)
    bosses[name] = {
        "spawn_time": spawn_time,
        "death_time": None,
        "respawn_time": timedelta(hours=respawn_hours),
        "killed_by": None,
    }
    await save_bosses()
    await ctx.send(f"✅ Boss '{name}' added with respawn {respawn_hours}h.")


@bot.command(name="boss_delete")
async def boss_delete(ctx, name: str):
    if name not in bosses:
        await ctx.send(f"❌ Boss '{name}' not found!")
        return
    del bosses[name]
    await save_bosses()
    await ctx.send(f"✅ Boss '{name}' deleted.")


@bot.command(name="boss_status")
async def boss_status(ctx):
    await refresh_status_message()
    await ctx.send("✅ Boss status refreshed!")


@bot.command(name="boss_tod_edit")
async def boss_tod_edit(ctx, name: str = None, *, new_time: str = None):
    if not name or name not in bosses:
        await ctx.send(f"❌ Boss '{name}' not found.")
        return

    if new_time:
        try:
            naive_dt = datetime.strptime(new_time, "%m-%d-%Y %I:%M %p")
            death_time = sg_timezone.localize(naive_dt)
        except ValueError:
            await ctx.send("❌ Invalid time format! Use: `MM-DD-YYYY HH:MM AM/PM`")
            return
    else:
        death_time = datetime.now(sg_timezone)

    bosses[name]["death_time"] = death_time
    bosses[name]["killed_by"] = ctx.author.id
    await save_bosses()

    await ctx.send(
        f"✅ Time of Death for **{name}** updated to {death_time.strftime('%m-%d-%Y %I:%M %p')} by {ctx.author.mention}"
    )

# Add this command with the others
@bot.command(name="boss_add_schedule")
async def boss_add_scheduled(ctx, *, args: str):
    """
    Add a boss with scheduled spawn times
    Format: /test_add_schedule <name> <day> <time> [<day> <time> ...] OR /test_add_schedule <name> <time> [<time> ...]
    Examples:
    - Weekly: /test_add_schedule Dragon Monday 2:30PM Wednesday 7:45PM
    - Daily: /test_add_schedule Dragon 11:00AM 8:00PM
    """
    try:
        # Parse the arguments
        parts = args.split()
        if len(parts) < 2:
            await ctx.send(
                "❌ Invalid format! Use: `/boss_add_schedule <name> <day> <time> [<day> <time> ...]` for weekly schedule OR `/boss_add_schedule <name> <time> [<time> ...]` for daily schedule")
            return

        name = parts[0]
        schedule = []
        is_daily = False

        # Check if the second part is a day of the week or a time
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        time_regex = r'^([0-9]|1[0-2]):[0-5][0-9](AM|PM)$'

        if parts[1].capitalize() in days:
            # Weekly schedule with days
            if len(parts) < 3 or len(parts) % 2 != 1:
                await ctx.send(
                    "❌ Invalid format! For weekly schedule, use: `/boss_add_schedule <name> <day> <time> [<day> <time> ...]`")
                return

            for i in range(1, len(parts), 2):
                day_str = parts[i].capitalize()
                time_str = parts[i + 1].upper()

                # Validate day
                if day_str not in days:
                    await ctx.send(f"❌ Invalid day: {day_str}. Use full day names (Monday, Tuesday, etc.)")
                    return

                # Validate time format (12-hour format with AM/PM)
                if not re.match(time_regex, time_str):
                    await ctx.send(
                        f"❌ Invalid time format: {time_str}. Use H:MMAM/PM or HH:MMAM/PM (e.g., 2:30PM or 11:45AM)")
                    return

                schedule.append((day_str, time_str))
        else:
            # Daily schedule with times only
            is_daily = True
            # Parse time-only pairs for daily schedule
            for i in range(1, len(parts)):
                time_str = parts[i].upper()

                # Validate time format (12-hour format with AM/PM)
                if not re.match(time_regex, time_str):
                    await ctx.send(
                        f"❌ Invalid time format: {time_str}. Use H:MMAM/PM or HH:MMAM/PM (e.g., 2:30PM or 11:45AM)")
                    return

                schedule.append(("Daily", time_str))

        # Calculate next spawn time from the schedule
        now = datetime.now(sg_timezone)
        next_spawn = None

        for day, time_str in schedule:
            if is_daily:
                # For daily schedule, calculate next occurrence today or tomorrow
                target_date = now.date()

                # Parse 12-hour format time
                time_str_clean = time_str.replace("AM", "").replace("PM", "")
                time_parts = time_str_clean.split(":")
                hour = int(time_parts[0])
                minute = int(time_parts[1])

                # Adjust for PM
                if "PM" in time_str and hour != 12:
                    hour += 12
                # Adjust for AM (12AM becomes 0)
                if "AM" in time_str and hour == 12:
                    hour = 0

                target_time = time(hour, minute)
                candidate = sg_timezone.localize(
                    datetime.combine(target_date, target_time)
                )

                # If time already passed today, try tomorrow
                if candidate < now:
                    candidate += timedelta(days=1)
            else:
                # For weekly schedule, find the next occurrence of this day
                day_idx = days.index(day)
                current_day_idx = now.weekday()

                days_ahead = day_idx - current_day_idx
                if days_ahead <= 0:  # Target day already happened this week
                    days_ahead += 7

                target_date = now + timedelta(days=days_ahead)

                # Parse 12-hour format time
                time_str_clean = time_str.replace("AM", "").replace("PM", "")
                time_parts = time_str_clean.split(":")
                hour = int(time_parts[0])
                minute = int(time_parts[1])

                # Adjust for PM
                if "PM" in time_str and hour != 12:
                    hour += 12
                # Adjust for AM (12AM becomes 0)
                if "AM" in time_str and hour == 12:
                    hour = 0

                target_time = time(hour, minute)
                candidate = sg_timezone.localize(
                    datetime.combine(target_date.date(), target_time)
                )

            if next_spawn is None or candidate < next_spawn:
                next_spawn = candidate

        # Store the boss with its schedule
        bosses[name] = {
            "spawn_time": next_spawn,
            "death_time": None,
            "respawn_time": timedelta(days=1) if is_daily else timedelta(weeks=1),
            "killed_by": None,
            "schedule": schedule,  # Store the schedule for future calculations
            "is_scheduled": True,  # Flag to identify scheduled bosses
            "is_daily": is_daily  # Flag to identify daily vs weekly bosses
        }

        await save_bosses()

        if is_daily:
            times = [time for day, time in schedule]
            schedule_text = ", ".join(times)
            await ctx.send(
                f"✅ Boss '{name}' added with daily schedule: {schedule_text}. Next spawn: {next_spawn.strftime('%m-%d-%Y %I:%M %p')}")
        else:
            schedule_text = ", ".join([f"{day} {time}" for day, time in schedule])
            await ctx.send(
                f"✅ Boss '{name}' added with weekly schedule: {schedule_text}. Next spawn: {next_spawn.strftime('%m-%d-%Y %I:%M %p')}")

    except Exception as e:
        await ctx.send(f"❌ Error adding scheduled boss: {str(e)}")
        print(f"Error in boss_add_schedule: {e}")

        @bot.command(name="boss_export_json")
        async def export_bosses(ctx):
            import io
            data = {
                name: {
                    "spawn_time": info["spawn_time"].isoformat() if info.get("spawn_time") else None,
                    "death_time": info["death_time"].isoformat() if info.get("death_time") else None,
                    "respawn_hours": info["respawn_time"].total_seconds() / 3600,
                    "killed_by": info.get("killed_by"),
                    "schedule": info.get("schedule"),
                    "is_scheduled": info.get("is_scheduled", False),
                    "is_daily": info.get("is_daily", False)
                }
                for name, info in bosses.items()
            }
            file = io.StringIO()
            json.dump(data, file, indent=4)
            file.seek(0)
            await ctx.send(file=discord.File(fp=file, filename="bosses_export.json"))

# --- BOSS RESPAWN NOTIFICATIONS (NO AUTO REFRESH) ---
# We need to modify the boss_respawn_notifications function to properly handle respawn alerts
# We need to modify the boss_respawn_notifications function to handle daily schedules correctly
@tasks.loop(seconds=5)  # 5 seconds for better precision
async def boss_respawn_notifications():
    chat_channel = bot.get_channel(CHANNEL_ID)
    if not chat_channel:
        return

    now = datetime.now(sg_timezone)

    for boss_name, info in list(bosses.items()):
        is_scheduled = info.get("is_scheduled", False)

        # For scheduled bosses, calculate next spawn based on schedule
        if is_scheduled:
            schedule = info.get("schedule", [])
            is_daily = info.get("is_daily", False)

            # Get the current spawn time
            current_spawn_time = info.get("spawn_time")

            # Always calculate the next spawn time to ensure it's correct
            next_spawn = None

            for day, time_str in schedule:
                if is_daily:
                    # For daily schedule, calculate next occurrence
                    target_date = now.date()

                    # Parse 12-hour format time
                    time_str_clean = time_str.replace("AM", "").replace("PM", "")
                    time_parts = time_str_clean.split(":")
                    hour = int(time_parts[0])
                    minute = int(time_parts[1])

                    # Adjust for PM
                    if "PM" in time_str and hour != 12:
                        hour += 12
                    # Adjust for AM (12AM becomes 0)
                    if "AM" in time_str and hour == 12:
                        hour = 0

                    target_time = time(hour, minute)
                    candidate = sg_timezone.localize(
                        datetime.combine(target_date, target_time)
                    )

                    # If time already passed today, try tomorrow
                    if candidate <= now:
                        candidate += timedelta(days=1)
                else:
                    # For weekly schedule, find the next occurrence of this day
                    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                    day_idx = days.index(day)
                    current_day_idx = now.weekday()

                    days_ahead = day_idx - current_day_idx
                    if days_ahead < 0 or (days_ahead == 0 and now.time() > time(hour, minute)):
                        # Target day already happened this week or time already passed today
                        days_ahead += 7

                    target_date = now + timedelta(days=days_ahead)

                    # Parse 12-hour format time
                    time_str_clean = time_str.replace("AM", "").replace("PM", "")
                    time_parts = time_str_clean.split(":")
                    hour = int(time_parts[0])
                    minute = int(time_parts[1])

                    # Adjust for PM
                    if "PM" in time_str and hour != 12:
                        hour += 12
                    # Adjust for AM (12AM becomes 0)
                    if "AM" in time_str and hour == 12:
                        hour = 0

                    target_time = time(hour, minute)
                    candidate = sg_timezone.localize(
                        datetime.combine(target_date.date(), target_time)
                    )

                if next_spawn is None or candidate < next_spawn:
                    next_spawn = candidate

            # Update the spawn time if it's different
            if current_spawn_time != next_spawn:
                info["spawn_time"] = next_spawn
                current_spawn_time = next_spawn
                await save_bosses()

            # Calculate time until respawn
            seconds_until_respawn = (current_spawn_time - now).total_seconds()

            # 5-minute warning (300 seconds)
            if 0 < seconds_until_respawn <= 300 and (boss_name, "5m") not in reminder_sent:
                # Send warning exactly at 5 minutes before
                if seconds_until_respawn <= 300 and seconds_until_respawn > 295:
                    await chat_channel.send(f"⏰**REMINDER:** @everyone **{boss_name}** will respawn in **5 minutes**!")
                    reminder_sent.add((boss_name, "5m"))

            # Respawn alert - check if current time is equal to or past the spawn time
            if now >= current_spawn_time and (boss_name, "respawn") not in reminder_sent:
                await chat_channel.send(f"✅**ATTENTION!** @everyone __**{boss_name}**__ has respawned! Time to hunt!")
                reminder_sent.add((boss_name, "respawn"))
                reminder_sent.discard((boss_name, "5m"))

                # After respawn, calculate next spawn time
                if is_daily:
                    next_spawn_time = current_spawn_time + timedelta(days=1)
                else:
                    next_spawn_time = current_spawn_time + timedelta(weeks=1)
                info["spawn_time"] = next_spawn_time
                await save_bosses()

        # For regular bosses
        else:
            death_time = info.get("death_time")
            respawn_time = info.get("respawn_time", timedelta())

            if not death_time or not respawn_time:
                continue
                
            respawn_at = death_time + respawn_time
            seconds_until_respawn = (respawn_at - now).total_seconds()

            # 5-minute warning (300 seconds)
            if 0 < seconds_until_respawn <= 300 and (boss_name, "5m") not in reminder_sent:
                # Send warning exactly at 5 minutes before
                if seconds_until_respawn <= 300 and seconds_until_respawn > 295:
                    await chat_channel.send(f"⏰**REMINDER:** @everyone **{boss_name}** will respawn in **5 minutes**!")
                    reminder_sent.add((boss_name, "5m"))

            # Respawn alert
            if seconds_until_respawn <= 0 and (boss_name, "respawn") not in reminder_sent:
                view = View(timeout=None)
                tod_button = Button(label=f"Time of Death {boss_name}", style=discord.ButtonStyle.danger)

                async def button_callback(interaction, b_name=boss_name, ch=chat_channel):
                    now_click = datetime.now(sg_timezone)
                    bosses[b_name]["death_time"] = now_click
                    bosses[b_name]["killed_by"] = interaction.user.id
                    await save_bosses()

                    respawn_at_new = now_click + bosses[b_name]["respawn_time"]

                    await ch.send(
                        f"⚔️ Boss '{b_name}' marked dead by {interaction.user.mention} at {now_click.strftime('%m-%d-%Y %I:%M %p')}.\n"
                        f"Respawns at: {respawn_at_new.strftime('%m-%d-%Y %I:%M %p')}"
                    )
                    try:
                        await interaction.response.send_message("✅ Time of Death recorded.", ephemeral=True)
                    except:
                        pass

                    reminder_sent.discard((b_name, "5m"))
                    reminder_sent.discard((b_name, "respawn"))

                    try:
                        await interaction.message.edit(view=None)
                    except:
                        pass

                    # Refresh status embeds after TOD button is pressed
                    await refresh_status_message()

                tod_button.callback = button_callback
                view.add_item(tod_button)

                await chat_channel.send(f"✅**ATTENTION!** @everyone __**{boss_name}**__ has respawned! Time to hunt!",
                                        view=view)
                reminder_sent.add((boss_name, "respawn"))
                reminder_sent.discard((boss_name, "5m"))

# --- ON READY ---
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    load_bosses()
    # Only start the notification task, not auto-refresh
    boss_respawn_notifications.start()

# --- RUN ---
bot.run(TOKEN)
