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
        killed_by = info.get("killed_by")

        # --- Auto-convert old "<@ID>" format to raw ID ---
        if isinstance(killed_by, str):
            if killed_by.startswith("<@") and killed_by.endswith(">"):
                killed_by = killed_by.strip("<@>")
            try:
                killed_by = int(killed_by)
            except ValueError:
                killed_by = None

        bosses[name] = {
            "spawn_time": datetime.fromisoformat(info["spawn_time"]) if info.get("spawn_time") else None,
            "death_time": datetime.fromisoformat(info["death_time"]) if info.get("death_time") else None,
            "respawn_time": timedelta(hours=info.get("respawn_hours", 0)),
            "killed_by": killed_by
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
async def boss_add(ctx, name: str, respawn_hours: float):
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


# --- Helper: Get Marked By Username ---
async def get_marked_by_display(user_id, guild):
    if not user_id:
        return "N/A"

    # Handle both string "<@ID>" and integer ID
    if isinstance(user_id, str):
        # Extract integer from "<@1234567890>"
        import re
        match = re.search(r'\d+', user_id)
        if match:
            user_id = int(match.group(0))
        else:
            return user_id  # fallback to raw string if no digits

    try:
        member = guild.get_member(user_id)
        if member:
            return member.display_name  # nickname if available
        else:
            user = await guild.fetch_member(user_id)  # fallback
            return user.display_name
    except:
        try:
            user = await bot.fetch_user(user_id)  # global fallback
            return user.name
        except:
            return str(user_id)  # last resort

# --- Boss Status Command ---
@bot.command(name="boss_status")
async def boss_status(ctx, name: str = None):
    now = datetime.now(sg_timezone)

    # --- Single boss view ---
    if name:
        boss = bosses.get(name)
        if not boss:
            await ctx.send(f"❌ Boss '{name}' not found.")
            return

        death_time = boss.get("death_time")
        if isinstance(death_time, str):
            death_time = datetime.fromisoformat(death_time)

        respawn_duration = boss.get("respawn_time", timedelta())
        if death_time:
            respawn_at = death_time + respawn_duration
            remaining = respawn_at - now
            if remaining.total_seconds() > 0:
                status = "Dead"
                respawn_in = f"{int(remaining.total_seconds()//3600)}h {(int(remaining.total_seconds())%3600)//60}m {int(remaining.total_seconds()%60)}s"
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

    # --- Full list view (no buttons) ---
    header = f"{'Boss Name':<15}{'Status':<10}{'Respawn In':<15}{'Respawn At':<15}{'Marked By':<15}\n"
    header += "-" * 70 + "\n"

    lines = []
    for boss_name, info in bosses.items():
        death_time = info.get("death_time")
        if isinstance(death_time, str):
            death_time = datetime.fromisoformat(death_time)

        respawn_duration = info.get("respawn_time", timedelta())
        if death_time:
            respawn_at = death_time + respawn_duration
            remaining = respawn_at - now
            if remaining.total_seconds() > 0:
                status = "Dead"
                respawn_in = f"{int(remaining.total_seconds()//3600)}h {(int(remaining.total_seconds())%3600)//60}m {int(remaining.total_seconds()%60)}s"
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
            f"{respawn_at.strftime('%I:%M:%S %p'):<15}{marked_by:<15}"
        )

    final_message = "```" + header + "\n".join(lines) + "```"
    await ctx.send(final_message)
# --- Edit Time of Death ---
@bot.command(name="boss_tod_edit")
async def boss_tod_edit(ctx, name: str = None, *, new_time: str = None):
    if not name:
        await ctx.send(
            "❌ **Usage:** `/boss_tod_edit <boss_name> [MM-DD-YYYY HH:MM AM/PM]`\n"
            "Example: `/boss_tod_edit Clemantis 09-07-2025 02:30 PM`"
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

# --- Delete a Boss ---
@bot.command(name="boss_delete")
async def boss_delete(ctx, name: str):
    if name not in bosses:
        await ctx.send(f"❌ Boss '{name}' not found!")
        return

    # Remove from memory
    del bosses[name]

    # Save updated JSON
    save_bosses()

    await ctx.send(f"✅ Boss '{name}' has been deleted successfully!")

# --- Clear all bosses (Admin) ---
@bot.command(name="boss_clear_adm")
async def boss_clear_adm(ctx):
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
