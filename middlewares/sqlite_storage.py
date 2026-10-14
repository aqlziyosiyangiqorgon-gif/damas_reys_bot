import json
from typing import Any, Mapping

from aiogram.fsm.storage.base import BaseStorage, StorageKey

from database.db import Database


class SQLiteFSMStorage(BaseStorage):
    """Small SQLite-backed aiogram FSM storage for the single-process bot."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _key(key: StorageKey) -> str:
        return json.dumps(
            [key.bot_id, key.chat_id, key.user_id, key.thread_id,
             key.business_connection_id, key.destiny],
            separators=(",", ":"),
        )

    async def set_state(self, key: StorageKey, state: Any = None) -> None:
        state_value = getattr(state, "state", state)
        conn = await self.db.get_connection()
        try:
            await conn.execute(
                "INSERT INTO fsm_storage (storage_key, state, data) VALUES (?, ?, '{}') "
                "ON CONFLICT(storage_key) DO UPDATE SET state = excluded.state",
                (self._key(key), state_value),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def get_state(self, key: StorageKey) -> str | None:
        conn = await self.db.get_connection()
        try:
            cur = await conn.execute("SELECT state FROM fsm_storage WHERE storage_key = ?", (self._key(key),))
            row = await cur.fetchone()
            return row["state"] if row else None
        finally:
            await conn.close()

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        conn = await self.db.get_connection()
        try:
            if not data:
                await conn.execute(
                    "DELETE FROM fsm_storage WHERE storage_key = ? AND state IS NULL",
                    (self._key(key),),
                )
                await conn.commit()
                return
            await conn.execute(
                "INSERT INTO fsm_storage (storage_key, state, data) VALUES (?, NULL, ?) "
                "ON CONFLICT(storage_key) DO UPDATE SET data = excluded.data",
                (self._key(key), json.dumps(dict(data), ensure_ascii=False)),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        conn = await self.db.get_connection()
        try:
            cur = await conn.execute("SELECT data FROM fsm_storage WHERE storage_key = ?", (self._key(key),))
            row = await cur.fetchone()
            return json.loads(row["data"]) if row else {}
        finally:
            await conn.close()

    async def update_data(self, key: StorageKey, data: Mapping[str, Any]) -> dict[str, Any]:
        conn = await self.db.get_connection()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute("SELECT data FROM fsm_storage WHERE storage_key = ?", (self._key(key),))
            row = await cur.fetchone()
            current = json.loads(row["data"]) if row else {}
            current.update(data)
            await conn.execute(
                "INSERT INTO fsm_storage (storage_key, state, data) VALUES (?, NULL, ?) "
                "ON CONFLICT(storage_key) DO UPDATE SET data = excluded.data",
                (self._key(key), json.dumps(current, ensure_ascii=False)),
            )
            await conn.commit()
            return current.copy()
        finally:
            await conn.close()

    async def close(self) -> None:
        return None
