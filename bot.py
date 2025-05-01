import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Select, Modal, TextInput
from datetime import datetime, timedelta
import pytz
import json
import asyncio
import os

import os
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_CONFIG_FILE = 'channels.json'
DATA_FILE = 'reminders.json'
server_channels = {}

data_lock = asyncio.Lock()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

user_reminders = {}

def load_reminders():
    global user_reminders
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            try:
                raw_data = json.load(f)
                for uid, entry in raw_data.items():
                    if 'next_reminder' in entry:
                        entry['next_reminder'] = datetime.fromisoformat(entry['next_reminder'])
                user_reminders = {int(k): v for k, v in raw_data.items()}
            except json.JSONDecodeError:
                print("Failed to load reminders. JSON corrupted.")

async def save_reminders():
    async with data_lock:
        with open(DATA_FILE, 'w') as f:
            json.dump({str(k): {'next_reminder': v['next_reminder'].isoformat(), 'timezone': v['timezone']} for k, v in user_reminders.items() if 'next_reminder' in v and 'timezone' in v}, f, indent=2)

@tasks.loop(minutes=15)
async def reminder_loop():
    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    for user_id, data in list(user_reminders.items()):
        if 'next_reminder' not in data or 'timezone' not in data:
            continue

        next_time = data['next_reminder']
        if now_utc >= next_time:
            user = await bot.fetch_user(user_id)
            if not user:
                continue

            try:
                await user.send("🕐 Your 7-day item is expiring soon! Don't forget to renew it!")
                user_reminders.pop(user_id)
                await save_reminders()
            except discord.Forbidden:
                print(f"Cannot send DM to user {user_id}")

@bot.command()
async def cancel(ctx):
    user_id = ctx.author.id
    if user_id in user_reminders:
        user_reminders.pop(user_id)
        await save_reminders()
        await ctx.send("❌ Your reminder has been cancelled.")
    else:
        await ctx.send("ℹ️ You don't have an active reminder.")

@bot.command()
async def status(ctx):
    user_id = ctx.author.id
    if user_id in user_reminders:
        tz = pytz.timezone(user_reminders[user_id]['timezone'])
        local_reminder_time = user_reminders[user_id]['next_reminder'].astimezone(tz)
        await ctx.reply(f"⏳ Your reminder is set for: {local_reminder_time.strftime('%Y-%m-%d %I:%M %p')} your time zone ({tz.zone})")
    else:
        await ctx.reply("ℹ️ You don't have an active reminder.")

@bot.command()
async def list_reminders(ctx):
    if not user_reminders:
        await ctx.reply("📭 No active reminders.")
        return
    result = "📋 Active Reminders:\n"
    for uid, entry in user_reminders.items():
        user = await bot.fetch_user(uid)
        tz = pytz.timezone(entry['timezone'])
        time_str = entry['next_reminder'].astimezone(tz).strftime('%Y-%m-%d %I:%M %p')
        result += f"- {user.name}: {time_str} ({entry['timezone']})\n"
    await ctx.reply(result)

@bot.command()
async def register_channel(ctx):
    if not ctx.guild:
        await ctx.send("❌ This command must be run in a server channel.")
        return
    if not os.path.exists(CHANNEL_CONFIG_FILE):
        with open(CHANNEL_CONFIG_FILE, 'w') as f:
            json.dump({}, f)
    with open(CHANNEL_CONFIG_FILE, 'r') as f:
        config = json.load(f)
    config[str(ctx.guild.id)] = {
        'channel_id': ctx.channel.id,
        'admin_id': ctx.author.id
    }
    with open(CHANNEL_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)
    await ctx.send(f"✅ This channel has been registered as the setup channel for this server. You are now the bot admin for this server.")

@bot.command()
async def test_reminder(ctx):
    if not ctx.guild or not os.path.exists(CHANNEL_CONFIG_FILE):
        await ctx.send("⚠️ Cannot verify admin privileges.")
        return
    with open(CHANNEL_CONFIG_FILE, 'r') as f:
        config = json.load(f)
    server_cfg = config.get(str(ctx.guild.id))
    if not server_cfg or ctx.author.id != server_cfg['admin_id']:
        await ctx.send("⛔ You are not authorized to use this command.")
        return
    user_id = ctx.author.id
    if user_id not in user_reminders:
        await ctx.reply("⚠️ You don't have a reminder set.")
        return
    user = await bot.fetch_user(user_id)
    try:
        await user.send("🧪 Test reminder: Your 7-day item is expiring soon! Don't forget to renew it!")
        await ctx.message.delete()
    except discord.Forbidden:
        await ctx.reply("❌ Failed to send DM. You may have DMs disabled.")

