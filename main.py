import os
import asyncio

import discord
from discord.ext import commands
from dotenv import load_dotenv

from commands.va import va_group
from commands.ai import ai_group

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

if not DISCORD_TOKEN:
    print("Error: DISCORD_TOKEN is not set in .env file.")
    raise SystemExit(1)

# Intent 設定（VC メンバーを取るのに members / voice_states を有効にする）
intents = discord.Intents.default()
intents.guilds = True
intents.members = True        # 開発者ポータルで「SERVER MEMBERS INTENT」を有効化しておく
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

async def setup_hook():
    bot.tree.add_command(va_group)
    bot.tree.add_command(ai_group)
    print("va_group commands registered successfully.")

bot.setup_hook = setup_hook

@bot.event
async def on_ready():
    print(f"Bot is ready. Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Commands synced successfully: {len(synced)} commands")
    except Exception as e:
        print(f"Error syncing commands: {e}")

async def main():
    async with bot:
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
