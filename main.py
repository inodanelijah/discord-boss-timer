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
TOKEN = "MTQxMzI0MTAwNTExNjg4MzA5OA.GpyhkL.uaSYogKFGZlqoIhC1ufRfOMMWskFxivUuNrhfw"  # Replace with your bot token
CHANNEL_ID = 1413127621214081158  # Replace with your channel ID
DATA_FILE = "bosses.json"  # File to save boss timers

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True  # REQUIRED for commands
bot = commands.Bot(command_prefix='/', intents=intents)

bosses = {}  # Store boss info
reminder_sent = set()  # Track which bosses already had 5-min reminder

# --- Button class for Time of Death ---
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
            if self.boss_name in reminder_sent:
                reminder_sent.remove(self.boss_name)

            await interaction.response.send_message(
                f"Boss '{self.boss_name}' marked dead by {interaction.user.mention} at {now.strftime('%I:%M:%S %p')}.\n"
                f"Respawns at: {respawn_time.strftime('%I:%M:%S %p')} (in {bosses[self.boss_name]['respawn_time']})",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(f"Boss '{self.boss_name}' not found!", ephemeral=True)

# --- Add a boss ---
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

# --- Check boss status ---
@bot.command(name="boss_status")
async def boss_status(ctx, name: str = None):
    now = datetime.now()

    if name:
        # Single boss status
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
        # All bosses status in a single message
        if not bosses:
            await ctx.send("No bosses added yet!")
            return

        message_lines = []
        alive_views = []
        for b_name, info in bosses.items():
            status = "Alive" if info["death_time"] is None else "Dead"
            respawn_msg = "N/A"
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

            message_lines.append(f"{b_name} - {status} - Respawn In: {respawn_msg}")
            if status == "Alive":
                alive_views.append(BossDeathButton(b_name))

        view = None
        if alive_views:
            view = View(timeout=None)
            for v in alive_views:
                for child in v.children:
                    view.add_item(child)

        await ctx.send("\n".join(message_lines), view=view)

# --- Delete a specific boss ---
@bot.command(name="boss_delete")
async def boss_delete(ctx, name: str):
    if name in bosses:
        bosses.pop(name)
        if name in reminder_sent:
            reminder_sent.remove(name)
        await ctx.send(f"Boss '{name}' has been deleted.")
    else:
        await ctx.send(f"Boss '{name}' not found!")

# --- Clear all bosses ---
@bot.command(name="boss_clear")
async def boss_clear(ctx):
    bosses.clear()
    reminder_sent.clear()
    await ctx.send("All bosses have been cleared!")

# --- Background task for respawns and 5-min reminders ---
@tasks.loop(seconds=30)
async def check_boss_respawns():
    now = datetime.now()
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

            # Boss has respawned
            if now >= respawn_time:
                await channel.send(f"Boss '{name}' has respawned!")
                info["death_time"] = None
                if name in reminder_sent:
                    reminder_sent.remove(name)


# --- Bot ready event ---
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")

    # --- TEMP: REMOVE ALL GLOBAL SLASH COMMANDS ---
    bot.tree.clear_commands(guild=None)  # Clear globally
    await bot.tree.sync()  # Push the changes
    print("All old slash commands have been cleared!")

    # Start your background task after clearing
    check_boss_respawns.start()


bot.run(TOKEN)
