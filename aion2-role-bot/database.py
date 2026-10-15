import re
import aiosqlite
from config import (
    DATABASE_PATH,
    DEFAULT_CLASSES,
    DEFAULT_LANGUAGES,
    DEFAULT_NOTIFICATIONS,
)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "item"


class Database:
    def __init__(self) -> None:
        self.path = DATABASE_PATH

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS legion_logos (
                    guild_id INTEGER NOT NULL,
                    leader_id INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    data BLOB NOT NULL,
                    PRIMARY KEY (guild_id, leader_id)
                );

                CREATE TABLE IF NOT EXISTS legion_profiles (
                guild_id INTEGER NOT NULL,
                leader_id INTEGER NOT NULL,
                legion_key TEXT NOT NULL,
                name TEXT NOT NULL,
                focus TEXT,
                language TEXT,
                description TEXT,
                invite_url TEXT,
                ad_message_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, leader_id),
                UNIQUE (guild_id, legion_key)
                );

                CREATE TABLE IF NOT EXISTS extra_panels (
                    guild_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, kind)
                );
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    roles_channel_id INTEGER,
                    panel_message_id INTEGER,
                    allow_multiple_classes INTEGER NOT NULL DEFAULT 0,
                    allow_multiple_languages INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS role_mappings (
                    guild_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    role_id INTEGER,
                    emoji TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (guild_id, category, item_key)
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    actor_id INTEGER,
                    action TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.commit()

    async def ensure_guild(self, guild_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)",
                (guild_id,),
            )
            await db.commit()

    async def log(self, guild_id: int, actor_id: int | None, action: str, details: str = "") -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO audit_log (guild_id, actor_id, action, details) VALUES (?, ?, ?, ?)",
                (guild_id, actor_id, action, details),
            )
            await db.commit()

    async def get_guild_settings(self, guild_id: int) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def set_panel(self, guild_id: int, channel_id: int, message_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE guild_settings SET roles_channel_id = ?, panel_message_id = ? WHERE guild_id = ?",
                (channel_id, message_id, guild_id),
            )
            await db.commit()

    async def seed_defaults(self, guild_id: int) -> None:
        rows = []
        for name, emoji in DEFAULT_CLASSES:
            rows.append((guild_id, "class", slugify(name), name, emoji))
        for name, emoji in DEFAULT_LANGUAGES:
            rows.append((guild_id, "language", slugify(name), name, emoji))
        for key, name, emoji in DEFAULT_NOTIFICATIONS:
            rows.append((guild_id, "notification", key, name, emoji))
        rows.append((guild_id, "legion", "none", "No Legion", "🚫"))

        async with aiosqlite.connect(self.path) as db:
            await db.executemany(
                """
                INSERT OR IGNORE INTO role_mappings
                (guild_id, category, item_key, display_name, emoji)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
            await db.commit()

    async def list_mappings(self, guild_id: int, category: str, enabled_only: bool = True) -> list[dict]:
        query = "SELECT * FROM role_mappings WHERE guild_id = ? AND category = ?"
        if enabled_only:
            query += " AND enabled = 1"
        query += " ORDER BY rowid"
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(query, (guild_id, category))
            return [dict(r) for r in await cur.fetchall()]

    async def set_mapping_role(self, guild_id: int, category: str, item_key: str, role_id: int | None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE role_mappings SET role_id = ? WHERE guild_id = ? AND category = ? AND item_key = ?",
                (role_id, guild_id, category, item_key),
            )
            await db.commit()

    async def upsert_mapping(
        self, guild_id: int, category: str, item_key: str,
        display_name: str, role_id: int | None, emoji: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO role_mappings
                (guild_id, category, item_key, display_name, role_id, emoji, enabled)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT (guild_id, category, item_key) DO UPDATE SET
                    display_name = excluded.display_name,
                    role_id = excluded.role_id,
                    emoji = excluded.emoji,
                    enabled = 1
                """,
                (guild_id, category, item_key, display_name, role_id, emoji),
            )
            await db.commit()

    async def get_profile(self, guild_id: int, leader_id: int) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM legion_profiles WHERE guild_id = ? AND leader_id = ?",
                (guild_id, leader_id),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def save_profile(
        self, guild_id: int, leader_id: int, legion_key: str, name: str,
        focus: str | None, language: str | None,
        description: str | None, invite_url: str | None,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO legion_profiles
                (guild_id, leader_id, legion_key, name, focus, language, description, invite_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (guild_id, leader_id) DO UPDATE SET
                    name = excluded.name,
                    focus = excluded.focus,
                    language = excluded.language,
                    description = excluded.description,
                    invite_url = excluded.invite_url
                """,
                (guild_id, leader_id, legion_key, name, focus, language, description, invite_url),
            )
            await db.commit()

    async def set_ad_message(self, guild_id: int, leader_id: int, message_id: int | None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE legion_profiles SET ad_message_id = ? WHERE guild_id = ? AND leader_id = ?",
                (message_id, guild_id, leader_id),
            )
            await db.commit()

    async def delete_profile(self, guild_id: int, leader_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "DELETE FROM legion_profiles WHERE guild_id = ? AND leader_id = ?",
                (guild_id, leader_id),
            )
            await db.execute(
                "DELETE FROM legion_logos WHERE guild_id = ? AND leader_id = ?",
                (guild_id, leader_id),
            )
            await db.commit()

    async def delete_mapping(self, guild_id: int, category: str, item_key: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "DELETE FROM role_mappings WHERE guild_id = ? AND category = ? AND item_key = ?",
                (guild_id, category, item_key),
            )
            await db.commit()

    async def get_extra_panel(self, guild_id: int, kind: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM extra_panels WHERE guild_id = ? AND kind = ?",
                (guild_id, kind),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def set_extra_panel(self, guild_id: int, kind: str, channel_id: int, message_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO extra_panels (guild_id, kind, channel_id, message_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (guild_id, kind) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    message_id = excluded.message_id
                """,
                (guild_id, kind, channel_id, message_id),
            )
            await db.commit()



    async def get_logo(self, guild_id: int, leader_id: int) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT filename, data FROM legion_logos WHERE guild_id = ? AND leader_id = ?",
                (guild_id, leader_id),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def save_logo(self, guild_id: int, leader_id: int, filename: str, data: bytes) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO legion_logos (guild_id, leader_id, filename, data)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (guild_id, leader_id) DO UPDATE SET
                    filename = excluded.filename,
                    data = excluded.data
                """,
                (guild_id, leader_id, filename, data),
            )
            await db.commit()

    async def delete_logo(self, guild_id: int, leader_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "DELETE FROM legion_logos WHERE guild_id = ? AND leader_id = ?",
                (guild_id, leader_id),
            )
            await db.commit()           