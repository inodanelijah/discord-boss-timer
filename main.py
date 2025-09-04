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
#from datetime import datetime, timedelta, UTC
from datetime import datetime, timedelta, UTC
import json
import os

# --- CONFIG ---
TOKEN = "MTQxMzI0MTAwNTExNjg4MzA5OA.G1U8iU.8ZYLejoMhP8L_PiOhVPNkGO_8GRUiFVpdNCLgA"  # Replace with your bot token
CHANNEL_ID = 1413127621214081158  # Replace with your channel ID
DATA_FILE = "bosses.json"  # File to save boss timers

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True  # REQUIRED for commands
bot = commands.Bot(command_prefix='/', intents=intents)

bosses = {}  # Store boss info
reminder_sent = set()  # Track which bosses already had 5-min reminder

# Button class to mark time of death
class BossDeathButton(discord.ui.View):
    def __init__(self, boss_name: str):
        super().__init__(timeout=None)
        self.boss_name = boss_name

    @discord.ui.button(label="Time of Death", style=discord.ButtonStyle.red)
    async def death_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.boss_name in bosses:
            now = datetime.now()
            bosses[self.boss_name]["death_time"] = now
            respawn_time = now + bosses[self.boss_name]["respawn_time"]
            # Reset reminder tracking
            if self.boss_name in reminder_sent:
                reminder_sent.remove(self.boss_name)
            await interaction.response.send_message(
                f"Boss '{self.boss_name}' marked dead at {now.strftime('%I:%M:%S %p')}.\n"
                f"Respawns at: {respawn_time.strftime('%I:%M:%S %p')} (in {bosses[self.boss_name]['respawn_time']})",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(f"Boss '{self.boss_name}' not found!", ephemeral=True)

# Command to add a boss with a button
@bot.command(name="boss_add")
async def boss_add(ctx, name: str, respawn_hours: float):
    spawn_time = datetime.now()
    bosses[name] = {
        "spawn_time": spawn_time,
        "death_time": None,
        "respawn_time": timedelta(hours=respawn_hours)
    }
    view = BossDeathButton(name)
    await ctx.send(f"Boss '{name}' added! Respawn time: {respawn_hours} hours.", view=view)

# Command to check status of a boss
@bot.command(name="boss_status")
async def boss_status(ctx, name: str = None):
    now = datetime.now()
    if name:
        if name in bosses:
            boss = bosses[name]
            status = "Alive" if boss["death_time"] is None else "Dead"
            respawn_msg = "N/A"
            if boss["death_time"]:
                respawn_time = boss["death_time"] + boss["respawn_time"]
                if now >= respawn_time:
                    status = "Alive"
                    boss["death_time"] = None
                else:
                    remaining = respawn_time - now
                    hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                    minutes, seconds = divmod(remainder, 60)
                    respawn_msg = f"{hours}h {minutes}m {seconds}s (at {respawn_time.strftime('%I:%M:%S %p')})"

            view = BossDeathButton(name) if status == "Alive" else None

            await ctx.send(
                f"{name} - Status: {status}\n"
                f"Spawn Time: {boss['spawn_time'].strftime('%I:%M:%S %p')}\n"
                f"Death Time: {boss['death_time'].strftime('%I:%M:%S %p') if boss['death_time'] else 'N/A'}\n"
                f"Respawn In: {respawn_msg}",
                view=view
            )
        else:
            await ctx.send(f"Boss '{name}' not found!")
    else:
        for b_name, info in bosses.items():
            status = "Alive" if info["death_time"] is None else "Dead"
            respawn_msg = ""
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

            view = BossDeathButton(b_name) if status == "Alive" else None
            await ctx.send(f"{b_name} - {status} - Respawn In: {respawn_msg or 'N/A'}", view=view)

# Background task to announce respawns and 5-minute reminders
@tasks.loop(seconds=30)
async def check_boss_respawns():
    now = datetime.now()
    for name, info in bosses.items():
        if info["death_time"]:
            respawn_time = info["death_time"] + info["respawn_time"]
            channel = discord.utils.get(bot.get_all_channels(), name='general')  # Change to your channel
            if not channel:
                continue

            # 5-minute reminder
            if 0 < (respawn_time - now).total_seconds() <= 300:  # 5 minutes
                if name not in reminder_sent:
                    await channel.send(f"Reminder: Boss '{name}' will respawn in 5 minutes!")
                    reminder_sent.add(name)

            # Boss has respawned
            if now >= respawn_time:
                await channel.send(f"Boss '{name}' has respawned!")
                info["death_time"] = None
                if name in reminder_sent:
                    reminder_sent.remove(name)

@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    check_boss_respawns.start()

bot.run(TOKEN)
