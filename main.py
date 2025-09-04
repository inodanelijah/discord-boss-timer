import discord
from discord.ext import commands, tasks
from discord.ui import Button, View
from datetime import datetime, timedelta, timezone
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

# ----------------------------
# Data handling
# ----------------------------
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        bosses = json.load(f)
else:
    bosses = {}

def save_bosses():
    with open(DATA_FILE, "w") as f:
        json.dump(bosses, f, indent=4)

# ----------------------------
# Button View for Update TOD
# ----------------------------
class BossButtons(View):
    def __init__(self, boss_name):
        super().__init__(timeout=None)
        self.boss_name = boss_name

    @discord.ui.button(label="Update TOD", style=discord.ButtonStyle.primary)
    async def update_tod(self, interaction: discord.Interaction, button: discord.ui.Button):
        now = datetime.now(UTC)

        boss = bosses.get(self.boss_name)
        if not boss:
            await interaction.response.send_message("Boss not found!", ephemeral=True)
            return

        boss["last_tod"] = now.isoformat()

        if boss["type"] == "interval":
            interval_hours = boss.get("interval", 0)
            boss["next_respawn"] = (now + timedelta(hours=interval_hours)).isoformat()

        elif boss["type"] == "fixed":
            times = boss.get("times", [])
            next_time = None
            days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

            for i in range(7):  # check next 7 days
                check_day = (now.weekday() + i) % 7
                for t in times:
                    day, clock = t.split()
                    if day not in days:
                        continue
                    day_index = days.index(day)
                    candidate = now.replace(hour=int(clock.split(":")[0]),
                                            minute=int(clock.split(":")[1]),
                                            second=0, microsecond=0)
                    candidate += timedelta(days=(day_index - now.weekday() + i) % 7)
                    if candidate > now:
                        next_time = candidate
                        break
                if next_time:
                    break

            if next_time:
                boss["next_respawn"] = next_time.isoformat()

        save_bosses()

        await interaction.response.send_message(
            f"**{self.boss_name}** TOD updated to now. Next respawn: {boss['next_respawn']}",
            ephemeral=False
        )

# ----------------------------
# Commands
# ----------------------------
@bot.tree.command(name="boss_add", description="Add a boss (interval or fixed).")
async def boss_add(interaction: discord.Interaction, name: str, type: str, time: str = None, hours: int = None):
    name = name.strip()

    if type == "fixed":
        if not time:
            await interaction.response.send_message("Provide times: e.g., Mon 11:30,Thu 19:00", ephemeral=True)
            return
        bosses[name] = {
            "type": "fixed",
            "times": [t.strip() for t in time.split(",")],
            "next_respawn": None
        }

    elif type == "interval":
        if hours is None:
            await interaction.response.send_message("For interval bosses, provide hours (e.g., 4)", ephemeral=True)
            return
        now = datetime.now(UTC)
        bosses[name] = {
            "type": "interval",
            "interval": hours,
            "next_respawn": (now + timedelta(hours=hours)).isoformat()
        }
    else:
        await interaction.response.send_message("Type must be 'fixed' or 'interval'.", ephemeral=True)
        return

    save_bosses()
    await interaction.response.send_message(f"Boss **{name}** added successfully!", ephemeral=False)

@bot.tree.command(name="boss_status", description="Show all boss timers with Update TOD buttons.")
async def boss_status(interaction: discord.Interaction):
    if not bosses:
        await interaction.response.send_message("No bosses added yet.", ephemeral=True)
        return

    embed = discord.Embed(title="Boss Status", color=discord.Color.blue())
    for name, data in bosses.items():
        if data["type"] == "interval":
            respawn = data.get("next_respawn", "Not set")
            embed.add_field(name=name, value=f"Interval: {data['interval']}h\nNext: {respawn}", inline=False)
        elif data["type"] == "fixed":
            times = ", ".join(data.get("times", []))
            embed.add_field(name=name, value=f"Fixed: {times}\nNext: {data.get('next_respawn', 'Not set')}", inline=False)

    view = View()
    for boss_name in bosses.keys():
        view.add_item(BossButtons(boss_name).children[0])  # Add Update TOD button per boss

    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="boss_list", description="Quick list of all boss names.")
async def boss_list(interaction: discord.Interaction):
    if not bosses:
        await interaction.response.send_message("No bosses found.", ephemeral=True)
        return
    boss_names = ", ".join(bosses.keys())
    await interaction.response.send_message(f"Bosses: {boss_names}")

@bot.tree.command(name="boss_clear", description="Delete all bosses.")
async def boss_clear(interaction: discord.Interaction):
    bosses.clear()
    save_bosses()
    await interaction.response.send_message("All bosses have been cleared.")

@bot.tree.command(name="boss_update", description="Manually update TOD for a boss.")
async def boss_update(interaction: discord.Interaction, name: str, new_tod: str):
    boss = bosses.get(name)
    if not boss:
        await interaction.response.send_message(f"Boss '{name}' not found.", ephemeral=True)
        return
    boss["last_tod"] = new_tod
    save_bosses()
    await interaction.response.send_message(f"Boss {name} TOD updated to {new_tod}")

@bot.tree.command(name="boss_next", description="Show next boss to spawn.")
async def boss_next(interaction: discord.Interaction):
    next_boss = None
    soonest_time = None

    for name, data in bosses.items():
        if data.get("next_respawn"):
            respawn = datetime.fromisoformat(data["next_respawn"])
            if soonest_time is None or respawn < soonest_time:
                soonest_time = respawn
                next_boss = name

    if next_boss:
        await interaction.response.send_message(f"Next boss: **{next_boss}** at {soonest_time}")
    else:
        await interaction.response.send_message("No upcoming bosses found.")

# ----------------------------
# Background Task for Reminders
# ----------------------------
@tasks.loop(minutes=1)
async def boss_checker():
    now = datetime.now(timezone.utc)
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return

    for name, data in bosses.items():
        if data["type"] == "fixed":
            if "next_respawn" in data and data["next_respawn"]:
                respawn_time = datetime.fromisoformat(data["next_respawn"])
                if 0 < (respawn_time - now).total_seconds() <= 300:
                    await channel.send(f"⚠️ **{name}** will respawn in 5 minutes! ({respawn_time})")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await bot.tree.sync()
    boss_checker.start()

bot.run(TOKEN)