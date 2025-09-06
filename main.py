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

import discord
from discord.ext import commands, tasks
from discord.ui import View, Button
from datetime import datetime, timedelta
import pytz
import json
import os


# --- CONFIG ---
TOKEN = "MTQxMzI0MTAwNTExNjg4MzA5OA.GpyhkL.uaSYogKFGZlqoIhC1ufRfOMMWskFxivUuNrhfw"  # Replace with your bot token
CHANNEL_ID = 1413127621214081158  # Replace with your channel ID
DATA_FILE = "bosses.json"  # File to save boss timers

sg_timezone = pytz.timezone("Asia/Singapore")

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

bosses = {}
reminder_sent = set()


# --- Helper: Save bosses to JSON ---
def save_bosses():
    data = {
        name: {
            "spawn_time": info["spawn_time"].isoformat() if info["spawn_time"] else None,
            "death_time": info["death_time"].isoformat() if info["death_time"] else None,
            "respawn_hours": info["respawn_time"].total_seconds() / 3600,
            "killed_by": info.get("killed_by")
        }
        for name, info in bosses.items()
    }
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)


# --- Helper: Load bosses from JSON ---
def load_bosses():
    if not os.path.exists(DATA_FILE):
        return
    with open(DATA_FILE, "r") as f:
        data = json.load(f)
    for name, info in data.items():
        bosses[name] = {
            "spawn_time": datetime.fromisoformat(info["spawn_time"]) if info["spawn_time"] else None,
            "death_time": datetime.fromisoformat(info["death_time"]) if info["death_time"] else None,
            "respawn_time": timedelta(hours=info["respawn_hours"]),
            "killed_by": info.get("killed_by")
        }


# --- Button for Time of Death ---
class BossDeathButton(View):
    def __init__(self, boss_name: str):
        super().__init__(timeout=None)
        self.boss_name = boss_name

    @discord.ui.button(label="Time of Death", style=discord.ButtonStyle.red)
    async def death_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.boss_name in bosses:
            now = datetime.now(sg_timezone)
            bosses[self.boss_name]["death_time"] = now
            bosses[self.boss_name]["killed_by"] = interaction.user.mention
            save_bosses()

            respawn_time = now + bosses[self.boss_name]["respawn_time"]
            await interaction.response.send_message(
                f"Boss '{self.boss_name}' marked dead by {interaction.user.mention} at {now.strftime('%m-%d-%Y %I:%M %p')}.\n"
                f"Respawns at: {respawn_time.strftime('%m-%d-%Y %I:%M %p')} "
                f"(in {bosses[self.boss_name]['respawn_time']})"
                f"(hours)",

            )

            await interaction.message.edit(view=None)
        else:
            await interaction.response.send_message(f"Boss '{self.boss_name}' not found!", ephemeral=True)


# --- Add a boss ---
@bot.command(name="boss_add")
async def test_add(ctx, name: str, respawn_hours: float):
    spawn_time = datetime.now(sg_timezone)
    bosses[name] = {
        "spawn_time": spawn_time,
        "death_time": None,
        "respawn_time": timedelta(hours=respawn_hours),
        "killed_by": None
    }
    save_bosses()
    view = BossDeathButton(name)
    await ctx.send(f"Boss '{name}' added! Respawn time: {respawn_hours} hours.", view=view)


