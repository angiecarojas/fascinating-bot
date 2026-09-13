
import asyncio
import re
from collections import deque
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
from aiohttp import web, ClientSession
from bs4 import BeautifulSoup
import yt_dlp
import imageio_ffmpeg


# =========================================================
# CONFIGURACIÓN
# =========================================================

TOKEN = os.environ["DISCORD_TOKEN"]

MUSIC_CHANNEL_NAME = "🎵・Music"
CODES_CHANNEL_NAME = "🎁・dbd-codes"
CODES_URL = "https://nightlight.gg/codes"

FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web"]
        }
    },
}

YTDL_PLAYLIST_OPTIONS = {
    **YTDL_OPTIONS,
    "noplaylist": False,
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


# =========================================================
# DISCORD
# =========================================================

intents = discord.Intents.default()
intents.voice_states = True
intents.guilds = True

from discord.ext import commands

client = commands.Bot(command_prefix="!", intents=intents)
tree = client.tree

GUILD_ID = 512748868761550859
GUILD = discord.Object(id=GUILD_ID)

# =========================================================
# MÚSICA
# =========================================================

class Song:
    def __init__(self, title, url, webpage_url=None, duration=0, thumbnail=None):
        self.title = title
        self.url = url
        self.webpage_url = webpage_url
        self.duration = duration or 0
        self.thumbnail = thumbnail


music_queues = {}
now_playing = {}


def get_queue(guild_id):
    if guild_id not in music_queues:
        music_queues[guild_id] = deque()
    return music_queues[guild_id]


async def extract_song(query):
    """
    Busca una canción primero en YouTube.
    Si YouTube falla, intenta automáticamente SoundCloud.
    """

    def make_song(info):

        if not info:
            return None

        if "entries" in info:

            entries = [
                x for x in info["entries"]
                if x
            ]

            if not entries:
                return None

            info = entries[0]

        audio_url = info.get("url")

        if not audio_url:
            return None

        return Song(
            title=info.get(
                "title",
                "Canción desconocida"
            ),
            url=audio_url,
            webpage_url=info.get(
                "webpage_url"
            ),
            duration=info.get(
                "duration",
                0
            ),
            thumbnail=info.get(
                "thumbnail"
            ),
        )

    def extract():

        is_url = query.startswith(
            ("http://", "https://")
        )

        # =================================================
        # 1. YOUTUBE
        # =================================================

        try:

            options = {
                **YTDL_OPTIONS,
                "quiet": True,
                "no_warnings": True,
                "noprogress": True,
            }

            if is_url:

                search_query = query

            else:

                search_query = (
                    f"ytsearch1:{query}"
                )

            print(
                f"[MUSIC] Intentando YouTube: {search_query}"
            )

            with yt_dlp.YoutubeDL(
                options
            ) as ydl:

                info = ydl.extract_info(
                    search_query,
                    download=False
                )

            song = make_song(info)

            if song:

                print(
                    f"[MUSIC] YouTube OK: {song.title}"
                )

                return song

        except Exception as exc:

            print(
                f"[MUSIC] YouTube no disponible: {type(exc).__name__}"
            )

        # =================================================
        # 2. SOUNDCLOUD
        # =================================================

        try:

            options = {
                **YTDL_OPTIONS,
                "quiet": True,
                "no_warnings": True,
                "noprogress": True,
            }

            if is_url:

                if "soundcloud.com" not in query.lower():

                    print(
                        "[MUSIC] URL no compatible con SoundCloud."
                    )

                    return None

                search_query = query

            else:

                search_query = (
                    f"scsearch1:{query}"
                )

            print(
                f"[MUSIC] Intentando SoundCloud: {search_query}"
            )

            with yt_dlp.YoutubeDL(
                options
            ) as ydl:

                info = ydl.extract_info(
                    search_query,
                    download=False
                )

            song = make_song(info)

            if song:

                print(
                    f"[MUSIC] SoundCloud OK: {song.title}"
                )

                return song

        except Exception as exc:

            print(
                f"[MUSIC] SoundCloud no disponible: {type(exc).__name__}: {exc}"
            )

        print(
            "[MUSIC] No se encontró una fuente reproducible."
        )

        return None

    return await asyncio.to_thread(extract)

async def connect_to_user_channel(interaction):
    if not interaction.user.voice:
        await interaction.followup.send(
            "❌ Primero entra a **🎵・Music** o a un canal de voz."
        )
        return None

    channel = interaction.user.voice.channel
    guild_id = interaction.guild.id

    voice_client = interaction.guild.voice_client

    if voice_client:
        if voice_client.channel != channel:
            await voice_client.move_to(channel)
        return voice_client

    return await channel.connect()


async def play_next(guild_id):
    guild = client.get_guild(guild_id)

    if not guild:
        return

    voice_client = guild.voice_client

    if not voice_client:
        return

    queue = get_queue(guild_id)

    if not queue:
        now_playing.pop(guild_id, None)
        return

    song = queue.popleft()
    now_playing[guild_id] = song

    source = discord.FFmpegPCMAudio(
        song.url,
        executable=FFMPEG_PATH,
        **FFMPEG_OPTIONS
    )

    def after_playing(error):
        if error:
            print(f"[MUSIC] Playback error: {error}")

        asyncio.run_coroutine_threadsafe(
            play_next(guild_id),
            client.loop
        )

    voice_client.play(source, after=after_playing)

    print(f"[MUSIC] Playing: {song.title}")


# =========================================================
# /PLAY
# =========================================================

@tree.command(
    name="play",
    description="Busca y reproduce una canción o reproduce una URL."
)
@app_commands.describe(query="Nombre de la canción o URL")
async def play(interaction: discord.Interaction, query: str):

    await interaction.response.defer()

    if not interaction.guild:
        await interaction.followup.send(
            "❌ Este comando solo funciona dentro de un servidor."
        )
        return

    try:
        voice_client = await connect_to_user_channel(interaction)

        if not voice_client:
            return

        await interaction.followup.send(
            f"🔎 Buscando **{query}**..."
        )

        song = await extract_song(query)

        if not song:
            await interaction.followup.send(
                "❌ No encontré esa canción."
            )
            return

        queue = get_queue(interaction.guild.id)

        was_playing = voice_client.is_playing() or voice_client.is_paused()

        queue.append(song)

        embed = discord.Embed(
            title="🎵 Añadido a la cola",
            description=f"**{song.title}**",
            timestamp=datetime.now(timezone.utc)
        )

        if song.thumbnail:
            embed.set_thumbnail(url=song.thumbnail)

        if song.webpage_url:
            embed.url = song.webpage_url

        embed.add_field(
            name="📋 Posición",
            value=str(len(queue)),
            inline=True
        )

        await interaction.followup.send(embed=embed)

        if not was_playing:
            await play_next(interaction.guild.id)

    except Exception as exc:
        print(f"[PLAY] Error: {exc}")

        await interaction.followup.send(
            "❌ No pude reproducir esa canción. "
            "Prueba con otro nombre o pega directamente el enlace."
        )


# =========================================================
# /SKIP
# =========================================================

@tree.command(
    name="skip",
    description="Salta la canción actual."
)
async def skip(interaction: discord.Interaction):

    if not interaction.guild:
        return

    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_playing():
        await interaction.response.send_message(
            "❌ No hay ninguna canción reproduciéndose."
        )
        return

    voice_client.stop()

    await interaction.response.send_message(
        "⏭️ **Canción saltada.**"
    )


# =========================================================
# /PAUSE
# =========================================================

@tree.command(
    name="pause",
    description="Pausa la música."
)
async def pause(interaction: discord.Interaction):

    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_playing():
        await interaction.response.send_message(
            "❌ No hay música reproduciéndose."
        )
        return

    voice_client.pause()

    await interaction.response.send_message(
        "⏸️ **Música pausada.**"
    )


# =========================================================
# /RESUME
# =========================================================

@tree.command(
    name="resume",
    description="Continúa la música."
)
async def resume(interaction: discord.Interaction):

    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_paused():
        await interaction.response.send_message(
            "❌ La música no está pausada."
        )
        return

    voice_client.resume()

    await interaction.response.send_message(
        "▶️ **Música reanudada.**"
    )


# =========================================================
# /STOP
# =========================================================

@tree.command(
    name="stop",
    description="Detiene la música y limpia la cola."
)
async def stop(interaction: discord.Interaction):

    guild_id = interaction.guild.id
    voice_client = interaction.guild.voice_client

    get_queue(guild_id).clear()
    now_playing.pop(guild_id, None)

    if voice_client and voice_client.is_playing():
        voice_client.stop()

    await interaction.response.send_message(
        "⏹️ **Música detenida y cola limpiada.**"
    )


# =========================================================
# /QUEUE
# =========================================================

@tree.command(
    name="queue",
    description="Muestra la cola de reproducción."
)
async def queue_command(interaction: discord.Interaction):

    queue = get_queue(interaction.guild.id)
    current = now_playing.get(interaction.guild.id)

    lines = []

    if current:
        lines.append(
            f"🎵 **Reproduciendo:** {current.title}"
        )

    if queue:
        lines.append("\n📋 **Siguiente:**")

        for i, song in enumerate(list(queue)[:15], start=1):
            lines.append(
                f"`{i}.` {song.title}"
            )

    if not lines:
        await interaction.response.send_message(
            "📭 La cola está vacía."
        )
        return

    embed = discord.Embed(
        title="📜 Cola de Fascinating",
        description="\n".join(lines)
    )

    await interaction.response.send_message(embed=embed)


# =========================================================
# /NOWPLAYING
# =========================================================

@tree.command(
    name="nowplaying",
    description="Muestra la canción actual."
)
async def nowplaying(interaction: discord.Interaction):

    song = now_playing.get(interaction.guild.id)

    if not song:
        await interaction.response.send_message(
            "📭 No hay ninguna canción reproduciéndose."
        )
        return

    embed = discord.Embed(
        title="🎵 Ahora reproduciendo",
        description=f"**{song.title}**"
    )

    if song.thumbnail:
        embed.set_thumbnail(url=song.thumbnail)

    if song.webpage_url:
        embed.url = song.webpage_url

    await interaction.response.send_message(embed=embed)


# =========================================================
# /JOIN
# =========================================================

@tree.command(
    name="join",
    description="Hace que Fascinating Bot entre a tu canal de voz."
)
async def join(interaction: discord.Interaction):

    await interaction.response.defer()

    voice_client = await connect_to_user_channel(interaction)

    if voice_client:
        await interaction.followup.send(
            f"🎵 Ya estoy en **{voice_client.channel.name}**."
        )


# =========================================================
# /LEAVE
# =========================================================

@tree.command(
    name="leave",
    description="Hace que Fascinating Bot salga del canal de voz."
)
async def leave(interaction: discord.Interaction):

    voice_client = interaction.guild.voice_client

    if not voice_client:
        await interaction.response.send_message(
            "❌ No estoy conectado a ningún canal."
        )
        return

    get_queue(interaction.guild.id).clear()
    now_playing.pop(interaction.guild.id, None)

    await voice_client.disconnect()

    await interaction.response.send_message(
        "👋 **Fascinating Bot salió del canal.**"
    )


# =========================================================
# AUTOMÁTICO: ENTRAR A 🎵・Music + REPRODUCIR SHAKIRA
# =========================================================

AUTO_SONG_URL = "https://soundcloud.com/dj-nonoparana/shakira-las-de-la-intuicion-dj-nono-parana-remix"


async def play_auto_song(guild):

    voice_client = guild.voice_client

    if not voice_client:
        return

    if voice_client.is_playing() or voice_client.is_paused():
        return

    try:
        print(f"[AUTO MUSIC] Buscando fuente: {AUTO_SONG_URL}")

        song = await extract_song(AUTO_SONG_URL)

        if not song:
            print("[AUTO MUSIC] No pude obtener el audio de SoundCloud.")
            return

        queue = get_queue(guild.id)
        queue.append(song)

        print(f"[AUTO MUSIC] Canción preparada: {song.title}")

        await play_next(guild.id)

    except Exception as exc:
        print(f"[AUTO MUSIC] Error reproduciendo: {type(exc).__name__}: {exc}")


@client.event
async def on_voice_state_update(member, before, after):

    if member.bot:
        return

    if after.channel is None:
        return

    if after.channel.name != MUSIC_CHANNEL_NAME:
        return

    guild = member.guild

    if guild.voice_client is not None:
        return

    try:
        await after.channel.connect()

        print(
            f"[VOICE] Entré automáticamente a {after.channel.name}"
        )

        await play_auto_song(guild)

    except Exception as exc:
        print(
            f"[VOICE/AUTO MUSIC] Error: {type(exc).__name__}: {exc}"
        )


# =========================================================
# DBD CODES
# =========================================================

last_codes = set()


async def fetch_codes():

    async with ClientSession() as session:

        async with session.get(
            CODES_URL,
            timeout=30,
            headers={
                "User-Agent": "FascinatingBot/1.0"
            }
        ) as response:

            response.raise_for_status()

            return await response.text()


def extract_codes(html):

    soup = BeautifulSoup(html, "html.parser")

    text = soup.get_text("\n", strip=True)

    candidates = set(
        re.findall(
            r"\b[A-Z0-9]{5,40}\b",
            text
        )
    )

    noise = {
        "DEADBYDAYLIGHT",
        "NIGHTLIGHT",
        "BLOODPOINTS",
        "EXPIRES",
        "ADDED",
        "ACTIVE",
        "CODES",
        "LATEST",
        "REDEEM",
        "STORE"
    }

    return {
        code
        for code in candidates
        if code not in noise
    }


@tree.command(
    name="codes",
    description="Muestra los códigos activos de Dead by Daylight."
)
async def codes(interaction: discord.Interaction):

    await interaction.response.defer()

    try:

        html = await fetch_codes()

        current = sorted(
            extract_codes(html)
        )

        if not current:

            await interaction.followup.send(
                "🎁 No encontré códigos activos actualmente."
            )

            return

        embed = discord.Embed(
            title="🎁 Códigos de Dead by Daylight",
            description="\n".join(
                f"🎟️ `{code}`"
                for code in current[:25]
            ),
            url=CODES_URL
        )

        embed.set_footer(
            text="Fascinating Bot • NightLight"
        )

        await interaction.followup.send(
            embed=embed
        )

    except Exception as exc:

        print(f"[CODES] Error: {exc}")

        await interaction.followup.send(
            "❌ No pude consultar los códigos ahora."
        )


@tasks.loop(minutes=15)
async def check_dbd_codes():

    global last_codes

    try:

        html = await fetch_codes()

        current = extract_codes(html)

        if not last_codes:

            last_codes = current

            return

        new_codes = sorted(
            current - last_codes
        )

        last_codes = current

        if not new_codes:
            return

        for guild in client.guilds:

            channel = discord.utils.get(
                guild.text_channels,
                name=CODES_CHANNEL_NAME
            )

            if not channel:
                continue

            for code in new_codes:

                embed = discord.Embed(
                    title="🎁 ¡NUEVO CÓDIGO DBD!",
                    description=(
                        f"## 🎟️ `{code}`\n\n"
                        "¡Canjea este código antes de que expire! 🔥"
                    ),
                    url=CODES_URL,
                    timestamp=datetime.now(timezone.utc)
                )

                embed.set_footer(
                    text="Fascinating Bot • NightLight"
                )

                await channel.send(
                    embed=embed
                )

    except Exception as exc:

        print(
            f"[DBD CODES] Error: {exc}"
        )


@check_dbd_codes.before_loop
async def before_codes():

    await client.wait_until_ready()


# =========================================================
# WEB SERVER PARA RENDER
# =========================================================

async def health(request):

    return web.Response(
        text="Fascinating Bot is online 🎵🤖"
    )


async def start_web_server():

    app = web.Application()

    app.router.add_get(
        "/",
        health
    )

    app.router.add_get(
        "/health",
        health
    )

    runner = web.AppRunner(app)

    await runner.setup()

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()


# =========================================================
# READY
# =========================================================

@client.event
async def on_ready():

    print(f"🤖 Logged in as {client.user} ({client.user.id})")

    try:
        client.tree.copy_global_to(guild=GUILD)
        synced = await client.tree.sync(guild=GUILD)
        print(f"✅ Synced {len(synced)} slash commands.")
    except Exception as exc:
        print(f"❌ [SYNC] Error: {exc}")

    try:
        await start_web_server()
        print("🌐 Web server started.")
    except Exception as exc:
        print(f"❌ [WEB] Error: {exc}")

    if not check_dbd_codes.is_running():
        check_dbd_codes.start()
        print("🎁 DBD code checker started.")


# =========================================================
# INICIAR
# =========================================================

client.run(TOKEN)