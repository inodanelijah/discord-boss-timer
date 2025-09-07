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
from datetime import datetime, timedelta
import pytz
from threading import Thread
import discord
from discord.ext import commands, tasks
from discord.ui import View, Button


# --- CONFIG ---
TOKEN = "MTQxMzI0MTAwNTExNjg4MzA5OA.GpyhkL.uaSYogKFGZlqoIhC1ufRfOMMWskFxivUuNrhfw"  # Replace with your bot token
CHANNEL_ID = 1413785757990260836  # Replace with your channel ID
DATA_FILE = "bosses.json"  # File to save boss timers

sg_timezone = pytz.timezone("Asia/Singapore")

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

# --- DATA STRUCTURES ---
bosses = {}
reminder_sent = set()
json_lock = asyncio.Lock()


# --- HELPER FUNCTIONS ---
def parse_datetime(dt_str):
    if not dt_str:
        return None
    dt = datetime.fromisoformat(dt_str)
    # Only localize if naive
    if dt.tzinfo is None:
        dt = sg_timezone.localize(dt)
    return dt

async def save_bosses():
    async with json_lock:
        data = {
            name: {
                "spawn_time": info["spawn_time"].isoformat() if info.get("spawn_time") else None,
                "death_time": info["death_time"].isoformat() if info.get("death_time") else None,
                "respawn_hours": info["respawn_time"].total_seconds() / 3600,
                "killed_by": info.get("killed_by")
            }
            for name, info in bosses.items()
        }
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=4)

def save_bosses_sync():
    asyncio.create_task(save_bosses())

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
            "killed_by": info.get("killed_by")
        }

# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Needed for "marked_by" display

bot = commands.Bot(command_prefix='/', intents=intents)

# --- BUTTON FOR TIME OF DEATH ---
class BossDeathButton(View):
    def __init__(self, boss_name: str):
        super().__init__(timeout=None)
        self.boss_name = boss_name

    @discord.ui.button(label="Time of Death", style=discord.ButtonStyle.red)
    async def death_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.boss_name in bosses:
            now = datetime.now(sg_timezone)
            bosses[self.boss_name]["death_time"] = now
            bosses[self.boss_name]["killed_by"] = interaction.user.id
            await save_bosses()

            respawn_time = now + bosses[self.boss_name]["respawn_time"]
            await interaction.response.send_message(
                f"Boss '{self.boss_name}' marked dead by {interaction.user.mention} at {now.strftime('%m-%d-%Y %I:%M %p')}.\n"
                f"Respawns at: {respawn_time.strftime('%m-%d-%Y %I:%M %p')} "
                f"(in {bosses[self.boss_name]['respawn_time']})"
            )
            await interaction.message.edit(view=None)
        else:
            await interaction.response.send_message(f"Boss '{self.boss_name}' not found!", ephemeral=True)

# --- HELPER: Get Marked By Username ---
async def get_marked_by_display(user_id, guild):
    if not user_id:
        return "N/A"
    if isinstance(user_id, str):
        import re
        match = re.search(r'\d+', user_id)
        if match:
            user_id = int(match.group(0))
        else:
            return user_id
    try:
        member = guild.get_member(user_id)
        if member:
            return member.display_name
        else:
            user = await guild.fetch_member(user_id)
            return user.display_name
    except:
        try:
            user = await bot.fetch_user(user_id)
            return user.name
        except:
            return str(user_id)

# --- BOSS COMMANDS ---

