import os
import asyncio
import re
import json
from datetime import datetime, timezone

import discord
from discord.ext import tasks
from aiohttp import web, ClientSession
from bs4 import BeautifulSoup

TOKEN = os.environ["DISCORD_TOKEN"]
MUSIC_CHANNEL_NAME = "🎵・Music"
CODES_CHANNEL_NAME = "🎁・dbd-codes"
CODES_URL = "https://nightlight.gg/codes"

intents = discord.Intents.default()
intents.voice_states = True
intents.guilds = True

client = discord.Client(intents=intents)

last_codes = set()
http_runner = None


async def health(request):
    return web.Response(text="Fascinating Bot is online.")


async def start_web_server():
    global http_runner
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    http_runner = runner


async def fetch_codes():
    async with ClientSession() as session:
        async with session.get(
            CODES_URL,
            timeout=30,
            headers={"User-Agent": "FascinatingBot/1.0"}
        ) as response:
            response.raise_for_status()
            return await response.text()


def extract_codes(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    # NightLight currently exposes codes as uppercase alphanumeric strings.
    candidates = set(re.findall(r"\b[A-Z0-9]{5,40}\b", text))

    # Remove obvious non-code labels/noise.
    noise = {
        "DEADBYDAYLIGHT", "NIGHTLIGHT", "BLOODPOINTS",
        "EXPIRES", "ADDED", "ACTIVE", "CODES",
        "LATEST", "REDEEM", "STORE"
    }
    return {c for c in candidates if c not in noise}


@tasks.loop(minutes=15)
async def check_dbd_codes():
    global last_codes

    try:
        html = await fetch_codes()
        current = extract_codes(html)

        if not last_codes:
            # First run: remember current codes without flooding the channel.
            last_codes = current
            return

        new_codes = sorted(current - last_codes)
        last_codes = current

        if not new_codes:
            return

        for guild in client.guilds:
            channel = discord.utils.get(guild.text_channels, name=CODES_CHANNEL_NAME)
            if channel is None:
                continue

            for code in new_codes:
                embed = discord.Embed(
                    title="🎁 Nuevo código de Dead by Daylight",
                    description=f"**`{code}`**",
                    url=CODES_URL,
                    timestamp=datetime.now(timezone.utc)
                )
                embed.set_footer(text="Fascinating • NightLight")
                await channel.send(embed=embed)

    except Exception as exc:
        print(f"[DBD CODES] Error: {exc}")


@check_dbd_codes.before_loop
async def before_codes_loop():
    await client.wait_until_ready()


@client.event
async def on_ready():
    print(f"Logged in as {client.user} ({client.user.id})")
    await start_web_server()
    if not check_dbd_codes.is_running():
        check_dbd_codes.start()


@client.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    if after.channel is None:
        return

    if after.channel.name != MUSIC_CHANNEL_NAME:
        return

    # This bot joins the music channel itself.
    # It cannot force another bot (such as Jockie) to join.
    if member.guild.voice_client is not None:
        return

    try:
        await after.channel.connect()
        print(f"Joined voice channel: {after.channel.name}")
    except Exception as exc:
        print(f"[VOICE] Could not join: {exc}")


client.run(TOKEN)
