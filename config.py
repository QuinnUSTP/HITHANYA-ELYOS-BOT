import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/aion2_roles.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
ALLOWED_CHANNEL_ID = int(os.getenv("ALLOWED_CHANNEL_ID", "0"))
ADVERTISE_CHANNEL_ID = int(os.getenv("ADVERTISE_CHANNEL_ID", "0"))
LEGION_LEADER_ROLE_ID = int(os.getenv("LEGION_LEADER_ROLE_ID", "0"))

DEFAULT_CLASSES = [
    ("Gladiator", "⚔️"),
    ("Templar", "🛡️"),
    ("Assassin", "🗡️"),
    ("Ranger", "🏹"),
    ("Sorcerer", "🔥"),
    ("Spiritmaster", "🔮"),
    ("Cleric", "✨"),
    ("Chanter", "🎵"),
]

DEFAULT_LANGUAGES = [
    ("English", "🇺🇸"),
    ("Filipino / Tagalog", "🇵🇭"),
    ("Indonesian", "🇮🇩"),
    ("Thai", "🇹🇭"),
    ("Vietnamese", "🇻🇳"),
    ("Korean", "🇰🇷"),
    ("Japanese", "🇯🇵"),
    ("Chinese", "🇨🇳"),
    ("Other", "🌐"),
]

DEFAULT_NOTIFICATIONS = [
    ("siege_fortress", "Siege & Fortress", "🏰"),
    ("raids_dungeons", "Raids & Dungeons", "🐉"),
    ("open_world_pvp", "Open-world PvP", "⚡"),
    ("legion_events", "Legion Events", "🎉"),
    ("party_finder", "Party Finder", "🔎"),
    ("spacetime_rift", "Spacetime Rift", "🌀"),
    ("watcher_kaira", "Watcher Kaira", "👁️"),
]

Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)