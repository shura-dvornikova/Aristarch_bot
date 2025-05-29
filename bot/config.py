from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()           # читает .env в корне проекта

@dataclass
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")

config = Settings()