# --- Boss status ---
@bot.command(name="boss_status")
async def test_status(ctx, name: str = None):
    now = datetime.now(sg_timezone)

    if name:
        if name not in bosses:
            await ctx.send(f"Boss '{name}' not found!")
            return

        boss = bosses[name]
        status = "Alive" if boss["death_time"] is None else "Dead"
        respawn_msg = "N/A"
        exact_respawn_time = "N/A"

        if boss["death_time"]:
            respawn_time = boss["death_time"] + boss["respawn_time"]
            if now >= respawn_time:
                status = "Alive"
                boss["death_time"] = None
            else:
                remaining = respawn_time - now
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                minutes, seconds = divmod(remainder, 60)
                respawn_msg = f"{hours}h {minutes}m {seconds}s"
                exact_respawn_time = respawn_time.strftime('%m-%d-%Y %I:%M %p')

        view = BossDeathButton(name) if status == "Alive" else None

        killed_by_msg = f"Marked by: {boss['killed_by']}" if boss.get("killed_by") else ""

        await ctx.send(
            f"{name} - Status: {status}\n"
            f"Spawn Time: {boss['spawn_time'].strftime('%m-%d-%Y %I:%M %p')}\n"
            f"Death Time: {boss['death_time'].strftime('%m-%d-%Y %I:%M %p') if boss['death_time'] else 'N/A'}\n"
            f"{killed_by_msg}\n"
            f"Respawn In: {respawn_msg}\n"
            f"Respawn At: {exact_respawn_time}",
            view=view
        )
    else:
        if not bosses:
            await ctx.send("No bosses added yet!")
            return

        message_lines = []
        for b_name, info in bosses.items():
            status = "Alive" if info["death_time"] is None else "Dead"
            respawn_msg = "N/A"
            exact_respawn_time = "N/A"

            if info["death_time"]:
                respawn_time = info["death_time"] + info["respawn_time"]
                if now >= respawn_time:
                    status = "Alive"
                    info["death_time"] = None
                else:
                    remaining = respawn_time - now
                    hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                    minutes, seconds = divmod(remainder, 60)
                    respawn_msg = f"{hours}h {minutes}m {seconds}s"
                    exact_respawn_time = respawn_time.strftime('%m-%d-%Y %I:%M %p')

            message_lines.append(
                f"{b_name} - {status} - Respawn In: {respawn_msg} - Respawn At: {exact_respawn_time}"
            )

        await ctx.send("\n".join(message_lines))


# --- Edit Time of Death ---
@bot.command(name="boss_tod_edit")
async def test_tod_edit(ctx, name: str = None, *, new_time: str = None):
    if not name:
        await ctx.send(
            "❌ **Usage:** `/test_tod_edit <boss_name> [MM-DD-YYYY HH:MM AM/PM]`\n"
            "Example: `/test_tod_edit Clemantis 09-07-2025 02:30 PM`"
        )
        return

    if name not in bosses:
        await ctx.send(f"❌ Boss '{name}' not found.")
        return

    if new_time:
        try:
            naive_dt = datetime.strptime(new_time, "%m-%d-%Y %I:%M %p")
            death_time = sg_timezone.localize(naive_dt)
        except ValueError:
            await ctx.send(
                "❌ Invalid time format! Use: `MM-DD-YYYY HH:MM AM/PM`\n"
                "Example: `09-07-2025 02:30 PM`"
            )
            return
    else:
        death_time = datetime.now(sg_timezone)

    bosses[name]["death_time"] = death_time
    bosses[name]["killed_by"] = ctx.author.mention

    respawn_time = death_time + bosses[name]["respawn_time"]
    bosses[name]["next_respawn"] = respawn_time.isoformat()
    save_bosses()

    await ctx.send(
        f"✅ Time of Death for **{name}** updated to {death_time.strftime('%m-%d-%Y %I:%M %p')} by {ctx.author.mention}"
    )


# --- Clear all bosses (Admin) ---
@bot.command(name="boss_clear_adm")
async def test_clear_adm(ctx):
    bosses.clear()
    reminder_sent.clear()
    if os.path.exists(DATA_FILE):
        os.remove(DATA_FILE)
    await ctx.send("All bosses have been cleared and JSON file reset!")


# --- Background task ---
@tasks.loop(seconds=30)
async def check_boss_respawns():
    now = datetime.now(sg_timezone)
    for name, info in bosses.items():
        if info["death_time"]:
            respawn_time = info["death_time"] + info["respawn_time"]
            channel = discord.utils.get(bot.get_all_channels(), id=CHANNEL_ID)
            if not channel:
                continue

            if 0 < (respawn_time - now).total_seconds() <= 300:
                if name not in reminder_sent:
                    await channel.send(f"Reminder: Boss '{name}' will respawn in 5 minutes!")
                    reminder_sent.add(name)

            if now >= respawn_time:
                await channel.send(f"Boss '{name}' has respawned!")
                info["death_time"] = None
                info["killed_by"] = None
                save_bosses()
                if name in reminder_sent:
                    reminder_sent.remove(name)


# --- On Ready ---
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    load_bosses()
    check_boss_respawns.start()


bot.run(TOKEN)