@bot.event
async def on_ready():
    load_reminders()
    print(f'Logged in as {bot.user}')
    reminder_loop.start()

@bot.event
async def on_message(message):
    if message.guild and os.path.exists(CHANNEL_CONFIG_FILE):
        with open(CHANNEL_CONFIG_FILE, 'r') as f:
            config = json.load(f)
        server_cfg = config.get(str(message.guild.id))
        if server_cfg and message.channel.id == server_cfg['channel_id'] and message.author != bot.user:
            await message.channel.purge()

        info_embed = discord.Embed(
            title="ℹ️ Reminder Bot Setup Info",
            description="✅ To receive reminders, make sure:\n"
                        "- Your **DMs are open** (enable 'Allow direct messages from server members' in Privacy Settings)\n"
                        "- You’ve configured your **timezone and item duration** via the setup channel\n"
                        "- You haven’t left the server\n\n"
                        "🔕 If DMs are off or you block the bot, you won't receive reminder messages.",
            color=0x3498db
        )

        embed = discord.Embed(
            title="⏰ Weekly Reminder Setup",
            description="This bot helps you track your 7-day item renewal. Click below to configure your timezone and current item duration.",
            color=0x00ff99
        )

        await message.channel.send(embed=info_embed)
        await message.channel.send(embed=embed, view=ConfigButtonView())

    await bot.process_commands(message)

class ConfigButtonView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ConfigButton())

class ConfigButton(Button):
    def __init__(self):
        super().__init__(label="Configure Reminder", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(view=TimezoneSelector(interaction.user.id), ephemeral=True)

class TimezoneSelector(View):
    def __init__(self, user_id):
        super().__init__(timeout=60)
        self.add_item(TimezoneDropdown(user_id))

class TimezoneDropdown(Select):
    def __init__(self, user_id):
        self.user_id = user_id
        options = [
            discord.SelectOption(label="Central Europe (Europe/Stockholm)", value="Europe/Stockholm"),
            discord.SelectOption(label="USA - Eastern (America/New_York)", value="America/New_York"),
            discord.SelectOption(label="USA - Pacific (America/Los_Angeles)", value="America/Los_Angeles"),
            discord.SelectOption(label="India (Asia/Kolkata)", value="Asia/Kolkata"),
            discord.SelectOption(label="Australia (Australia/Sydney)", value="Australia/Sydney")
        ]
        super().__init__(placeholder="Select your timezone...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        tz = self.values[0]
        user_id = self.user_id
        if user_id not in user_reminders:
            user_reminders[user_id] = {}
        user_reminders[user_id]['timezone'] = tz
        await save_reminders()
        await interaction.response.send_modal(DurationInputModal(user_id, tz))

class DurationInputModal(Modal):
    def __init__(self, user_id, timezone):
        super().__init__(title="How much time is left on your 7-day item?")
        self.user_id = user_id
        self.timezone = timezone
        self.days_input = TextInput(label="Days left", placeholder="e.g. 5", required=True)
        self.hours_input = TextInput(label="Hours left", placeholder="e.g. 12", required=True)
        self.add_item(self.days_input)
        self.add_item(self.hours_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            days = int(self.days_input.value)
            hours = int(self.hours_input.value)
            now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
            user_tz = pytz.timezone(self.timezone)
            expiration_time = now_utc + timedelta(days=days, hours=hours)
            hour = expiration_time.astimezone(user_tz).hour
            if 0 <= hour < 12:
                remind_time = expiration_time - timedelta(days=1)
            else:
                remind_time = expiration_time - timedelta(hours=12)
            user_reminders[self.user_id] = {
                'next_reminder': remind_time,
                'timezone': self.timezone
            }
            await save_reminders()

            with open(CHANNEL_CONFIG_FILE, 'r') as f:
                config = json.load(f)
                guild_id = interaction.guild.id if interaction.guild else None
                channel = None
                if guild_id and str(guild_id) in config:
                    chan_id = config[str(guild_id)]['channel_id']
                    channel = discord.utils.get(bot.get_all_channels(), id=chan_id)
            async for msg in channel.history(limit=100):
                if msg.author == interaction.user:
                    try:
                        await msg.delete()
                    except:
                        pass

            await interaction.response.send_message(f"✅ Reminder set for {remind_time.astimezone(user_tz).strftime('%Y-%m-%d %I:%M %p')} your time.", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❗ Invalid input. Please enter numbers for days and hours.", ephemeral=True)

bot.run(TOKEN)
