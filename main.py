import discord
from discord.ext import commands, tasks
from discord.ui import Button, View
from datetime import datetime, timedelta
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

bosses = {}

# --------- Data Helpers ---------

def load_bosses():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_bosses(bosses):
    with open(DATA_FILE, "w") as f:
        json.dump(bosses, f, indent=4)

# --------- Interactive Buttons ---------

class BossButtons(View):
    def __init__(self, boss_name):
        super().__init__(timeout=None)
        self.boss_name = boss_name

    @discord.ui.button(label="Update TOD", style=discord.ButtonStyle.primary)
    async def update_tod(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"Enter new TOD for **{self.boss_name}** in format `YYYY-MM-DD HH:MM`:",
            ephemeral=True
        )

        def check(msg):
            return msg.author == interaction.user and msg.channel == interaction.channel

        try:
            msg = await bot.wait_for("message", timeout=60.0, check=check)
            new_tod = msg.content.strip()
            bosses = load_bosses()
            if self.boss_name not in bosses:
                await interaction.followup.send("❌ Boss not found.", ephemeral=True)
                return
            bosses[self.boss_name]["last_killed"] = new_tod
            save_bosses(bosses)
            await interaction.followup.send(f"✅ TOD updated for **{self.boss_name}** → {new_tod}", ephemeral=True)
        except:
            await interaction.followup.send("❌ Timeout. No TOD updated.", ephemeral=True)

    @discord.ui.button(label="Delete Boss", style=discord.ButtonStyle.danger)
    async def delete_boss(self, interaction: discord.Interaction, button: discord.ui.Button):
        bosses = load_bosses()
        if self.boss_name in bosses:
            del bosses[self.boss_name]
            save_bosses(bosses)
            await interaction.response.send_message(f"🗑 Deleted boss **{self.boss_name}**", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Boss not found.", ephemeral=True)

# --------- Commands ---------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    boss_checker.start()

@bot.command()
async def boss_add(ctx, name: str, respawn_type: str, *, data: str = None):
    bosses = load_bosses()
    respawn_type = respawn_type.lower()

    if respawn_type == "interval":
        try:
            hours = int(data)
            bosses[name] = {
                "type": "interval",
                "hours": hours,
                "last_killed": None
            }
            save_bosses(bosses)
            await ctx.send(f"✅ Added interval boss **{name}** with respawn every {hours} hours.")
        except (ValueError, TypeError):
            await ctx.send("❌ Invalid interval format. Use `/boss_add Name interval 4`.")

    elif respawn_type == "fixed":
        if not data:
            await ctx.send("❌ Please provide a schedule: `/boss_add Clemantis fixed Mon 11:30,Thu 19:00`")
            return
        schedules = [s.strip() for s in data.split(",")]
        bosses[name] = {
            "type": "fixed",
            "schedule": schedules,
            "last_killed": None
        }
        save_bosses(bosses)
        await ctx.send(f"✅ Added fixed boss **{name}** with schedule: {', '.join(schedules)}")

    else:
        await ctx.send("❌ Invalid type. Use `interval` or `fixed`.")

@bot.command()
async def boss_status(ctx):
    bosses = load_bosses()
    if not bosses:
        await ctx.send("No bosses added yet.")
        return

    for name, data in bosses.items():
        description = ""
        if data["type"] == "interval":
            description = f"Every {data['hours']}h"
        else:
            description = f"Fixed: {', '.join(data['schedule'])}"

        view = BossButtons(name)
        await ctx.send(f"**{name}** → {description}", view=view)

@bot.command()
async def boss_list(ctx):
    bosses = load_bosses()
    if not bosses:
        await ctx.send("No bosses available.")
        return
    await ctx.send(f"**Bosses:** {', '.join(bosses.keys())}")

@bot.command()
async def boss_clear(ctx):
    save_bosses({})
    await ctx.send("All bosses cleared!")

@bot.command()
async def boss_update(ctx, name: str, *, new_tod: str):
    bosses = load_bosses()
    if name not in bosses:
        await ctx.send(f"Boss {name} not found.")
        return
    bosses[name]["last_killed"] = new_tod
    save_bosses(bosses)
    await ctx.send(f"Updated TOD for **{name}** to {new_tod}")

@bot.command()
async def boss_next(ctx):
    bosses = load_bosses()
    now = datetime.now()
    next_boss = None
    next_time = None
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for name, data in bosses.items():
        if data["type"] == "fixed":
            for sched in data["schedule"]:
                try:
                    day_str, time_str = sched.split()
                    target_day = days.index(day_str)
                    target_time = datetime.strptime(time_str, "%H:%M").time()
                    today_num = now.weekday()
                    delta_days = (target_day - today_num) % 7
                    spawn_date = (now + timedelta(days=delta_days)).replace(hour=target_time.hour,
                                                                           minute=target_time.minute,
                                                                           second=0, microsecond=0)
                    if spawn_date < now:
                        spawn_date += timedelta(days=7)
                    if not next_time or spawn_date < next_time:
                        next_time = spawn_date
                        next_boss = name
                except:
                    continue

    if next_boss:
        await ctx.send(f"Next boss: **{next_boss}** at {next_time.strftime('%a %H:%M')}")
    else:
        await ctx.send("No upcoming bosses found.")

# --------- Background Task ---------

@tasks.loop(minutes=1)
async def boss_checker():
    bosses = load_bosses()
    now = datetime.now()
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for name, data in bosses.items():
        if data["type"] == "fixed":
            for sched in data["schedule"]:
                try:
                    day_str, time_str = sched.split()
                    target_day = days.index(day_str)
                    target_time = datetime.strptime(time_str, "%H:%M").time()
                    today_num = now.weekday()
                    delta_days = (target_day - today_num) % 7
                    spawn_time = (now + timedelta(days=delta_days)).replace(hour=target_time.hour,
                                                                           minute=target_time.minute,
                                                                           second=0, microsecond=0)
                    if spawn_time < now:
                        spawn_time += timedelta(days=7)

                    reminder_time = spawn_time - timedelta(minutes=5)
                    if now >= reminder_time and now < reminder_time + timedelta(minutes=1):
                        channel = bot.get_channel(CHANNEL_ID)
                        if channel:
                            await channel.send(f"⏳ **{name}** will respawn in 5 minutes! ({spawn_time.strftime('%H:%M')})")
                except:
                    continue

# --------- Run ---------

bot.run(TOKEN)