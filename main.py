import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from commands.help import help_command
from commands.punish import punish_command
from commands.random import random_command
from commands.riotcon import riotcon_command
from commands.tactic import tactic_command
#from commands.tournament import tournament_command
from core.agents_data import validate_agents_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger(__name__)

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

if not DISCORD_TOKEN:
    raise SystemExit("Error: DISCORD_TOKEN is not set in .env file.")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)


async def setup_hook() -> None:
    bot.tree.add_command(random_command)
    bot.tree.add_command(punish_command)
    bot.tree.add_command(tactic_command)
    bot.tree.add_command(help_command)
    bot.tree.add_command(riotcon_command)
    #bot.tree.add_command(tournament_command)

    logger.info("Slash commands registered.")

    for warning in validate_agents_file():
        logger.warning("agents.json validation: %s", warning)

    try:
        synced = await bot.tree.sync()
        logger.info("Commands globally synced: %s command(s)", len(synced))
    except Exception:
        logger.exception("Command sync failed during setup_hook().")
        raise


bot.setup_hook = setup_hook


@bot.event
async def on_ready() -> None:
    logger.info("Bot is ready. Logged in as %s", bot.user)


async def main() -> None:
    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
