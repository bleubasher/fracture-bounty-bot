from dotenv import load_dotenv
import os
import json
import asyncio
from datetime import datetime

import discord
from discord.ext import commands
from discord import app_commands

# -------------------- ENV / CONFIG --------------------
load_dotenv()

print("🔥 Bounty bot starting up")

OWNER_ID = int(os.getenv("OWNER_ID", "217324902447841282"))
DATA_FILE = os.getenv("DATA_FILE", "bounties.json")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("TEST_GUILD_ID")  # fast guild sync when set

print(f"Loaded OWNER_ID: {OWNER_ID}")
print(f"Loaded GUILD_ID: {GUILD_ID}")
print(f"Loaded DATA_FILE: {DATA_FILE}")
print(f"Discord Token present: {bool(DISCORD_TOKEN)}")

# -------------------- DISCORD CLIENT --------------------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

# -------------------- STORAGE --------------------
bounties = []          # list of {message, post_time(optional), channel_id, sent}
authorized_users = []  # list of user IDs

# Special marker item to store default channel
CHANNEL_MARKER = "__CHANNEL_SET__"


def load_data():
    """Load bounties and authorized list from disk; always include OWNER_ID."""
    global bounties, authorized_users
    if not os.path.exists(DATA_FILE):
        bounties = []
        authorized_users = [OWNER_ID]
    else:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            bounties = data.get("bounties", [])
            authorized = data.get("authorized_users", [])
            if OWNER_ID not in authorized:
                authorized.append(OWNER_ID)
            authorized_users[:] = authorized  # mutate in place


def save_data():
    """Persist bounties and authorized list to disk."""
    print(f"Saving data to {DATA_FILE}...")
    payload = {
        "bounties": bounties,
        "authorized_users": authorized_users,
    }
    print(json.dumps(payload, indent=4))
    with open(DATA_FILE, "w") as f:
        json.dump(payload, f, indent=4)


load_data()
print(">>> Initial data load")
print(f"Current bounties: {bounties}")
print(f"OWNER_ID (from env) = {OWNER_ID}")
print(f"AUTHORIZED_USERS at load = {authorized_users}")

# -------------------- AUTH HELPERS --------------------
def is_authorized(user_id: int) -> bool:
    return user_id in authorized_users


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


# -------------------- DEFAULT CHANNEL HELPERS --------------------
def set_default_channel(channel_id: int):
    """Create/update the special marker entry holding default channel."""
    for b in bounties:
        if b.get("message") == CHANNEL_MARKER:
            b["channel_id"] = channel_id
            save_data()
            return
    bounties.append({"message": CHANNEL_MARKER, "channel_id": channel_id, "sent": True})
    save_data()


def get_default_channel_id() -> int | None:
    for b in bounties:
        if b.get("message") == CHANNEL_MARKER and "channel_id" in b:
            return b["channel_id"]
    return None


# -------------------- SCHEDULER --------------------
scheduled_tasks: list[asyncio.Task] = []


def schedule_bounty(message: str, post_time: str | None, channel_id: int):
    async def send_later():
        # Determine delay or send immediately if in the past
        if post_time:
            try:
                target = datetime.strptime(post_time, "%Y-%m-%d %H:%M")
            except ValueError:
                # If stored time somehow invalid, just send now
                target = datetime.utcnow()
            now = datetime.utcnow()
            delay = (target - now).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)

        # Post the message
        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except Exception as e:
                print(f"Failed to fetch channel {channel_id}: {e}")

        if channel:
            try:
                await channel.send(message)
            except Exception as e:
                print(f"Error sending message to {channel_id}: {e}")

        # Mark as sent
        for bounty in bounties:
            if (
                bounty.get("message") == message
                and bounty.get("channel_id") == channel_id
                and (not post_time or bounty.get("post_time") == post_time)
                and not bounty.get("sent")
            ):
                bounty["sent"] = True
                break

        save_data()

    task = asyncio.create_task(send_later())
    scheduled_tasks.append(task)


# -------------------- EVENTS --------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    # Reschedule pending bounties
    for bounty in bounties:
        if bounty.get("message") == CHANNEL_MARKER:
            continue
        if not bounty.get("sent"):
            schedule_bounty(
                bounty.get("message"),
                bounty.get("post_time"),  # may be None for immediate
                bounty.get("channel_id"),
            )

    # Debug: print what slash commands are registered
    cmds = bot.tree.get_commands()
    print("Registered slash commands:", [c.name for c in cmds])
    for c in cmds:
        if hasattr(c, "commands"):
            print(f"Group {c.name} subcommands:", [sc.name for sc in c.commands])


@bot.event
async def setup_hook():
    try:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            # Clear only the guild's commands first (prevents ghost entries)
            bot.tree.clear_commands(guild=guild)
            # Re-register (decorators have already populated the tree)
            await bot.tree.sync(guild=guild)
            print(f"Cleared & synced commands to guild: {GUILD_ID}")
        else:
            # Global sync (can take a while to propagate)
            await bot.tree.sync()
            print("Synced commands globally (may take time to appear)")
    except Exception as e:
        print("Slash command sync failed:", e)



# -------------------- COMMANDS --------------------
@tree.command(name="bounty", description="Show bounty bot command help")
async def bounty_help(interaction: discord.Interaction):
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


