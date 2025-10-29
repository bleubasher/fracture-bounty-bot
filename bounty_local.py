from dotenv import load_dotenv
load_dotenv()

print("🔥 Bounty bot starting up")

import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime
import asyncio
import json
import os


# Load config from environment variables
OWNER_ID = int(os.getenv("OWNER_ID", "217324902447841282"))
DATA_FILE = os.getenv("DATA_FILE", "bounties.json")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

bounties = []
authorized_users = []





def load_data():
    global bounties, authorized_users
    if not os.path.exists(DATA_FILE):
        bounties = []
        authorized_users = [217324902447841282]
    else:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            bounties = data.get("bounties", [])
            authorized_users = data.get("authorized_users", [])
            if 217324902447841282 not in authorized_users:
                authorized_users.append(217324902447841282)
            


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

import os
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
        "`/bounty new <message> <time>` — Schedule a bounty message for a future UTC time.\n"
        "`/bounty now <message>` — Send a bounty message immediately.\n"
        "`/bounty list` — View all scheduled bounties.\n"
        "`/bounty channel` — Set the channel where bounty messages will be sent.\n"
        "`/bounty auth <user>` — Authorize another user to manage bounties. (Owner only)\n"
        "`/bounty deauth <user>` — Remove a user's authorization. (Owner only)\n"
        "`/bounty who` — View all currently authorized users.\n"
        "`/bounty time` — View the current UTC time.\n"
    )

    await interaction.response.send_message(help_text, ephemeral=True)

@tree.command(name="bounty_time", description="Get the current UTC time")
async def bounty_time(interaction: discord.Interaction):
    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    await interaction.response.send_message(f"Current UTC time: {now_utc}", ephemeral=True)

@tree.command(name="bounty_new", description="Schedule a new bounty message")
@app_commands.describe(message="The message to send", time="Time in UTC (YYYY-MM-DD HH:MM)")
async def bounty_new(interaction: discord.Interaction, message: str, time: str):
    print(">>> /bounty_new called")
    if not is_authorized(interaction.user.id):
        await interaction.response.send_message("Only Depth can setup bounties for The Chase.", ephemeral=True)
        return

    channel_id = None
    for bounty in reversed(bounties):
        if bounty["message"] == "__CHANNEL_SET__":
            channel_id = bounty["channel_id"]
            break

    if channel_id is None:
        await interaction.response.send_message("Please set a channel first using /bounty channel", ephemeral=True)
        return

    try:
        datetime.strptime(time, "%Y-%m-%d %H:%M")
    except ValueError:
        await interaction.response.send_message("Time must be in format YYYY-MM-DD HH:MM", ephemeral=True)
        return

    bounties.append({
        "message": message,
        "post_time": time,
        "channel_id": interaction.channel_id,
        "sent": False
    })
    save_data()
    schedule_bounty(message, time, channel_id)

    await interaction.response.send_message(f"Bounty scheduled for {time} UTC in <#{channel_id}>", ephemeral=True)

@tree.command(name="bounty_now", description="Send a bounty message immediately")
@app_commands.describe(message="The message to send immediately")
async def bounty_now(interaction: discord.Interaction, message: str):
    if not is_authorized(interaction.user.id):
        await interaction.response.send_message("Only Depth can setup bounties for The Chase.", ephemeral=True)
        return

    channel_id = None
    for bounty in reversed(bounties):
        if bounty["message"] == "__CHANNEL_SET__":
            channel_id = bounty["channel_id"]
            break

    if channel_id is None:
        await interaction.response.send_message("Please set a channel first using /bounty channel", ephemeral=True)
        return

    channel = bot.get_channel(channel_id)
    if channel:
        await channel.send(message)
        await interaction.response.send_message(f"Message sent immediately to <#{channel_id}>", ephemeral=True)
    else:
        await interaction.response.send_message("Failed to get the channel. Is the bot in that channel?", ephemeral=True)

@tree.command(name="bounty_list", description="List all scheduled bounties")
async def bounty_list(interaction: discord.Interaction):
    if not is_authorized(interaction.user.id):
        await interaction.response.send_message("Only Depth can setup bounties for The Chase.", ephemeral=True)
        return

    active_bounties = [b for b in bounties if b["message"] != "__CHANNEL_SET__"]
    if not active_bounties:
        await interaction.response.send_message("No bounties scheduled.", ephemeral=True)
        return

    msg = "\n".join([
        f"**{b['post_time']} UTC** - {b['message']}" + (" ✅" if b.get("sent") else "")
        for b in active_bounties
    ])
    await interaction.response.send_message(msg, ephemeral=True)

@tree.command(name="bounty_channel", description="Set the channel for bounties to post")
async def bounty_channel(interaction: discord.Interaction):
    # NOTE: Run this command from the channel you want bounties to post in.
    if not is_authorized(interaction.user.id):
        await interaction.response.send_message("Only Depth can setup bounties for The Chase.", ephemeral=True)
        return

    bounties.append({
        "message": "__CHANNEL_SET__",
        "post_time": "2099-12-31 23:59",
        "channel_id": channel.id
    })
    save_data()
    await interaction.response.send_message(f"Channel set to <#{interaction.channel_id}> for future bounties.", ephemeral=True)

@tree.command(name="bounty_auth", description="Authorize another user to use bounty commands")
@app_commands.describe(user="The user to authorize")
async def bounty_auth(interaction: discord.Interaction, user: discord.User):
    if not is_owner(interaction.user.id):
        await interaction.response.send_message("Only Depth can setup bounties for The Chase.", ephemeral=True)
        return

    if user.id not in authorized_users:
        authorized_users.append(user.id)
        save_data()
        await interaction.response.send_message(f"{user.mention} has been authorized.", ephemeral=True)
    else:
        await interaction.response.send_message(f"{user.mention} is already authorized.", ephemeral=True)

@tree.command(name="bounty_deauth", description="Remove a user from the authorized list")
@app_commands.describe(user="The user to deauthorize")
async def bounty_deauth(interaction: discord.Interaction, user: discord.User):
    if not is_owner(interaction.user.id):
        await interaction.response.send_message("Only Depth can setup bounties for The Chase.", ephemeral=True)
        return

    if user.id in authorized_users:
        authorized_users.remove(user.id)
        save_data()
        await interaction.response.send_message(f"{user.mention} has been deauthorized.", ephemeral=True)
    else:
        await interaction.response.send_message(f"{user.mention} was not in the authorized list.", ephemeral=True)

@tree.command(name="bounty_who", description="List all users authorized to manage bounties")
async def bounty_who(interaction: discord.Interaction):
    if not is_authorized(interaction.user.id):
        await interaction.response.send_message("Only Depth can setup bounties for The Chase.", ephemeral=True)
        return

    mentions = []
    for user_id in authorized_users:
        user = await bot.fetch_user(user_id)
        mentions.append(user.mention if user else f"`{user_id}`")

    response = "**Authorized users:**\n" + "\n".join(mentions)
    await interaction.response.send_message(response, ephemeral=True)

@bot.event
async def setup_hook():
    TEST_GUILD_ID = os.getenv("TEST_GUILD_ID")
    if TEST_GUILD_ID:
        guild = discord.Object(id=int(TEST_GUILD_ID))
        await tree.sync(guild=guild)
        print(f"Synced commands to test guild: {TEST_GUILD_ID}")
    else:
        await tree.sync()
        print("Synced commands globally (may take up to 1 hour)")

bot.setup_hook = setup_hook
bot.run(DISCORD_TOKEN)
