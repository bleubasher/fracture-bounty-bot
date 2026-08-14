from dotenv import load_dotenv
import os
import json
import asyncio
import uuid
from datetime import datetime, timedelta

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
bounties = []          # list of {id, message, post_time(optional), channel_id, sent}
authorized_users = []  # list of user IDs

# Special marker item to store default channel
CHANNEL_MARKER = "__CHANNEL_SET__"


def _normalize_bounties_after_load() -> bool:
    """Merge duplicate channel markers; assign stable id to each real bounty row."""
    global bounties
    changed = False
    markers = [b for b in bounties if b.get("message") == CHANNEL_MARKER]
    rest = [b for b in bounties if b.get("message") != CHANNEL_MARKER]

    for b in rest:
        if not b.get("id"):
            b["id"] = str(uuid.uuid4())
            changed = True

    if len(markers) > 1:
        channel_id = None
        for m in markers:
            if m.get("channel_id") is not None:
                channel_id = m["channel_id"]
        bounties = [
            {"message": CHANNEL_MARKER, "channel_id": channel_id, "sent": True},
            *rest,
        ]
        changed = True
    elif len(markers) == 1:
        m = markers[0]
        if "post_time" in m:
            del m["post_time"]
            changed = True
        if not m.get("sent", True):
            m["sent"] = True
            changed = True
        bounties = [m, *rest]
    else:
        bounties = rest

    return changed


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

    if _normalize_bounties_after_load():
        save_data()


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

# after loading GUILD_ID
from typing import Optional  # for Python 3.10 compatibility

GUILD_OBJ = discord.Object(id=int(GUILD_ID)) if GUILD_ID else None


# -------------------- AUTH HELPERS --------------------
def is_authorized(user_id: int) -> bool:
    return user_id in authorized_users


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


# -------------------- DEFAULT CHANNEL HELPERS --------------------
def set_default_channel(channel_id: int):
    """Create/update the single marker entry holding default channel."""
    for b in bounties:
        if b.get("message") == CHANNEL_MARKER:
            b["channel_id"] = channel_id
            b["sent"] = True
            b.pop("post_time", None)
            save_data()
            return
    bounties.insert(0, {"message": CHANNEL_MARKER, "channel_id": channel_id, "sent": True})
    save_data()


def get_default_channel_id() -> int | None:
    for b in bounties:
        if b.get("message") == CHANNEL_MARKER and "channel_id" in b:
            return b["channel_id"]
    return None
    
def chunk_lines(lines, max_len=1900):
    blocks, cur = [], ""
    for line in lines:
        # +1 for newline if cur isn't empty
        add_len = len(line) + (1 if cur else 0)
        if len(cur) + add_len > max_len:
            blocks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        blocks.append(cur)
    return blocks


# -------------------- SCHEDULER --------------------
scheduled_tasks: list[asyncio.Task] = []
bounty_tasks_inflight: set[str] = set()


def schedule_bounty(bounty_id: str, message: str, post_time: str | None, channel_id: int):
    if bounty_id in bounty_tasks_inflight:
        return
    bounty_tasks_inflight.add(bounty_id)

    async def send_later():
        try:
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

            # Mark as sent by stable id
            for bounty in bounties:
                if bounty.get("id") == bounty_id and not bounty.get("sent"):
                    bounty["sent"] = True
                    break

            save_data()
        finally:
            bounty_tasks_inflight.discard(bounty_id)

    task = asyncio.create_task(send_later())
    scheduled_tasks.append(task)


# -------------------- EVENTS --------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    # Reschedule pending bounties (schedule_bounty dedupes by bounty id / reconnect)
    for bounty in bounties:
        if bounty.get("message") == CHANNEL_MARKER:
            continue
        if not bounty.get("sent"):
            bid = bounty.get("id")
            if not bid:
                continue
            schedule_bounty(
                bid,
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

            # 1) Clear ONLY this guild's commands (removes any stale guild entries)
            bot.tree.clear_commands(guild=guild)   # note: no await

            # 2) Copy your global decorators into this guild scope
            #    (Works whether your decorators are global or a mix)
            bot.tree.copy_global_to(guild=guild)

            # 3) Sync the guild (fast propagation)
            cmds = await bot.tree.sync(guild=guild)
            print(f"✅ Guild sync complete: {len(cmds)} commands → {GUILD_ID}")
        else:
            # Global sync (slower)
            cmds = await bot.tree.sync()
            print(f"✅ Global sync complete: {len(cmds)} commands")
    except Exception as e:
        import traceback
        print("❌ Slash command sync failed:", e)
        traceback.print_exc()

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
        "`/bounty_test` — Run an automated systems check to verify bot functions.\n"
    )
    await interaction.response.send_message(help_text, ephemeral=True)


@tree.command(name="bounty_time", description="Get the current UTC time")
async def bounty_time(interaction: discord.Interaction):
    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    await interaction.response.send_message(f"Current UTC time: {now_utc}", ephemeral=True)


