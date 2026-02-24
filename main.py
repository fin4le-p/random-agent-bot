import os
import asyncio

import discord
from discord.ext import commands
from dotenv import load_dotenv

from commands.random import random_command
from commands.punish import punish_command
from commands.tactic import tactic_command
from commands.help import help_command
from commands.vs import vs_group

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
    bot.tree.add_command(random_command)
    print("random command registered successfully.")
    bot.tree.add_command(punish_command)
    print("punish command registered successfully.")
    bot.tree.add_command(tactic_command)
    print("tactic command registered successfully.")
    bot.tree.add_command(help_command)
    print("help command registered successfully.")

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
