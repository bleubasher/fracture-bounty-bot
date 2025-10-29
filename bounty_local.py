from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

print("🔥 Bounty bot starting up")

import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime
import asyncio
import json

# Load config from environment variables
OWNER_ID = int(os.getenv("OWNER_ID", "217324902447841282"))
DATA_FILE = os.getenv("DATA_FILE", "bounties.json")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("TEST_GUILD_ID")

# Sanity check
print(f"Loaded OWNER_ID: {OWNER_ID}")
print(f"Loaded GUILD_ID: {GUILD_ID}")
print(f"Loaded DATA_FILE: {DATA_FILE}")
print(f"Discord Token present: {bool(DISCORD_TOKEN)}")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

bounties = []
authorized_users = []


def load_data():
    global bounties, authorized_users
    if not os.path.exists(DATA_FILE):
        bounties = []
        authorized_users = [OWNER_ID]
    else:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            bounties = data.get("bounties", [])
            authorized_users = data.get("authorized_users", [])
            if OWNER_ID not in authorized_users:
                authorized_users.append(OWNER_ID)


def save_data():
    print(f"Saving data to {DATA_FILE}...")
    print(json.dumps({
        "bounties": bounties,
        "authorized_users": authorized_users
    }, indent=4))
    with open(DATA_FILE, "w") as f:
        json.dump({
            "bounties": bounties,
            "authorized_users": authorized_users
        }, f, indent=4)


load_data()

def is_authorized(user_id):
    return user_id in authorized_users

def is_owner(user_id):
    return user_id == OWNER_ID

print("Available env vars:", os.environ)
print(">>> Checking for __CHANNEL_SET__ bounty")
print(f"Current bounties: {bounties}")
print(f"OWNER_ID (from env) = {OWNER_ID}")
print(f"AUTHORIZED_USERS at load = {authorized_users}")

scheduled_tasks = []

def schedule_bounty(message, post_time, channel_id):
    async def send_later():
        now = datetime.utcnow()
        target = datetime.strptime(post_time, "%Y-%m-%d %H:%M")
        delay = (target - now).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)

        channel = bot.get_channel(channel_id)
        if channel:
            await channel.send(message)

        for bounty in bounties:
            if (
                bounty["message"] == message and
                bounty["post_time"] == post_time and
                bounty["channel_id"] == channel_id and
                not bounty.get("sent")
            ):
                bounty["sent"] = True
                break

        save_data()

    task = asyncio.create_task(send_later())
    scheduled_tasks.append(task)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    for bounty in bounties:
        if bounty["message"] != "__CHANNEL_SET__" and not bounty.get("sent"):
            schedule_bounty(bounty["message"], bounty["post_time"], bounty["channel_id"])


@tree.command(name="bounty", description="Show bounty bot command help")
async def bounty(interaction: discord.Interaction):
    is_auth = is_authorized(interaction.user.id)
    header = "**Depth's Bounty Bot for The Chase**\n"
    status = (
        "You **ARE authorized** to make changes to this bot."
        if is_auth else
        "You are **NOT authorized** to make changes to this bot."
    )

    help_text = (
        f"{header}\n{status}\n\n"
        "**Available Commands:**\n"
        "`/bounty_new <message> <time>` — Schedule a bounty message for a future UTC time.\n"
        "`/bounty_now <message>` — Send a bounty message immediately.\n"
        "`/bounty_list` — View all scheduled bounties.\n"
        "`/bounty_channel` — Set the channel where bounty messages will be sent.\n"
        "`/bounty_auth <user>` — Authorize another user to manage bounties. (Owner only)\n"
        "`/bounty_deauth <user>` — Remove a user's authorization. (Owner only)\n"
        "`/bounty_who` — View all currently authorized users.\n"
        "`/bounty_time` — View the current UTC time.\n"
    )

    await interaction.response.send_message(help_text, ephemeral=True)


@tree.command(name="bounty_time", description="Get the current UTC time")
async def bounty_time(interaction: discord.Interaction):
    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    await interaction.response.send_message(f"Current UTC time: {now_utc}", ephemeral=True)


@bot.event
async def setup_hook():
    TEST_GUILD_ID = GUILD_ID
    if TEST_GUILD_ID:
        guild = discord.Object(id=int(TEST_GUILD_ID))
        await tree.sync(guild=guild)
        print(f"Synced commands to test guild: {TEST_GUILD_ID}")
    else:
        await tree.sync()
        print("Synced commands globally (may take up to 1 hour)")


bot.setup_hook = setup_hook
bot.run(DISCORD_TOKEN)