# 1) /bounty_channel — Set or update the default channel (authorized only)
@tree.command(name="bounty_channel", description="Set the default channel where bounty messages are posted.")
@app_commands.describe(channel="Pick the channel where bounty messages should go by default")
async def bounty_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_authorized(interaction.user.id):
        await interaction.response.send_message("You are not authorized to change the bounty channel.", ephemeral=True)
        return

    set_default_channel(channel.id)
    await interaction.response.send_message(f"Default bounty channel set to {channel.mention}.", ephemeral=True)


# 2) /bounty_now — send immediately (authorized only)
@tree.command(name="bounty_now", description="Post a bounty message immediately to the default/override channel.")
@app_commands.describe(message="Message to post", channel="Override channel (optional)")
async def bounty_now(interaction: discord.Interaction, message: str, channel: discord.TextChannel | None = None):
    if not is_authorized(interaction.user.id):
        await interaction.response.send_message("You are not authorized to post bounties.", ephemeral=True)
        return

    chan_id = (channel.id if channel else get_default_channel_id())
    if not chan_id:
        await interaction.response.send_message(
            "No default channel set. Use `/bounty_channel` first or provide a channel.",
            ephemeral=True
        )
        return

    # Save a record as already-sent immediate bounty (optional)
    entry = {"message": message, "post_time": None, "channel_id": chan_id, "sent": False}
    bounties.append(entry)
    save_data()

    # Schedule with no delay → sends now
    schedule_bounty(message, None, chan_id)

    await interaction.response.send_message("Bounty posted.", ephemeral=True)


# 3) /bounty_new — schedule for specific UTC time (authorized only)
@tree.command(name="bounty_new", description="Schedule a bounty message for a future UTC time.")
@app_commands.describe(
    message="Message to post",
    post_time="UTC time in format YYYY-MM-DD HH:MM",
    channel="Override channel (optional)"
)
async def bounty_new(interaction: discord.Interaction, message: str, post_time: str, channel: discord.TextChannel | None = None):
    if not is_authorized(interaction.user.id):
        await interaction.response.send_message("You are not authorized to schedule bounties.", ephemeral=True)
        return

    # Validate time format
    try:
        _ = datetime.strptime(post_time, "%Y-%m-%d %H:%M")
    except ValueError:
        await interaction.response.send_message(
            "Invalid time. Use format `YYYY-MM-DD HH:MM` (UTC).",
            ephemeral=True
        )
        return

    chan_id = (channel.id if channel else get_default_channel_id())
    if not chan_id:
        await interaction.response.send_message(
            "No default channel set. Use `/bounty_channel` first or provide a channel.",
            ephemeral=True
        )
        return

    # Save and schedule
    bounties.append({"message": message, "post_time": post_time, "channel_id": chan_id, "sent": False})
    save_data()
    schedule_bounty(message, post_time, chan_id)

    await interaction.response.send_message(f"Scheduled bounty for `{post_time} UTC`.", ephemeral=True)


# 4) /bounty_list — list pending/sent bounties
@tree.command(name="bounty_list", description="List all scheduled bounties and their status.")
async def bounty_list(interaction: discord.Interaction):
    # Visibility: everyone can view (change to auth-only if desired)
    visible = [b for b in bounties if b.get("message") != CHANNEL_MARKER]
    if not visible:
        await interaction.response.send_message("No bounties scheduled.", ephemeral=True)
        return

    lines = []
    for b in visible:
        status = "sent ✅" if b.get("sent") else "pending ⏳"
        when = b.get("post_time", "now")
        ch = b.get("channel_id")
        lines.append(f"- [{status}] {when} → {b.get('message')} (channel_id={ch})")

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


# 5) /bounty_auth — add an authorized user (owner only)
@tree.command(name="bounty_auth", description="Authorize a user to manage bounties. (Owner only)")
@app_commands.describe(user="User to authorize")
async def bounty_auth(interaction: discord.Interaction, user: discord.User):
    if not is_owner(interaction.user.id):
        await interaction.response.send_message("Owner only command.", ephemeral=True)
        return

    if user.id not in authorized_users:
        authorized_users.append(user.id)
        save_data()
        await interaction.response.send_message(f"Authorized <@{user.id}>.", ephemeral=True)
    else:
        await interaction.response.send_message(f"<@{user.id}> is already authorized.", ephemeral=True)


# 6) /bounty_deauth — remove an authorized user (owner only)
@tree.command(name="bounty_deauth", description="Remove a user's authorization. (Owner only)")
@app_commands.describe(user="User to deauthorize")
async def bounty_deauth(interaction: discord.Interaction, user: discord.User):
    if not is_owner(interaction.user.id):
        await interaction.response.send_message("Owner only command.", ephemeral=True)
        return

    if user.id == OWNER_ID:
        await interaction.response.send_message("You cannot deauthorize the owner.", ephemeral=True)
        return

    if user.id in authorized_users:
        authorized_users.remove(user.id)
        save_data()
        await interaction.response.send_message(f"Deauthorized <@{user.id}>.", ephemeral=True)
    else:
        await interaction.response.send_message(f"<@{user.id}> is not currently authorized.", ephemeral=True)


# 7) /bounty_who — show authorized users (everyone can view)
@tree.command(name="bounty_who", description="Show the current list of authorized users.")
async def bounty_who(interaction: discord.Interaction):
    if not authorized_users:
        await interaction.response.send_message("No authorized users.", ephemeral=True)
        return

    mentions = [f"<@{uid}>" for uid in authorized_users]
    await interaction.response.send_message(
        f"Authorized users ({len(mentions)}): " + ", ".join(mentions),
        ephemeral=True
    )


# -------------------- RUN --------------------
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing from environment")

bot.run(DISCORD_TOKEN)
