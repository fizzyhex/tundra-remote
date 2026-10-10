import logging

import discord

from .commands import TundraRemote
from .config import load


def main():
    discord.utils.setup_logging(level=logging.INFO)
    config = load()
    TundraRemote(config).run(config.token, log_handler=None)


if __name__ == "__main__":
    main()
