from dotenv import load_dotenv
import os
import json
import datetime
import io
import base64
import asyncio
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont
from pilmoji import Pilmoji
from config import (
    TIERS, GAMEMODE_EMOJIS, GAMEMODE_ABBREV, REGION_COLORS,
    CARD_BG, CARD_HEADER, CARD_ACCENT, CARD_CIRCLE_FILL, CARD_CIRCLE_BORDER_LT,
    CARD_DIVIDER, CARD_TEXT_WHITE, CARD_TEXT_GREY, CARD_EMOJI_SIZE,
    QUEUE_TIMEOUT_SECONDS, FONT_BOLD, FONT_REGULAR,
    TIER_POINTS, OVERALL_RANKS,
)

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

DATA_FILE = "tiers_data.json"

GITHUB_OWNER = "shadyy000777-commits"
GITHUB_REPO  = "My-site"
GITHUB_FILE  = "tiers_data.json"

async def _push_data_to_github():
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return
    try:
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_FILE}"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }
        with open(DATA_FILE, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        async with aiohttp.ClientSession() as session:
            for attempt in range(3):
                async with session.get(url, headers=headers) as r:
                    sha = (await r.json()).get("sha") if r.status == 200 else None
                body = {"message": "Auto-sync: player data updated", "content": content}
                if sha:
                    body["sha"] = sha
                async with session.put(url, headers=headers, json=body) as r:
                    if r.status in (200, 201):
                        print("[GitHub sync] tiers_data.json pushed to GitHub ✅")
                        return
                    elif r.status == 409 and attempt < 2:
                        print(f"[GitHub sync] 409 on tiers_data.json, retrying (attempt {attempt + 1})...")
                        await asyncio.sleep(2)
                    else:
                        print(f"[GitHub sync] Failed: HTTP {r.status}")
                        return
    except Exception as e:
        print(f"[GitHub sync] Error: {e}")


async def _push_file_to_github(github_path: str, local_path: str, commit_msg: str):
    """Push any local file to GitHub repo at the given path. Retries once on 409 conflict."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return
    try:
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{github_path}"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }
        with open(local_path, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        async with aiohttp.ClientSession() as session:
            for attempt in range(3):
                async with session.get(url, headers=headers) as r:
                    sha = (await r.json()).get("sha") if r.status == 200 else None
                body = {"message": commit_msg, "content": content}
                if sha:
                    body["sha"] = sha
                async with session.put(url, headers=headers, json=body) as r:
                    if r.status in (200, 201):
                        print(f"[GitHub sync] {github_path} pushed to GitHub ✅")
                        return
                    elif r.status == 409 and attempt < 2:
                        print(f"[GitHub sync] 409 conflict for {github_path}, retrying (attempt {attempt + 1})...")
                        await asyncio.sleep(2)
                    else:
                        print(f"[GitHub sync] Failed to push {github_path}: HTTP {r.status}")
                        return
    except Exception as e:
        print(f"[GitHub sync] Error pushing {github_path}: {e}")


async def _push_website_to_github():
    """Push website/index.html and all static assets to GitHub so Netlify stays in sync."""
    # Push index.html
    await _push_file_to_github(
        "index.html", "website/index.html", "Auto-sync: website update"
    )
    # Push static assets
    static_dir = "website/static"
    if os.path.isdir(static_dir):
        for fname in os.listdir(static_dir):
            fpath = os.path.join(static_dir, fname)
            if os.path.isfile(fpath):
                await _push_file_to_github(
                    f"static/{fname}", fpath, f"Auto-sync: static asset {fname}"
                )


async def _push_image_to_github(filename: str, img_bytes: bytes):
    """Push a skin image to GitHub repo under skins/ so Netlify can serve it."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return
    try:
        github_path = f"skins/{filename}"
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{github_path}"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }
        content = base64.b64encode(img_bytes).decode()
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as r:
                sha = (await r.json()).get("sha") if r.status == 200 else None
            body = {"message": f"Upload skin: {filename}", "content": content}
            if sha:
                body["sha"] = sha
            async with session.put(url, headers=headers, json=body) as r:
                if r.status not in (200, 201):
                    print(f"[GitHub image] Failed to push {filename}: HTTP {r.status}")
                else:
                    print(f"[GitHub image] {filename} pushed to GitHub ✅")
    except Exception as e:
        print(f"[GitHub image] Error pushing {filename}: {e}")


def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"players": {}, "tests": []}


def save_data(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_push_data_to_github())
    except Exception:
        pass


STAFF_COMMANDS = [
    "settier", "submittest", "remove", "removetier", "panel",
    "addgamemode", "removegamemode", "clearwaitlist", "clearallwaitlists",
    "nexttester", "setwaitlistcategory", "queue", "kickfromqueue", "leaderboard", "profile",
    "pointsto", "setimage", "point", "region",
    "gettier", "history", "image", "tierlist", "waitlist", "website",
    "removeplayerrole",
]


def require_command_role(command_name: str):
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        if interaction.user.id == interaction.guild.owner_id:
            return True
        if interaction.user.guild_permissions.administrator:
            return True
        data = load_data()
        role_id = data.get("command_roles", {}).get(command_name)
        if not role_id:
            await interaction.response.send_message(
                f"❌ No role has been assigned for **/{command_name}** yet.\n"
                f"Ask a server admin to use `/setrole` to set one.",
                ephemeral=True,
            )
            return False
        role = interaction.guild.get_role(int(role_id))
        if role and role in interaction.user.roles:
            return True
        await interaction.response.send_message(
            f"❌ You need the **{role.name if role else 'required'}** role to use **/{command_name}**.",
            ephemeral=True,
        )
        return False
    return app_commands.check(predicate)




async def _fetch_img(url: str):
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                if r.status == 200:
                    return Image.open(io.BytesIO(await r.read())).convert("RGBA")
    except Exception:
        return None


_EMOJI_IMG_CACHE: dict = {}


def _emoji_codepoint(emoji_str: str) -> str:
    codes = [f"{ord(c):x}" for c in emoji_str if ord(c) != 0xFE0F]
    return "-".join(codes)


def _parse_custom_emoji_id(emoji_str: str):
    import re
    m = re.match(r"<a?:[\w~]+:(\d+)>", emoji_str)
    return m.group(1) if m else None


async def _fetch_emoji_img(emoji_str: str, size: int = 30):
    if not emoji_str:
        return None
    key = f"{emoji_str}_{size}"
    if key in _EMOJI_IMG_CACHE:
        return _EMOJI_IMG_CACHE[key]

    custom_id = _parse_custom_emoji_id(emoji_str)
    if custom_id:
        img = await _fetch_img(f"https://cdn.discordapp.com/emojis/{custom_id}.png?size=64")
        if img:
            img = img.resize((size, size), Image.LANCZOS)
            _EMOJI_IMG_CACHE[key] = img
            return img
        return None

    cp = _emoji_codepoint(emoji_str)
    for url in [
        f"https://cdn.jsdelivr.net/gh/jdecked/twemoji@latest/assets/72x72/{cp}.png",
        f"https://twemoji.maxcdn.com/v/latest/72x72/{cp}.png",
    ]:
        img = await _fetch_img(url)
        if img:
            img = img.resize((size, size), Image.LANCZOS)
            _EMOJI_IMG_CACHE[key] = img
            return img
    return None