# 1) /bounty_channel — Set or update the default channel (authorized only)
@tree.command(name="bounty_channel", description="Set the channel for bounties to post")
@app_commands.describe(channel="Optional: choose a channel; defaults to the current one")
async def bounty_channel(interaction: discord.Interaction, channel: discord.TextChannel | None = None):
    if not is_authorized(interaction.user.id):
        await interaction.response.send_message("Only Depth can setup bounties for The Chase.", ephemeral=True)
        return

    target_channel = channel or interaction.channel
    channel_id = target_channel.id

    set_default_channel(channel_id)

    await interaction.response.send_message(
        f"Channel set to <#{channel_id}> for future bounties.",
        ephemeral=True
    )


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

    entry = {
        "id": str(uuid.uuid4()),
        "message": message,
        "post_time": None,
        "channel_id": chan_id,
        "sent": False,
    }
    bounties.append(entry)
    save_data()

    # Schedule with no delay → sends now
    schedule_bounty(entry["id"], message, None, chan_id)

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

    entry = {
        "id": str(uuid.uuid4()),
        "message": message,
        "post_time": post_time,
        "channel_id": chan_id,
        "sent": False,
    }
    bounties.append(entry)
    save_data()
    schedule_bounty(entry["id"], message, post_time, chan_id)

    await interaction.response.send_message(f"Scheduled bounty for `{post_time} UTC`.", ephemeral=True)


# 4) /bounty_list — list pending/sent bounties
@tree.command(name="bounty_list", description="List all scheduled bounties")
async def bounty_list(interaction: discord.Interaction):
    if not is_authorized(interaction.user.id):
        await interaction.response.send_message(
            "Only Depth can setup bounties for The Chase.", ephemeral=True
        )
        return

    active = [b for b in bounties if b.get("message") != CHANNEL_MARKER]
    if not active:
        await interaction.response.send_message("No bounties scheduled.", ephemeral=True)
        return

    # Sort by time if present, otherwise keep insertion order
    def _key(b):
        try:
            return datetime.strptime(b["post_time"], "%Y-%m-%d %H:%M")
        except Exception:
            return datetime.max
    active.sort(key=_key)

    lines = [
        f"**{b.get('post_time','?')} UTC** — {b.get('message','(no message)')}" + (" ✅" if b.get("sent") else "")
        for b in active
    ]

    blocks = chunk_lines(lines, max_len=1900)

    # Send first chunk as the initial response
    await interaction.response.send_message(blocks[0], ephemeral=True)

    # Send remaining chunks as follow-ups (still ephemeral)
    for block in blocks[1:]:
        await interaction.followup.send(block, ephemeral=True)


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
    
# 8) /bounty_test — automated systems check (authorized only)
@tree.command(name="bounty_test", description="Run an automated systems check for bounty posting.")
async def bounty_test(interaction: discord.Interaction):
    if not is_authorized(interaction.user.id):
        await interaction.response.send_message("You are not authorized to run the systems check.", ephemeral=True)
        return

    # Get the default channel for Part 2
    default_chan_id = get_default_channel_id()
    if not default_chan_id:
        await interaction.response.send_message(
            "No default channel set. Use `/bounty_channel` first before running the test.",
            ephemeral=True
        )
        return

    # Get the channel where the command was currently run for Part 1
    current_chan_id = interaction.channel.id

    # --- Part 1: Immediate message in current channel ---
    msg_part1 = "This is part 1 of Depth's Bounty bot automated systems check. Please standby for part 2."
    entry1 = {
        "id": str(uuid.uuid4()),
        "message": msg_part1,
        "post_time": None,
        "channel_id": current_chan_id,
        "sent": False,
    }
    bounties.append(entry1)

    # --- Part 2: Scheduled message 3 minutes from now in the default channel ---
    msg_part2 = (
        "This is part 2 of Depth's Bounty Bot automated systems check. If both of these messages have posted "
        "and there are no errors, Depth is not a moron. If something is fucked up, or there are errors in the log, "
        "Depth is a moron and needs to go fix things. Thank you for your time."
    )
    
    # Calculate 3 minutes in the future (UTC)
    post_time_dt = datetime.utcnow() + timedelta(minutes=3)
    post_time_str = post_time_dt.strftime("%Y-%m-%d %H:%M")

    entry2 = {
        "id": str(uuid.uuid4()),
        "message": msg_part2,
        "post_time": post_time_str,
        "channel_id": default_chan_id,
        "sent": False,
    }
    bounties.append(entry2)

    # Save data for both entries to JSON
    save_data()

    # Schedule both messages via the bot's scheduling loop
    schedule_bounty(entry1["id"], msg_part1, None, current_chan_id)
    schedule_bounty(entry2["id"], msg_part2, post_time_str, default_chan_id)

    # Respond ephemerally to the user who ran the command
    await interaction.response.send_message(
        f"Automated systems check initiated.\n"
        f"Part 1 sent to <#{current_chan_id}>.\n"
        f"Part 2 scheduled for `{post_time_str} UTC` in <#{default_chan_id}>.", 
        ephemeral=True
    )


# -------------------- RUN --------------------
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing from environment")

bot.run(DISCORD_TOKEN)