@bot.command(name="boss_status")
async def boss_status(ctx, name: str = None):
    now = datetime.now(sg_timezone)

    # Auto-sync bosses on status check
    for boss in bosses.values():
        if boss.get("death_time") and now >= boss["death_time"] + boss["respawn_time"]:
            boss["death_time"] = None
            boss["killed_by"] = None
    await save_bosses()

    # --- Single boss view ---
    if name:
        boss = bosses.get(name)
        if not boss:
            await ctx.send(f"❌ Boss '{name}' not found.")
            return

        death_time = boss.get("death_time")
        respawn_duration = boss.get("respawn_time", timedelta())
        if death_time:
            respawn_at = death_time + respawn_duration
            remaining = respawn_at - now
            if remaining.total_seconds() > 0:
                status = "Dead"
                respawn_in = f"{int(remaining.total_seconds()//3600)}h {(int(remaining.total_seconds()%3600)//60)}m {int(remaining.total_seconds()%60)}s"
            else:
                status = "Alive"
                respawn_in = "0h 0m 0s"
        else:
            status = "Alive"
            respawn_in = "N/A"
            respawn_at = now

        marked_by = await get_marked_by_display(boss.get("killed_by"), ctx.guild)

        message = (
            f"**Boss:** {name}\n"
            f"**Status:** {status}\n"
            f"**Respawn In:** {respawn_in}\n"
            f"**Respawn At:** {respawn_at.strftime('%m-%d-%Y %I:%M %p')}\n"
            f"**Marked By:** {marked_by}"
        )

        view = BossDeathButton(name) if status == "Alive" else None
        await ctx.send(message, view=view)
        return

    # --- Full list view ---
    header = f"{'Boss Name':<15}{'Status':<10}{'Respawn In':<15}{'Respawn At':<15}{'Marked By':<15}\n"
    header += "-" * 70 + "\n"

    lines = []
    for boss_name, info in bosses.items():
        death_time = info.get("death_time")
        respawn_duration = info.get("respawn_time", timedelta())
        if death_time:
            respawn_at = death_time + respawn_duration
            remaining = respawn_at - now
            if remaining.total_seconds() > 0:
                status = "Dead"
                respawn_in = f"{int(remaining.total_seconds()//3600)}h {(int(remaining.total_seconds()%3600)//60)}m {int(remaining.total_seconds()%60)}s"
            else:
                status = "Alive"
                respawn_in = "0h 0m 0s"
        else:
            status = "Alive"
            respawn_in = "N/A"
            respawn_at = now

        marked_by = await get_marked_by_display(info.get("killed_by"), ctx.guild)
        lines.append(
            f"{boss_name:<15}{status:<10}{respawn_in:<15}"
            f"{respawn_at.strftime('%m-%d-%Y %I:%M %p'):<22}{marked_by:<15}"
        )

    final_message = "```" + header + "\n".join(lines) + "```"
    await ctx.send(final_message)

@bot.command(name="boss_add")
async def boss_add(ctx, name: str, respawn_hours: float):
    spawn_time = datetime.now(sg_timezone)
    bosses[name] = {
        "spawn_time": spawn_time,
        "death_time": None,
        "respawn_time": timedelta(hours=respawn_hours),
        "killed_by": None
    }
    await save_bosses()
    view = BossDeathButton(name)
    await ctx.send(f"Boss '{name}' added! Respawn time: {respawn_hours} hours.", view=view)

@bot.command(name="boss_delete")
async def boss_delete(ctx, name: str):
    if name not in bosses:
        await ctx.send(f"❌ Boss '{name}' not found!")
        return
    del bosses[name]
    await save_bosses()
    await ctx.send(f"✅ Boss '{name}' has been deleted successfully!")

@bot.command(name="test_clear_adm")
async def boss_clear_adm(ctx):
    bosses.clear()
    reminder_sent.clear()
    if os.path.exists(DATA_FILE):
        os.remove(DATA_FILE)
    await ctx.send("All bosses have been cleared and JSON file reset!")

@bot.command(name="boss_tod_edit")
async def boss_tod_edit(ctx, name: str = None, *, new_time: str = None):
    if name not in bosses:
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

# --- EXPORT BOSS DATA ---
@bot.command(name="boss_export_json")
async def export_bosses(ctx):
    import io
    data = {
        name: {
            "spawn_time": info["spawn_time"].isoformat() if info.get("spawn_time") else None,
            "death_time": info["death_time"].isoformat() if info.get("death_time") else None,
            "respawn_hours": info["respawn_time"].total_seconds() / 3600,
            "killed_by": info.get("killed_by")
        }
        for name, info in bosses.items()
    }
    file = io.StringIO()
    json.dump(data, file, indent=4)
    file.seek(0)
    await ctx.send(file=discord.File(fp=file, filename="bosses_export.json"))

# --- BACKGROUND TASK: CHECK BOSS RESPAWNS ---
@tasks.loop(seconds=30)
async def check_boss_respawns():
    now = datetime.now(sg_timezone)
    for name, info in bosses.items():
        if info["death_time"]:
            respawn_time = info["death_time"] + info["respawn_time"]
            channel = discord.utils.get(bot.get_all_channels(), id=CHANNEL_ID)
            if not channel:
                continue

            # 5-minute reminder
            if 0 < (respawn_time - now).total_seconds() <= 300:
                if name not in reminder_sent:
                    await channel.send(f"Reminder: Boss '{name}' will respawn in 5 minutes!")
                    reminder_sent.add(name)

            # Boss respawned
            if now >= respawn_time:
                await channel.send(f"Boss '{name}' has respawned!")
                info["death_time"] = None
                info["killed_by"] = None
                await save_bosses()
                if name in reminder_sent:
                    reminder_sent.remove(name)

# --- ON READY ---
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    load_bosses()
    check_boss_respawns.start()

# --- RUN BOT ---
bot.run(TOKEN)