async def build_profile_card(mc_username: str, region: str, account_type: str, tiers: dict) -> io.BytesIO:
    W, H = 680, 230

    try:
        fn  = ImageFont.truetype(FONT_BOLD,    30)
        fs  = ImageFont.truetype(FONT_REGULAR, 15)
        fr  = ImageFont.truetype(FONT_BOLD,    18)
        ftl = ImageFont.truetype(FONT_BOLD,    12)
        fgm = ImageFont.truetype(FONT_BOLD,    13)
        ftv = ImageFont.truetype(FONT_BOLD,    14)
    except Exception:
        fn = fs = fr = ftl = fgm = ftv = ImageFont.load_default()

    img  = Image.new("RGBA", (W, H), CARD_BG)
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0), (W, 112)], fill=CARD_HEADER)
    draw.polygon([(0, 0), (105, 0), (85, 112), (0, 112)], fill=CARD_ACCENT)
    draw.polygon([(0, 0), (93, 0), (74, 112), (0, 112)], fill=CARD_HEADER)

    avatar = await _fetch_img(f"https://crafatar.com/renders/body/{mc_username}?scale=4&overlay")
    if avatar is None:
        avatar = await _fetch_img(f"https://mc-heads.net/body/{mc_username}/100")
    if avatar:
        ratio  = 100 / avatar.height
        av_w   = int(avatar.width * ratio)
        avatar = avatar.resize((av_w, 100), Image.LANCZOS)
        img.paste(avatar, (max(0, (93 - av_w) // 2), 6), avatar)
    else:
        draw.rectangle([(8, 10), (82, 102)], fill=CARD_CIRCLE_FILL)

    draw.text((112, 18), mc_username, fill=CARD_TEXT_WHITE, font=fn)
    draw.text((114, 58), f"{account_type}  •  Verified Profile", fill=CARD_TEXT_GREY, font=fs)

    reg  = (region or "?").upper()[:4]
    rcol = REGION_COLORS.get(reg, (65, 70, 90))
    bw, bh = 66, 44
    bx, by = W - bw - 18, 18
    draw.rounded_rectangle([(bx, by), (bx + bw, by + bh)], radius=8, fill=rcol)
    bb = draw.textbbox((0, 0), reg, font=fr)
    draw.text(
        (bx + (bw - (bb[2] - bb[0])) // 2, by + (bh - (bb[3] - bb[1])) // 2),
        reg, fill=CARD_TEXT_WHITE, font=fr,
    )

    draw.rectangle([(0, 112), (W, 114)], fill=CARD_DIVIDER)
    draw.text((20, 122), "TIERS", fill=CARD_TEXT_GREY, font=ftl)

    emoji_lookup = {k.lower(): v for k, v in GAMEMODE_EMOJIS.items()}
    ranked_map = {k.lower(): v for k, v in tiers.items() if isinstance(v, dict) and "tier" in v}

    # Build ordered display list: ranked gamemodes first (preserving order), then
    # fill remaining slots with unranked DEFAULT_GAMEMODES up to 10 total.
    display_order = list(dict.fromkeys(
        [gm for gm in DEFAULT_GAMEMODES if gm.lower() in ranked_map] +
        [gm for gm in DEFAULT_GAMEMODES if gm.lower() not in ranked_map]
    ))

    cd, gap, sx, sy = 52, 12, 20, 143

    for i, gm in enumerate(display_order[:10]):
        gm_key = gm.lower()
        gd     = ranked_map.get(gm_key)
        tv     = gd["tier"] if gd else "UR"
        is_ht  = tv.startswith("HT")
        is_ur  = tv == "UR"
        cx     = sx + i * (cd + gap)

        outline_col = CARD_ACCENT if is_ht else (CARD_CIRCLE_BORDER_LT if not is_ur else (40, 44, 62))
        draw.ellipse(
            [(cx, sy), (cx + cd, sy + cd)],
            fill=CARD_CIRCLE_FILL,
            outline=outline_col,
            width=2,
        )

        tier_col = CARD_ACCENT if is_ht else (CARD_TEXT_GREY if not is_ur else (60, 64, 82))
        bb3 = draw.textbbox((0, 0), tv, font=ftv)
        draw.text(
            (cx + (cd - (bb3[2] - bb3[0])) // 2, sy + cd + 4),
            tv, fill=tier_col, font=ftv,
        )

        gm_emoji = emoji_lookup.get(gm_key)
        if gm_emoji:
            eimg = await _fetch_emoji_img(gm_emoji, size=CARD_EMOJI_SIZE)
            if eimg:
                if is_ur:
                    eimg = eimg.copy()
                    r, g, b, a = eimg.split()
                    a = a.point(lambda x: int(x * 0.35))
                    eimg.putalpha(a)
                ex = cx + (cd - CARD_EMOJI_SIZE) // 2
                ey = sy + (cd - CARD_EMOJI_SIZE) // 2
                img.paste(eimg, (ex, ey), eimg)
                continue

        fallback = GAMEMODE_ABBREV.get(gm_key, gm[:2].upper())
        bb2 = draw.textbbox((0, 0), fallback, font=fgm)
        draw.text(
            (cx + (cd - (bb2[2] - bb2[0])) // 2, sy + (cd - (bb2[3] - bb2[1])) // 2),
            fallback, fill=CARD_TEXT_WHITE if not is_ur else (60, 64, 82), font=fgm,
        )

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf


intents = discord.Intents.default()

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


DEFAULT_GAMEMODES = ["Sword", "Axe", "NethOP", "UHC", "SMP", "Pot", "Mace", "Crystal"]

active_queues: dict[int, "QueueView"] = {}

def build_waitlist_embed(gamemode: str, queue: list, profiles: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"📋 {gamemode} — Waitlist",
        color=discord.Color.purple(),
    )
    if queue:
        lines = []
        for i, uid in enumerate(queue, 1):
            profile = profiles.get(uid)
            mc_name = profile["minecraft_username"] if profile else "Unknown"
            region = profile.get("region", "?") if profile else "?"
            account_type = profile.get("account_type", "?") if profile else "?"
            lines.append(f"`{i}.` **{mc_name}** | {region} | {account_type} | <@{uid}>")
        embed.description = "\n".join(lines)
    else:
        embed.description = "*No players in the waitlist yet.*"
    embed.set_footer(text=f"🔒 Only the server owner can delete this channel • Updated {datetime.datetime.utcnow().strftime('%H:%M UTC')}")
    return embed


async def update_waitlist_channel(guild: discord.Guild, gamemode: str, data: dict):
    channels_data = data.setdefault("waitlist_channels", {})
    info = channels_data.get(gamemode, {})

    channel = guild.get_channel(info.get("channel_id", 0)) if info.get("channel_id") else None

    if not channel:
        safe_name = f"waitlist-{gamemode.lower().replace(' ', '-')}"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                read_message_history=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_messages=True,
                manage_channels=True,
            ),
        }

        category = None
        cat_id = data.get("waitlist_category_id")
        if cat_id:
            category = guild.get_channel(int(cat_id))
            if not isinstance(category, discord.CategoryChannel):
                category = None

        channel = await guild.create_text_channel(
            name=safe_name,
            overwrites=overwrites,
            topic=f"Waitlist for {gamemode} tier testing • Only the server owner can delete this channel.",
            category=category,
        )
        channels_data[gamemode] = {"channel_id": channel.id, "message_id": None}
        info = channels_data[gamemode]

    queue = data.get("waitlist", {}).get(gamemode, [])
    profiles = data.get("profiles", {})
    embed = build_waitlist_embed(gamemode, queue, profiles)

    message = None
    if info.get("message_id"):
        try:
            message = await channel.fetch_message(info["message_id"])
        except (discord.NotFound, discord.HTTPException):
            message = None

    view = DeleteWaitlistView(gamemode)
    if message:
        await message.edit(embed=embed, view=view)
    else:
        message = await channel.send(embed=embed, view=view)
        channels_data[gamemode]["message_id"] = message.id

    save_data(data)


class McUsernameModal(discord.ui.Modal, title="Verify Your Profile"):
    mc_username = discord.ui.TextInput(
        label="Minecraft Username",
        placeholder="e.g. Steve",
        max_length=100,
    )

    def __init__(self, region: str, account_type: str):
        super().__init__()
        self.region = region
        self.account_type = account_type

    async def on_submit(self, interaction: discord.Interaction):
        data = load_data()
        key = str(interaction.user.id)
        data.setdefault("profiles", {})[key] = {
            "discord_id":        key,
            "discord_name":      str(interaction.user),
            "minecraft_username": self.mc_username.value.strip(),
            "region":            self.region,
            "account_type":      self.account_type,
            "verified_at":       datetime.datetime.utcnow().isoformat(),
        }
        save_data(data)
        region_flags = {"NA": "🇺🇸", "EU": "🇪🇺", "AS": "🇮🇳", "SA": "🇧🇷", "OCE": "🇦🇺"}
        flag = region_flags.get(self.region, "🌍")
        account_emoji = "☕" if self.account_type == "Java" else "🪨"
        await interaction.response.send_message(
            f"✅ **Profile verified!**\n"
            f"**Username:** `{self.mc_username.value.strip()}`\n"
            f"**Region:** {flag} `{self.region}`\n"
            f"**Account:** {account_emoji} `{self.account_type}`",
            ephemeral=True,
        )


class AccountTypeSelectView(discord.ui.View):
    def __init__(self, region: str = ""):
        super().__init__(timeout=None)
        self.region = region

    @discord.ui.select(
        placeholder="☕ / 🪨  Select your account type...",
        options=[
            discord.SelectOption(label="Java",    description="Java Edition",    emoji="☕"),
            discord.SelectOption(label="Bedrock", description="Bedrock Edition", emoji="🪨"),
        ],
        custom_id="account_type_select",
    )
    async def select_account_type(self, interaction: discord.Interaction, select: discord.ui.Select):
        account_type = select.values[0]
        if not self.region:
            await interaction.response.send_message(
                "⚠️ Session expired. Please click **Verify Profile** again.", ephemeral=True
            )
            return
        await interaction.response.send_modal(McUsernameModal(self.region, account_type))


class RegionSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="🌍  Select your region...",
        options=[
            discord.SelectOption(label="NA",  description="North America", emoji="🇺🇸"),
            discord.SelectOption(label="EU",  description="Europe",        emoji="🇪🇺"),
            discord.SelectOption(label="AS",  description="Asia",          emoji="🇮🇳"),
            discord.SelectOption(label="SA",  description="South America", emoji="🇧🇷"),
            discord.SelectOption(label="OCE", description="Oceania",       emoji="🇦🇺"),
        ],
        custom_id="region_select",
    )
    async def select_region(self, interaction: discord.Interaction, select: discord.ui.Select):
        region = select.values[0]
        region_flags = {"NA": "🇺🇸", "EU": "🇪🇺", "AS": "🇮🇳", "SA": "🇧🇷", "OCE": "🇦🇺"}
        flag = region_flags.get(region, "🌍")
        await interaction.response.edit_message(
            content=f"**Step 2 of 3** — Region: {flag} **{region}**\nNow select your account type:",
            view=AccountTypeSelectView(region),
        )


class VerifyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Verify Profile",
            emoji=discord.PartialEmoji(name="reg_book", id=1508436330311581706),
            style=discord.ButtonStyle.danger,
            custom_id="verify_profile",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "**Step 1 of 3** — Select your region:",
            view=RegionSelectView(),
            ephemeral=True,
        )


def _to_button_emoji(emoji_str: str):
    custom_id = _parse_custom_emoji_id(emoji_str)
    if custom_id:
        import re
        m = re.match(r"<a?:([\w~]+):(\d+)>", emoji_str)
        if m:
            return discord.PartialEmoji(name=m.group(1), id=int(m.group(2)))
    return emoji_str


class GamemodeButton(discord.ui.Button):
    def __init__(self, gamemode: str, row: int):
        emoji = _to_button_emoji(GAMEMODE_EMOJIS.get(gamemode, "🎮"))
        super().__init__(
            label=gamemode,
            emoji=emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"gm_{gamemode.replace(' ', '_')}",
            row=row,
        )
        self.gamemode = gamemode

    async def callback(self, interaction: discord.Interaction):
        try:
            data = load_data()
            user_key = str(interaction.user.id)
            profile = data.get("profiles", {}).get(user_key)

            if not profile:
                await interaction.response.send_message(
                    "❌ Please click **Verify Profile** first before joining a waitlist!",
                    ephemeral=True,
                )
                return

            waitlist = data.setdefault("waitlist", {})
            queue = waitlist.setdefault(self.gamemode, [])

            if user_key in queue:
                await interaction.response.send_message(
                    f"⏳ You're already in the **{self.gamemode}** waitlist!", ephemeral=True
                )
                return

            queue.append(user_key)
            save_data(data)
            mc_name = profile["minecraft_username"]

            # Auto-assign gamemode role if configured
            role_assigned = None
            if interaction.guild:
                role_id = data.get("gamemode_roles", {}).get(self.gamemode)
                if role_id:
                    role = interaction.guild.get_role(int(role_id))
                    if role:
                        try:
                            member = interaction.guild.get_member(interaction.user.id) or interaction.user
                            await member.add_roles(role, reason=f"Joined {self.gamemode} waitlist via panel")
                            role_assigned = role.name
                        except discord.Forbidden:
                            print(f"[GamemodeButton] Missing permission to assign role '{role.name}'")
                        except Exception as re:
                            print(f"[GamemodeButton] Role assign error: {re}")

            role_line = f"\n🎭 You've been given the **{role_assigned}** role!" if role_assigned else ""
            await interaction.response.send_message(
                f"✅ **{mc_name}** added to the **{self.gamemode}** waitlist!\n"
                f"⏰ A tester will ping you soon. Make sure your DMs are open.{role_line}",
                ephemeral=True,
            )
            if interaction.guild:
                try:
                    await update_waitlist_channel(interaction.guild, self.gamemode, data)
                except Exception as ch_err:
                    print(f"Channel update error: {ch_err}")
        except Exception as e:
            print(f"GamemodeButton error: {e}")
            try:
                await interaction.response.send_message("❌ Something went wrong. Please try again.", ephemeral=True)
            except Exception:
                pass


class PanelView(discord.ui.View):
    def __init__(self, gamemodes: list):
        super().__init__(timeout=None)
        self.add_item(VerifyButton())
        for i, gm in enumerate(gamemodes):
            row = (i // 4) + 1
            if row > 4:
                break
            self.add_item(GamemodeButton(gm, row=row))


class DeleteWaitlistButton(discord.ui.Button):
    def __init__(self, gamemode: str):
        safe = gamemode.replace(" ", "_")
        super().__init__(
            label="🗑️ Delete Waitlist",
            style=discord.ButtonStyle.danger,
            custom_id=f"delete_waitlist_{safe}",
        )
        self.gamemode = gamemode

    async def callback(self, interaction: discord.Interaction):
        data = load_data()
        is_owner = interaction.user.id == interaction.guild.owner_id
        role_id = data.get("command_roles", {}).get("clearwaitlist")
        has_role = role_id and any(str(r.id) == str(role_id) for r in interaction.user.roles)
        is_admin = interaction.user.guild_permissions.administrator

        if not (is_owner or has_role or is_admin):
            await interaction.response.send_message(
                "❌ Only the server owner or staff with the **clearwaitlist** role can delete this waitlist.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        data.setdefault("waitlist", {})[self.gamemode] = []
        channels_data = data.get("waitlist_channels", {})
        if self.gamemode in channels_data:
            del channels_data[self.gamemode]
        save_data(data)

        channel = interaction.channel
        try:
            await channel.delete(reason=f"Waitlist for {self.gamemode} deleted by {interaction.user}")
        except discord.HTTPException:
            await interaction.followup.send("⚠️ Could not delete the channel. Please delete it manually.", ephemeral=True)


class DeleteWaitlistView(discord.ui.View):
    def __init__(self, gamemode: str):
        super().__init__(timeout=None)
        self.add_item(DeleteWaitlistButton(gamemode))


_replit_domain = os.environ.get("REPLIT_DEV_DOMAIN", "")
WEBSITE_URL = os.environ.get("WEBSITE_URL", f"https://{_replit_domain}" if _replit_domain else "")


async def resolve_discord_names():
    """Look up all Discord-mention player keys and cache their display names."""
    data = load_data()
    names = data.setdefault("discord_names", {})
    changed = False
    for key in list(data.get("players", {}).keys()):
        if key.startswith("<@") and key.endswith(">"):
            uid = key[2:-1]
            if uid not in names:
                for guild in bot.guilds:
                    try:
                        member = guild.get_member(int(uid)) or await guild.fetch_member(int(uid))
                        if member:
                            names[uid] = member.display_name
                            changed = True
                            break
                    except Exception:
                        pass
    if changed:
        save_data(data)
    print(f"Resolved {len(names)} Discord display names for leaderboard.")


@bot.event
async def on_ready():
    data = load_data()
    gamemodes = data.get("gamemodes", DEFAULT_GAMEMODES)
    bot.add_view(PanelView(gamemodes))
    bot.add_view(RegionSelectView())
    bot.add_view(AccountTypeSelectView())
    for gm in gamemodes:
        bot.add_view(DeleteWaitlistView(gm))

    if WEBSITE_URL:
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"Leaderboard 🏆 | {WEBSITE_URL}",
            )
        )

    synced_guilds = []
    for guild in bot.guilds:
        tree.copy_global_to(guild=guild)
        synced = await tree.sync(guild=guild)
        synced_guilds.append(guild.name)
        print(f"Synced {len(synced)} commands to {guild.name}: {[c.name for c in synced]}")
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    if WEBSITE_URL:
        print(f"Website: {WEBSITE_URL}")
    print(f"Total guilds synced: {len(synced_guilds)}")
    print("------")

    await resolve_discord_names()
    await _push_website_to_github()


@bot.event
async def on_guild_join(guild: discord.Guild):
    tree.copy_global_to(guild=guild)
    synced = await tree.sync(guild=guild)
    print(f"Joined new guild '{guild.name}' — synced {len(synced)} commands")


@tree.command(name="settier", description="Set a player's tier for a specific gamemode")
@app_commands.describe(
    username="Minecraft username",
    gamemode="The gamemode (e.g. Crystal, Sword, Mace)",
    tier="Tier to assign (e.g. HT1, LT3)",
)
@require_command_role("settier")
async def settier(interaction: discord.Interaction, username: str, gamemode: str, tier: str):
    if username.startswith("<@") and username.endswith(">"):
        await interaction.response.send_message(
            "❌ Please enter the player's **Minecraft username**, not a Discord mention.", ephemeral=True
        )
        return
    tier = tier.upper().strip()
    if tier not in TIERS:
        valid = ", ".join(TIERS)
        await interaction.response.send_message(
            f"❌ Invalid tier `{tier}`. Valid tiers: {valid}", ephemeral=True
        )
        return

    data = load_data()
    key = username.lower()
    gm_key = gamemode.lower()
    old_tier = data["players"].get(key, {}).get(gm_key, {}).get("tier", "Unranked")
    data["players"].setdefault(key, {})[gm_key] = {
        "tier": tier,
        "updated_at": datetime.datetime.utcnow().isoformat(),
        "updated_by": str(interaction.user),
    }
    save_data(data)

    embed = discord.Embed(title="Tier Updated", color=discord.Color.green())
    embed.add_field(name="Player", value=username, inline=True)
    embed.add_field(name="Gamemode", value=gamemode, inline=True)
    embed.add_field(name="Old Tier", value=old_tier, inline=True)
    embed.add_field(name="New Tier", value=tier, inline=True)
    embed.set_footer(text=f"Set by {interaction.user}")
    await interaction.response.send_message(embed=embed)


VALID_REGIONS = ["NA", "EU", "AS", "SA", "OCE"]

@tree.command(name="region", description="Set a player's region shown on the leaderboard")
@app_commands.describe(
    username="Minecraft username of the player",
    region="Region code: NA, EU, AS, SA, OCE",
)
@app_commands.choices(region=[
    app_commands.Choice(name="NA — North America", value="NA"),
    app_commands.Choice(name="EU — Europe",        value="EU"),
    app_commands.Choice(name="AS — Asia",          value="AS"),
    app_commands.Choice(name="SA — South America", value="SA"),
    app_commands.Choice(name="OCE — Oceania",      value="OCE"),
])
@require_command_role("settier")
async def set_region(
    interaction: discord.Interaction,
    username: str,
    region: app_commands.Choice[str],
):
    data = load_data()
    key = username.lower()

    if key not in data["players"]:
        await interaction.response.send_message(
            f"❌ No player found named `{username}`. Set their tier first with `/settier`.", ephemeral=True
        )
        return

    old_region = data["players"][key].get("region", "Not set")
    data["players"][key]["region"] = region.value

    # Also update profile if one exists with this minecraft_username
    for prof in data.get("profiles", {}).values():
        if prof.get("minecraft_username", "").lower() == key:
            prof["region"] = region.value
            break

    save_data(data)

    region_flags = {"NA": "🇺🇸", "EU": "🇪🇺", "AS": "🇮🇳", "SA": "🇧🇷", "OCE": "🇦🇺"}
    flag = region_flags.get(region.value, "🌍")

    embed = discord.Embed(title="Region Updated", color=discord.Color.blue())
    embed.add_field(name="Player",      value=username,    inline=True)
    embed.add_field(name="Old Region",  value=old_region,  inline=True)
    embed.add_field(name="New Region",  value=f"{flag} {region.value}", inline=True)
    embed.set_footer(text=f"Set by {interaction.user}")
    await interaction.response.send_message(embed=embed)


@tree.command(name="gettier", description="Check a player's tier(s) — all gamemodes or a specific one")
@app_commands.describe(username="Minecraft username", gamemode="Optional: specific gamemode to check")
@require_command_role("gettier")
async def gettier(interaction: discord.Interaction, username: str, gamemode: str = ""):
    data = load_data()
    player = data["players"].get(username.lower())

    if not player:
        await interaction.response.send_message(
            f"❌ No tiers found for `{username}`.", ephemeral=True
        )
        return

    embed = discord.Embed(title=f"🎮 {username} — Tiers", color=discord.Color.blurple())

    if gamemode:
        gm_data = player.get(gamemode.lower())
        if not gm_data or "tier" not in gm_data:
            await interaction.response.send_message(
                f"❌ No tier found for `{username}` in **{gamemode}**.", ephemeral=True
            )
            return
        embed.add_field(name="Gamemode", value=gamemode, inline=True)
        embed.add_field(name="Tier", value=gm_data["tier"], inline=True)
        embed.add_field(name="Updated by", value=gm_data.get("updated_by", "Unknown"), inline=True)
        embed.add_field(name="Updated at", value=gm_data.get("updated_at", "?")[:10], inline=True)
    else:
        ranked = [(gm, gd) for gm, gd in player.items() if isinstance(gd, dict) and "tier" in gd]
        if not ranked:
            await interaction.response.send_message(f"❌ No tiers found for `{username}`.", ephemeral=True)
            return
        for gm, gd in ranked:
            embed.add_field(name=gm.capitalize(), value=gd["tier"], inline=True)

    await interaction.response.send_message(embed=embed)


class RemoveRoleSelect(discord.ui.UserSelect):
    """User-select dropdown that strips the gamemode role from whoever the tester picks."""

    def __init__(self, target_role: discord.Role | None, already_removed: bool):
        if already_removed:
            placeholder = "✅ Role already auto-removed"
            disabled = True
        elif target_role is None:
            placeholder = "⚠️ Gamemode role not configured — use /setgamerole"
            disabled = True
        else:
            placeholder = f"🗑️ Select player to remove {target_role.name}…"
            disabled = False
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, disabled=disabled)
        self.target_role = target_role

    async def callback(self, interaction: discord.Interaction):
        member = self.values[0]
        try:
            if self.target_role and self.target_role in member.roles:
                await member.remove_roles(
                    self.target_role,
                    reason=f"Removed via submittest by {interaction.user}",
                )
                self.placeholder = f"✅ {self.target_role.name} removed from {member.display_name}"
                self.disabled = True
                await interaction.response.edit_message(view=self.view)
                await interaction.followup.send(
                    f"✅ Removed **{self.target_role.name}** from {member.mention}.",
                    ephemeral=True,
                )
            elif self.target_role:
                await interaction.response.send_message(
                    f"ℹ️ {member.mention} doesn't have the **{self.target_role.name}** role.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ No role configured for this gamemode.", ephemeral=True
                )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Bot lacks permission to remove roles. Make sure its role is ranked above the gamemode roles.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


class RemoveRoleView(discord.ui.View):
    """View shown after /submittest with a user-select to strip the player's gamemode role."""

    def __init__(self, target_role: discord.Role | None, already_removed: bool = False):
        super().__init__(timeout=300)
        self.add_item(RemoveRoleSelect(target_role, already_removed))


@tree.command(name="submittest", description="Submit a tier test result for a player")
@app_commands.describe(
    username="Minecraft username of the player being tested",
    tester_name="Name of the tester (e.g. Nethpot | MIR)",
    gamemode="Game mode used for the test (e.g. NethOP, Crystal, SMP)",
    tested_tier="Tier that was tested (e.g. HT1, LT3)",
    result="Result of the test",
    notes="Optional notes about the test",
)
@app_commands.choices(result=[
    app_commands.Choice(name="Passed", value="passed"),
    app_commands.Choice(name="Failed", value="failed"),
    app_commands.Choice(name="Voided", value="voided"),
])
@require_command_role("submittest")
async def submittest(
    interaction: discord.Interaction,
    username: str,
    tester_name: str,
    gamemode: str,
    tested_tier: str,
    result: app_commands.Choice[str],
    notes: str = "",
):
    tested_tier = tested_tier.upper().strip()
    if tested_tier not in TIERS:
        valid = ", ".join(TIERS)
        await interaction.response.send_message(
            f"❌ Invalid tier `{tested_tier}`. Valid tiers: {valid}", ephemeral=True
        )
        return

    data = load_data()
    key = username.lower()
    gm_key = gamemode.lower()
    rank_before = data["players"].get(key, {}).get(gm_key, {}).get("tier", "Unranked")

    test = {
        "id": len(data["tests"]) + 1,
        "username": username,
        "tester_name": tester_name,
        "gamemode": gamemode,
        "tested_tier": tested_tier,
        "rank_before": rank_before,
        "result": result.value,
        "notes": notes,
        "judged_by": str(interaction.user),
        "tested_at": datetime.datetime.utcnow().isoformat(),
    }
    data["tests"].append(test)

    if result.value == "passed":
        data["players"].setdefault(key, {})[gm_key] = {
            "tier": tested_tier,
            "updated_at": test["tested_at"],
            "updated_by": str(interaction.user),
        }

    save_data(data)

    # Remove gamemode role from the player after their test result is uploaded
    role_removed = None
    target_member = None
    target_role = None
    if interaction.guild:
        try:
            # Find the player's Discord ID by matching their Minecraft username in profiles
            discord_id = None
            for uid, profile in data.get("profiles", {}).items():
                if profile.get("minecraft_username", "").lower() == username.lower():
                    discord_id = int(uid)
                    break

            print(f"[submittest] username={username!r} gamemode={gamemode!r} discord_id={discord_id}")

            if discord_id:
                # Match gamemode case-insensitively against gamemode_roles keys
                gm_roles = data.get("gamemode_roles", {})
                matched_role_id = None
                for gm_name, rid in gm_roles.items():
                    if gm_name.lower() == gamemode.lower():
                        matched_role_id = rid
                        break

                print(f"[submittest] gamemode_roles keys={list(gm_roles.keys())} matched_role_id={matched_role_id}")

                # Resolve role — get_role uses cache; fall back to fetching all roles if needed
                if matched_role_id:
                    target_role = interaction.guild.get_role(int(matched_role_id))
                    if target_role is None:
                        all_roles = await interaction.guild.fetch_roles()
                        target_role = next((r for r in all_roles if r.id == int(matched_role_id)), None)

                    print(f"[submittest] target_role={target_role}")

                    # fetch_member makes an API call so it works even if not cached
                    try:
                        target_member = await interaction.guild.fetch_member(discord_id)
                    except discord.NotFound:
                        target_member = None

                    print(f"[submittest] target_member={target_member} roles={[r.name for r in target_member.roles] if target_member else None}")

                    if target_role and target_member and target_role in target_member.roles:
                        await target_member.remove_roles(target_role, reason=f"Test result submitted for {gamemode}")
                        role_removed = target_role.name
                        print(f"[submittest] Auto-removed role {target_role.name} from {username}")
            else:
                print(f"[submittest] No profile found for {username!r} — cannot auto-remove role")
        except discord.Forbidden:
            print(f"[submittest] Missing permission to remove gamemode role from {username}")
        except Exception as re_err:
            print(f"[submittest] Role removal error: {re_err}")

    color_map = {
        "passed": discord.Color.yellow(),
        "failed": discord.Color.yellow(),
        "voided": discord.Color.yellow(),
    }
    emoji_map = {"passed": "✅", "failed": "❌", "voided": "⬜"}
    rank_earned = tested_tier if result.value == "passed" else "—"

    embed = discord.Embed(
        title=f"{username} TEST RESULTS 🏆",
        color=color_map[result.value],
    )
    embed.add_field(name="Player Name", value=username, inline=False)
    embed.add_field(name="Tester Name", value=tester_name, inline=False)
    embed.add_field(name="Game Mode", value=gamemode, inline=False)
    embed.add_field(name="Rank Before", value=rank_before, inline=False)
    embed.add_field(name="Rank Earned", value=rank_earned, inline=False)
    if notes:
        embed.add_field(name="Notes", value=notes, inline=False)
    if role_removed:
        embed.add_field(name="Role Removed", value=f"🎭 **{role_removed}** removed", inline=False)
    embed.set_thumbnail(url=f"https://mc-heads.net/avatar/{username}/100")

    view = RemoveRoleView(
        target_role=target_role,
        already_removed=role_removed is not None,
    )
    await interaction.response.send_message(content=f"**{username}**", embed=embed, view=view)


@tree.command(name="history", description="View tier test history for a player")
@app_commands.describe(username="Minecraft username")
@require_command_role("history")
async def history(interaction: discord.Interaction, username: str):
    data = load_data()
    player_tests = [
        t for t in data["tests"]
        if t.get("username", t.get("usernamek", "")).lower() == username.lower()
    ]

    if not player_tests:
        await interaction.response.send_message(
            f"❌ No test history found for `{username}`.", ephemeral=True
        )
        return

    recent = player_tests[-5:][::-1]
    embed = discord.Embed(
        title=f"Test History — {username}",
        color=discord.Color.blurple(),
    )
    for t in recent:
        emoji = {"passed": "✅", "failed": "❌", "voided": "⬜"}.get(t["result"], "❓")
        date = t["tested_at"][:10]
        lines = [
            f"Tester: {t.get('tester_name', 'Unknown')}",
            f"Mode: {t.get('gamemode', 'Unknown')}",
            f"Rank Before: {t.get('rank_before', 'Unknown')} → Rank Earned: {t['tested_tier'] if t['result'] == 'passed' else '—'}",
        ]
        if t.get("notes"):
            lines.append(f"Notes: {t['notes']}")
        embed.add_field(
            name=f"{emoji} {t['tested_tier']} — {t['result'].capitalize()} ({date})",
            value="\n".join(lines),
            inline=False,
        )
    embed.set_footer(text=f"Showing last {len(recent)} of {len(player_tests)} tests")
    await interaction.response.send_message(embed=embed)


@tree.command(name="profile", description="View a player's full profile card with all tier rankings")
@app_commands.describe(member="The Discord user whose profile to view")
@require_command_role("profile")
async def profile_cmd(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer()
    data = load_data()

    prof = data.get("profiles", {}).get(str(member.id))
    if prof is None:
        await interaction.followup.send(
            f"❌ {member.mention} has no verified profile.", ephemeral=True
        )
        return

    mc_username  = prof.get("minecraft_username", member.name)
    region       = prof.get("region", "?")
    account_type = prof.get("account_type", "?")

    # Collect tiers: players dict (all known key formats)
    raw_tiers = {}
    raw_tiers.update(data["players"].get(mc_username.lower(), {}))
    raw_tiers.update(data["players"].get(mc_username, {}))
    raw_tiers.update(data["players"].get(f"<@{member.id}>", {}))
    tiers = {k: v for k, v in raw_tiers.items() if isinstance(v, dict) and "tier" in v}

    # Recover any passed tests not already reflected in the players dict
    # (handles legacy usernamek typo field, mention-keyed entries, etc.)
    key_variants = {mc_username.lower(), mc_username, f"<@{member.id}>"}
    for t in data.get("tests", []):
        if t.get("result") != "passed":
            continue
        test_user = (t.get("username") or t.get("usernamek") or "").strip()
        if test_user.lower() not in {v.lower() for v in key_variants} and test_user not in key_variants:
            continue
        gm = t.get("gamemode", "").lower()
        if not gm or gm in tiers:
            continue
        tiers[gm] = {
            "tier": t["tested_tier"],
            "updated_at": t.get("tested_at", ""),
            "updated_by": t.get("judged_by", ""),
        }

    try:
        buf  = await build_profile_card(mc_username, region, account_type, tiers)
        file = discord.File(buf, filename=f"{mc_username}_profile.png")
        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_image(url=f"attachment://{mc_username}_profile.png")
        await interaction.followup.send(file=file, embed=embed)
    except Exception as e:
        print(f"Profile card error: {e}")
        await interaction.followup.send("❌ Failed to generate the profile card.", ephemeral=True)


@tree.command(name="image", description="Upload a profile image shown on the website leaderboard")
@app_commands.describe(image="Upload your profile image (PNG, JPG, GIF, WebP)")
@require_command_role("image")
async def image_cmd(interaction: discord.Interaction, image: discord.Attachment):
    allowed_types = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
    if image.content_type not in allowed_types:
        await interaction.response.send_message(
            "❌ Please upload a valid image file (PNG, JPG, GIF, or WebP).", ephemeral=True
        )
        return

    data = load_data()
    user_key = str(interaction.user.id)
    profile = data.get("profiles", {}).get(user_key)

    if not profile:
        await interaction.response.send_message(
            "❌ You need to verify your profile first. Click **Verify Profile** in the testing panel.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    ext = image.content_type.split("/")[-1].replace("jpeg", "jpg")
    filename = f"{user_key}.{ext}"
    save_path = os.path.join("static", "skins", filename)
    os.makedirs(os.path.join("static", "skins"), exist_ok=True)

    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(image.url) as resp:
                if resp.status != 200:
                    raise Exception("Download failed")
                img_bytes = await resp.read()
        with open(save_path, "wb") as f:
            f.write(img_bytes)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to save image: {e}", ephemeral=True)
        return

    skin_url = f"/skins/{filename}"
    data["profiles"][user_key]["skin_url"] = skin_url
    save_data(data)

    # Push image to GitHub so Netlify can serve it
    loop = asyncio.get_event_loop()
    loop.create_task(_push_image_to_github(filename, img_bytes))

    mc_name = profile.get("minecraft_username", "your profile")
    embed = discord.Embed(
        title="✅ Profile Image Updated!",
        description=(
            f"Your image has been saved for **{mc_name}**.\n"
            f"It will now display on the website leaderboard."
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text="Use /image again any time to update it.")
    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="setimage", description="Set a profile image for any player on the website leaderboard")
@app_commands.describe(
    member="The Discord member to set the image for",
    image="The image to upload (PNG, JPG, GIF, WebP)",
)
@require_command_role("setimage")
async def setimage_cmd(interaction: discord.Interaction, member: discord.Member, image: discord.Attachment):
    allowed_types = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
    if image.content_type not in allowed_types:
        await interaction.response.send_message(
            "❌ Please upload a valid image file (PNG, JPG, GIF, or WebP).", ephemeral=True
        )
        return

    data = load_data()
    user_key = str(member.id)
    profile = data.get("profiles", {}).get(user_key)

    if not profile:
        await interaction.response.send_message(
            f"❌ **{member.display_name}** hasn't verified a profile yet. They need to click **Verify Profile** in the testing panel first.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    ext = image.content_type.split("/")[-1].replace("jpeg", "jpg")
    filename = f"{user_key}.{ext}"
    save_path = os.path.join("static", "skins", filename)
    os.makedirs(os.path.join("static", "skins"), exist_ok=True)

    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(image.url) as resp:
                if resp.status != 200:
                    raise Exception("Download failed")
                img_bytes = await resp.read()
        with open(save_path, "wb") as f:
            f.write(img_bytes)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to save image: {e}", ephemeral=True)
        return

    skin_url = f"/skins/{filename}"
    data["profiles"][user_key]["skin_url"] = skin_url
    save_data(data)

    # Push image to GitHub so Netlify can serve it
    loop = asyncio.get_event_loop()
    loop.create_task(_push_image_to_github(filename, img_bytes))

    mc_name = profile.get("minecraft_username", member.display_name)
    embed = discord.Embed(
        title="✅ Profile Image Set!",
        description=(
            f"Image saved for **{mc_name}** ({member.mention}).\n"
            f"It will now display on the website leaderboard."
        ),
        color=discord.Color.green(),
    )
    embed.set_image(url=image.url)
    embed.set_footer(text=f"Set by {interaction.user}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="setimageall", description="Apply one image to every player on the leaderboard (admin only)")
@app_commands.describe(image="The image to set for all players (PNG, JPG, GIF, WebP)")
@app_commands.checks.has_permissions(administrator=True)
async def setimageall_cmd(interaction: discord.Interaction, image: discord.Attachment):
    allowed_types = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
    if image.content_type not in allowed_types:
        await interaction.response.send_message(
            "❌ Please upload a valid image file (PNG, JPG, GIF, or WebP).", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    # Download the image once
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(image.url) as resp:
                if resp.status != 200:
                    raise Exception("Download failed")
                img_bytes = await resp.read()
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to download image: {e}", ephemeral=True)
        return

    data = load_data()
    profiles = data.get("profiles", {})

    if not profiles:
        await interaction.followup.send("❌ No players have profiles yet.", ephemeral=True)
        return

    ext = image.content_type.split("/")[-1].replace("jpeg", "jpg")
    os.makedirs(os.path.join("static", "skins"), exist_ok=True)

    updated = 0
    loop = asyncio.get_event_loop()

    for user_key in profiles:
        filename = f"{user_key}.{ext}"
        save_path = os.path.join("static", "skins", filename)
        try:
            with open(save_path, "wb") as f:
                f.write(img_bytes)
            data["profiles"][user_key]["skin_url"] = f"/skins/{filename}"
            loop.create_task(_push_image_to_github(filename, img_bytes))
            updated += 1
        except Exception:
            continue

    save_data(data)

    embed = discord.Embed(
        title="✅ Image Set for All Players",
        description=(
            f"The image has been applied to **{updated}** player profile(s) on the leaderboard.\n"
            f"It will appear on the website after GitHub syncs."
        ),
        color=discord.Color.green(),
    )
    embed.set_image(url=image.url)
    embed.set_footer(text=f"Set by {interaction.user}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="leaderboard", description="Show top players by tier for a specific gamemode")
@app_commands.describe(gamemode="The gamemode to show leaderboard for (e.g. Crystal, Sword)")
@require_command_role("leaderboard")
async def leaderboard(interaction: discord.Interaction, gamemode: str):
    data = load_data()
    gm_key = gamemode.lower()

    ranked = []
    for uname, gamemodes in data["players"].items():
        if isinstance(gamemodes, dict) and gm_key in gamemodes:
            gd = gamemodes[gm_key]
            if isinstance(gd, dict) and "tier" in gd:
                ranked.append({"username": uname, "tier": gd["tier"]})

    if not ranked:
        await interaction.response.send_message(f"No players ranked in **{gamemode}** yet.", ephemeral=True)
        return

    tier_rank = {t: i for i, t in enumerate(TIERS)}
    sorted_players = sorted(ranked, key=lambda p: tier_rank.get(p["tier"], len(TIERS)))

    embed = discord.Embed(title=f"🏆 {gamemode} Leaderboard", color=discord.Color.gold())
    lines = [f"`{i:>2}.` **{p['username']}** — {p['tier']}" for i, p in enumerate(sorted_players[:15], 1)]
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Total players in {gamemode}: {len(ranked)}")
    await interaction.response.send_message(embed=embed)


@tree.command(name="tierlist", description="Show all players grouped by tier for a specific gamemode")
@app_commands.describe(gamemode="The gamemode to show tier list for (e.g. Crystal, Sword)")
@require_command_role("tierlist")
async def tierlist(interaction: discord.Interaction, gamemode: str):
    data = load_data()
    gm_key = gamemode.lower()

    grouped: dict[str, list[str]] = {}
    for uname, gamemodes in data["players"].items():
        if isinstance(gamemodes, dict) and gm_key in gamemodes:
            gd = gamemodes[gm_key]
            if isinstance(gd, dict) and "tier" in gd:
                grouped.setdefault(gd["tier"], []).append(uname)

    if not grouped:
        await interaction.response.send_message(f"No players ranked in **{gamemode}** yet.", ephemeral=True)
        return

    embed = discord.Embed(title=f"📋 {gamemode} Tier List", color=discord.Color.blurple())
    for tier in TIERS:
        if tier in grouped:
            embed.add_field(name=f"Tier {tier}", value=", ".join(grouped[tier]), inline=False)
    await interaction.response.send_message(embed=embed)


@tree.command(name="remove", description="Remove a player from the website leaderboard by their position number")
@app_commands.describe(position="The player's rank position on the website leaderboard (e.g. 1, 2, 3)")
@require_command_role("remove")
async def remove_player(interaction: discord.Interaction, position: int):
    data = load_data()
    gamemodes = data.get("gamemodes", DEFAULT_GAMEMODES)
    profiles = data.get("profiles", {})

    # Build same ranking as the website api_players()
    id_to_profile = {}
    for v in profiles.values():
        if "discord_id" in v and "minecraft_username" in v:
            id_to_profile[v["discord_id"]] = v["minecraft_username"]

    discord_names = data.get("discord_names", {})

    def resolve_name(raw_key):
        if raw_key.startswith("<@") and raw_key.endswith(">"):
            uid = raw_key[2:-1]
            if uid in id_to_profile:
                return id_to_profile[uid]
            if uid in discord_names:
                return discord_names[uid]
            return None
        return raw_key

    player_scores = {}
    for raw_key, gm_data in data.get("players", {}).items():
        if not isinstance(gm_data, dict):
            continue
        name = resolve_name(raw_key)
        if not name:
            continue
        pts = gm_data.get("bonus_points", 0)
        for gm in gamemodes:
            gm_key = gm.lower()
            if gm_key in gm_data and isinstance(gm_data[gm_key], dict) and "tier" in gm_data[gm_key]:
                pts += TIER_POINTS.get(gm_data[gm_key]["tier"], 0)
        if name not in player_scores or pts > player_scores[name][1]:
            player_scores[name] = (raw_key, pts)

    sorted_players = sorted(player_scores.items(), key=lambda x: -x[1][1])

    if position < 1 or position > len(sorted_players):
        await interaction.response.send_message(
            f"❌ Position **{position}** is out of range. There are **{len(sorted_players)}** players on the leaderboard.",
            ephemeral=True,
        )
        return

    display_name, (raw_key, pts) = sorted_players[position - 1]

    del data["players"][raw_key]
    save_data(data)

    embed = discord.Embed(title="🗑️ Player Removed from Leaderboard", color=discord.Color.red())
    embed.add_field(name="Position", value=f"#{position}", inline=True)
    embed.add_field(name="Player", value=display_name, inline=True)
    embed.add_field(name="Points", value=f"{pts} pts", inline=True)
    embed.set_footer(text=f"Removed by {interaction.user}")
    await interaction.response.send_message(embed=embed)


@tree.command(name="removetier", description="Remove a player's tier for a specific gamemode from the leaderboard")
@app_commands.describe(
    username="Minecraft username of the player",
    gamemode="The gamemode to remove (e.g. Crystal, Sword, Axe)",
)
@require_command_role("removetier")
async def removetier(interaction: discord.Interaction, username: str, gamemode: str):
    data = load_data()
    gm_key = gamemode.lower()

    # Try to find the player key — could be stored as mc_username (lowercase)
    # or as <@discord_id> (resolved via profiles)
    player_key = None
    username_lower = username.lower()

    # 1. Direct key match (MC username stored directly)
    if username_lower in data.get("players", {}):
        player_key = username_lower
    else:
        # 2. Search profiles for a matching minecraft_username, then find their discord key
        profiles = data.get("profiles", {})
        discord_id = None
        for v in profiles.values():
            if v.get("minecraft_username", "").lower() == username_lower:
                discord_id = v.get("discord_id")
                break
        if discord_id:
            mention_key = f"<@{discord_id}>"
            if mention_key in data.get("players", {}):
                player_key = mention_key

    if player_key is None:
        await interaction.response.send_message(
            f"❌ No player found with username `{username}`. Check the spelling or use `/leaderboard` to find them.",
            ephemeral=True,
        )
        return

    player_data = data["players"][player_key]

    # Check the gamemode exists on this player
    if gm_key not in player_data or not isinstance(player_data.get(gm_key), dict) or "tier" not in player_data[gm_key]:
        gamemodes = data.get("gamemodes", DEFAULT_GAMEMODES)
        existing = [gm for gm in gamemodes if gm.lower() in player_data and isinstance(player_data.get(gm.lower()), dict) and "tier" in player_data[gm.lower()]]
        existing_str = ", ".join(f"`{g}`" for g in existing) if existing else "*(none)*"
        await interaction.response.send_message(
            f"❌ `{username}` has no `{gamemode}` tier to remove.\n**Their current tiers:** {existing_str}",
            ephemeral=True,
        )
        return

    old_tier = player_data[gm_key]["tier"]
    del data["players"][player_key][gm_key]
    save_data(data)

    embed = discord.Embed(
        title="🗑️ Gamemode Tier Removed",
        color=discord.Color.red(),
        timestamp=datetime.datetime.utcnow(),
    )
    embed.add_field(name="Player",    value=f"`{username}`",  inline=True)
    embed.add_field(name="Gamemode",  value=f"`{gamemode}`",  inline=True)
    embed.add_field(name="Tier Removed", value=f"`{old_tier}`", inline=True)
    embed.set_footer(text=f"Removed by {interaction.user}")
    await interaction.response.send_message(embed=embed)


@tree.command(name="panel", description="Post the testing panel with waitlist buttons")
@require_command_role("panel")
async def panel(interaction: discord.Interaction):
    data = load_data()
    gamemodes = data.get("gamemodes", DEFAULT_GAMEMODES)

    embed = discord.Embed(
        title="🎮 AFTERSHOCK TIERS | Testing Panel",
        description=(
            "Verify your profile or click any gamemode button below to join the waitlist.\n\n"
            "✅ **Step 1:** Click **Verify Profile** to set your Minecraft username, region, and account type\n"
            "🎮 **Step 2:** Click any **Gamemode Button** below to join that waitlist\n"
            "⏰ **Step 3:** Wait for a tester to ping you\n\n"
            "⚠️ **Important:** Each gamemode has a **5-day cooldown** after each test.\n"
            "🔓 Make sure your DMs are open to receive tester pings."
        ),
        color=discord.Color.purple(),
    )
    embed.set_footer(text=f"AFTERSHOCK TIERS | Today at {datetime.datetime.utcnow().strftime('%H:%M')}")

    view = PanelView(gamemodes)
    await interaction.response.send_message(embed=embed, view=view)


@tree.command(name="addgamemode", description="Add a gamemode to the testing panel")
@app_commands.describe(name="Gamemode name (e.g. Crystal)", emoji="Optional emoji for the button")
@require_command_role("addgamemode")
async def addgamemode(interaction: discord.Interaction, name: str, emoji: str = ""):
    data = load_data()
    gamemodes = data.setdefault("gamemodes", list(DEFAULT_GAMEMODES))

    if name in gamemodes:
        await interaction.response.send_message(f"❌ **{name}** is already in the panel.", ephemeral=True)
        return
    if len(gamemodes) >= 16:
        await interaction.response.send_message("❌ Maximum 16 gamemodes allowed.", ephemeral=True)
        return

    gamemodes.append(name)
    if emoji:
        GAMEMODE_EMOJIS[name] = emoji
    save_data(data)
    await interaction.response.send_message(
        f"✅ Added **{name}** to the panel. Run `/panel` again to post an updated panel.", ephemeral=True
    )


@tree.command(name="removegamemode", description="Remove a gamemode from the testing panel")
@app_commands.describe(name="Gamemode name to remove")
@require_command_role("removegamemode")
async def removegamemode(interaction: discord.Interaction, name: str):
    data = load_data()
    gamemodes = data.get("gamemodes", list(DEFAULT_GAMEMODES))

    if name not in gamemodes:
        await interaction.response.send_message(f"❌ **{name}** is not in the panel.", ephemeral=True)
        return

    gamemodes.remove(name)
    data["gamemodes"] = gamemodes
    save_data(data)
    await interaction.response.send_message(
        f"✅ Removed **{name}** from the panel. Run `/panel` again to post an updated panel.", ephemeral=True
    )


@tree.command(name="waitlist", description="View the waitlist for a gamemode")
@app_commands.describe(gamemode="The gamemode to check")
@require_command_role("waitlist")
async def waitlist_cmd(interaction: discord.Interaction, gamemode: str):
    data = load_data()
    queue = data.get("waitlist", {}).get(gamemode, [])
    profiles = data.get("profiles", {})

    if not queue:
        await interaction.response.send_message(f"📋 The **{gamemode}** waitlist is empty.", ephemeral=True)
        return

    lines = []
    for i, uid in enumerate(queue, 1):
        profile = profiles.get(uid)
        mc_name = profile["minecraft_username"] if profile else f"<@{uid}>"
        region = profile.get("region", "?") if profile else "?"
        lines.append(f"`{i}.` **{mc_name}** ({region}) — <@{uid}>")

    embed = discord.Embed(
        title=f"📋 {gamemode} Waitlist — {len(queue)} player(s)",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="clearwaitlist", description="Clear the waitlist for a gamemode")
@app_commands.describe(gamemode="The gamemode to clear")
@require_command_role("clearwaitlist")
async def clearwaitlist(interaction: discord.Interaction, gamemode: str):
    data = load_data()
    if gamemode not in data.get("waitlist", {}):
        await interaction.response.send_message(f"❌ No waitlist found for **{gamemode}**.", ephemeral=True)
        return

    data["waitlist"][gamemode] = []
    save_data(data)
    await interaction.response.send_message(f"✅ Cleared the **{gamemode}** waitlist.", ephemeral=True)
    if interaction.guild:
        try:
            await update_waitlist_channel(interaction.guild, gamemode, data)
        except Exception as e:
            print(f"Channel update error on clear: {e}")


@tree.command(name="clearallwaitlists", description="Clear every gamemode waitlist at once")
@require_command_role("clearallwaitlists")
async def clearallwaitlists(interaction: discord.Interaction):
    data = load_data()
    waitlist = data.get("waitlist", {})

    if not waitlist:
        await interaction.response.send_message("📋 All waitlists are already empty.", ephemeral=True)
        return

    cleared = [gm for gm, q in waitlist.items() if q]
    for gm in cleared:
        data["waitlist"][gm] = []
    save_data(data)

    await interaction.response.send_message(
        f"✅ Cleared **{len(cleared)}** waitlist(s): {', '.join(f'**{g}**' for g in cleared)}",
        ephemeral=True,
    )

    if interaction.guild:
        for gm in cleared:
            try:
                await update_waitlist_channel(interaction.guild, gm, data)
            except Exception as e:
                print(f"Channel update error on clearall ({gm}): {e}")


@tree.command(name="nexttester", description="Ping the next player in a gamemode waitlist and remove them from the queue")
@app_commands.describe(gamemode="The gamemode to call next from")
@require_command_role("nexttester")
async def nexttester(interaction: discord.Interaction, gamemode: str):
    data = load_data()
    queue = data.get("waitlist", {}).get(gamemode, [])

    if not queue:
        await interaction.response.send_message(f"📋 The **{gamemode}** waitlist is empty.", ephemeral=True)
        return

    next_uid = queue.pop(0)
    data["waitlist"][gamemode] = queue
    save_data(data)

    profile = data.get("profiles", {}).get(next_uid)
    mc_name = profile["minecraft_username"] if profile else "Unknown"
    region = profile.get("region", "?") if profile else "?"
    account_type = profile.get("account_type", "?") if profile else "?"

    embed = discord.Embed(
        title=f"🎮 {gamemode} — Next Player Called!",
        color=discord.Color.green(),
    )
    embed.add_field(name="Minecraft Name", value=mc_name, inline=True)
    embed.add_field(name="Region", value=region, inline=True)
    embed.add_field(name="Account Type", value=account_type, inline=True)
    embed.add_field(name="Players Remaining", value=str(len(queue)), inline=False)
    embed.set_thumbnail(url=f"https://mc-heads.net/avatar/{mc_name}/64")
    embed.set_footer(text=f"Called by {interaction.user}")

    await interaction.response.send_message(
        content=f"<@{next_uid}> — you're up for **{gamemode}** testing! 🏆",
        embed=embed,
    )

    if interaction.guild:
        try:
            await update_waitlist_channel(interaction.guild, gamemode, data)
        except Exception as e:
            print(f"Channel update error on nexttester: {e}")


@tree.command(name="setwaitlistcategory", description="Set the category where waitlist channels are created")
@app_commands.describe(category="The category to place waitlist channels in")
@require_command_role("setwaitlistcategory")
async def setwaitlistcategory(interaction: discord.Interaction, category: discord.CategoryChannel):
    data = load_data()
    data["waitlist_category_id"] = str(category.id)
    save_data(data)
    await interaction.response.send_message(
        f"✅ Waitlist channels will now be created under **{category.name}**.",
        ephemeral=True,
    )


def build_queue_embed(gamemode: str, tester: discord.Member, members: list, closed: bool = False) -> discord.Embed:
    color = discord.Color.red() if closed else discord.Color.green()
    status = "🔴 CLOSED" if closed else "🟢 OPEN"
    title = f"🎮 {gamemode} Queue — {status}"
    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="Tester", value=str(tester), inline=True)
    embed.add_field(name="Gamemode", value=gamemode, inline=True)
    if members:
        lines = [f"`{i}.` <@{m}>" for i, m in enumerate(members, 1)]
        embed.add_field(name=f"Players in Queue ({len(members)})", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Players in Queue (0)", value="*Nobody yet — click Join!*", inline=False)
    if not closed:
        embed.set_footer(text="Queue closes in 3 minutes • Click Join to enter, Leave to exit")
    else:
        embed.set_footer(text="This queue has closed.")
    return embed


class QueueView(discord.ui.View):
    def __init__(self, gamemode: str, tester: discord.Member, message_ref: list, channel_id: int):
        super().__init__(timeout=QUEUE_TIMEOUT_SECONDS)
        self.gamemode = gamemode
        self.tester = tester
        self.members: list[int] = []
        self.message_ref = message_ref
        self.channel_id = channel_id

    async def _update(self, interaction: discord.Interaction):
        embed = build_queue_embed(self.gamemode, self.tester, self.members)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _update_silent(self):
        msg = self.message_ref[0] if self.message_ref else None
        if msg:
            try:
                embed = build_queue_embed(self.gamemode, self.tester, self.members)
                await msg.edit(embed=embed, view=self)
            except Exception:
                pass

    @discord.ui.button(label="Join", emoji="✅", style=discord.ButtonStyle.success, custom_id="queue_join")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid in self.members:
            await interaction.response.send_message("⏳ You're already in this queue!", ephemeral=True)
            return
        self.members.append(uid)
        await self._update(interaction)

    @discord.ui.button(label="Leave", emoji="❌", style=discord.ButtonStyle.danger, custom_id="queue_leave")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid not in self.members:
            await interaction.response.send_message("❌ You're not in this queue.", ephemeral=True)
            return
        self.members.remove(uid)
        await self._update(interaction)

    async def on_timeout(self):
        active_queues.pop(self.channel_id, None)
        for item in self.children:
            item.disabled = True
        embed = build_queue_embed(self.gamemode, self.tester, self.members, closed=True)
        msg = self.message_ref[0] if self.message_ref else None
        if msg:
            try:
                await msg.edit(embed=embed, view=self)
            except Exception:
                pass


@tree.command(name="queue", description="Open a live queue for a gamemode (3 minutes)")
@app_commands.describe(gamemode="The gamemode you are testing")
@require_command_role("queue")
async def queue_cmd(interaction: discord.Interaction, gamemode: str):
    channel_id = interaction.channel_id
    if channel_id in active_queues:
        await interaction.response.send_message(
            "❌ There's already an active queue in this channel. Wait for it to close first.",
            ephemeral=True,
        )
        return
    message_ref: list = []
    view = QueueView(gamemode, interaction.user, message_ref, channel_id)
    embed = build_queue_embed(gamemode, interaction.user, [])
    await interaction.response.send_message(embed=embed, view=view)
    msg = await interaction.original_response()
    message_ref.append(msg)
    active_queues[channel_id] = view


@tree.command(name="kickfromqueue", description="Remove a player from the active queue in this channel")
@app_commands.describe(player="The Discord member to remove from the queue")
@require_command_role("kickfromqueue")
async def kickfromqueue(interaction: discord.Interaction, player: discord.Member):
    channel_id = interaction.channel_id
    view = active_queues.get(channel_id)

    if not view:
        await interaction.response.send_message(
            "❌ There's no active queue in this channel.", ephemeral=True
        )
        return

    if player.id not in view.members:
        await interaction.response.send_message(
            f"❌ **{player.display_name}** is not in the current queue.", ephemeral=True
        )
        return

    view.members.remove(player.id)
    await view._update_silent()
    await interaction.response.send_message(
        f"✅ **{player.display_name}** has been removed from the **{view.gamemode}** queue.",
        ephemeral=True,
    )


@tree.command(name="setrole", description="Assign a Discord role to a bot command (admin only)")
@app_commands.describe(command="The command to assign a role to", role="The role that can use this command")
@app_commands.checks.has_permissions(administrator=True)
async def setrole(interaction: discord.Interaction, command: str, role: discord.Role):
    if command not in STAFF_COMMANDS:
        valid = ", ".join(f"`{c}`" for c in STAFF_COMMANDS)
        await interaction.response.send_message(
            f"❌ Unknown command **/{command}**. Valid commands:\n{valid}", ephemeral=True
        )
        return
    data = load_data()
    data.setdefault("command_roles", {})[command] = str(role.id)
    save_data(data)
    await interaction.response.send_message(
        f"✅ **/{command}** is now restricted to members with the **{role.name}** role.",
        ephemeral=True,
    )


@setrole.autocomplete("command")
async def setrole_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=c, value=c)
        for c in STAFF_COMMANDS
        if current.lower() in c.lower()
    ][:25]


@tree.command(name="viewroles", description="View all current command role assignments (admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def viewroles(interaction: discord.Interaction):
    data = load_data()
    command_roles = data.get("command_roles", {})

    embed = discord.Embed(title="🔐 Command Role Assignments", color=discord.Color.blurple())
    lines = []
    for cmd in STAFF_COMMANDS:
        role_id = command_roles.get(cmd)
        if role_id:
            role = interaction.guild.get_role(int(role_id))
            role_text = f"**{role.name}**" if role else f"*(deleted role)*"
        else:
            role_text = "*not set — admins/owner only*"
        lines.append(f"`/{cmd}` → {role_text}")
    embed.description = "\n".join(lines)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="setgamerole", description="Map a gamemode to a Discord role — auto-assigned when players join that waitlist (admin only)")
@app_commands.describe(
    gamemode="Gamemode name exactly as shown on the panel (e.g. Sword, Axe, Crystal)",
    role="The Discord role to auto-assign",
)
@app_commands.checks.has_permissions(administrator=True)
async def setgamerole(interaction: discord.Interaction, gamemode: str, role: discord.Role):
    data = load_data()
    data.setdefault("gamemode_roles", {})[gamemode] = str(role.id)
    save_data(data)
    await interaction.response.send_message(
        f"✅ Players who click **{gamemode}** on the panel will now automatically receive the **{role.name}** role.",
        ephemeral=True,
    )


@tree.command(name="viewgameroles", description="View all gamemode → role auto-assignments (admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def viewgameroles(interaction: discord.Interaction):
    data = load_data()
    gamemode_roles = data.get("gamemode_roles", {})
    embed = discord.Embed(
        title="🎭 Gamemode Role Auto-Assignments",
        description="Roles automatically given when a player clicks a gamemode on the panel.",
        color=discord.Color.gold(),
    )
    if not gamemode_roles:
        embed.description = "No gamemode roles set yet. Use `/setgamerole` to add one."
    else:
        lines = []
        for gm, role_id in gamemode_roles.items():
            role = interaction.guild.get_role(int(role_id))
            role_text = f"**{role.name}**" if role else "*(deleted role)*"
            lines.append(f"🎮 **{gm}** → {role_text}")
        embed.description = "\n".join(lines)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@setgamerole.error
async def setgamerole_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You need **Administrator** permission to use this command.", ephemeral=True)


@viewgameroles.error
async def viewgameroles_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You need **Administrator** permission to use this command.", ephemeral=True)


@tree.command(name="removeplayerrole", description="Remove a gamemode role from a player")
@app_commands.describe(
    gamemode="The gamemode whose role you want to remove (e.g. Sword, Axe, Crystal)",
    member="The Discord member to remove the role from",
)
@require_command_role("removeplayerrole")
async def removeplayerrole(interaction: discord.Interaction, gamemode: str, member: discord.Member):
    data = load_data()
    gm_roles = data.get("gamemode_roles", {})

    # Match gamemode case-insensitively
    matched_role_id = None
    matched_gm_name = None
    for gm_name, rid in gm_roles.items():
        if gm_name.lower() == gamemode.lower():
            matched_role_id = rid
            matched_gm_name = gm_name
            break

    if not matched_role_id:
        configured = ", ".join(f"**{g}**" for g in gm_roles) or "none"
        await interaction.response.send_message(
            f"❌ No role is configured for gamemode **{gamemode}**.\n"
            f"Configured gamemodes: {configured}\n"
            f"Use `/setgamerole` to set one.",
            ephemeral=True,
        )
        return

    role = interaction.guild.get_role(int(matched_role_id))
    if role is None:
        # Role may have been deleted from the server
        await interaction.response.send_message(
            f"❌ The role configured for **{matched_gm_name}** no longer exists in this server. "
            f"Use `/setgamerole` to reconfigure it.",
            ephemeral=True,
        )
        return

    if role not in member.roles:
        await interaction.response.send_message(
            f"ℹ️ {member.mention} doesn't have the **{role.name}** role.",
            ephemeral=True,
        )
        return

    try:
        await member.remove_roles(role, reason=f"/removeplayerrole used by {interaction.user} for {matched_gm_name}")
        embed = discord.Embed(
            title="🎭 Role Removed",
            color=discord.Color.green(),
        )
        embed.add_field(name="Player", value=member.mention, inline=True)
        embed.add_field(name="Gamemode", value=matched_gm_name, inline=True)
        embed.add_field(name="Role Removed", value=f"**{role.name}**", inline=True)
        embed.set_footer(text=f"Removed by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I don't have permission to remove that role. Make sure my role is ranked **above** the gamemode roles in Server Settings → Roles.",
            ephemeral=True,
        )


@removeplayerrole.error
async def removeplayerrole_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You need **Administrator** permission to use this command.", ephemeral=True)


@tree.command(name="website", description="Get the Aftershock Tiers public leaderboard link")
@require_command_role("website")
async def website_cmd(interaction: discord.Interaction):
    if not WEBSITE_URL:
        await interaction.response.send_message("❌ Website URL not configured.", ephemeral=True)
        return
    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="🏆 Aftershock Tiers Leaderboard",
        url=WEBSITE_URL,
        style=discord.ButtonStyle.link
    ))
    embed = discord.Embed(
        title="🌐 Aftershock Tiers — Public Leaderboard",
        description=f"View live tier rankings for all gamemodes:\n{WEBSITE_URL}",
        color=discord.Color.from_rgb(255, 165, 0)
    )
    await interaction.response.send_message(embed=embed, view=view)


@tree.command(name="syncwebsite", description="Manually push website files to GitHub so Netlify updates")
@require_command_role("syncwebsite")
async def syncwebsite_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        await _push_website_to_github()
        await _push_data_to_github()
        embed = discord.Embed(
            title="✅ Website Synced to GitHub",
            description="All website files and player data have been pushed to GitHub.\nNetlify will redeploy in ~1–2 minutes.",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Sync failed: {e}", ephemeral=True)


def _resolve_player_key(data: dict, member_id: int, mc_username: str | None) -> str:
    """Find the existing key in data['players'] for this member, or return the best new key.
    Priority: <@discord_id> → mc_username (any case) → mc_username.lower()
    This prevents duplicate entries when bonus points are applied."""
    players = data.get("players", {})
    mention_key = f"<@{member_id}>"
    if mention_key in players:
        return mention_key
    if mc_username:
        lower = mc_username.lower()
        if lower in players:
            return lower
        for k in players:
            if k.lower() == lower:
                return k
        return mention_key
    return mention_key


@tree.command(name="pointsto", description="Give (or remove) bonus points to a player")
@app_commands.describe(
    member="The Discord member to give points to",
    amount="Points to give (use negative to remove, e.g. -5)",
    reason="Optional reason for the points",
)
@require_command_role("pointsto")
async def pointsto(interaction: discord.Interaction, member: discord.Member, amount: int, reason: str = ""):
    data = load_data()
    profile = data.get("profiles", {}).get(str(member.id))
    mc_username = profile.get("minecraft_username") if profile else None
    display_name = mc_username or member.display_name
    key = _resolve_player_key(data, member.id, mc_username)

    current = data["players"].setdefault(key, {}).get("bonus_points", 0)
    new_total = current + amount
    data["players"][key]["bonus_points"] = new_total
    save_data(data)

    color = discord.Color.green() if amount >= 0 else discord.Color.red()
    sign = "+" if amount >= 0 else ""
    embed = discord.Embed(title="⭐ Bonus Points Updated", color=color)
    embed.add_field(name="Player", value=f"{member.mention} ({display_name})", inline=False)
    embed.add_field(name="Change", value=f"{sign}{amount} pts", inline=True)
    embed.add_field(name="New Bonus Total", value=f"{new_total} pts", inline=True)
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"Set by {interaction.user}")
    await interaction.response.send_message(embed=embed)


@tree.command(name="point", description="Add or deduct points from a player's website leaderboard score")
@app_commands.describe(
    member="The Discord member to update points for",
    amount="Points to add (positive) or deduct (negative), e.g. 5 or -3",
    reason="Optional reason shown in the embed",
)
@require_command_role("point")
async def point_cmd(interaction: discord.Interaction, member: discord.Member, amount: int, reason: str = ""):
    data = load_data()
    profile = data.get("profiles", {}).get(str(member.id))
    mc_username = profile.get("minecraft_username") if profile else None
    display_name = mc_username or member.display_name
    key = _resolve_player_key(data, member.id, mc_username)

    player_data = data["players"].setdefault(key, {})
    old_bonus = player_data.get("bonus_points", 0)
    new_bonus = old_bonus + amount
    player_data["bonus_points"] = new_bonus
    save_data(data)

    # Calculate total score (tier pts + new bonus) to show website-accurate number
    tier_pts = sum(
        TIER_POINTS.get(v["tier"], 0)
        for k, v in player_data.items()
        if isinstance(v, dict) and "tier" in v
    )
    old_total = tier_pts + old_bonus
    new_total = tier_pts + new_bonus

    def _rank_label(pts):
        for min_pts, label, _bg, _fg in OVERALL_RANKS:
            if pts >= min_pts:
                return label
        return "Unranked"

    color = discord.Color.green() if amount >= 0 else discord.Color.red()
    sign = "+" if amount >= 0 else ""
    action = "added to" if amount >= 0 else "deducted from"

    embed = discord.Embed(
        title=f"{'⬆️' if amount >= 0 else '⬇️'} Points {action.split()[0].capitalize()} — {display_name}",
        color=color,
    )
    embed.add_field(name="Player", value=f"{member.mention}", inline=True)
    embed.add_field(name="Change", value=f"`{sign}{amount} pts`", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(name="Before", value=f"`{old_total} pts`  ({_rank_label(old_total)})", inline=True)
    embed.add_field(name="After (website)", value=f"`{new_total} pts`  ({_rank_label(new_total)})", inline=True)
    embed.add_field(name="Bonus Total", value=f"`{new_bonus} pts`", inline=True)
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Updated by {interaction.user} • Rank thresholds: Rookie 1+ | Combat Cadet 75+ | Combat Specialist 175+ | Combat Ace 300+ | Combat Master 500+ | Conquered 700+")
    await interaction.response.send_message(embed=embed)


@settier.error
@submittest.error
@remove_player.error
@panel.error
@addgamemode.error
@removegamemode.error
@clearwaitlist.error
@clearallwaitlists.error
@nexttester.error
@setwaitlistcategory.error
@queue_cmd.error
@kickfromqueue.error
@leaderboard.error
@profile_cmd.error
@pointsto.error
@setimage_cmd.error
@point_cmd.error
async def staff_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, (app_commands.CheckFailure, app_commands.MissingPermissions)):
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True
            )


@setrole.error
@viewroles.error
async def admin_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ You need the **Administrator** permission to use this command.", ephemeral=True
        )


import threading
from flask import Flask, jsonify

web_app = Flask(__name__, static_folder='website/static')
web_app.json.sort_keys = False

LEADERBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Aftershock Tiers — Leaderboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg:        #09090f;
      --surface:   #111118;
      --surface2:  #18181f;
      --surface3:  #1e1e28;
      --border:    rgba(99,59,196,0.18);
      --border2:   rgba(99,59,196,0.35);
      --text:      #e8ecff;
      --muted:     #6b738f;
      --gold:      #f5c842;
      --silver:    #b0bcd4;
      --bronze:    #c87840;
      --rank1-bg:  #c8960a;
      --rank2-bg:  #5a7090;
      --rank3-bg:  #8c6030;
      --blue:      #5b8dee;
      --green:     #3cde7e;
      --red:       #e05050;
      --violet:    #7c3aed;
      --violet-lt: #a78bfa;
      --indigo:    #312e81;
      --mono:      'Space Mono', monospace;
      --sans:      'Inter', system-ui, sans-serif;
      --radius:    12px;
      --radius-lg: 14px;
    }

    body {
      background: var(--bg);
      color: var(--text);
      font-family: var(--sans);
      min-height: 100vh;
      background-image:
        linear-gradient(105deg, rgba(0,0,0,0) 40px, rgba(124,58,237,0.05) 40px, rgba(124,58,237,0.05) 42px, rgba(0,0,0,0) 42px),
        linear-gradient(105deg, rgba(0,0,0,0) 110px, rgba(124,58,237,0.03) 110px, rgba(124,58,237,0.03) 112px, rgba(0,0,0,0) 112px),
        linear-gradient(105deg, rgba(0,0,0,0) 190px, rgba(124,58,237,0.05) 190px, rgba(124,58,237,0.05) 192px, rgba(0,0,0,0) 192px);
      background-size: 240px 400px;
    }

    /* ── HEADER ── */
    .site-header {
      background: linear-gradient(rgba(9,9,15,0.55), rgba(9,9,15,0.55)), url('/static/bg.gif') center/cover no-repeat;
      border: 1px solid var(--border);
      padding: 10px 1.25rem;
      position: sticky;
      top: 0;
      z-index: 200;
      backdrop-filter: blur(14px);
      box-shadow: 0 8px 32px rgba(109,40,217,0.18);
      border-radius: 0 0 18px 18px;
      margin: 0 10px;
    }
    .header-inner {
      max-width: 920px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      gap: 14px;
      height: 72px;
    }
    .logo {
      height: 44px;
      width: auto;
      flex-shrink: 0;
      object-fit: contain;
    }
    .nav-links {
      display: flex;
      align-items: center;
      gap: 2px;
      margin-left: 16px;
      flex-shrink: 0;
    }
    .nav-link {
      display: flex; align-items: center; gap: 5px;
      padding: 6px 10px;
      font-size: 14px; font-weight: 500;
      color: var(--muted);
      border-radius: 8px;
      text-decoration: none;
      transition: color 0.15s, background 0.15s;
      white-space: nowrap;
    }
    .nav-link.active { color: var(--violet-lt); }
    .nav-link:hover { background: rgba(124,58,237,0.08); color: var(--violet-lt); }
    .search-wrap { flex: 1; position: relative; }
    .search-wrap svg {
      position: absolute; left: 12px; top: 50%; transform: translateY(-50%);
      width: 16px; height: 16px; color: var(--muted); pointer-events: none;
    }
    .search-input {
      width: 100%;
      background: rgba(124,58,237,0.06);
      border: 1px solid var(--border2);
      border-radius: 999px;
      padding: 8px 16px 8px 36px;
      font-family: var(--sans);
      font-size: 13px;
      color: var(--text);
      outline: none;
      transition: border-color 0.15s, box-shadow 0.15s;
    }
    .search-input::placeholder { color: var(--muted); }
    .search-input:focus { border-color: rgba(124,58,237,0.5); box-shadow: 0 0 0 3px rgba(124,58,237,0.1); }
    .search-kbd {
      position: absolute; right: 12px; top: 50%; transform: translateY(-50%);
      background: var(--surface2); border: 1px solid var(--border2);
      color: var(--muted); font-size: 11px; font-family: var(--mono);
      padding: 2px 6px; border-radius: 5px; pointer-events: none;
    }

    /* ── TABS ── */
    .tabs-wrap {
      background: rgba(9,9,15,0.95);
      border-bottom: 1px solid var(--border);
      padding: 0 1rem;
      overflow-x: auto;
      scrollbar-width: none;
      margin-top: 3rem;
    }
    .tabs-wrap::-webkit-scrollbar { display: none; }
    .tabs-inner {
      max-width: 920px; margin: 0 auto;
      display: flex; gap: 8px; align-items: flex-end;
      padding: 8px 0 0;
      position: relative;
    }
    @keyframes gold-shine {
      0%   { color: #b8860b; text-shadow: 0 0 8px rgba(184,134,11,0.4); }
      50%  { color: #ffd700; text-shadow: 0 0 24px rgba(255,215,0,0.9), 0 0 48px rgba(255,215,0,0.4); }
      100% { color: #b8860b; text-shadow: 0 0 8px rgba(184,134,11,0.4); }
    }
    .season-badge {
      margin-left: auto;
      font-size: 11px; font-weight: 900; font-style: italic;
      letter-spacing: 0.1em; text-transform: uppercase;
      padding: 4px 10px; border-radius: 6px;
      background: rgba(184,134,11,0.12); border: 1px solid rgba(184,134,11,0.3);
      white-space: nowrap; align-self: center;
      animation: gold-shine 2.4s ease-in-out infinite;
    }
    .tab {
      display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px;
      padding: 10px 0;
      width: 97px;
      min-width: 97px;
      font-size: 11px; font-weight: 700; color: var(--muted);
      cursor: pointer;
      border-radius: 14px 14px 0 0;
      background: rgba(255,255,255,0.05);
      white-space: nowrap;
      transition: color 0.15s, background 0.15s;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      user-select: none;
      border-bottom: none;
      margin-bottom: 0;
    }
    .tab:hover { color: var(--violet-lt); background: rgba(255,255,255,0.08); }
    .tab.active { color: #fff; background: rgba(138,99,210,0.22); box-shadow: 0 0 0 1.5px rgba(138,99,210,0.5); }
    .tab-icon {
      width: 28px; height: 28px;
      object-fit: contain;
      display: block;
      flex-shrink: 0;
    }

    /* ── SERVER BANNER ── */
    .server-banner {
      max-width: 920px;
      margin: 4px auto 0;
      padding: 10px 1rem;
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .info-btn {
      display: flex; align-items: center; gap: 6px;
      background: var(--surface2);
      border: 1px solid var(--border2);
      border-radius: 999px;
      padding: 6px 14px;
      font-size: 12px; font-weight: 600; color: var(--text);
      cursor: pointer;
      transition: border-color 0.15s, background 0.15s;
      position: relative;
    }
    .info-btn:hover { border-color: var(--violet-lt); background: var(--surface3,#23263a); }
    .info-btn svg { width: 14px; height: 14px; color: var(--violet-lt); }

    /* ── Info Panel ── */
    .info-panel-wrap { position: relative; }
    .info-panel {
      display: none;
      position: absolute;
      top: calc(100% + 10px);
      left: 0;
      z-index: 999;
      width: 340px;
      background: var(--surface2);
      border: 1px solid var(--border2);
      border-radius: 14px;
      box-shadow: 0 8px 32px rgba(0,0,0,0.45);
      overflow: hidden;
      animation: fadeDown 0.18s ease;
    }
    .info-panel.open { display: block; }
    @keyframes fadeDown { from { opacity:0; transform:translateY(-6px); } to { opacity:1; transform:translateY(0); } }
    .info-tabs {
      display: flex;
      background: var(--bg);
      border-bottom: 1px solid var(--border2);
      border-radius: 10px 10px 0 0;
      padding: 6px;
      gap: 4px;
    }
    .info-tab {
      flex: 1; text-align: center;
      padding: 8px 0;
      border-radius: 8px;
      font-size: 13px; font-weight: 600; color: var(--muted);
      cursor: pointer;
      transition: background 0.15s, color 0.15s;
      background: transparent; border: none;
    }
    .info-tab.active { background: var(--surface2); color: var(--text); }
    .info-tab:not(.active):hover { color: var(--text); background: var(--surface3,#23263a); }
    .info-body { padding: 18px 18px 14px; max-height: 420px; overflow-y: auto; }
    .info-body h3 {
      font-size: 15px; font-weight: 700; color: var(--text);
      margin: 0 0 16px;
    }
    .info-body h3 u { text-decoration-color: var(--violet-lt); }
    .info-tab-content { display: none; }
    .info-tab-content.active { display: block; }

    /* Titles tab */
    .title-item { display: flex; flex-direction: column; margin-bottom: 14px; }
    .title-item-name {
      display: flex; align-items: center; gap: 8px;
      font-size: 14px; font-weight: 700;
    }
    .title-item-desc { font-size: 12px; color: var(--muted); margin-top: 2px; padding-left: 26px; }
    .title-icon { font-size: 18px; line-height:1; }

    /* Points tab */
    .pts-tier { margin-bottom: 18px; }
    .pts-tier-name {
      display: flex; align-items: center; gap: 8px;
      font-size: 15px; font-weight: 700; color: var(--text);
      margin-bottom: 8px;
    }
    .pts-tier-bar {
      border-left: 3px solid var(--border2);
      padding-left: 12px;
      display: flex; gap: 8px; flex-wrap: wrap;
    }
    .pts-badge {
      display: flex; align-items: center; gap: 5px;
      background: rgba(255,255,255,0.07);
      border-radius: 999px;
      padding: 5px 13px;
      font-size: 12px; font-weight: 600; color: var(--text);
    }
    .pts-badge.ht { background: rgba(240,168,0,0.15); color: #f0c040; }
    .pts-badge.lt { background: rgba(255,255,255,0.06); color: var(--muted); }
    .pts-t1 .pts-tier-name { color: #f0c040; }
    .pts-t1 .pts-tier-bar { border-color: #f0c040; }
    .pts-t2 .pts-tier-name { color: #b0b8c8; }
    .pts-t2 .pts-tier-bar { border-color: #b0b8c8; }
    .pts-t3 .pts-tier-name { color: #c87840; }
    .pts-t3 .pts-tier-bar { border-color: #c87840; }
    .server-ip-wrap { display: flex; align-items: center; gap: 8px; margin-left: auto; }
    .pvp-badge {
      height: 72px;
      width: auto;
      border-radius: 8px;
      object-fit: contain;
    }
    .server-ip-label { font-size: 10px; font-weight: 700; color: var(--violet-lt); letter-spacing: 0.1em; text-transform: uppercase; }
    .server-ip-val {
      font-family: var(--mono);
      font-size: 12px; font-weight: 700; color: var(--text);
      background: var(--surface2);
      border: 1px solid var(--border2);
      border-radius: 8px;
      padding: 4px 10px;
      display: flex; align-items: center; gap: 6px;
      cursor: pointer;
      transition: border-color 0.15s;
    }
    .server-ip-val:hover { border-color: var(--violet-lt); }
    .discord-btn {
      background: #5865F2; border-radius: 8px;
      width: 28px; height: 28px;
      display: flex; align-items: center; justify-content: center;
      cursor: pointer; text-decoration: none;
      transition: opacity 0.15s;
    }
    .discord-btn:hover { opacity: 0.85; }
    .discord-btn svg { width: 16px; height: 16px; fill: #fff; }

    /* ── TABLE HEADER ── */
    .table-header {
      max-width: 920px; margin: 0 auto;
      padding: 4px 1rem 6px;
      display: grid;
      grid-template-columns: 140px 1fr 80px;
      font-size: 10px; font-weight: 700; color: var(--muted);
      letter-spacing: 0.12em; text-transform: uppercase;
    }
    .table-header-gm {
      max-width: 920px; margin: 0 auto;
      padding: 4px 1rem 6px;
      display: grid;
      grid-template-columns: 60px 1fr 100px 100px;
      font-size: 10px; font-weight: 700; color: var(--muted);
      letter-spacing: 0.12em; text-transform: uppercase;
    }

    /* ── BOARD ── */
    .cards-wrap {
      max-width: 460px; margin: 0 auto;
      padding: 0 1.5rem 5rem;
      display: flex; flex-direction: column; gap: 14px;
    }

    /* ── DESKTOP TABLE HEADER ── */
    .desktop-table-header {
      display: none;
    }

    /* ── OVERALL PLAYER CARD ── */
    @keyframes gold-anim {
      0%   { border-color: #b8860b; box-shadow: 0 0 14px rgba(200,150,10,0.55), 0 0 36px rgba(200,150,10,0.22); }
      50%  { border-color: #f5c842; box-shadow: 0 0 24px rgba(245,200,66,0.75), 0 0 54px rgba(245,200,66,0.28); }
      100% { border-color: #b8860b; box-shadow: 0 0 14px rgba(200,150,10,0.55), 0 0 36px rgba(200,150,10,0.22); }
    }
    @keyframes silver-anim {
      0%   { border-color: #7a8fa0; box-shadow: 0 0 14px rgba(122,143,160,0.55); }
      50%  { border-color: #c0cedd; box-shadow: 0 0 24px rgba(192,206,221,0.75); }
      100% { border-color: #7a8fa0; box-shadow: 0 0 14px rgba(122,143,160,0.55); }
    }
    @keyframes bronze-anim {
      0%   { border-color: #7a4818; box-shadow: 0 0 14px rgba(140,90,40,0.55); }
      50%  { border-color: #c87840; box-shadow: 0 0 24px rgba(200,120,64,0.75); }
      100% { border-color: #7a4818; box-shadow: 0 0 14px rgba(140,90,40,0.55); }
    }
    .player-card {
      background: rgba(17,17,24,0.8);
      border: 2px solid var(--border);
      border-radius: var(--radius-lg);
      overflow: hidden;
      transition: border-color 0.15s, box-shadow 0.15s;
    }
    .player-card:hover { border-color: var(--border2); box-shadow: 0 4px 20px rgba(109,40,217,0.08); }
    .player-card.rank-1 { border-width: 2px; background: linear-gradient(160deg, #7a5200 0%, #c8940a 28%, #e8c040 50%, #c09018 72%, #7a5800 100%) !important; animation: gold-anim 2.4s ease-in-out infinite; }
    .player-card.rank-2 { border-width: 2px; background: linear-gradient(160deg, #4a5560 0%, #8a9aaa 28%, #c0ccd8 50%, #8898a8 72%, #505e6a 100%) !important; animation: silver-anim 2.4s ease-in-out infinite; }
    .player-card.rank-3 { border-width: 2px; background: linear-gradient(160deg, #5a3018 0%, #a06030 28%, #c88448 50%, #9a5c28 72%, #5a3010 100%) !important; animation: bronze-anim 2.4s ease-in-out infinite; }
    .player-card.rank-1:hover, .player-card.rank-2:hover, .player-card.rank-3:hover { border-color: inherit; box-shadow: none; }

    .card-top {
      display: grid;
      grid-template-columns: 40px 80px 1fr 60px;
      align-items: stretch;
      min-height: 68px;
    }
    .card-rank {
      display: flex; align-items: center; justify-content: center;
      font-size: 23px; font-weight: 900; font-style: italic;
      color: #ffffff; line-height: 1;
      text-shadow: 0 1px 6px rgba(0,0,0,0.7), 0 0px 2px rgba(0,0,0,0.9);
    }
    .card-rank.rank-1 { color: #ffffff; }
    .card-rank.rank-2 { color: #ffffff; }
    .card-rank.rank-3 { color: #ffffff; }
    .card-skin {
      position: relative; width: 80px; height: 68px;
      overflow: hidden; flex-shrink: 0; margin-left: 4px;
      background: transparent;
      border-radius: 8px;
    }
    .skin-img {
      position: absolute; bottom: 0; left: 50%; transform: translateX(-50%);
      height: 115%; width: auto; max-width: 78px;
      object-fit: contain; object-position: bottom center;
      filter: drop-shadow(0 3px 10px rgba(0,0,0,0.8));
    }
    .skin-img.skin-uploaded {
      position: absolute; top: 0; left: 0; bottom: auto; transform: none;
      width: 100%; max-width: 100%; height: 200%;
      object-fit: cover; object-position: top center; filter: none;
    }
    .skin-placeholder {
      position: absolute; inset: 0;
      display: flex; align-items: center; justify-content: center;
      font-size: 22px; font-weight: 900; color: rgba(255,255,255,0.2);
      font-family: var(--mono);
    }
    .card-info { padding: 8px 10px; display: flex; flex-direction: column; justify-content: center; min-width: 0; }
    .card-name { font-size: 18px; font-weight: 800; color: #fff; letter-spacing: -0.02em; line-height: 1.1; word-break: break-word; }
    .card-subtitle { display: flex; align-items: center; gap: 4px; margin-top: 3px; }
    .card-subtitle-icon { width: 12px; height: 12px; flex-shrink: 0; }
    .card-subtitle-text { font-size: 11px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .rank-pill {
      display: inline-flex; align-items: center; gap: 5px;
      align-self: flex-start; width: fit-content; margin-top: 4px;
      padding: 2px 8px 2px 7px; border-radius: 999px;
      background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.12);
      font-size: 11px; font-weight: 800; letter-spacing: 0.08em;
      text-transform: uppercase; white-space: nowrap;
    }
    .card-region { display: flex; align-items: center; justify-content: center; padding: 8px 6px; }
    .region-badge {
      font-size: 13px; font-weight: 900; padding: 8px 13px;
      border-radius: 8px; letter-spacing: 0.08em;
      background: #3a3a50; color: #fff; border: none;
      min-width: 44px; text-align: center;
    }
    .region-badge.NA   { background: #a83232; color: #fff; }
    .region-badge.EU   { background: #27824a; color: #fff; }
    .region-badge.AS,
    .region-badge.ASIA { background: #5a3580; color: #fff; }
    .region-badge.OCE  { background: #2a5fa8; color: #fff; }
    .region-badge.SA   { background: #a86020; color: #fff; }
    .player-card { display: flex; flex-direction: column; }
    .card-top { flex-shrink: 0; }
    .card-tiers { padding: 6px 10px 8px; margin-top: -1px; }
    .tiers-label {
      font-size: 10px; font-weight: 900; color: var(--violet-lt);
      letter-spacing: 0.12em; text-transform: uppercase; margin-bottom: 8px; opacity: 0.7;
    }
    .tiers-row { display: flex; gap: 5px; flex-wrap: wrap; }
    .tier-badge { display: flex; flex-direction: column; align-items: center; gap: 3px; }
    .tier-badge-icon {
      width: 28px; height: 28px; border-radius: 50%;
      background: rgba(0,0,0,0.4); border: 2px solid rgba(99,59,196,0.25);
      display: flex; align-items: center; justify-content: center; font-size: 13px;
    }
    .tier-badge-label {
      font-family: 'Space Mono', monospace; font-size: 9px; font-weight: 700;
      border-radius: 4px; padding: 2px 5px; letter-spacing: 0.02em; line-height: 1.3;
      background: transparent; color: #50566e; border: 1.5px solid #2a2e42;
    }
    .tier-badge-label.ht1 { background: #c8960a; color: #fff8e0; border: none; }
    .tier-badge-label.ht2 { background: #b07c08; color: #fff3d0; border: none; }
    .tier-badge-label.ht3 { background: #986206; color: #ffecb8; border: none; }
    .tier-badge-label.ht4 { background: #7e4e05; color: #ffe090; border: none; }
    .tier-badge-label.ht5 { background: #663c04; color: #ffd468; border: none; }
    .tier-badge-label.lt1 { background: #7a3015; color: #ffd4b0; border: none; }
    .tier-badge-label.lt2 { background: #622510; color: #ffc49a; border: none; }
    .tier-badge-label.lt3 { background: #4e1c0c; color: #ffb082; border: none; }
    .tier-badge-label.lt4 { background: #3c1408; color: #ffa068; border: none; }
    .tier-badge-label.lt5 { background: #2c0e04; color: #ff9050; border: none; }
    .tier-badge-label.ur  { background: transparent; color: #50566e; border: 1.5px solid #2a2e42; }

    /* ── GAMEMODE LEADERBOARD ROW ── */
    .gm-card {
      background: rgba(17,17,24,0.8);
      border: 2px solid var(--border);
      border-radius: var(--radius-lg);
      overflow: hidden;
      transition: border-color 0.15s, box-shadow 0.15s;
      display: grid;
      grid-template-columns: 60px 1fr 100px 100px;
      align-items: center;
      min-height: 72px;
      cursor: default;
    }
    .gm-card:hover { border-color: var(--border2); box-shadow: 0 4px 20px rgba(109,40,217,0.08); }
    .gm-card.rank-1 { animation: gold-anim 2.4s ease-in-out infinite; }
    .gm-card.rank-2 { animation: silver-anim 2.4s ease-in-out infinite; }
    .gm-card.rank-3 { animation: bronze-anim 2.4s ease-in-out infinite; }
    .gm-rank {
      display: flex; align-items: center; justify-content: center;
      font-size: 20px; font-weight: 900; font-style: italic;
      color: rgba(255,255,255,0.18); line-height: 1; padding: 0 8px;
    }
    .gm-rank.rank-1 { color: #c8960a; }
    .gm-rank.rank-2 { color: #7a8fa0; }
    .gm-rank.rank-3 { color: #8c6040; }
    .gm-player { padding: 12px; display: flex; align-items: center; gap: 12px; min-width: 0; }
    .gm-skin {
      width: 44px; height: 44px; border-radius: 8px;
      overflow: hidden; flex-shrink: 0; position: relative;
      background: rgba(0,0,0,0.3); border: 1px solid var(--border);
    }
    .gm-skin img { width: 100%; height: 100%; object-fit: cover; object-position: top center; }
    .gm-skin img.gm-skin-local { width: 200%; height: 200%; margin-left: -50%; object-fit: cover; object-position: top center; }
    .gm-name { font-size: 16px; font-weight: 800; color: #fff; letter-spacing: -0.02em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .gm-sub { font-size: 11px; color: var(--muted); margin-top: 2px; }
    .gm-region-cell { display: flex; justify-content: center; padding: 8px; }
    .gm-tier-cell { display: flex; justify-content: center; padding: 8px; }
    .gm-tier-pill {
      font-family: var(--mono); font-size: 13px; font-weight: 700;
      border-radius: 8px; padding: 6px 14px; letter-spacing: 0.04em;
      background: transparent; color: #50566e; border: 1.5px solid #2a2e42;
    }
    .gm-tier-pill.ht1 { background: rgba(200,150,10,0.2); color: #f5c842; border-color: rgba(200,150,10,0.4); }
    .gm-tier-pill.ht2 { background: rgba(176,124,8,0.18); color: #e5b030; border-color: rgba(176,124,8,0.35); }
    .gm-tier-pill.ht3 { background: rgba(152,98,6,0.16); color: #d0a020; border-color: rgba(152,98,6,0.32); }
    .gm-tier-pill.ht4 { background: rgba(126,78,5,0.14); color: #c09010; border-color: rgba(126,78,5,0.28); }
    .gm-tier-pill.ht5 { background: rgba(102,60,4,0.14); color: #b08010; border-color: rgba(102,60,4,0.28); }
    .gm-tier-pill.lt1 { background: rgba(122,48,21,0.16); color: #e07040; border-color: rgba(122,48,21,0.3); }
    .gm-tier-pill.lt2 { background: rgba(98,37,16,0.14); color: #d06030; border-color: rgba(98,37,16,0.28); }
    .gm-tier-pill.lt3 { background: rgba(78,28,12,0.14); color: #c05020; border-color: rgba(78,28,12,0.28); }
    .gm-tier-pill.lt4 { background: rgba(60,20,8,0.12); color: #b04010; border-color: rgba(60,20,8,0.24); }
    .gm-tier-pill.lt5 { background: rgba(44,14,4,0.12); color: #a03008; border-color: rgba(44,14,4,0.24); }

    /* ── FOOTER ── */
    footer {
      border-top: 1px solid var(--border);
      padding: 1.5rem 1rem;
      text-align: center;
      font-family: var(--mono);
      font-size: 11px;
      color: var(--muted);
      margin-top: 2rem;
    }
    footer span { color: var(--violet-lt); }

    /* ── UTILS ── */
    .player-count {
      max-width: 920px; margin: 0 auto;
      padding: 0 1rem 8px;
      font-size: 11px; font-weight: 600; color: var(--muted);
      letter-spacing: 0.06em; text-transform: uppercase;
    }

    @media (min-width: 769px) {
      .cards-wrap {
        max-width: 1160px;
        padding: 0 2rem 5rem;
        gap: 14px;
      }
      .player-count {
        max-width: 1160px;
        padding: 0 2rem 8px;
      }

      /* ── Table column header ── */
      .desktop-table-header {
        display: grid;
        grid-template-columns: 52px 58px 220px 84px 1fr;
        align-items: center;
        padding: 4px 0 8px 0;
        font-size: 10px; font-weight: 800; letter-spacing: 0.12em;
        text-transform: uppercase; color: var(--muted);
        border-bottom: 1px solid var(--border);
        margin-bottom: 4px;
      }
      .desktop-table-header .dth-rank   { text-align: center; }
      .desktop-table-header .dth-player { padding-left: 10px; }
      .desktop-table-header .dth-region { text-align: center; }
      .desktop-table-header .dth-tiers  { padding-left: 18px; }

      /* ── Flatten card into a true single grid row ── */
      .player-card {
        display: grid !important;
        grid-template-columns: 52px 58px 220px 84px 1fr;
        align-items: center;
        min-height: 58px;
        border-radius: 8px;
        flex-direction: unset;
      }

      /* card-top becomes invisible — its children join the parent grid */
      .card-top {
        display: contents !important;
      }

      /* ── Grid cell overrides ── */
      .card-rank {
        font-size: 20px;
        padding: 0;
        height: 100%;
        display: flex; align-items: center; justify-content: center;
      }

      .card-skin {
        width: 58px; height: 58px;
        border-radius: 0; margin-left: 0;
        overflow: hidden;
      }
      .skin-img {
        height: 140%; max-width: 56px;
      }
      .skin-img.skin-uploaded {
        height: 200%; width: 100%; max-width: 100%;
      }

      .card-info {
        padding: 8px 14px;
        display: flex; flex-direction: column; justify-content: center;
        height: 100%;
      }
      .card-name { font-size: 14px; }
      .card-subtitle { margin-top: 2px; }
      .card-subtitle-text { font-size: 10px; }
      .rank-pill { font-size: 10px; padding: 1px 6px; }

      .card-region {
        display: flex; align-items: center; justify-content: center;
        height: 100%; padding: 0;
      }
      .region-badge { font-size: 11px; padding: 5px 9px; min-width: 38px; }

      /* ── Tiers: 5th grid column ── */
      .card-tiers {
        display: flex !important;
        align-items: center;
        padding: 0 18px;
        margin-top: 0;
        height: 100%;
        border-left: 1px solid rgba(99,59,196,0.15);
      }
      .tiers-label { display: none; }
      .tiers-row   { flex-wrap: nowrap; gap: 7px; align-items: flex-start; padding-top: 6px; }
      .tier-badge  { align-items: center; gap: 3px; }
      .tier-badge-icon  { width: 28px; height: 28px; font-size: 12px; border-width: 1.5px; }
      .tier-badge-label { font-size: 8px; padding: 1px 4px; }

      /* ── Gamemode rows (already single-row, just widen) ── */
      .gm-card {
        grid-template-columns: 52px 1fr 100px 120px;
        min-height: 56px; border-radius: 8px;
      }
    }

    @media (max-width: 580px) {
      .card-top { grid-template-columns: 36px 80px 1fr 54px; min-height: 74px; }
      .table-header { grid-template-columns: 100px 1fr 54px; }
      .card-rank { font-size: 23px; }
      .card-skin { width: 80px; height: 74px; }
      .skin-img { max-width: 60px; }
      .card-name { font-size: 17px; }
      .card-subtitle-text { font-size: 11px; }
      .region-badge { font-size: 12px; padding: 7px 10px; }
      .tier-badge-icon { width: 32px; height: 32px; font-size: 14px; }
      .gm-card { grid-template-columns: 44px 1fr 60px 80px; }
      .gm-name { font-size: 14px; }
      .table-header-gm { grid-template-columns: 44px 1fr 60px 80px; }
      .nav-links { display: none; }
    }
  </style>
</head>
<body>

<header class="site-header">
  <div class="header-inner">
    <img class="logo" src="/static/aftershock-logo-cropped.png" alt="Aftershock Tiers">
    <nav class="nav-links">
      <span class="nav-link active">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
        Home
      </span>
      <span class="nav-link active">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>
        Rankings
      </span>
    </nav>
    <div class="search-wrap">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
      <input class="search-input" type="text" placeholder="Search player..." id="searchInput" oninput="handleSearch()">
      <span class="search-kbd">/</span>
    </div>
  </div>
</header>

<div class="tabs-wrap">
  <div class="tabs-inner" id="tabsInner">
    <div class="tab active" data-tab="overall" onclick="switchTab('overall', this)">
      <span class="tab-icon" style="font-size:22px;display:flex;align-items:center;justify-content:center;"><img src="https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/1f3c6.png" width="26" height="26" style="vertical-align:middle;image-rendering:crisp-edges;" alt="🏆"></span>
      Overall
    </div>
    <div class="tab" data-tab="sword" onclick="switchTab('sword', this)">
      <img class="tab-icon" src="https://cdn.discordapp.com/emojis/1512168702362255380.png" alt="Sword">
      Sword
    </div>
    <div class="tab" data-tab="axe" onclick="switchTab('axe', this)">
      <img class="tab-icon" src="https://cdn.discordapp.com/emojis/1500454383681273986.png" alt="Axe">
      Axe
    </div>
    <div class="tab" data-tab="nethop" onclick="switchTab('nethop', this)">
      <img class="tab-icon" src="https://cdn.discordapp.com/emojis/1466808945522770132.png" alt="NethOP">
      NethOP
    </div>
    <div class="tab" data-tab="uhc" onclick="switchTab('uhc', this)">
      <img class="tab-icon" src="https://cdn.discordapp.com/emojis/1512169002082762912.png" alt="UHC">
      UHC
    </div>
    <div class="tab" data-tab="smp" onclick="switchTab('smp', this)">
      <img class="tab-icon" src="https://cdn.discordapp.com/emojis/1512168820830109858.png" alt="SMP">
      SMP
    </div>
    <div class="tab" data-tab="pot" onclick="switchTab('pot', this)">
      <img class="tab-icon" src="https://cdn.discordapp.com/emojis/1512168870922948848.png" alt="Pot">
      Pot
    </div>
    <div class="tab" data-tab="mace" onclick="switchTab('mace', this)">
      <img class="tab-icon" src="https://cdn.discordapp.com/emojis/1466809319810142260.png" alt="Mace">
      Mace
    </div>
    <div class="tab" data-tab="crystal" onclick="switchTab('crystal', this)">
      <img class="tab-icon" src="https://cdn.discordapp.com/emojis/1512168746918084679.png" alt="Crystal">
      Crystal
    </div>
    <div class="season-badge">&#x1F451; SEASON 1</div>
  </div>
</div>

<div class="server-banner">
  <div class="info-panel-wrap">
    <button class="info-btn" id="infoPanelBtn" onclick="toggleInfoPanel(event)">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
      Information
    </button>
    <div class="info-panel" id="infoPanel">
      <div class="info-tabs">
        <button class="info-tab active" id="tabTitles" onclick="switchInfoTab('titles')">Titles</button>
        <button class="info-tab" id="tabPoints" onclick="switchInfoTab('points')">Points</button>
      </div>
      <div class="info-body">
        <!-- Titles tab -->
        <div class="info-tab-content active" id="infoTitles">
          <h3>How to obtain <u>Achievement Titles</u></h3>
          <div class="title-item">
            <div class="title-item-name"><span class="title-icon"><img src="https://cdn.discordapp.com/emojis/1521028405238304872.png" width="23" height="23" style="vertical-align:middle;"></span><span style="color:#f0c040">Conquered</span></div>
            <div class="title-item-desc">Obtained 700+ total points.</div>
          </div>
          <div class="title-item">
            <div class="title-item-name"><span class="title-icon"><img src="https://cdn.discordapp.com/emojis/1521028367812530317.png" width="23" height="23" style="vertical-align:middle;"></span><span style="color:#c8960a">Combat Master</span></div>
            <div class="title-item-desc">Obtained 500+ total points.</div>
          </div>
          <div class="title-item">
            <div class="title-item-name"><span class="title-icon"><img src="https://cdn.discordapp.com/emojis/1521028320446251120.png" width="23" height="23" style="vertical-align:middle;"></span><span style="color:#5b8dee">Combat Ace</span></div>
            <div class="title-item-desc">Obtained 300+ total points.</div>
          </div>
          <div class="title-item">
            <div class="title-item-name"><span class="title-icon"><img src="https://cdn.discordapp.com/emojis/1521028343305076786.png" width="23" height="23" style="vertical-align:middle;"></span><span style="color:#3cde7e">Combat Specialist</span></div>
            <div class="title-item-desc">Obtained 175+ total points.</div>
          </div>
          <div class="title-item">
            <div class="title-item-name"><span class="title-icon"><img src="https://cdn.discordapp.com/emojis/1521028241488609371.png" width="23" height="23" style="vertical-align:middle;"></span><span style="color:#a060ff">Combat Cadet</span></div>
            <div class="title-item-desc">Obtained 75+ total points.</div>
          </div>
          <div class="title-item">
            <div class="title-item-name"><span class="title-icon"><img src="https://cdn.discordapp.com/emojis/1521028195514716230.png" width="23" height="23" style="vertical-align:middle;"></span><span style="color:#c8cce0">Rookie</span></div>
            <div class="title-item-desc">Starting rank for players with less than 75 points.</div>
          </div>
        </div>
        <!-- Points tab -->
        <div class="info-tab-content" id="infoPoints">
          <h3>How <u>ranking points</u> are calculated</h3>
          <div class="pts-tier pts-t1">
            <div class="pts-tier-name"><img src="https://cdn.discordapp.com/emojis/1511391682451472504.png" width="20" height="20" style="vertical-align:middle;margin-right:4px;"> Tier 1</div>
            <div class="pts-tier-bar">
              <span class="pts-badge ht">⬆⬆ 100 Points</span>
              <span class="pts-badge lt">⬆ 90 Points</span>
            </div>
          </div>
          <div class="pts-tier pts-t2">
            <div class="pts-tier-name"><img src="https://cdn.discordapp.com/emojis/1511391377236164678.png" width="20" height="20" style="vertical-align:middle;margin-right:4px;"> Tier 2</div>
            <div class="pts-tier-bar">
              <span class="pts-badge ht">⬆⬆ 80 Points</span>
              <span class="pts-badge lt">⬆ 70 Points</span>
            </div>
          </div>
          <div class="pts-tier pts-t3">
            <div class="pts-tier-name"><img src="https://cdn.discordapp.com/emojis/1511391138282606712.png" width="20" height="20" style="vertical-align:middle;margin-right:4px;"> Tier 3</div>
            <div class="pts-tier-bar">
              <span class="pts-badge ht">⬆⬆ 60 Points</span>
              <span class="pts-badge lt">⬆ 50 Points</span>
            </div>
          </div>
          <div class="pts-tier">
            <div class="pts-tier-name">Tier 4</div>
            <div class="pts-tier-bar">
              <span class="pts-badge ht">⬆⬆ 40 Points</span>
              <span class="pts-badge lt">⬆ 30 Points</span>
            </div>
          </div>
          <div class="pts-tier">
            <div class="pts-tier-name">Tier 5</div>
            <div class="pts-tier-bar">
              <span class="pts-badge ht">⬆⬆ 20 Points</span>
              <span class="pts-badge lt">⬆ 10 Points</span>
            </div>
          </div>
          <p style="font-size:11px;color:var(--muted);margin-top:10px">⬆⬆ = High Tier (HT) &nbsp;·&nbsp; ⬆ = Low Tier (LT)</p>
        </div>
      </div>
    </div>
  </div>
  <div class="server-ip-wrap">
    <img class="pvp-badge" src="/static/shockhub.png" alt="Shock Hub">
    <div>
      <div class="server-ip-label">Discord</div>
      <div class="server-ip-val" onclick="navigator.clipboard&&navigator.clipboard.writeText('discord.gg/4KdjtN6eE').then(()=>{this.style.borderColor='var(--green)';setTimeout(()=>this.style.borderColor='',1500)})">
        discord.gg/4KdjtN6eE
        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
      </div>
    </div>
    <a class="discord-btn" href="https://discord.gg/4KdjtN6eE" target="_blank" rel="noreferrer">
      <svg viewBox="0 0 24 24"><path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03z"/></svg>
    </a>
  </div>
</div>

<div id="player-count" class="player-count"></div>
<div class="cards-wrap">
  <div class="desktop-table-header" id="desktopTableHeader">
    <div class="dth-rank">#</div>
    <div></div>
    <div class="dth-player">Player</div>
    <div class="dth-region">Region</div>
    <div class="dth-tiers">Tiers</div>
  </div>
  <div id="board"></div>
</div>

<footer>
  <span id="ts"></span> &nbsp;&middot;&nbsp; Points determine rank &nbsp;&middot;&nbsp; discord.gg/4KdjtN6eE &nbsp;&middot;&nbsp; <span>Aftershock Tiers &copy; 2026</span>
</footer>

<script>
  const REFRESH_INTERVAL = 30000;

  const emojiMap = {
    MACE:    "https://cdn.discordapp.com/emojis/1466809319810142260.png",
    POT:     "https://cdn.discordapp.com/emojis/1512168870922948848.png",
    SMP:     "https://cdn.discordapp.com/emojis/1512168820830109858.png",
    SWORD:   "https://cdn.discordapp.com/emojis/1512168702362255380.png",
    UHC:     "https://cdn.discordapp.com/emojis/1512169002082762912.png",
    AXE:     "https://cdn.discordapp.com/emojis/1500454383681273986.png",
    NETHOP:  "https://cdn.discordapp.com/emojis/1466808945522770132.png",
    CRYSTAL: "https://cdn.discordapp.com/emojis/1512168746918084679.png",
    VANILLA: "https://cdn.discordapp.com/emojis/1512168746918084679.png",
  };

  let allPlayers = [];
  let gmData = {};
  let currentTab = 'overall';
  let searchQuery = '';

  /* ── Keyboard shortcut: / focuses search ── */
  document.addEventListener('keydown', e => {
    if (e.key === '/' && document.activeElement !== document.getElementById('searchInput')) {
      e.preventDefault();
      document.getElementById('searchInput').focus();
    }
    if (e.key === 'Escape') document.getElementById('searchInput').blur();
  });

  function handleSearch() {
    searchQuery = document.getElementById('searchInput').value.toLowerCase().trim();
    renderCurrentView();
  }

  /* ── Info Panel ── */
  function toggleInfoPanel(e) {
    e.stopPropagation();
    document.getElementById('infoPanel').classList.toggle('open');
  }
  function switchInfoTab(tab) {
    document.getElementById('infoTitles').classList.toggle('active', tab === 'titles');
    document.getElementById('infoPoints').classList.toggle('active', tab === 'points');
    document.getElementById('tabTitles').classList.toggle('active', tab === 'titles');
    document.getElementById('tabPoints').classList.toggle('active', tab === 'points');
  }
  document.addEventListener('click', function(e) {
    const panel = document.getElementById('infoPanel');
    const btn   = document.getElementById('infoPanelBtn');
    if (panel && btn && !panel.contains(e.target) && !btn.contains(e.target)) {
      panel.classList.remove('open');
    }
  });

  function rankClass(rank) {
    if (rank === 1) return 'rank-1';
    if (rank === 2) return 'rank-2';
    if (rank === 3) return 'rank-3';
    return '';
  }

  function tierCls(label) {
    return (label || '').toLowerCase().replace('-', '');
  }

  function isValidPlayer(p) {
    return p && typeof p.rank === 'number' && typeof p.name === 'string'
      && p.name.trim() !== '' && typeof p.points === 'number' && Array.isArray(p.tiers);
  }

  /* ── Overall card ── */
  function renderCard(p) {
    const tiersHTML = (p.tiers || []).map(t => {
      const imgSrc = emojiMap[t.icon] || '';
      return `<div class="tier-badge">
        <div class="tier-badge-icon"><img src="${imgSrc}" alt="${t.icon || ''}" width="26" height="26" style="image-rendering:auto;object-fit:contain;" onerror="this.style.display='none'"></div>
        <div class="tier-badge-label ${tierCls(t.label)}">${t.label || ''}</div>
      </div>`;
    }).join('');

    const subtitleColor = (p.tier || '').startsWith('HT') ? '#f5c842' : '#8b9bbf';
    const subtitleFg = (p.overall_rank && p.overall_rank.fg) || subtitleColor;
    const ptsText = p.points > 0 ? `${p.points} pts` : '';
    const rankEmoji = {'Conquered':'1521028405238304872','Combat Master':'1521028367812530317','Combat Ace':'1521028320446251120','Combat Specialist':'1521028343305076786','Combat Cadet':'1521028241488609371','Rookie':'1521028195514716230'};
    const hasRank = p.overall_rank && p.overall_rank.label && p.overall_rank.label !== 'Unranked';
    const rankEmojiId = rankEmoji[p.overall_rank && p.overall_rank.label];
    const rankEmojiHTML = rankEmojiId ? `<img src="https://cdn.discordapp.com/emojis/${rankEmojiId}.png" width="16" height="16" style="vertical-align:middle;margin-right:3px;">` : '';
    const rankPillHTML = hasRank
      ? `<div class="rank-pill" style="color:${p.overall_rank.fg};border-color:${p.overall_rank.bg}60;">${rankEmojiHTML}${p.overall_rank.label}</div>`
      : '';

    const regionClass = (p.region || '').toUpperCase().replace(/\\s+/g,'').substring(0,4);
    const customSkin  = p.skin_url || '';
    const isLocalSkin = customSkin.startsWith('/skins/');
    const visageUrl   = 'https://visage.surgeplay.com/full/256/' + encodeURIComponent(p.name) + '.png';
    const crafatarUrl = 'https://crafatar.com/renders/body/' + encodeURIComponent(p.name) + '?overlay&scale=4';
    const mcheadsUrl  = 'https://mc-heads.co/player/' + encodeURIComponent(p.name) + '/left';
    const primaryUrl  = isLocalSkin ? customSkin : (customSkin || visageUrl);
    const fb1 = isLocalSkin ? '' : (customSkin ? visageUrl : crafatarUrl);
    const fb2 = isLocalSkin ? '' : (customSkin ? crafatarUrl : mcheadsUrl);
    const rc = rankClass(p.rank);

    return `<div class="player-card ${rc}" data-name="${p.name.toLowerCase()}">
      <div class="card-top">
        <div class="card-rank ${rc}">${p.rank}.</div>
        <div class="card-skin ${rc}">
          <img class="${isLocalSkin ? 'skin-img skin-uploaded' : 'skin-img'}"
            src="${primaryUrl}" data-fb1="${fb1}" data-fb2="${fb2}"
            onerror="const f1=this.dataset.fb1,f2=this.dataset.fb2;if(!this.dataset.try&&f1){this.dataset.try='1';this.src=f1;}else if(this.dataset.try==='1'&&f2){this.dataset.try='2';this.src=f2;}else{this.dataset.try='done';this.style.display='none';this.nextElementSibling.style.display='flex';}"
            alt="${p.name} skin">
          <div class="skin-placeholder" style="display:none">${p.name.slice(0,2).toUpperCase()}</div>
        </div>
        <div class="card-info">
          <div class="card-name">${p.name}</div>
          <div class="card-subtitle">
            ${rankPillHTML}
            <span class="card-subtitle-text" style="color:${subtitleFg}">${ptsText}</span>
          </div>
        </div>
        <div class="card-region">
          <div class="region-badge ${regionClass}">${p.region || '?'}</div>
        </div>
      </div>
      ${tiersHTML ? `<div class="card-tiers"><div class="tiers-label">Tiers</div><div class="tiers-row">${tiersHTML}</div></div>` : ''}
    </div>`;
  }

  /* ── Gamemode row ── */
  function renderGmRow(entry, rank) {
    const rc = rankClass(rank);
    const localSkin   = entry.skin_url || '';
    const isLocal     = localSkin.startsWith('/skins/');
    const visageUrl   = 'https://visage.surgeplay.com/bust/64/' + encodeURIComponent(entry.username) + '.png';
    const crafatarUrl = 'https://crafatar.com/avatars/' + encodeURIComponent(entry.username) + '?overlay&size=64';
    const primaryUrl  = isLocal ? localSkin : visageUrl;
    const fb1         = isLocal ? visageUrl : crafatarUrl;
    const fb2         = isLocal ? crafatarUrl : '';
    const imgCls      = isLocal ? 'gm-skin-local' : '';
    const tcls = tierCls(entry.tier);
    const regionClass = (entry.region || '').toUpperCase().replace(/\\s+/g,'').substring(0,4);
    return `<div class="gm-card ${rc}" data-name="${entry.username.toLowerCase()}">
      <div class="gm-rank ${rc}">${rank}.</div>
      <div class="gm-player">
        <div class="gm-skin">
          <img class="${imgCls}" src="${primaryUrl}" data-fb1="${fb1}" data-fb2="${fb2}"
            onerror="const f1=this.dataset.fb1,f2=this.dataset.fb2;if(!this.dataset.t&&f1){this.dataset.t='1';this.src=f1;this.className='';}else if(this.dataset.t==='1'&&f2){this.dataset.t='2';this.src=f2;}else{this.style.display='none';}"
            alt="${entry.username}">
        </div>
        <div>
          <div class="gm-name">${entry.username}</div>
          ${entry.region ? `<div class="gm-sub">${entry.region}</div>` : ''}
        </div>
      </div>
      <div class="gm-region-cell">
        <div class="region-badge ${regionClass}">${entry.region || '?'}</div>
      </div>
      <div class="gm-tier-cell">
        <div class="gm-tier-pill ${tcls}">${entry.tier}</div>
      </div>
    </div>`;
  }

  /* ── Render ── */
  const PAGE_SIZE = 50;
  let currentList = [];
  let visibleCount = 0;

  function renderCurrentView() {
    if (currentTab === 'overall') {
      const filtered = allPlayers.filter(p => !searchQuery || p.name.toLowerCase().includes(searchQuery));
      renderOverall(filtered);
    } else {
      const list = (gmData[currentTab] || []).filter(e => !searchQuery || e.username.toLowerCase().includes(searchQuery));
      renderGm(list);
    }
  }

  function renderOverall(list) {
    currentList = list; visibleCount = 0;
    const board = document.getElementById('board');
    if (!list || list.length === 0) {
      board.innerHTML = '<p style="text-align:center;color:var(--muted);padding:3rem 1rem;">No players found.</p>';
      setCount(0, 0); return;
    }
    board.innerHTML = '';
    appendOverallPage();
  }

  function appendOverallPage() {
    const board = document.getElementById('board');
    const oldBtn = document.getElementById('load-more-btn');
    if (oldBtn) oldBtn.remove();
    const slice = currentList.slice(visibleCount, visibleCount + PAGE_SIZE);
    slice.forEach(p => board.insertAdjacentHTML('beforeend', renderCard(p)));
    visibleCount += slice.length;
    setCount(visibleCount, currentList.length);
    if (visibleCount < currentList.length) {
      const remaining = currentList.length - visibleCount;
      const btn = document.createElement('div');
      btn.id = 'load-more-btn';
      btn.style.cssText = 'text-align:center;padding:1.5rem 1rem 2rem;';
      btn.innerHTML = `<button onclick="appendOverallPage()" style="background:var(--surface2);border:1px solid var(--border2);border-radius:999px;color:var(--text);font-family:var(--sans);font-size:13px;font-weight:600;padding:10px 28px;cursor:pointer;transition:border-color 0.15s;" onmouseover="this.style.borderColor='var(--violet-lt)'" onmouseout="this.style.borderColor='var(--border2)'">Load more <span style="color:var(--muted)">(${remaining} remaining)</span></button>`;
      board.parentElement.appendChild(btn);
    }
  }

  function renderGm(list) {
    const board = document.getElementById('board');
    if (!list || list.length === 0) {
      board.innerHTML = '<p style="text-align:center;color:var(--muted);padding:3rem 1rem;">No players found for this gamemode.</p>';
      setCount(0, 0); return;
    }
    board.innerHTML = list.map((e, i) => renderGmRow(e, i + 1)).join('');
    setCount(list.length, list.length);
  }

  function setCount(shown, total) {
    const el = document.getElementById('player-count');
    el.textContent = total > 0 ? `Showing ${shown} of ${total} players` : '';
  }

  function updateTimestamp(fromAPI) {
    const el = document.getElementById('ts');
    const now = new Date();
    const source = fromAPI ? '🟢 Live' : '🟡 Cached';
    el.innerHTML = source + ' · Updated ' + now.toLocaleTimeString('en-US', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
  }

  /* ── Tab switching ── */
  function switchTab(tab, el) {
    if (currentTab === tab) return;
    currentTab = tab;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    el.classList.add('active');
    const dth = document.getElementById('desktopTableHeader');
    if (dth) dth.style.display = tab === 'overall' ? '' : 'none';
    renderCurrentView();
  }

  /* ── Static data processing (reads tiers_data.json — works even when bot is offline) ── */
  const TIER_PTS = {"HT1":100,"LT1":90,"HT2":80,"LT2":70,"HT3":60,"LT3":50,"HT4":40,"LT4":30,"HT5":20,"LT5":10};
  const RANK_TIERS = [[700,"Conquered","#ff6b35","#fff0e8"],[500,"Combat Master","#c8960a","#fff8e0"],[300,"Combat Ace","#5b8dee","#e8f0ff"],[175,"Combat Specialist","#3cde7e","#e0fff0"],[75,"Combat Cadet","#a060ff","#f0e8ff"],[1,"Rookie","#50566e","#c8cce0"]];
  const GM_ICON_MAP = {"sword":"SWORD","axe":"AXE","crystal":"CRYSTAL","nethop":"NETHOP","uhc":"UHC","smp":"SMP","pot":"POT","mace":"MACE","vanilla":"VANILLA"};
  const REGION_FLAGS = {"NA":"🇺🇸","EU":"🇪🇺","AS":"🇮🇳","SA":"🇧🇷","OCE":"🇦🇺"};
  const DEFAULT_GMS  = ["Sword","Axe","NethOP","UHC","SMP","Pot","Mace","Crystal"];

  function overallRankFor(pts) {
    for (const [min,label,bg,fg] of RANK_TIERS) if (pts >= min) return {label,bg,fg};
    return {label:"Unranked",bg:"#21253a",fg:"#50566e"};
  }

  function processRawData(raw) {
    const profiles  = raw.profiles  || {};
    const players   = raw.players   || {};
    const gamemodes = raw.gamemodes || DEFAULT_GMS;
    const discNames = raw.discord_names || {};

    // discord_id → { username, region, skin_url }
    const idToProf = {};
    for (const v of Object.values(profiles)) {
      if (v.discord_id && v.minecraft_username)
        idToProf[v.discord_id] = {username: v.minecraft_username, region: v.region||'', skin_url: v.skin_url||''};
    }

    function resolve(rawKey) {
      const entry = players[rawKey] || {};
      const dregion = typeof entry === 'object' ? (entry.region||'') : '';
      if (rawKey.startsWith('<@') && rawKey.endsWith('>')) {
        const uid = rawKey.slice(2,-1);
        if (idToProf[uid]) { const p=idToProf[uid]; return {name:p.username, region:p.region||dregion, skin_url:p.skin_url}; }
        if (discNames[uid]) return {name:discNames[uid], region:dregion, skin_url:''};
        return null;
      }
      for (const v of Object.values(profiles))
        if ((v.minecraft_username||'').toLowerCase()===rawKey.toLowerCase())
          return {name:rawKey, region:v.region||dregion, skin_url:v.skin_url||''};
      return {name:rawKey, region:dregion, skin_url:''};
    }

    const pmap = {};
    for (const [rawKey, gmData2] of Object.entries(players)) {
      if (typeof gmData2 !== 'object') continue;
      const r = resolve(rawKey);
      if (!r) continue;
      const {name, region, skin_url} = r;
      if (!pmap[name]) { const bonus=gmData2.bonus_points||0; pmap[name]={region,skin_url,tiers:{},points:bonus,bonus_points:bonus}; }
      for (const gm of gamemodes) {
        const gk = gm.toLowerCase();
        if (gmData2[gk] && typeof gmData2[gk]==='object' && gmData2[gk].tier) {
          pmap[name].tiers[gm] = gmData2[gk].tier;
          pmap[name].points += (TIER_PTS[gmData2[gk].tier]||0);
        }
      }
      if (region)   pmap[name].region   = region;
      if (skin_url) pmap[name].skin_url = skin_url;
    }

    const sorted = Object.entries(pmap).sort((a,b)=>b[1].points-a[1].points);

    const overallOut = sorted.map(([name,info],i) => {
      const pts=info.points, region=info.region||'', flag=REGION_FLAGS[region]||'🌍';
      let bestTier=null;
      for (const gm of gamemodes) { const t=info.tiers[gm]; if(t&&(!bestTier||(TIER_PTS[t]||0)>(TIER_PTS[bestTier]||0))) bestTier=t; }
      const tiersList = gamemodes.map(gm=>({icon:GM_ICON_MAP[gm.toLowerCase()]||gm.toUpperCase().slice(0,6),label:info.tiers[gm]||'UR'}))
        .sort((a,b)=>(TIER_PTS[b.label]||0)-(TIER_PTS[a.label]||0));
      return {rank:i+1,name,points:pts,bonus_points:info.bonus_points||0,tier:bestTier||'Unranked',overall_rank:overallRankFor(pts),region,flag,tiers:tiersList,skin_url:info.skin_url||''};
    });

    const gmOut = {};
    for (const gm of gamemodes) {
      const gk = gm.toLowerCase();
      const list = [];
      for (const [rawKey,gmData2] of Object.entries(players)) {
        if (typeof gmData2!=='object') continue;
        if (gmData2[gk]&&typeof gmData2[gk]==='object'&&gmData2[gk].tier) {
          const r=resolve(rawKey); if(!r) continue;
          list.push({username:r.name,tier:gmData2[gk].tier,region:r.region,skin_url:r.skin_url});
        }
      }
      list.sort((a,b)=>(TIER_PTS[b.tier]||0)-(TIER_PTS[a.tier]||0));
      gmOut[gk] = list;
    }
    return {overallOut, gmOut};
  }

  async function loadAllData(isRefresh) {
    if (!isRefresh) document.getElementById('board').innerHTML = '<p style="text-align:center;color:var(--muted);padding:3rem 1rem;">Loading players...</p>';
    try {
      const res = await fetch('/tiers_data.json', { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const raw = await res.json();
      const {overallOut, gmOut} = processRawData(raw);
      allPlayers = overallOut.filter(isValidPlayer);
      gmData     = gmOut;
      updateTimestamp(true);
    } catch(err) {
      console.error('Data fetch error:', err);
      updateTimestamp(false);
    }
  }

  async function init() {
    await loadAllData(false);
    renderCurrentView();
    setInterval(async () => {
      await loadAllData(true);
      renderCurrentView();
    }, REFRESH_INTERVAL);
  }

  init();
</script>
</body>
</html>"""


@web_app.route("/")
def leaderboard_page():
    from flask import make_response
    resp = make_response(LEADERBOARD_HTML)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp


@web_app.route("/api/tiers")
def api_tiers():
    data = load_data()
    gamemodes = data.get("gamemodes", DEFAULT_GAMEMODES)
    profiles = data.get("profiles", {})
    tier_rank = {t: i for i, t in enumerate(TIERS)}

    # Build lookups: discord_id → minecraft_username and discord_id → discord_name
    id_to_mc      = {v["discord_id"]: v["minecraft_username"]
                     for v in profiles.values() if v.get("minecraft_username")}
    id_to_discord = {v["discord_id"]: v.get("discord_name", "")
                     for v in profiles.values() if v.get("discord_id")}
    discord_names_cache = data.get("discord_names", {})

    def resolve_name(raw_key: str) -> str:
        """Always returns a display name — MC username, Discord name, or short ID."""
        if raw_key.startswith("<@") and raw_key.endswith(">"):
            uid = raw_key[2:-1]
            mc = id_to_mc.get(uid)
            if mc:
                return mc
            dn = id_to_discord.get(uid, "").strip()
            if dn:
                return dn
            cached = discord_names_cache.get(uid, "").strip()
            if cached:
                return cached
            return f"User_{uid[-6:]}"   # no profile — show short ID
        return raw_key

    result = {}
    for gm in gamemodes:
        gm_key = gm.lower()
        players = []
        for uname, gm_data in data.get("players", {}).items():
            if isinstance(gm_data, dict) and gm_key in gm_data:
                gd = gm_data[gm_key]
                if isinstance(gd, dict) and "tier" in gd:
                    players.append({"username": resolve_name(uname), "tier": gd["tier"]})
        players.sort(key=lambda p: -TIER_POINTS.get(p["tier"], 0))
        result[gm] = players
    from flask import make_response
    resp = make_response(jsonify(result))
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@web_app.route("/api/tiers", methods=["OPTIONS"])
def api_tiers_options():
    from flask import make_response
    resp = make_response("", 204)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@web_app.route("/skins/<path:filename>")
def serve_skin(filename):
    from flask import send_from_directory
    return send_from_directory(os.path.join(os.getcwd(), "static", "skins"), filename)


@web_app.route("/api/players")
def api_players():
    """
    Returns player data in the exact format expected by the Netlify website:
    { "players": [ { rank, name, points, tier, region, flag, tiers:[{icon,label}] } ] }
    Sorted by overall score (best tier across all gamemodes).
    CORS enabled so the external Netlify site can fetch it.
    """
    from flask import make_response
    data = load_data()
    gamemodes = data.get("gamemodes", DEFAULT_GAMEMODES)
    profiles = data.get("profiles", {})

    tier_rank = {t: i for i, t in enumerate(TIERS)}

    # discord_id → { minecraft_username, region }
    id_to_profile = {}
    for v in profiles.values():
        if "discord_id" in v and "minecraft_username" in v:
            id_to_profile[v["discord_id"]] = {
                "username": v["minecraft_username"],
                "region": v.get("region", ""),
                "skin_url": v.get("skin_url", ""),
            }

    discord_names = data.get("discord_names", {})

    def resolve_name_and_region(raw_key):
        """Returns (username, region, skin_url) or (None, ...) to skip."""
        player_entry = data.get("players", {}).get(raw_key, {})
        direct_region = player_entry.get("region", "") if isinstance(player_entry, dict) else ""
        if raw_key.startswith("<@") and raw_key.endswith(">"):
            uid = raw_key[2:-1]
            # Prefer linked Minecraft profile
            if uid in id_to_profile:
                p = id_to_profile[uid]
                return p["username"], p["region"] or direct_region, p.get("skin_url", "")
            # Fall back to cached Discord display name
            if uid in discord_names:
                return discord_names[uid], direct_region, ""
            # Cannot resolve — skip
            return None, "", ""
        # Check if this key matches any minecraft_username in profiles
        for v in profiles.values():
            if v.get("minecraft_username", "").lower() == raw_key:
                return raw_key, v.get("region", "") or direct_region, v.get("skin_url", "")
        return raw_key, direct_region, ""

    REGION_FLAGS = {"NA": "🇺🇸", "EU": "🇪🇺", "AS": "🇮🇳", "SA": "🇧🇷", "OCE": "🇦🇺"}

    def tier_points(t):
        return TIER_POINTS.get(t, 0)

    def overall_rank_for(pts):
        for min_pts, label, bg, fg in OVERALL_RANKS:
            if pts >= min_pts:
                return {"label": label, "bg": bg, "fg": fg}
        return {"label": "Unranked", "bg": "#21253a", "fg": "#50566e"}

    # Gamemode icon name map (what the website's emojiMap uses)
    GM_ICON = {
        "sword": "SWORD", "axe": "AXE", "crystal": "CRYSTAL",
        "nethop": "NETHOP", "uhc": "UHC", "smp": "SMP",
        "pot": "POT", "mace": "MACE", "vanilla": "VANILLA",
        "cart": "CART", "dia smp": "DIA",
    }

    # Build per-player aggregated data
    player_map = {}
    for raw_key, gm_data in data.get("players", {}).items():
        if not isinstance(gm_data, dict):
            continue
        name, region, skin_url = resolve_name_and_region(raw_key)
        # Skip if name could not be resolved
        if not name:
            continue
        if name not in player_map:
            bonus = gm_data.get("bonus_points", 0) if isinstance(gm_data, dict) else 0
            player_map[name] = {"region": region, "skin_url": skin_url, "tiers": {}, "points": bonus, "bonus_points": bonus}
        for gm in gamemodes:
            gm_key = gm.lower()
            if gm_key in gm_data and isinstance(gm_data[gm_key], dict) and "tier" in gm_data[gm_key]:
                t = gm_data[gm_key]["tier"]
                player_map[name]["tiers"][gm] = t
                player_map[name]["points"] += tier_points(t)
        if region:
            player_map[name]["region"] = region
        if skin_url:
            player_map[name]["skin_url"] = skin_url

    # Sort by total points descending
    sorted_players = sorted(player_map.items(), key=lambda x: -x[1]["points"])

    # Build output list
    out = []
    for i, (name, info) in enumerate(sorted_players):
        pts = info["points"]
        rank = i + 1

        region = info.get("region", "")
        flag = REGION_FLAGS.get(region, "🌍")

        # Overall tier label = best single-gamemode tier (by points)
        best_tier = None
        for gm in gamemodes:
            t = info["tiers"].get(gm)
            if t and (best_tier is None or TIER_POINTS.get(t, 0) > TIER_POINTS.get(best_tier, 0)):
                best_tier = t
        overall_label = best_tier or "Unranked"

        tiers_list = []
        for gm in gamemodes:
            t = info["tiers"].get(gm) or "UR"
            icon = GM_ICON.get(gm.lower(), gm.upper()[:6])
            tiers_list.append({"icon": icon, "label": t})
        tiers_list.sort(key=lambda x: -TIER_POINTS.get(x["label"], 0))

        out.append({
            "rank": rank,
            "name": name,
            "points": pts,
            "bonus_points": info.get("bonus_points", 0),
            "tier": overall_label,
            "overall_rank": overall_rank_for(pts),
            "region": region,
            "flag": flag,
            "tiers": tiers_list,
            "skin_url": info.get("skin_url", ""),
        })

    resp = make_response(jsonify({"players": out}))
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@web_app.route("/api/players", methods=["OPTIONS"])
def api_players_options():
    from flask import make_response
    resp = make_response("", 204)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@web_app.route("/tiers_data.json")
def serve_tiers_data():
    """Serve raw tiers_data.json so the leaderboard JS can process it client-side."""
    from flask import make_response, send_file
    resp = make_response(send_file(DATA_FILE, mimetype="application/json"))
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


def run_web():
    from waitress import serve
    port = int(os.environ.get("PORT", 5000))
    print(f"🌐 Starting web server on port {port}")
    serve(web_app, host="0.0.0.0", port=port, threads=8)


def run_bot():
    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN is not set. Add it to your Replit Secrets or .env file."
        )
    bot.run(TOKEN)

if __name__ == "__main__":
    if TOKEN:
        threading.Thread(target=run_bot, daemon=True).start()
    else:
        print("⚠️  DISCORD_TOKEN not set — running in web-only mode (leaderboard still works).")
    run_web()
