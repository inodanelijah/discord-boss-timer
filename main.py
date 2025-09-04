import discord
from discord.ext import commands, tasks
from discord.ui import View, Button
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

# ------------------- DATA HANDLING -------------------
def load_data():
    global bosses
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            bosses = json.load(f)

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(bosses, f, indent=4)

load_data()

# ------------------- BUTTONS -------------------
class BossButtons(View):
    def __init__(self, boss_name):
        super().__init__(timeout=None)
        self.boss_name = boss_name

    @discord.ui.button(label="Set TOD Now", style=discord.ButtonStyle.success)
    async def set_tod_now(self, interaction: discord.Interaction, button: Button):
        now = datetime.now().strftime("%H:%M")

        if self.boss_name not in bosses:
            await interaction.response.send_message(f"❌ Boss **{self.boss_name}** not found.", ephemeral=True)
            return

        boss = bosses[self.boss_name]
        if boss["type"] != "interval":
            await interaction.response.send_message(
                f"⚠️ Boss **{self.boss_name}** is weekly-based — edit its schedule manually.",
                ephemeral=True
            )
            return

        boss["tod"] = now
        save_data()
        await interaction.response.send_message(
            f"✅ TOD for **{self.boss_name}** updated to `{now}`.\nNext spawn recalculated!",
            ephemeral=True
        )

# ------------------- COMMANDS -------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    boss_checker.start()

@bot.command(name="boss_add")
async def boss_add(ctx, name: str, time: str, hours: int):
    bosses[name] = {"type": "interval", "tod": time, "hours": hours}
    save_data()
    await ctx.send(f"✅ Added **{name}**! TOD: {time}, Respawn: {hours}h", view=BossButtons(name))

@bot.command(name="boss_add_weekly")
async def boss_add_weekly(ctx, name: str, *schedule):
    # Auto-convert short day names to full
    days_full = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    days_short = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    converted_schedule = []
    for entry in schedule:
        if any(entry.startswith(d) for d in days_short + days_full):
            converted_schedule.append(entry)
        else:
            converted_schedule[-1] += " " + entry  # Combine day with time

    bosses[name] = {"type": "weekly", "schedule": converted_schedule}
    save_data()
    await ctx.send(f"✅ Added **{name}** with schedule: {', '.join(converted_schedule)}")

@bot.command(name="boss_remove")
async def boss_remove(ctx, name: str):
    if name in bosses:
        del bosses[name]
        save_data()
        await ctx.send(f"🗑️ Removed boss **{name}**")
    else:
        await ctx.send(f"❌ Boss **{name}** not found")

@bot.command(name="boss_clear")
async def boss_clear(ctx):
    bosses.clear()
    save_data()
    await ctx.send("🗑️ All bosses have been cleared!")

@bot.command(name="boss_list")
async def boss_list(ctx):
    if not bosses:
        await ctx.send("No bosses added yet.")
    else:
        boss_names = "\n".join(bosses.keys())
        await ctx.send(f"**Boss List:**\n{boss_names}")

@bot.command(name="boss_update")
async def boss_update(ctx, name: str, new_tod: str):
    if name not in bosses or bosses[name]["type"] != "interval":
        await ctx.send(f"❌ Boss **{name}** not found or not an interval boss.")
        return

    bosses[name]["tod"] = new_tod
    save_data()
    await ctx.send(f"✅ TOD for **{name}** updated to `{new_tod}`.")

@bot.command(name="boss_next")
async def boss_next(ctx):
    now = datetime.now()
    soonest_name = None
    soonest_time = None

    days_full = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    days_short = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for name, data in bosses.items():
        if data["type"] == "interval":
            tod = datetime.strptime(data["tod"], "%H:%M").replace(year=now.year, month=now.month, day=now.day)
            next_spawn = tod + timedelta(hours=data["hours"])
        else:  # weekly
            next_spawn = None
            for schedule in data["schedule"]:
                day, time = schedule.split()
                if day in days_short:
                    day = days_full[days_short.index(day)]
                spawn_time = datetime.strptime(time, "%H:%M").replace(year=now.year, month=now.month, day=now.day)
                day_num = days_full.index(day)
                spawn_time = spawn_time.replace(day=now.day + ((day_num - now.weekday()) % 7))
                if next_spawn is None or spawn_time < next_spawn:
                    next_spawn = spawn_time

        if soonest_time is None or next_spawn < soonest_time:
            soonest_name = name
            soonest_time = next_spawn

    if soonest_name:
        await ctx.send(f"**Next Boss:** {soonest_name} → {soonest_time.strftime('%A %H:%M')}")
    else:
        await ctx.send("No upcoming bosses found.")

@bot.command(name="boss_status")
async def boss_status(ctx):
    if not bosses:
        await ctx.send("No bosses added yet.")
        return

    now = datetime.now()
    days_full = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    days_short = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for name, data in bosses.items():
        embed = discord.Embed(title=f"Boss: {name}", color=0x00ff00)
        if data["type"] == "interval":
            tod = datetime.strptime(data["tod"], "%H:%M").replace(year=now.year, month=now.month, day=now.day)
            interval = timedelta(hours=data["hours"])
            next_spawn = tod + interval
            remaining = next_spawn - now
            embed.add_field(name="TOD", value=data['tod'], inline=True)
            embed.add_field(name="Respawn", value=f"{data['hours']}h", inline=True)
            embed.add_field(name="Next Spawn", value=next_spawn.strftime('%A %H:%M'), inline=False)
            embed.add_field(name="Countdown", value=str(remaining).split('.')[0], inline=False)
            await ctx.send(embed=embed, view=BossButtons(name))
        elif data["type"] == "weekly":
            schedule_str = ""
            for schedule in data["schedule"]:
                day, time = schedule.split()
                if day in days_short:
                    day = days_full[days_short.index(day)]
                schedule_str += f"{day} {time}\n"
            embed.add_field(name="Schedule", value=schedule_str.strip(), inline=False)
            await ctx.send(embed=embed)

# ------------------- TASKS -------------------
@tasks.loop(seconds=60)
async def boss_checker():
    now = datetime.now()
    channel = bot.get_channel(CHANNEL_ID)

    days_full = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    days_short = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for name, data in bosses.items():
        if data["type"] == "interval":
            tod = datetime.strptime(data["tod"], "%H:%M").replace(year=now.year, month=now.month, day=now.day)
            next_spawn = tod + timedelta(hours=data["hours"])
            remaining = next_spawn - now

            if 0 < remaining.total_seconds() <= 300:  # 5-minute reminder
                await channel.send(f"⏰ **{name}** will respawn in 5 minutes! ({next_spawn.strftime('%H:%M')})")

        elif data["type"] == "weekly":
            for schedule in data["schedule"]:
                day, time = schedule.split()
                if day in days_short:
                    day = days_full[days_short.index(day)]
                spawn_time = datetime.strptime(time, "%H:%M").replace(year=now.year, month=now.month, day=now.day)
                day_num = days_full.index(day)
                spawn_time = spawn_time.replace(day=now.day + ((day_num - now.weekday()) % 7))

                remaining = spawn_time - now
                if 0 < remaining.total_seconds() <= 300:
                    await channel.send(f"⏰ **{name}** (weekly) will respawn in 5 minutes! ({spawn_time.strftime('%A %H:%M')})")

bot.run(TOKEN)