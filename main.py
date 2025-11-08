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
import csv
import pytz

from datetime import datetime, timedelta, time
from threading import Thread
import discord
from discord.ext import commands, tasks
from discord.ext.commands import cooldown, BucketType
from discord.ui import View, Button
import re
from dotenv import load_dotenv

# --- CONFIG ---
load_dotenv()  # safe even if .env doesn't exist in Railway
# TOKEN = "MTQxMzI0MTAwNTExNjg4MzA5OA.GpyhkL.uaSYogKFGZlqoIhC1ufRfOMMWskFxivUuNrhfw"

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # 👈 required for get_member and guild.members to work
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN is None:
    raise ValueError("❌ DISCORD_TOKEN environment variable not set!")
else:
    bot.run(TOKEN)
    
CHANNEL_ID = 1413785757990260836  #field-boss-updates
status_channel_id = 1416452770017317034 #boss-timer
sg_timezone = pytz.timezone("Asia/Singapore")

# --- MERGED CONTENT FROM script.py START ---

# Files to store data
ATTENDANCE_FILE = "attendance.csv"
POINTS_FILE = "points.csv"
KILL_LOG_FILE = "boss_kills.json"
DATA_FILE = "bosses.json"

attendance_lock = asyncio.Lock()
points_lock = asyncio.Lock()

# Ensure attendance file exists with headers
with open(ATTENDANCE_FILE, "a", newline="") as f:
    writer = csv.writer(f)
    if f.tell() == 0:
        writer.writerow(["User", "Date", "Time"])

# Ensure points file exists with headers
with open(POINTS_FILE, "a", newline="") as f:
    writer = csv.writer(f)
    if f.tell() == 0:
        writer.writerow(["UserID", "Points"])  # ✅ Store ID, not names

# Load and save helpers
def load_kill_log():
    if not os.path.exists(KILL_LOG_FILE):
        return {}
    with open(KILL_LOG_FILE, "r") as f:
        return json.load(f)

def save_kill_log(log):
    with open(KILL_LOG_FILE, "w") as f:
        json.dump(log, f, indent=4)

# Dynamic attendance/absentee points
ATTENDANCE_POINTS = 10
ABSENTEE_PENALTY = 10

def is_admin(interaction: discord.Interaction):
    """Check if the user has the Admin role or administrator permission."""
    return (
        any(r.name == "Admin" for r in interaction.user.roles)
        or interaction.user.guild_permissions.administrator
    )

async def resolve_name(guild, user_id: int):
    """Resolve a user's display name from cache or via API (safe fallback)."""
    if guild is None:
        return f"Unknown ({user_id})"
    member = guild.get_member(user_id)
    if member:
        return member.display_name
    try:
        member = await guild.fetch_member(user_id)
        return member.display_name
    except discord.NotFound:
        return f"Unknown ({user_id})"
    except Exception:
        return f"Unknown ({user_id})"

# ----------------- Utility functions -----------------
def update_points(user_id, points_to_add=10):
    users = {}
    with open(POINTS_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            users[int(row["UserID"])] = int(row["Points"])

    users[user_id] = users.get(user_id, 0) + points_to_add

    with open(POINTS_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["UserID", "Points"])
        for uid, points in users.items():
            writer.writerow([uid, points])

def get_points(user_id):
    with open(POINTS_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if int(row["UserID"]) == user_id:
                return int(row["Points"])
    return 0

def delete_user_points(user_id: int):
    """Remove a user entirely from points.csv."""
    if not os.path.exists(POINTS_FILE):
        return False

    users = {}
    with open(POINTS_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                uid = int(row["UserID"])
                points = int(row["Points"])
                users[uid] = points
            except Exception:
                continue

    if user_id not in users:
        return False

    del users[user_id]

    with open(POINTS_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["UserID", "Points"])
        for uid, pts in users.items():
            writer.writerow([uid, pts])

    return True



# ----------------- Attendance System -----------------
class AttendanceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.attendees = []

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success, custom_id="join_btn")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        user_display = interaction.user.display_name
        today = datetime.now().strftime("%Y-%m-%d")

        # Prevent duplicate join today
        if any(a["user_id"] == user_id and a["date"] == today for a in self.attendees):
            await interaction.response.send_message("⚠️ You already joined today!", ephemeral=True)
            return

        now = datetime.now().strftime("%H:%M:%S")

        async with attendance_lock:
            self.attendees.append({"user_id": user_id, "date": today, "time": now})

            with open(ATTENDANCE_FILE, "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([user_display, today, now])

        async with points_lock:
            update_points(user_id, ATTENDANCE_POINTS)
            total = get_points(user_id)

        # Update leaderboard asynchronously without blocking the button
        if points_panel_view:
            asyncio.create_task(points_panel_view.update_leaderboard(updated_by=interaction.user))

        # Edit message embed (Discord edit calls can lag under heavy load)
        try:
            embed = interaction.message.embeds[0]
            attendees_list = "\n".join([
                f"• {interaction.guild.get_member(a['user_id']).display_name}"
                for a in self.attendees if a["date"] == today
            ])
            embed.set_field_at(
                0,
                name=f"Attendees ({len(self.attendees)})",
                value=attendees_list or "No attendees yet",
                inline=False
            )
            await interaction.message.edit(embed=embed, view=self)
        except Exception as e:
            print(f"Error updating embed: {e}")

        await interaction.response.send_message(
            f"✅ You joined attendance and earned {ATTENDANCE_POINTS} points (Total: {total}).",
            ephemeral=True
        )

    @discord.ui.button(label="Close Attendance", style=discord.ButtonStyle.danger, custom_id="close_btn")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Only admins can close attendance.", ephemeral=True)
            return

        guild = interaction.guild
        today = datetime.now().strftime("%Y-%m-%d")
        attendees_today_ids = [a["user_id"] for a in self.attendees if a["date"] == today]

        absentees = []
        for member in guild.members:
            if not member.bot and member.id not in attendees_today_ids:
                absentees.append(member.display_name)
                update_points(member.id, -ABSENTEE_PENALTY)

        # Update embed
        embed = interaction.message.embeds[0]
        embed.title = "📋 Attendance (Closed)"
        self.clear_items()
        await interaction.message.edit(embed=embed, view=self)

        if points_panel_view:
            await points_panel_view.update_leaderboard(updated_by="Attendance")

        # Summary (admin only)
        await interaction.response.send_message(
            f"📋 Attendance closed.\n✅ Present: {len(attendees_today)}\n❌ Absent: {len(absentees)} (−{ABSENTEE_PENALTY} each)",
            ephemeral=True
        )


# ----------------- Bot Events -----------------
# ----------------- Attendance Commands -----------------
@bot.command()
@commands.has_permissions(administrator=True)
async def startattendance(ctx):
    embed = discord.Embed(title="📋Guild Dungeon Attendance", description=f"Created by: {ctx.author.display_name}",
                          color=discord.Color.blue())
    embed.add_field(name="Attendees (0)", value="No attendees yet", inline=False)
    view = AttendanceView()
    await ctx.send(embed=embed, view=view)


@bot.command()
@commands.has_permissions(administrator=True)
async def setattendancepoints(ctx, amount: int):
    global ATTENDANCE_POINTS
    ATTENDANCE_POINTS = amount
    await ctx.send(f"✅ Attendance reward points set to {amount}.")


@bot.command()
@commands.has_permissions(administrator=True)
async def setabsenteepoints(ctx, amount: int):
    global ABSENTEE_PENALTY
    ABSENTEE_PENALTY = amount
    await ctx.send(f"✅ Absentee penalty points set to {amount}.")


@bot.command()
@commands.has_permissions(administrator=True)
async def showsettings(ctx):
    await ctx.send(
        f"⚙️ Current settings:\nAttendance reward: {ATTENDANCE_POINTS}\nAbsentee penalty: {ABSENTEE_PENALTY}")


# ----------------- Dropdown + Modal Combo -----------------
class PointsAmountModal(discord.ui.Modal, title="Enter Points"):
    def __init__(self, member, mode):
        super().__init__()
        self.member = member  # member object
        self.mode = mode
        self.amount = discord.ui.TextInput(label="Amount", placeholder="Enter number of points")
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        amt = int(self.amount.value)
        if self.mode == "add":
            update_points(self.member.id, amt)
            result = f"✅ Added {amt} points to {self.member.display_name}."
        elif self.mode == "remove":
            update_points(self.member.id, -amt)
            result = f"✅ Removed {amt} points from {self.member.display_name}."
        elif self.mode == "set":
            update_points(self.member.id, -get_points(self.member.id))
            update_points(self.member.id, amt)
            result = f"✅ {self.member.display_name}'s points set to {amt}."

        total = get_points(self.member.id)
        await interaction.response.send_message(f"{result} (Total: {total})", ephemeral=True)


class MemberSelect(discord.ui.Select):
    def __init__(self, matches, mode):
        # Use member.id as value to keep everything unique
        options = [
            discord.SelectOption(label=m.display_name, value=str(m.id))
            for m in matches[:25]
        ]
        super().__init__(placeholder="Select a member...", options=options)
        self.mode = mode

    async def callback(self, interaction: discord.Interaction):
        selected_id = int(self.values[0])
        member = interaction.guild.get_member(selected_id)
        if not member:
            await interaction.response.send_message("❌ Could not find this member.", ephemeral=True)
            return
        await interaction.response.send_modal(PointsAmountModal(member, self.mode))


class MemberSelectView(discord.ui.View):
    def __init__(self, matches, mode):
        super().__init__(timeout=30)
        self.add_item(MemberSelect(matches, mode))

async def ask_for_member_with_callback(interaction: discord.Interaction, mode, panel_view):
    modal = discord.ui.Modal(title="Member, Points & Reason")

    name_box = discord.ui.TextInput(
        label="Discord Nickname or Username",
        placeholder="Enter nickname or username"
    )
    amount_box = discord.ui.TextInput(
        label="Points",
        placeholder="Enter number of points"
    )
    reason_box = discord.ui.TextInput(
        label="Reason",
        placeholder="Enter reason for adding/deducting points (e.g., attendance, event win, penalty)"
    )

    modal.add_item(name_box)
    modal.add_item(amount_box)
    modal.add_item(reason_box)

    async def on_submit(interaction2: discord.Interaction):
        member_name = name_box.value.strip()

        # ✅ Resolve member by nickname or username (case-insensitive)
        member = discord.utils.find(
            lambda m: m.display_name.lower() == member_name.lower() or m.name.lower() == member_name.lower(),
            interaction2.guild.members
        )
        if not member:
            await interaction2.response.send_message(
                f"❌ Could not find user `{member_name}` in this server.",
                ephemeral=True
            )
            return

        try:
            amount = int(amount_box.value)
        except ValueError:
            await interaction2.response.send_message("❌ Points must be a number.", ephemeral=True)
            return

        reason = reason_box.value.strip() or "No reason provided"

        # ✅ Update points using member.id
        if mode == "add":
            update_points(member.id, amount)
            msg = f"✅ Added {amount} points to {member.display_name}."
        elif mode == "remove":
            update_points(member.id, -amount)
            msg = f"✅ Removed {amount} points from {member.display_name}."
        elif mode == "set":
            update_points(member.id, -get_points(member.id))
            update_points(member.id, amount)
            msg = f"✅ {member.display_name}'s points set to {amount}."

        total = get_points(member.id)

        # Acknowledge silently
        await interaction2.response.defer(ephemeral=True)

        # ✅ Log to pointspanel-log with reason
        await log_to_pointspanel(
            interaction2,
            f"📝 **{interaction2.user.display_name}** {mode} points for **{member.display_name}** by {amount}. "
            f"**Reason:** {reason}. (New total: {total})"
        )

        # ✅ Refresh leaderboard in real time
        if points_panel_view:
            await points_panel_view.update_leaderboard(updated_by=interaction2.user)

    modal.on_submit = on_submit
    await interaction.response.send_modal(modal)


async def generate_leaderboard_page(page=0, per_page=25, guild=None):
    """
    Async: returns (leaderboard_text, total_pages).
    Resolves display names via resolve_name(guild, user_id).
    """
    users = []
    if not os.path.exists(POINTS_FILE):
        return ("No users found.", 1)

    with open(POINTS_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                users.append((int(row["UserID"]), int(row["Points"])))
            except Exception:
                continue

    users.sort(key=lambda x: x[1], reverse=True)
    total_pages = (len(users) - 1) // per_page + 1 if users else 1

    start = page * per_page
    end = start + per_page
    sliced = users[start:end]

    if not sliced:
        return "No users found.", total_pages

    table = ""
    for idx, (uid, p) in enumerate(sliced, start=start + 1):
        if guild is not None:
            name = await resolve_name(guild, uid)
        else:
            name = f"Unknown ({uid})"
        table += f"{idx:>2}. {name:<20} {p}\n"

    return f"```{table}```", total_pages


class PointsPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.message = None
        self.showing = True
        self.page = 0
        self.per_page = 25
        self.last_updated_by = None

    async def update_leaderboard(self, updated_by=None):
        """Refresh leaderboard and show who made the last change."""
        if updated_by:
            if hasattr(updated_by, "display_name"):
                self.last_updated_by = updated_by.display_name
            else:
                self.last_updated_by = str(updated_by)
            self.last_update_time = datetime.now()

        if not self.message:
            return

        embed = self.message.embeds[0]
        if self.showing:
            lb_text, total_pages = await generate_leaderboard_page(
                self.page, self.per_page, guild=self.message.guild
            )
            footer_text = f"_Page {self.page + 1}/{total_pages}_"
            if self.last_updated_by:
                ts = self.last_update_time.strftime("%Y-%m-%d %H:%M:%S")
                footer_text += f"\n_Last updated by: {self.last_updated_by} at {ts}_"

            embed.description = (
                f"Use the buttons below to manage points and settings.\n\n"
                f"**Current Points:**\n{lb_text}\n{footer_text}"
            )
        else:
            embed.description = "Use the buttons below to manage points and settings.\n\n(Leaderboard hidden)"

        await self.message.edit(embed=embed, view=self)


    @discord.ui.button(label="➕ Add Points", style=discord.ButtonStyle.green, row=0)
    async def add_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await ask_for_member_with_callback(interaction, "add", self)

    @discord.ui.button(label="➖ Remove Points", style=discord.ButtonStyle.red, row=0)
    async def remove_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await ask_for_member_with_callback(interaction, "remove", self)

    @discord.ui.button(label="🎯 Set Points", style=discord.ButtonStyle.blurple, row=0)
    async def set_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await ask_for_member_with_callback(interaction, "set", self)

    @discord.ui.button(label="📊 Toggle Leaderboard", style=discord.ButtonStyle.gray, row=1)
    async def toggle_lb(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.showing = not self.showing
        self.page = 0
        await self.update_leaderboard(updated_by=interaction.user.display_name)
        await interaction.response.defer()

    @discord.ui.button(label="⬅️ Prev", style=discord.ButtonStyle.secondary, row=1)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            await self.update_leaderboard(updated_by=interaction.user.display_name)
        await interaction.response.defer()

    @discord.ui.button(label="➡️ Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Determine total pages asynchronously (message may be None if not yet set)
        _, total_pages = await generate_leaderboard_page(self.page, self.per_page,
                                                         guild=self.message.guild if self.message else None)
        if self.page < total_pages - 1:
            self.page += 1
            await self.update_leaderboard(updated_by=interaction.user.display_name)
        await interaction.response.defer()

    @discord.ui.button(label="⚙️ Set Attendance Points", style=discord.ButtonStyle.blurple, row=2)
    async def set_att_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = discord.ui.Modal(title="Set Attendance Points")
        amount = discord.ui.TextInput(label="Amount", placeholder="Enter points for attendance")
        modal.add_item(amount)

        async def on_submit(interaction2: discord.Interaction):
            global ATTENDANCE_POINTS
            ATTENDANCE_POINTS = int(amount.value)
            await interaction2.response.send_message(
                f"✅ Attendance reward points set to {ATTENDANCE_POINTS}.",
                ephemeral=True
            )
            await self.update_leaderboard(updated_by=interaction2.user.display_name)
            await log_to_pointspanel(interaction2,
                                     f"⚙️ **{interaction2.user.display_name}** set attendance reward to **{ATTENDANCE_POINTS}**.")

        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="⚙️ Set Absentee Points", style=discord.ButtonStyle.blurple, row=2)
    async def set_abs_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = discord.ui.Modal(title="Set Absentee Points")
        amount = discord.ui.TextInput(label="Amount", placeholder="Enter points to deduct")
        modal.add_item(amount)

        async def on_submit(interaction2: discord.Interaction):
            global ABSENTEE_PENALTY
            ABSENTEE_PENALTY = int(amount.value)
            await interaction2.response.send_message(
                f"✅ Absentee penalty points set to {ABSENTEE_PENALTY}.",
                ephemeral=True
            )
            await self.update_leaderboard(updated_by=interaction2.user.display_name)
            await log_to_pointspanel(interaction2,
                                     f"⚙️ **{interaction2.user.display_name}** set absentee penalty to **{ABSENTEE_PENALTY}**.")

        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="📖 Show Settings", style=discord.ButtonStyle.blurple, row=2)
    async def show_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"⚙️ Current settings:\nAttendance reward: {ATTENDANCE_POINTS}\nAbsentee penalty: {ABSENTEE_PENALTY}",
            ephemeral=True
        )

    @discord.ui.button(label="🗑️ Delete User", style=discord.ButtonStyle.danger, row=0)
    async def delete_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Open a modal to delete a user from the points list."""
        modal = discord.ui.Modal(title="Delete User from Leaderboard")

        name_box = discord.ui.TextInput(
            label="Discord Nickname or Username",
            placeholder="Enter nickname, username, or user ID"
        )
        modal.add_item(name_box)

        async def on_submit(interaction2: discord.Interaction):
            name_input = name_box.value.strip()

            # Try to find by ID first
            target_member = None
            try:
                uid = int(name_input)
                target_member = interaction2.guild.get_member(uid)
                if not target_member:
                    # Might be someone who left the server
                    deleted = delete_user_points(uid)
                    if deleted:
                        await interaction2.response.send_message(
                            f"🗑️ User with ID `{uid}` removed from points list.",
                            ephemeral=True
                        )
                        await self.update_leaderboard(updated_by=interaction2.user.display_name)
                        return
                    else:
                        await interaction2.response.send_message(
                            f"❌ Could not find user with ID `{uid}` in points list.",
                            ephemeral=True
                        )
                        return
            except ValueError:
                # Not an ID — search by name/nickname
                target_member = discord.utils.find(
                    lambda m: m.display_name.lower() == name_input.lower() or m.name.lower() == name_input.lower(),
                    interaction2.guild.members
                )

            if not target_member:
                await interaction2.response.send_message(
                    f"❌ Could not find user `{name_input}` in this server.",
                    ephemeral=True
                )
                return

            deleted = delete_user_points(target_member.id)
            if deleted:
                await interaction2.response.send_message(
                    f"🗑️ {target_member.display_name} has been removed from the points list.",
                    ephemeral=True
                )
                await self.update_leaderboard(updated_by=interaction2.user.display_name)
            else:
                await interaction2.response.send_message(
                    f"❌ {target_member.display_name} was not found in the points list.",
                    ephemeral=True
                )

        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)


global points_panel_view  # add this line at the top of your file
points_panel_view = None  # initialize

@bot.command()
@commands.has_permissions(administrator=True)
async def pointspanel(ctx):
    global points_panel_view
    view = PointsPanel()
    embed = discord.Embed(
        title="📊 Points Management",
        description="Use the buttons below to manage points and settings.\n\nLoading leaderboard...",
        color=discord.Color.purple()
    )
    message = await ctx.send(embed=embed, view=view)
    view.message = message
    points_panel_view = view  # ✅ store reference globally
    await view.update_leaderboard()


async def log_to_pointspanel(ctx_or_interaction, message: str):
    """Send a log message to #pointspanel-log channel if it exists."""
    guild = ctx_or_interaction.guild if hasattr(ctx_or_interaction, "guild") else None
    if guild:
        channel = discord.utils.get(guild.text_channels, name="pointspanel-log")
        if channel:
            await channel.send(message)


@bot.command()
@commands.has_permissions(administrator=True)
async def cleanpoints(ctx):
    """Convert old username-based points.csv to ID-based and merge duplicates."""
    guild = ctx.guild
    users = {}

    # ✅ Load and convert points
    with open(POINTS_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            user_key = row.get("UserID") or row.get("User")  # Support old files
            points = int(row["Points"])

            if row.get("UserID"):
                # Already ID-based
                user_id = int(user_key)
            else:
                # Old username-based → find member in guild
                member = discord.utils.find(
                    lambda m: m.name == user_key or (m.nick and m.nick == user_key),
                    guild.members
                )
                if member:
                    user_id = member.id
                else:
                    # Cannot find member → skip or fallback?
                    continue

            if user_id in users:
                users[user_id] += points
            else:
                users[user_id] = points

    # ✅ Write cleaned & migrated data back
    with open(POINTS_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["UserID", "Points"])
        for uid, pts in users.items():
            writer.writerow([uid, pts])

    await ctx.send(f"✅ Points file cleaned, migrated to ID-based format, and duplicates merged. {len(users)} unique users remain.")



# ----------------- Roll Timer -----------------
class RollView(discord.ui.View):
    def __init__(self, duration):
        super().__init__(timeout=duration)
        self.rolls = {}

    @discord.ui.button(label="🎲 Roll", style=discord.ButtonStyle.green, custom_id="roll_button")
    async def roll_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user.display_name

        if user in self.rolls:
            await interaction.response.send_message(
                f"⚠️ {user}, you already rolled **{self.rolls[user]}**."
            )
            return

        score = random.randint(0, 100)
        self.rolls[user] = score

        await interaction.response.send_message(
            f"🎲 {user} rolled **{score}**!"
        )

    async def on_timeout(self):
        # Called automatically when timer runs out
        if not self.rolls:
            result = "❌ No one rolled this time."
        else:
            winner, score = max(self.rolls.items(), key=lambda x: x[1])
            result = f"🏆 Roll event ended! Winner: **{winner}** with a roll of **{score}** 🎉"

        # Edit the original message to show results
        for child in self.children:
            child.disabled = True
        await self.message.edit(content=result, view=self)


@bot.command()
@commands.has_permissions(administrator=True)
async def startroll(ctx, time_in_seconds: int):
    """Start a roll event with button. Ends after given time."""
    view = RollView(duration=time_in_seconds)
    embed = discord.Embed(
        title="🎲 Roll Event",
        description=f"Press the button below to roll (0–100).\n"
                    f"Event ends in **{time_in_seconds} seconds**!",
        color=discord.Color.blue()
    )
    msg = await ctx.send(embed=embed, view=view)
    view.message = msg  # Keep reference so we can edit later

    # Wait for the timer
    await asyncio.sleep(time_in_seconds)

    # Determine results
    if not view.rolls:
        result = "❌ No one rolled this time."
    else:
        winner, score = max(view.rolls.items(), key=lambda x: x[1])
        result = f"🏆 Roll event ended! Winner: **{winner}** with a roll of **{score}** 🎉"

    # Disable the button in the original message
    for child in view.children:
        child.disabled = True
    await msg.edit(view=view)  # keep the embed but disable the button

    # Send results as a NEW message
    if not view.rolls:
        await ctx.send("❌ No one rolled this time.")
    else:
        winner, score = max(view.rolls.items(), key=lambda x: x[1])
        await ctx.send(f"🏆 Roll event ended! Winner: **{winner}** with a roll of **{score}** 🎉")


# ----------------- Reset Attendance Command -----------------
@bot.command()
@commands.has_permissions(administrator=True)
async def resetattendance(ctx):
    """Reset today's attendance so members can join again."""
    async for message in ctx.channel.history(limit=50):
        if message.author == bot.user and message.embeds:
            for view in bot.persistent_views:
                if isinstance(view, AttendanceView):
                    view.attendees.clear()  # reset in-memory list
                    await ctx.send("✅ Attendance has been reset. Everyone can join again today.")
                    return
    await ctx.send("⚠️ No active attendance panel found to reset.")


# ----------------- Leaderboard with Pagination -----------------
class LeaderboardView(discord.ui.View):
    def __init__(self, pages):
        super().__init__(timeout=60)
        self.pages = pages
        self.current = 0

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current > 0:
            self.current -= 1
            await interaction.response.edit_message(embed=self.pages[self.current], view=self)

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current < len(self.pages) - 1:
            self.current += 1
            await interaction.response.edit_message(embed=self.pages[self.current], view=self)


@bot.command()
async def leaderboard(ctx):
    """Show the leaderboard in multiple embeds (20 players per page)."""
    if not os.path.exists(POINTS_FILE):
        await ctx.send("❌ No points data yet.")
        return

    users = []
    with open(POINTS_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                users.append((int(row["UserID"]), int(row["Points"])))
            except Exception:
                continue

    if not users:
        await ctx.send("❌ No users found.")
        return

    users.sort(key=lambda x: x[1], reverse=True)

    per_page = 20
    total_pages = (len(users) - 1) // per_page + 1

    for page_num in range(total_pages):
        start = page_num * per_page
        end = start + per_page
        chunk = users[start:end]

        desc_lines = []
        for i, (uid, points) in enumerate(chunk, start=start + 1):
            name = await resolve_name(ctx.guild, uid)
            desc_lines.append(f"{i}. {name} — {points} pts")

        desc = "\n".join(desc_lines)
        embed = discord.Embed(
            title=f"🏆 Points Leaderboard (Page {page_num + 1}/{total_pages})",
            description=desc or "No data available.",
            color=discord.Color.gold()
        )
        await ctx.send(embed=embed)



# ----------------- Bidding System -----------------
class BidPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.current_bid = {
            "item": None,
            "highest_bid": 0,
            "highest_bidder": None,
            "min_bid": 0,
            "active": False
        }
        self.message = None

        # Save references to important buttons (decorator-created buttons live in self.children)
        self.start_button = None
        self.bid_button = None
        # loop children now (they exist after super().__init__ when using @discord.ui.button)
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.label == "🚀 Start Bid":
                    self.start_button = child
                elif child.label == "💸 Bid":
                    self.bid_button = child

        # If we didn't find them (defensive), try again later when message is set
    def _ensure_buttons(self):
        """Make sure start_button and bid_button are stored"""
        if self.start_button and self.bid_button:
            return
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.label == "🚀 Start Bid" and not self.start_button:
                    self.start_button = child
                elif child.label == "💸 Bid" and not self.bid_button:
                    self.bid_button = child

    def build_embed(self):
        desc = ""
        if self.current_bid["active"]:
            bidder_display = "None"
            if self.current_bid["highest_bidder"]:
                guild = self.message.guild if self.message else None
                if guild:
                    member = guild.get_member(self.current_bid["highest_bidder"])
                    bidder_display = member.display_name if member else f"User ID: {self.current_bid['highest_bidder']}"
                else:
                    bidder_display = f"User ID: {self.current_bid['highest_bidder']}"

            desc = (f"**Item:** {self.current_bid['item']}\n"
                    f"**Minimum Bid:** {self.current_bid['min_bid']}\n"
                    f"**Highest Bid:** {self.current_bid['highest_bid']} ({bidder_display})")
        else:
            desc = "No active bidding. Admins can start a bid."
        return discord.Embed(title="💰 Bidding Panel", description=desc, color=discord.Color.gold())

    async def refresh_message(self, source_message=None):
        """Edit the original message to update embed + view.
        Accepts a fallback source_message (modal interaction's message) when self.message isn't set."""
        # ensure we have button refs
        self._ensure_buttons()

        target_message = self.message or source_message
        if target_message:
            try:
                await target_message.edit(embed=self.build_embed(), view=self)
            except Exception:
                # best-effort: ignore edit errors (discord can sometimes reject duplicate edits)
                pass

    @discord.ui.button(label="🚀 Start Bid", style=discord.ButtonStyle.green, row=0)
    async def start_bid(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Only admins can start bids.", ephemeral=True)
            return
        if self.current_bid["active"]:
            await interaction.response.send_message(
                f"⚠️ A bid for **{self.current_bid['item']}** is already active. Wait for it to finish.",
                ephemeral=True
            )
            return

        modal = discord.ui.Modal(title="Start a Bid")
        item_input = discord.ui.TextInput(label="Item Name", placeholder="Enter item for bidding")
        min_bid_input = discord.ui.TextInput(label="Minimum Bid", placeholder="e.g. 50")
        time_input = discord.ui.TextInput(label="Duration (seconds)", placeholder="e.g. 60")
        modal.add_item(item_input)
        modal.add_item(min_bid_input)
        modal.add_item(time_input)

        async def on_submit(interaction2: discord.Interaction):
            # use the modal interaction as fallback for message edits
            source_msg = getattr(interaction2, "message", None)  # should be the original component message
            try:
                min_bid = int(min_bid_input.value)
                duration = int(time_input.value)
            except ValueError:
                await interaction2.response.send_message("❌ Duration and minimum bid must be numbers.", ephemeral=True)
                return

            self.current_bid = {
                "item": item_input.value,
                "highest_bid": 0,
                "highest_bidder": None,
                "min_bid": min_bid,
                "active": True
            }

            # ensure we have refs to buttons
            self._ensure_buttons()

            # enable the bid button and optionally disable start button
            if self.bid_button:
                self.bid_button.disabled = False
            if self.start_button:
                self.start_button.disabled = True

            # reply to the modal submit
            await interaction2.response.send_message(
                f"✅ Bidding started for **{item_input.value}** with minimum bid **{min_bid}**!",
                ephemeral=False
            )

            # refresh the original panel message (use stored message if available, otherwise use the modal's message)
            await self.refresh_message(source_message=source_msg)

            # Auto-close after duration (run in background task)
            await asyncio.sleep(duration)
            if self.current_bid["active"]:
                # use channel from the interaction2 (modal), fallback to stored message guild channel
                channel = interaction2.channel or (self.message.channel if self.message else None)
                if channel:
                    await self.end_bidding(channel)

        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="💸 Bid", style=discord.ButtonStyle.blurple, row=0, disabled=True)
    async def place_bid(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.current_bid["active"]:
            await interaction.response.send_message("❌ No active bidding right now.", ephemeral=True)
            return

        modal = discord.ui.Modal(title="Place Your Bid")
        amount_input = discord.ui.TextInput(label="Bid Amount", placeholder="Enter bid amount")
        modal.add_item(amount_input)

        async def on_submit(interaction2: discord.Interaction):
            user_id = interaction2.user.id
            try:
                amount = int(amount_input.value)
            except ValueError:
                await interaction2.response.send_message("❌ Bid must be a number.", ephemeral=True)
                return

            # minimum bid check
            if amount < self.current_bid["min_bid"]:
                await interaction2.response.send_message(
                    f"⚠️ Your bid must be at least {self.current_bid['min_bid']}.",
                    ephemeral=True
                )
                return

            if amount <= self.current_bid["highest_bid"]:
                await interaction2.response.send_message(
                    f"⚠️ Your bid must be higher than the current highest bid ({self.current_bid['highest_bid']}).",
                    ephemeral=True
                )
                return

            user_points = get_points(user_id)
            if amount > user_points:
                await interaction2.response.send_message(
                    f"❌ You don’t have enough points. You have {user_points} points.",
                    ephemeral=True
                )
                return

            self.current_bid["highest_bid"] = amount
            self.current_bid["highest_bidder"] = user_id

            await interaction2.response.send_message(
                f"✅ You are now the highest bidder with {amount} points!",
                ephemeral=True
            )
            await interaction2.channel.send(
                f"💸 {interaction2.user.mention} bid **{amount} points** for **{self.current_bid['item']}**!"
            )

            # refresh the original panel message; modal interaction has message attribute referencing original component
            await self.refresh_message(source_message=getattr(interaction2, "message", None))

        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)

    async def end_bidding(self, channel):
        # ensure we have refs to buttons
        self._ensure_buttons()

        winner_id = self.current_bid["highest_bidder"]
        winner_member = channel.guild.get_member(winner_id) if winner_id else None

        # disable the bid button safely
        if self.bid_button:
            self.bid_button.disabled = True
        if self.start_button:
            self.start_button.disabled = False

        if winner_member:
            update_points(winner_id, -self.current_bid["highest_bid"])
            total = get_points(winner_id)
            result = (f"🏆 Bidding ended for **{self.current_bid['item']}**!\n"
                      f"Winner: **{winner_member.display_name}** with {self.current_bid['highest_bid']} points.\n"
                      f"Remaining balance: {total} points.")
        else:
            result = f"⚠️ Bidding for **{self.current_bid['item']}** ended. No bids were placed."

        # mark as inactive and reset current bid (you can choose to reset or keep last item; here we keep item but mark inactive)
        self.current_bid["active"] = False

        await channel.send(result)

        # refresh panel message (use stored message if available)
        await self.refresh_message()

        if points_panel_view:
            await points_panel_view.update_leaderboard(updated_by="Bid System")


@bot.command()
@commands.has_permissions(administrator=True)
async def bidpanel(ctx):
    view = BidPanel()
    embed = view.build_embed()
    msg = await ctx.send(embed=embed, view=view)
    view.message = msg


# ----------------- Download/Extract csv files -----------------
@bot.command()
async def exportdata(ctx, error):
    """Export attendance.csv and points.csv (Admin only)."""
    files_to_send = []
    if os.path.exists(ATTENDANCE_FILE):
        files_to_send.append(discord.File(ATTENDANCE_FILE))
    if os.path.exists(POINTS_FILE):
        files_to_send.append(discord.File(POINTS_FILE))

    if isinstance(error, commands.MissingAnyRole):
        await ctx.send("❌ You must have the **Officer** or **Admin** role to use this command.")
    else:
        raise error

    if files_to_send:
        await ctx.send("📂 Here are the exported files:", files=files_to_send)
    else:
        await ctx.send("❌ No data files found.")


# --- MERGED CONTENT FROM script.py END ---


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

# --- FIXED EMBED REFRESH WITH BETTER RATE LIMIT HANDLING ---
async def refresh_status_message():
    global status_messages

    ch = bot.get_channel(status_channel_id)
    if ch is None:
        print("⚠️ Status channel not found!")
        return

    embeds = boss_status_embeds()

    # If we have no existing messages, create new ones
    if not status_messages:
        # Clean up old bot messages with better rate limit handling
        try:
            deleted_count = 0
            async for msg in ch.history(limit=50):
                if msg.author == bot.user:
                    try:
                        await msg.delete()
                        deleted_count += 1
                        # Add increasing delays to avoid rate limits
                        if deleted_count % 5 == 0:
                            await asyncio.sleep(1)
                        elif deleted_count % 10 == 0:
                            await asyncio.sleep(2)
                    except discord.HTTPException as e:
                        if e.status == 429:
                            # If rate limited, wait for the retry_after time
                            retry_after = e.retry_after
                            print(f"Rate limited while deleting. Waiting {retry_after} seconds.")
                            await asyncio.sleep(retry_after)
                            try:
                                await msg.delete()
                                deleted_count += 1
                            except:
                                pass
                        else:
                            print(f"Error deleting message: {e}")
                    except Exception as e:
                        print(f"Error deleting message: {e}")
        except Exception as e:
            print(f"Error cleaning up old messages: {e}")

        # Send new messages with delays to avoid rate limits
        status_messages = []
        for i, em in enumerate(embeds):
            try:
                msg = await ch.send(embed=em)
                status_messages.append(msg)
                # Add increasing delays between message creations
                if i < len(embeds) - 1:
                    await asyncio.sleep(2)  # Increased delay to 2 seconds
            except discord.HTTPException as e:
                if e.status == 429:
                    retry_after = e.retry_after
                    print(f"Rate limited while sending. Waiting {retry_after} seconds.")
                    await asyncio.sleep(retry_after)
                    try:
                        msg = await ch.send(embed=em)
                        status_messages.append(msg)
                    except:
                        pass
                else:
                    print(f"Error sending message: {e}")
            except Exception as e:
                print(f"Error sending message: {e}")
        print("✅ Boss timer embeds created.")
        return

    # If we have existing messages, edit them with better rate limit handling
    if len(status_messages) == len(embeds):
        for i, msg in enumerate(status_messages):
            try:
                await msg.edit(embed=embeds[i])
                # Add delays between edits to avoid rate limits
                if i < len(status_messages) - 1:
                    await asyncio.sleep(2)  # Increased delay to 2 seconds
            except discord.NotFound:
                # Message was deleted, need to recreate
                status_messages = []
                await refresh_status_message()
                return
            except discord.HTTPException as e:
                if e.status == 429:  # Rate limited
                    retry_after = e.retry_after
                    print(f"Rate limited while editing. Waiting {retry_after} seconds.")
                    await asyncio.sleep(retry_after)
                    try:
                        await msg.edit(embed=embeds[i])
                    except:
                        pass
                else:
                    print(f"Error editing message: {e}")
            except Exception as e:
                print(f"Error editing message: {e}")
    else:
        # Number of embeds changed, recreate all messages with better rate limiting
        try:
            # Delete old messages with rate limit handling
            for i, msg in enumerate(status_messages):
                try:
                    await msg.delete()
                    # Add delays between deletions
                    if i < len(status_messages) - 1:
                        await asyncio.sleep(2)  # Increased delay to 2 seconds
                except discord.HTTPException as e:
                    if e.status == 429:
                        retry_after = e.retry_after
                        print(f"Rate limited while deleting old messages. Waiting {retry_after} seconds.")
                        await asyncio.sleep(retry_after)
                        try:
                            await msg.delete()
                        except:
                            pass
                    else:
                        print(f"Error deleting old message: {e}")
                except Exception as e:
                    print(f"Error deleting old message: {e}")
        except Exception as e:
            print(f"Error deleting old messages: {e}")

        status_messages = []
        for i, em in enumerate(embeds):
            try:
                msg = await ch.send(embed=em)
                status_messages.append(msg)
                # Add delays between message creations
                if i < len(embeds) - 1:
                    await asyncio.sleep(2)  # Increased delay to 2 seconds
            except discord.HTTPException as e:
                if e.status == 429:
                    retry_after = e.retry_after
                    print(f"Rate limited while recreating. Waiting {retry_after} seconds.")
                    await asyncio.sleep(retry_after)
                    try:
                        msg = await ch.send(embed=em)
                        status_messages.append(msg)
                    except:
                        pass
                else:
                    print(f"Error sending message: {e}")
            except Exception as e:
                print(f"Error sending message: {e}")

    print("✅ Boss timer embeds refreshed.")


# Helper function to calculate next scheduled spawn
def calculate_next_scheduled_spawn(boss_name, info):
    now = datetime.now(sg_timezone)
    schedule = info.get("schedule", [])
    is_daily = info.get("is_daily", False)

    next_spawn = None

    for day, time_str in schedule:
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

        if is_daily:
            # For daily schedule, calculate next occurrence
            target_date = now.date()

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

            target_time = time(hour, minute)
            candidate = sg_timezone.localize(
                datetime.combine(target_date.date(), target_time)
            )

        if next_spawn is None or candidate < next_spawn:
            next_spawn = candidate

    return next_spawn

# --- EMBED BUILDER WITH SORTING ---
def boss_status_embeds():
    now = datetime.now(sg_timezone)

    # Create lists to store bosses for each category
    today_bosses = []
    other_bosses = []
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
                # For scheduled bosses, they're always "alive" during their spawn window
                # and "dead" outside of it
                if now >= spawn_time:
                    status = "✅ Alive"
                    # Calculate when this spawn window ends (next scheduled time)
                    next_spawn = calculate_next_scheduled_spawn(boss_name, info)
                    respawn_str = next_spawn.strftime("%m-%d-%Y %I:%M %p") if next_spawn else "N/A"
                else:
                    status = "⏰ Scheduled"
                    respawn_str = spawn_time.strftime("%m-%d-%Y %I:%M %p")

                # Format the schedule for display
                schedule_text = ""
                for day, time_str in schedule:
                    schedule_text += f"{day} {time_str}, "
                schedule_text = schedule_text.rstrip(", ")  # Remove trailing comma

                boss_data = {
                    "name": boss_name,
                    "status": status,
                    "schedule_text": schedule_text,
                    "respawn_str": respawn_str,
                    "respawn_at": spawn_time,
                    "death_time": death_time,
                    "is_scheduled": True
                }

                # Don't add alive scheduled bosses to main status - they'll be in /boss_alive
                if now >= spawn_time:
                    # Skip alive bosses from main status display
                    continue
                elif spawn_time.date() == now.date():
                    today_bosses.append(boss_data)
                elif (spawn_time.date() - now.date()).days <= 7:  # Spawning within a week
                    other_bosses.append(boss_data)
                else:
                    scheduled_bosses.append(boss_data)
            continue

        # Regular boss logic
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
                "death_time": death_time,
                "is_scheduled": False
            }

            # Don't add alive regular bosses to main status - they'll be in /boss_alive
            if now >= respawn_at:
                # Skip alive bosses from main status display
                continue
            elif respawn_at.date() == now.date():
                today_bosses.append(boss_data)
            else:
                other_bosses.append(boss_data)
        else:
            # Boss is alive (never died) - skip from main status
            continue

    # Sort bosses by respawn time (soonest first)
    today_bosses.sort(key=lambda x: x["respawn_at"])
    other_bosses.sort(key=lambda x: x["respawn_at"])
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
            if boss.get("is_scheduled"):  # Scheduled boss
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
            if boss.get("is_scheduled"):  # Scheduled boss
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
            description="No upcoming boss spawns currently tracked.\nUse `/boss_alive` to check currently alive bosses.",
            color=discord.Color.green()
        )
        embeds.append(embed)

    return embeds

# --- COMMANDS ---
# --- BOSS ALIVE COMMAND ---
@bot.command(name="boss_alive")
async def boss_alive(ctx):
    """Check all currently alive bosses"""
    now = datetime.now(sg_timezone)
    alive_bosses = []

    # Find all alive bosses
    for boss_name, info in bosses.items():
        is_scheduled = info.get("is_scheduled", False)

        if is_scheduled:
            # For scheduled bosses, check if current time is within spawn window
            spawn_time = info.get("spawn_time")
            if spawn_time and now >= spawn_time:
                # Calculate next spawn time
                next_spawn = calculate_next_scheduled_spawn(boss_name, info)
                respawn_str = next_spawn.strftime("%m-%d-%Y %I:%M %p") if next_spawn else "N/A"

                # Format schedule for display
                schedule = info.get("schedule", [])
                schedule_text = ""
                for day, time_str in schedule:
                    schedule_text += f"{day} {time_str}, "
                schedule_text = schedule_text.rstrip(", ")

                alive_bosses.append({
                    "name": boss_name,
                    "type": "Scheduled",
                    "respawn_str": respawn_str,
                    "schedule_text": schedule_text,
                    "is_scheduled": True
                })
        else:
            # For regular bosses, check if respawn time has passed
            death_time = info.get("death_time")
            respawn_time = info.get("respawn_time", timedelta())

            if death_time:
                respawn_at = death_time + respawn_time
                if now >= respawn_at:
                    killed_by = f"<@{info.get('killed_by')}>" if info.get('killed_by') else "N/A"
                    alive_bosses.append({
                        "name": boss_name,
                        "type": "Regular",
                        "respawn_str": respawn_at.strftime("%m-%d-%Y %I:%M %p"),
                        "killed_by": killed_by,
                        "is_scheduled": False
                    })
            else:
                # Boss never died (always alive)
                killed_by = f"<@{info.get('killed_by')}>" if info.get('killed_by') else "N/A"
                alive_bosses.append({
                    "name": boss_name,
                    "type": "Regular",
                    "respawn_str": "N/A (Never died)",
                    "killed_by": killed_by,
                    "is_scheduled": False
                })

    # Sort alive bosses alphabetically
    alive_bosses.sort(key=lambda x: x["name"])

    # Create embed
    if alive_bosses:
        embed = discord.Embed(
            title="✅ Currently Alive Bosses",
            description=f"**__BOSSES CURRENTLY SPAWNED__**\n\n"
                        f"_Timezone: Asia/Singapore_\n"
                        f"Last updated: {now.strftime('%m-%d-%Y %I:%M %p')}",
            color=discord.Color.green()
        )

        for boss in alive_bosses:
            if boss["is_scheduled"]:
                embed.add_field(
                    name=f"⚔️ {boss['name']} (Scheduled)",
                    value=f"**Next Spawn:** {boss['respawn_str']}\n**Schedule:** {boss['schedule_text']}",
                    inline=False
                )
            else:
                embed.add_field(
                    name=f"⚔️ {boss['name']} (Regular)",
                    value=f"**Respawn At:** {boss['respawn_str']}\n**Marked By:** {boss['killed_by']}",
                    inline=False
                )

        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(
            title="✅ Currently Alive Bosses",
            description="No bosses are currently alive.",
            color=discord.Color.orange()
        )
        await ctx.send(embed=embed)


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
@cooldown(1, 60, BucketType.guild)  # 1 use per 60 seconds per guild
async def boss_status(ctx):
    await refresh_status_message()
    await ctx.send("✅ Boss status refreshed!")


@bot.command(name="boss_tod_edit")
async def boss_tod_edit(ctx, name: str = None, *, new_time: str = None):
    """Manually update a boss's time of death."""
    if not name or name not in bosses:
        await ctx.send(f"❌ Boss '{name}' not found.")
        return

    # Prevent editing scheduled bosses
    if bosses[name].get("is_scheduled", False):
        await ctx.send(
            f"❌ Cannot update Time of Death for scheduled boss '{name}'. "
            f"Scheduled bosses respawn automatically based on their schedule."
        )
        return

    # Parse the new time if provided, otherwise use current time
    if new_time:
        try:
            naive_dt = datetime.strptime(new_time, "%m-%d-%Y %I:%M %p")
            death_time = sg_timezone.localize(naive_dt)
        except ValueError:
            await ctx.send("❌ Invalid time format! Use: `MM-DD-YYYY HH:MM AM/PM`")
            return
    else:
        death_time = datetime.now(sg_timezone)

    # Update boss record
    bosses[name]["death_time"] = death_time
    bosses[name]["killed_by"] = ctx.author.id

    # ✅ Update kill log (bug fixed here)
    kill_log = load_kill_log()
    if name not in kill_log:
        kill_log[name] = []
    kill_log[name].append(death_time.isoformat())
    save_kill_log(kill_log)

    # Clear any reminders for this boss
    reminder_sent.discard((name, "1h"))
    reminder_sent.discard((name, "15m"))
    reminder_sent.discard((name, "5m"))
    reminder_sent.discard((name, "respawn"))

    await save_bosses()

    respawn_time = bosses[name].get("respawn_time", timedelta(hours=0))
    respawn_at = death_time + respawn_time

    await ctx.send(
        f"✅ Time of Death for **{name}** updated to {death_time.strftime('%m-%d-%Y %I:%M %p')} by {ctx.author.mention}\n"
        f"**Respawn At:** {respawn_at.strftime('%m-%d-%Y %I:%M %p')}"
    )

    await refresh_status_message()


# Add this command with the others
@bot.command(name="boss_add_schedule")
async def boss_add_scheduled(ctx, *, args: str):
    """
    Add a boss with scheduled spawn times
    Format: /test_add_schedule <name> <day> <time> [<day> <time> ...] OR /test_add_schedule <name> <time> [<time> ...]
    Examples:
    - Weekly: /boss_add_schedule Dragon Monday 2:30PM Wednesday 7:45PM
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

        # --- DAILY RESPAWN ANNOUNCEMENT COMMAND ---
        # Add this command with the others (NOT nested inside boss_add_scheduled)
        @bot.command(name="boss_export_json")
        async def boss_export_json(ctx):
            """Export all boss data as a JSON file"""
            try:
                import io

                # Prepare the data for export in the exact format requested
                data = {}
                for name, info in bosses.items():
                    data[name] = {
                        "spawn_time": info["spawn_time"].isoformat() if info.get("spawn_time") else None,
                        "death_time": info["death_time"].isoformat() if info.get("death_time") else None,
                        "respawn_hours": float(info["respawn_time"].total_seconds() / 3600) if info.get(
                            "respawn_time") else 0.0,
                        "killed_by": info.get("killed_by"),
                        "schedule": info.get("schedule", []),
                        "is_scheduled": info.get("is_scheduled", False),
                        "is_daily": info.get("is_daily", False)
                    }

                # Create a JSON file in memory with the exact formatting
                json_data = json.dumps(data, indent=4, ensure_ascii=False)
                file = io.BytesIO(json_data.encode('utf-8'))
                file.seek(0)

                # Get current timestamp for filename
                timestamp = datetime.now(sg_timezone).strftime("%Y%m%d_%H%M%S")
                filename = f"bosses_export_{timestamp}.json"

                # Send the file
                await ctx.send(
                    f"✅ Boss data exported successfully!\n"
                    f"**Total bosses:** {len(bosses)}\n"
                    f"**Export time:** {datetime.now(sg_timezone).strftime('%m-%d-%Y %I:%M %p')}",
                    file=discord.File(fp=file, filename=filename)
                )

                print(f"Boss data exported by {ctx.author} at {timestamp}")

            except Exception as e:
                await ctx.send(f"❌ Error exporting boss data: {str(e)}")
                print(f"Error in boss_export_json: {e}")

        # Make sure boss_daily command is properly defined (add this with other commands)
        @bot.command(name="boss_daily")
        async def boss_daily(ctx):
            """Show all bosses respawning today"""
            now = datetime.now(sg_timezone)
            today_bosses = []

            # Find all bosses respawning today
            for boss_name, info in bosses.items():
                is_scheduled = info.get("is_scheduled", False)

                if is_scheduled:
                    # For scheduled bosses, check if they spawn today
                    spawn_time = info.get("spawn_time")
                    if spawn_time and spawn_time.date() == now.date():
                        # Format schedule for display
                        schedule = info.get("schedule", [])
                        schedule_text = ""
                        for day, time_str in schedule:
                            schedule_text += f"{day} {time_str}, "
                        schedule_text = schedule_text.rstrip(", ")

                        today_bosses.append({
                            "name": boss_name,
                            "spawn_time": spawn_time,
                            "type": "Scheduled",
                            "schedule_text": schedule_text,
                            "is_scheduled": True
                        })
                else:
                    # For regular bosses, check if they respawn today
                    death_time = info.get("death_time")
                    respawn_time = info.get("respawn_time", timedelta())

                    if death_time:
                        respawn_at = death_time + respawn_time
                        if respawn_at.date() == now.date():
                            killed_by = f"<@{info.get('killed_by')}>" if info.get('killed_by') else "N/A"
                            today_bosses.append({
                                "name": boss_name,
                                "spawn_time": respawn_at,
                                "type": "Regular",
                                "killed_by": killed_by,
                                "is_scheduled": False
                            })

            # Sort bosses by spawn time (earliest first)
            today_bosses.sort(key=lambda x: x["spawn_time"])

            # Create announcement embed
            if today_bosses:
                embed = discord.Embed(
                    title="📅 Today's Boss Respawns",
                    description=f"**__BOSSES RESPAWNING TODAY ({now.strftime('%A, %B %d, %Y')})__**\n\n"
                                f"_Timezone: Asia/Singapore_\n"
                                f"Current time: {now.strftime('%I:%M %p')}",
                    color=discord.Color.gold()
                )

                # Group by time for better organization
                time_groups = {}
                for boss in today_bosses:
                    time_str = boss["spawn_time"].strftime("%I:%M %p")
                    if time_str not in time_groups:
                        time_groups[time_str] = []
                    time_groups[time_str].append(boss)

                # Add fields grouped by time
                for time_str in sorted(time_groups.keys()):
                    bosses_list = time_groups[time_str]
                    boss_descriptions = []

                    for boss in bosses_list:
                        if boss["is_scheduled"]:
                            boss_descriptions.append(f"• **{boss['name']}** (Scheduled)")
                        else:
                            boss_descriptions.append(f"• **{boss['name']}** (Regular - Marked by {boss['killed_by']})")

                    embed.add_field(
                        name=f"🕐 {time_str}",
                        value="\n".join(boss_descriptions),
                        inline=False
                    )

                # Add summary
                total_bosses = len(today_bosses)
                scheduled_count = len([b for b in today_bosses if b["is_scheduled"]])
                regular_count = len([b for b in today_bosses if not b["is_scheduled"]])

                embed.add_field(
                    name="📊 Summary",
                    value=f"**Total bosses today:** {total_bosses}\n"
                          f"**Scheduled bosses:** {scheduled_count}\n"
                          f"**Regular bosses:** {regular_count}",
                    inline=False
                )

                await ctx.send(embed=embed)

                # Optional: Also send as a clean list for quick reading
                if total_bosses > 0:
                    quick_list = "**Quick List - Today's Bosses:**\n"
                    for time_str in sorted(time_groups.keys()):
                        boss_names = [boss['name'] for boss in time_groups[time_str]]
                        quick_list += f"**{time_str}:** {', '.join(boss_names)}\n"

                    await ctx.send(quick_list)

            else:
                embed = discord.Embed(
                    title="📅 Today's Boss Respawns",
                    description=f"**No bosses respawning today ({now.strftime('%A, %B %d, %Y')})**\n\n"
                                f"Check back tomorrow or use `/boss_status` for upcoming spawns.",
                    color=discord.Color.blue()
                )
                await ctx.send(embed=embed)

@bot.command(name="boss_weekly_stats")
async def boss_weekly_stats(ctx):
    """
    Show how many times each boss spawned this week.
    Splits results into multiple embeds if over 25 bosses.
    """
    now = datetime.now(sg_timezone)
    start_of_week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)

    kill_log = load_kill_log()
    boss_counts = {}
    total_kills = 0

    # Unscheduled bosses (actual kills)
    for boss_name, info in bosses.items():
        is_scheduled = info.get("is_scheduled", False)
        if not is_scheduled:
            count = 0
            for t in kill_log.get(boss_name, []):
                try:
                    dt = datetime.fromisoformat(t)
                    if dt.tzinfo is None:
                        dt = sg_timezone.localize(dt)
                except Exception:
                    continue
                if dt >= start_of_week:
                    count += 1
            boss_counts[boss_name] = count
            total_kills += count

    # Scheduled bosses (predicted spawns)
    for boss_name, info in bosses.items():
        if info.get("is_scheduled", False):
            schedule = info.get("schedule", [])
            is_daily = info.get("is_daily", False)
            count = len(schedule) * 7 if is_daily else len(schedule)
            boss_counts[boss_name] = count
            total_kills += count

    # Sort by spawn count descending
    sorted_bosses = sorted(boss_counts.items(), key=lambda x: x[1], reverse=True)

    # Split into chunks of 25
    chunk_size = 25
    chunks = [sorted_bosses[i:i + chunk_size] for i in range(0, len(sorted_bosses), chunk_size)]

    for i, chunk in enumerate(chunks, start=1):
        embed = discord.Embed(
            title=f"📊 Weekly Boss Spawn Summary — Page {i}/{len(chunks)}",
            description=(
                f"**__Boss Spawns This Week__**\n"
                f"Week starting {start_of_week.strftime('%B %d, %Y')}\n"
                f"Timezone: Asia/Singapore"
            ),
            color=discord.Color.purple(),
            timestamp=now
        )

        for name, count in chunk:
            if count > 0:
                embed.add_field(name=f"⚔️ {name}", value=f"{count} spawns this week", inline=False)

        if i == len(chunks):
            embed.add_field(name="📅 Total Boss Spawns", value=f"**{total_kills}**", inline=False)

        embed.set_footer(text="Includes actual kills for regular bosses and scheduled spawns for weekly/daily bosses.")
        await ctx.send(embed=embed)


@bot.command(name="boss_today")
async def boss_today(ctx):
    """Show all bosses respawning today, split into multiple embeds if needed."""
    now = datetime.now(sg_timezone)
    today_bosses = []

    # Find all bosses respawning today
    for boss_name, info in bosses.items():
        is_scheduled = info.get("is_scheduled", False)

        if is_scheduled:
            # Scheduled bosses that spawn today
            spawn_time = info.get("spawn_time")
            if spawn_time and spawn_time.date() == now.date():
                schedule = info.get("schedule", [])
                schedule_text = ", ".join([f"{day} {time}" for day, time in schedule])
                today_bosses.append({
                    "name": boss_name,
                    "spawn_time": spawn_time,
                    "type": "Scheduled",
                    "schedule_text": schedule_text
                })
        else:
            # Regular bosses that respawn today
            death_time = info.get("death_time")
            respawn_time = info.get("respawn_time", timedelta())
            if death_time:
                respawn_at = death_time + respawn_time
                if respawn_at.date() == now.date():
                    killed_by = f"<@{info.get('killed_by')}>" if info.get("killed_by") else "N/A"
                    today_bosses.append({
                        "name": boss_name,
                        "spawn_time": respawn_at,
                        "type": "Regular",
                        "killed_by": killed_by
                    })

    if not today_bosses:
        await ctx.send("📅 No bosses respawning today.")
        return

    # Sort bosses by spawn time
    today_bosses.sort(key=lambda x: x["spawn_time"])

    # Build description lines
    lines = []
    for boss in today_bosses:
        time_str = boss["spawn_time"].strftime("%I:%M %p")
        if boss["type"] == "Scheduled":
            lines.append(f"⚔️ **{boss['name']}** (Scheduled)\n🕓 {time_str}\n📅 {boss['schedule_text']}\n")
        else:
            lines.append(f"⚔️ **{boss['name']}** (Regular)\n🕓 {time_str}\n💀 Killed by: {boss.get('killed_by', 'N/A')}\n")

    # --- Split into multiple embeds if content is too long ---
    embeds = []
    desc = ""
    for line in lines:
        if len(desc) + len(line) > 3900:  # safety margin before 4096 limit
            embed = discord.Embed(
                title=f"📅 Today's Boss Respawns ({len(embeds) + 1})",
                description=desc,
                color=discord.Color.green()
            )
            embeds.append(embed)
            desc = ""  # start a new page
        desc += line + "\n"

    # Add last page
    if desc:
        embed = discord.Embed(
            title=f"📅 Today's Boss Respawns ({len(embeds) + 1})",
            description=desc,
            color=discord.Color.green()
        )
        embeds.append(embed)

    # Send all embeds
    for i, embed in enumerate(embeds):
        embed.set_footer(text=f"Page {i + 1}/{len(embeds)} • Timezone: Asia/Singapore")
        await ctx.send(embed=embed)



@bot.command(name="boss_reset_after_maintenance")
@commands.has_permissions(administrator=True)
async def boss_reset_after_maintenance(ctx):
    """
    Resets all unscheduled (regular) bosses after maintenance.
    Sends a single summary message with a button to re-post TOD buttons.
    """
    now = datetime.now(sg_timezone)
    reset_count = 0
    chat_channel = bot.get_channel(CHANNEL_ID)  # Make sure this points to your boss channel
    reset_bosses = []

    # Reset boss data
    for boss_name, info in bosses.items():
        is_scheduled = info.get("is_scheduled", False)
        if not is_scheduled:
            info["death_time"] = None
            info["killed_by"] = None
            info["spawn_time"] = now
            reset_bosses.append(boss_name)
            reset_count += 1

    await save_bosses()

    # Optional: clear weekly kill log
    if os.path.exists(KILL_LOG_FILE):
        with open(KILL_LOG_FILE, "w") as f:
            f.write("{}")

    # ✅ Create a single embed summarizing the reset
    boss_list_str = "\n".join([f"• **{b}**" for b in reset_bosses]) or "_No unscheduled bosses found._"

    embed = discord.Embed(
        title="🛠️ Boss Reset After Maintenance",
        description=(
            f"All **{reset_count} unscheduled bosses** have been reset.\n"
            f"They are now considered **spawned** and can be marked again after kills.\n\n"
            f"**Respawned Bosses:**\n{boss_list_str}\n\n"
            f"Click the button below to post TOD buttons for all bosses."
        ),
        color=discord.Color.orange(),
        timestamp=now
    )
    embed.set_footer(text=f"Triggered by {ctx.author.display_name}")

    # 🧩 Create button for generating TOD messages
    view = View(timeout=None)

    async def generate_tod_buttons(interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("🚫 Only admins can use this.", ephemeral=True)
            return

        await interaction.response.send_message("🕒 Posting TOD buttons...", ephemeral=True)

        for boss_name in reset_bosses:
            view_inner = View(timeout=None)
            tod_button = Button(label=f"Time of Death {boss_name}", style=discord.ButtonStyle.danger)

            async def button_callback(interaction, b_name=boss_name, ch=chat_channel):
                now_click = datetime.now(sg_timezone)
                bosses[b_name]["death_time"] = now_click
                bosses[b_name]["killed_by"] = interaction.user.id
                await save_bosses()

                # Record in kill log
                kill_log = load_kill_log()
                if b_name not in kill_log:
                    kill_log[b_name] = []
                kill_log[b_name].append(now_click.isoformat())
                save_kill_log(kill_log)

                respawn_at_new = now_click + bosses[b_name]["respawn_time"]
                await ch.send(
                    f"⚔️ Boss '{b_name}' marked dead by {interaction.user.mention} at "
                    f"{now_click.strftime('%m-%d-%Y %I:%M %p')}.\n"
                    f"Respawns at: {respawn_at_new.strftime('%m-%d-%Y %I:%M %p')}"
                )
                try:
                    await interaction.response.send_message("✅ Time of Death recorded.", ephemeral=True)
                except:
                    pass

                # Clear reminders and refresh embeds
                reminder_sent.discard((b_name, "1h"))
                reminder_sent.discard((b_name, "15m"))
                reminder_sent.discard((b_name, "5m"))
                reminder_sent.discard((b_name, "respawn"))

                try:
                    await interaction.message.edit(view=None)
                except:
                    pass

                await refresh_status_message()

            tod_button.callback = button_callback
            view_inner.add_item(tod_button)

            # Send respawn message for this boss
            await chat_channel.send(
                f"✅ **ATTENTION!** @everyone __**{boss_name}**__ has respawned! Time to hunt!",
                view=view_inner
            )

        await refresh_status_message()

    generate_button = Button(label="Generate TOD Buttons", style=discord.ButtonStyle.success)
    generate_button.callback = generate_tod_buttons
    view.add_item(generate_button)

    # Send summary + generate button
    await ctx.send(embed=embed, view=view)



@bot.command(name="help")
async def help(ctx):
    """
    Shows a list of all available bot commands and their usage.
    """
    embed = discord.Embed(
        title="📘 Celestial Boss Bot — Help Menu",
        description="Here are all available commands and what they do:",
        color=discord.Color.blue()
    )

    # ⚙️ General Commands
    embed.add_field(
        name="⚙️ General Commands",
        value=(
            "`!help` — Show this help menu.\n"
            "`!boss_weekly_stats` — Show how many bosses spawned this week.\n"
            "`!boss_reset_after_maintenance` — Reset all unscheduled bosses after server maintenance.\n"
        ),
        inline=False
    )

    # 🪓 Boss Management Commands
    embed.add_field(
        name="🪓 Boss Tracking Commands",
        value=(
            "`!boss_add <name> <respawn_hours>` — Add a new regular boss.\n"
            "`!boss_delete <name>` — Remove a boss from tracking.\n"
            "`!boss_tod_edit <name>` — Manually set or edit a boss's time of death.\n"
            "`Example:` — boss_tod_edit Amentis 10-05-2025 01:15 PM\n"

        ),
        inline=False
    )

    # ⏰ Status and Notifications
    embed.add_field(
        name="⏰ Status & Notifications",
        value=(
            "`!boss_status` — Force-refresh the boss status embed manually.\n"
        ),
        inline=False
    )

    # 🗓️ Scheduled Bosses
    embed.add_field(
        name="🗓️ Scheduled Boss Commands",
        value=(
            "`!boss_add_schedule <name> <day/time>` — Add a weekly or daily scheduled boss.\n"
            "`Example`: !boss_add_schedule Dragon Monday 2:30PM Wednesday 7:45PM\n"
        ),
        inline=False
    )

    # 🧠 Note / Footer
    embed.set_footer(
        text="Use commands without <>. Example: !boss_tod_edit Venatus"
    )

    await ctx.send(embed=embed)



# We need to modify the boss_respawn_notifications function to properly handle respawn alerts
# --- BOSS RESPAWN NOTIFICATIONS (NO AUTO REFRESH) ---
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

                    # ✅ Parse 12-hour format time BEFORE using hour/minute
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

                    days_ahead = day_idx - current_day_idx
                    if days_ahead < 0 or (days_ahead == 0 and now.time() > time(hour, minute)):
                        # Target day already happened this week or time already passed today
                        days_ahead += 7

                    target_date = now + timedelta(days=days_ahead)

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

            # 1-hour warning (3600 seconds)
            if 0 < seconds_until_respawn <= 3600 and (boss_name, "1h") not in reminder_sent:
                # Send warning exactly at 1 hour before
                if seconds_until_respawn <= 3600 and seconds_until_respawn > 3595:
                    await chat_channel.send(f"⏰**REMINDER:** @everyone **{boss_name}** will respawn in **1 hour**!")
                    reminder_sent.add((boss_name, "1h"))

            # 15-minute warning (900 seconds)
            if 0 < seconds_until_respawn <= 900 and (boss_name, "15m") not in reminder_sent:
                # Send warning exactly at 15 minutes before
                if seconds_until_respawn <= 900 and seconds_until_respawn > 895:
                    await chat_channel.send(f"⏰**REMINDER:** @everyone **{boss_name}** will respawn in **15 minutes**!")
                    reminder_sent.add((boss_name, "15m"))

            # 5-minute warning (300 seconds)
            if 0 < seconds_until_respawn <= 300 and (boss_name, "5m") not in reminder_sent:
                # Send warning exactly at 5 minutes before
                if seconds_until_respawn <= 300 and seconds_until_respawn > 295:
                    await chat_channel.send(f"⏰**REMINDER:** @everyone **{boss_name}** will respawn in **5 minutes**!")
                    reminder_sent.add((boss_name, "5m"))

            # Respawn alert - check if current time is equal to or past the spawn time
            if now >= current_spawn_time and (boss_name, "respawn") not in reminder_sent:
                await chat_channel.send(f"✅**ATTENTION!** @everyone __**{boss_name}**__ has respawned! Time to hunt!")
                # Clear all reminders for this boss when it respawns
                reminder_sent.discard((boss_name, "1h"))
                reminder_sent.discard((boss_name, "15m"))
                reminder_sent.discard((boss_name, "5m"))
                reminder_sent.add((boss_name, "respawn"))

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

            # 1-hour warning (3600 seconds)
            if 0 < seconds_until_respawn <= 3600 and (boss_name, "1h") not in reminder_sent:
                # Send warning exactly at 1 hour before
                if seconds_until_respawn <= 3600 and seconds_until_respawn > 3595:
                    await chat_channel.send(f"⏰**REMINDER:** @everyone **{boss_name}** will respawn in **1 hour**!")
                    reminder_sent.add((boss_name, "1h"))

            # 15-minute warning (900 seconds)
            if 0 < seconds_until_respawn <= 900 and (boss_name, "15m") not in reminder_sent:
                # Send warning exactly at 15 minutes before
                if seconds_until_respawn <= 900 and seconds_until_respawn > 895:
                    await chat_channel.send(f"⏰**REMINDER:** @everyone **{boss_name}** will respawn in **15 minutes**!")
                    reminder_sent.add((boss_name, "15m"))

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

                    # Log this kill for weekly statistics (use fresh current time)
                    kill_log = load_kill_log()
                    if b_name not in kill_log:
                        kill_log[b_name] = []

                    kill_log[b_name].append(now_click.isoformat())  # ✅ correct timestamp
                    save_kill_log(kill_log)

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

                    # Clear all reminders for this boss when TOD is recorded
                    reminder_sent.discard((b_name, "1h"))
                    reminder_sent.discard((b_name, "15m"))
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
                # Clear all reminders for this boss when it respawns
                reminder_sent.discard((boss_name, "1h"))
                reminder_sent.discard((boss_name, "15m"))
                reminder_sent.discard((boss_name, "5m"))
                reminder_sent.add((boss_name, "respawn"))


# --- AUTO DAILY ANNOUNCEMENT TASK ---
@tasks.loop(minutes=1)  # Check every minute
async def daily_announcement():
    """Automatically post daily boss respawn announcement at 12:00 AM and 8:00 AM"""
    now = datetime.now(sg_timezone)

    # Run at 12:00 AM (midnight) and 8:00 AM Singapore time
    if (now.hour == 0 and now.minute == 0) or (now.hour == 8 and now.minute == 0):
        channel = bot.get_channel(CHANNEL_ID)
        if not channel:
            return

        # Get today's bosses (same logic as boss_daily command)
        today_bosses = []
        for boss_name, info in bosses.items():
            is_scheduled = info.get("is_scheduled", False)

            if is_scheduled:
                spawn_time = info.get("spawn_time")
                if spawn_time and spawn_time.date() == now.date():
                    today_bosses.append({
                        "name": boss_name,
                        "spawn_time": spawn_time,
                        "type": "Scheduled",
                        "is_scheduled": True
                    })
            else:
                death_time = info.get("death_time")
                respawn_time = info.get("respawn_time", timedelta())

                if death_time:
                    respawn_at = death_time + respawn_time
                    if respawn_at.date() == now.date():
                        today_bosses.append({
                            "name": boss_name,
                            "spawn_time": respawn_at,
                            "type": "Regular",
                            "is_scheduled": False
                        })

        # Sort by time
        today_bosses.sort(key=lambda x: x["spawn_time"])

        # Different messages for midnight vs morning
        if now.hour == 0:  # Midnight announcement
            if today_bosses:
                embed = discord.Embed(
                    title="🌙 Midnight Boss Respawn Preview",
                    description=f"**@everyone\n__BOSSES RESPAWNING TODAY ({now.strftime('%A, %B %d, %Y')})__**\n\n"
                                f"Good evening! Here's a preview of bosses respawning today:",
                    color=discord.Color.dark_blue()
                )

                # Group by time
                time_groups = {}
                for boss in today_bosses:
                    time_str = boss["spawn_time"].strftime("%I:%M %p")
                    if time_str not in time_groups:
                        time_groups[time_str] = []
                    time_groups[time_str].append(boss)

                # Add time groups to embed
                for time_str in sorted(time_groups.keys()):
                    boss_names = [boss['name'] for boss in time_groups[time_str]]
                    embed.add_field(
                        name=f"🕐 {time_str}",
                        value=", ".join(boss_names),
                        inline=False
                    )

                embed.add_field(
                    name="💤 Late Night Hunters",
                    value="• Some bosses may spawn late tonight\n"
                          "• Set your alarms for important spawns\n"
                          "• Reminders will be sent automatically",
                    inline=False
                )

                await channel.send(embed=embed)

                # Midnight ping message
                total_bosses = len(today_bosses)
                if total_bosses > 0:
                    await channel.send(
                        f"@everyone **{total_bosses} boss(es) respawning today!** "
                        f"Happy hunting! 🌙"
                    )
            else:
                # No bosses at midnight
                embed = discord.Embed(
                    title="🌙 Midnight Boss Respawn Preview",
                    description=f"**No bosses respawning today ({now.strftime('%A, %B %d, %Y')})**\n\n"
                                f"Enjoy a peaceful night! Check back in the morning for updates.",
                    color=discord.Color.dark_blue()
                )
                await channel.send(embed=embed)

        else:  # 8:00 AM announcement
            if today_bosses:
                embed = discord.Embed(
                    title="🌅 Morning Boss Respawn Announcement",
                    description=f"**@everyone\n__BOSSES RESPAWNING TODAY ({now.strftime('%A, %B %d, %Y')})__**\n\n"
                                f"Good morning! Here are the bosses respawning today:",
                    color=discord.Color.gold()
                )

                # Group by time
                time_groups = {}
                for boss in today_bosses:
                    time_str = boss["spawn_time"].strftime("%I:%M %p")
                    if time_str not in time_groups:
                        time_groups[time_str] = []
                    time_groups[time_str].append(boss)

                # Add time groups to embed
                for time_str in sorted(time_groups.keys()):
                    boss_names = [boss['name'] for boss in time_groups[time_str]]
                    embed.add_field(
                        name=f"🕐 {time_str}",
                        value=", ".join(boss_names),
                        inline=False
                    )

                embed.add_field(
                    name="💡 Today's Reminders",
                    value="• 1-hour, 15-minute, and 5-minute reminders will be sent automatically\n"
                          "• Use `/boss_status` for full schedule\n",
                    inline=False
                )

                await channel.send(embed=embed)

                # Morning ping message
                total_bosses = len(today_bosses)
                if total_bosses > 0:
                    await channel.send(
                        f"@everyone **{total_bosses} boss(es) respawning today!** "
                        f"Get ready for some hunting! 🎯"
                    )
            else:
                # No bosses in the morning
                embed = discord.Embed(
                    title="🌅 Morning Boss Respawn Announcement",
                    description=f"**No bosses respawning today ({now.strftime('%A, %B %d, %Y')})**\n\n"
                                f"It's a quiet day! Perfect for other activities or preparing for tomorrow.",
                    color=discord.Color.blue()
                )
                await channel.send(embed=embed)

# --- ON READY ---
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    # Register persistent views from merged script.py features
    try:
        bot.add_view(AttendanceView())
    except Exception:
        pass
    try:
        bot.add_view(PointsPanel())
    except Exception:
        pass
    try:
        bot.add_view(BidPanel())
    except Exception:
        pass
    load_bosses()
    # Only start the notification task, not auto-refresh
    boss_respawn_notifications.start()
    # Start the daily announcement task
    daily_announcement.start()

# --- RUN ---
if TOKEN is None:
    raise ValueError("❌ DISCORD_TOKEN environment variable not set!")
else:
    bot.run(TOKEN)
