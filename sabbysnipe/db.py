import asyncio
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("red.sabbysnipe.db")


class SnipeDatabase:
    """High performance persistent SQLite database for SabbySnipe with WAL mode and background batching."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

    def init_schema(self) -> None:
        """Initialize tables and indexes synchronously before starting event loop."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode = WAL;")
            cur.execute("PRAGMA synchronous = NORMAL;")
            cur.execute("PRAGMA busy_timeout = 5000;")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS deleted_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    author_name TEXT NOT NULL,
                    author_display_name TEXT NOT NULL,
                    author_avatar_url TEXT NOT NULL,
                    content TEXT,
                    attachments TEXT,
                    stickers TEXT,
                    created_at REAL NOT NULL,
                    deleted_at REAL NOT NULL
                );
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS edited_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    author_name TEXT NOT NULL,
                    author_display_name TEXT NOT NULL,
                    author_avatar_url TEXT NOT NULL,
                    before_content TEXT,
                    after_content TEXT,
                    jump_url TEXT,
                    created_at REAL NOT NULL,
                    edited_at REAL NOT NULL
                );
                """
            )

            cur.execute("CREATE INDEX IF NOT EXISTS idx_del_chan ON deleted_messages (channel_id, deleted_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_del_guild_author ON deleted_messages (guild_id, author_id, deleted_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_del_time ON deleted_messages (deleted_at);")

            cur.execute("CREATE INDEX IF NOT EXISTS idx_edit_chan ON edited_messages (channel_id, edited_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_edit_guild_author ON edited_messages (guild_id, author_id, edited_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_edit_msg_id ON edited_messages (message_id, edited_at ASC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_edit_time ON edited_messages (edited_at);")

            conn.commit()
        finally:
            conn.close()

    def start_worker(self) -> None:
        """Starts the background batch write worker task."""
        if self._worker_task is None or self._worker_task.done():
            self._running = True
            self._worker_task = asyncio.create_task(self._batch_writer_loop())

    async def stop_worker(self) -> None:
        """Flushes queue and cleanly shuts down worker."""
        self._running = False
        if self._worker_task and not self._worker_task.done():
            await self.queue.join()
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    def enqueue_deleted(self, record: Dict[str, Any]) -> None:
        """Enqueues a deleted message record without blocking."""
        self.queue.put_nowait(("insert_deleted", record))

    def enqueue_edited(self, record: Dict[str, Any]) -> None:
        """Enqueues an edited message record without blocking."""
        self.queue.put_nowait(("insert_edited", record))

    def enqueue_clear_channel(self, channel_id: int) -> None:
        """Enqueues clearing channel history."""
        self.queue.put_nowait(("clear_channel", channel_id))

    async def _batch_writer_loop(self) -> None:
        """Processes queued writes in micro-batches to maximize throughput."""
        while self._running:
            try:
                item = await self.queue.get()
                batch = [item]

                while len(batch) < 50:
                    try:
                        next_item = self.queue.get_nowait()
                        batch.append(next_item)
                    except asyncio.QueueEmpty:
                        break

                await asyncio.to_thread(self._write_batch_sync, batch)

                for _ in batch:
                    self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in SnipeDatabase batch writer loop: %s", exc, exc_info=True)
                await asyncio.sleep(0.5)

    def _write_batch_sync(self, batch: List[Tuple[str, Any]]) -> None:
        """Executes a batch of write actions in a single transaction."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA busy_timeout = 5000;")
            cur.execute("BEGIN TRANSACTION;")

            for op_type, data in batch:
                if op_type == "insert_deleted":
                    cur.execute(
                        """
                        INSERT INTO deleted_messages (
                            message_id, guild_id, channel_id, author_id, author_name,
                            author_display_name, author_avatar_url, content,
                            attachments, stickers, created_at, deleted_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            data["message_id"],
                            data["guild_id"],
                            data["channel_id"],
                            data["author_id"],
                            data["author_name"],
                            data["author_display_name"],
                            data["author_avatar_url"],
                            data.get("content", ""),
                            json.dumps(data.get("attachments", [])),
                            json.dumps(data.get("stickers", [])),
                            data["created_at"],
                            data["deleted_at"],
                        ),
                    )
                elif op_type == "insert_edited":
                    cur.execute(
                        """
                        INSERT INTO edited_messages (
                            message_id, guild_id, channel_id, author_id, author_name,
                            author_display_name, author_avatar_url, before_content,
                            after_content, jump_url, created_at, edited_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            data["message_id"],
                            data["guild_id"],
                            data["channel_id"],
                            data["author_id"],
                            data["author_name"],
                            data["author_display_name"],
                            data["author_avatar_url"],
                            data.get("before_content", ""),
                            data.get("after_content", ""),
                            data.get("jump_url", ""),
                            data["created_at"],
                            data["edited_at"],
                        ),
                    )
                elif op_type == "clear_channel":
                    channel_id = data
                    cur.execute("DELETE FROM deleted_messages WHERE channel_id = ?;", (channel_id,))
                    cur.execute("DELETE FROM edited_messages WHERE channel_id = ?;", (channel_id,))

            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.error("Failed to commit batch to SnipeDatabase: %s", exc, exc_info=True)
        finally:
            conn.close()

    def _row_to_deleted_dict(self, row: Tuple) -> Dict[str, Any]:
        """Converts raw SQLite row to deleted message dictionary."""
        return {
            "id": row[0],
            "message_id": row[1],
            "guild_id": row[2],
            "channel_id": row[3],
            "author_id": row[4],
            "author_name": row[5],
            "author_display_name": row[6],
            "author_avatar_url": row[7],
            "content": row[8],
            "attachments": json.loads(row[9]) if row[9] else [],
            "stickers": json.loads(row[10]) if row[10] else [],
            "created_at": row[11],
            "deleted_at": row[12],
        }

    def _row_to_edited_dict(self, row: Tuple) -> Dict[str, Any]:
        """Converts raw SQLite row to edited message dictionary."""
        return {
            "id": row[0],
            "message_id": row[1],
            "guild_id": row[2],
            "channel_id": row[3],
            "author_id": row[4],
            "author_name": row[5],
            "author_display_name": row[6],
            "author_avatar_url": row[7],
            "before_content": row[8],
            "after_content": row[9],
            "jump_url": row[10],
            "created_at": row[11],
            "edited_at": row[12],
        }

    async def get_deleted(self, channel_id: int, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        """Fetch deleted messages for a channel ordered by deleted_at descending."""
        def _fetch() -> List[Dict[str, Any]]:
            conn = sqlite3.connect(str(self.db_path))
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id, message_id, guild_id, channel_id, author_id, author_name,
                           author_display_name, author_avatar_url, content, attachments,
                           stickers, created_at, deleted_at
                    FROM deleted_messages
                    WHERE channel_id = ?
                    ORDER BY deleted_at DESC
                    LIMIT ? OFFSET ?;
                    """,
                    (channel_id, limit, offset),
                )
                rows = cur.fetchall()
                return [self._row_to_deleted_dict(r) for r in rows]
            finally:
                conn.close()

        return await asyncio.to_thread(_fetch)

    async def get_deleted_by_author(
        self, guild_id: int, author_id: int, channel_id: Optional[int] = None, limit: int = 20, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Fetch deleted messages by author in a guild or specific channel."""
        def _fetch() -> List[Dict[str, Any]]:
            conn = sqlite3.connect(str(self.db_path))
            try:
                cur = conn.cursor()
                if channel_id:
                    cur.execute(
                        """
                        SELECT id, message_id, guild_id, channel_id, author_id, author_name,
                               author_display_name, author_avatar_url, content, attachments,
                               stickers, created_at, deleted_at
                        FROM deleted_messages
                        WHERE guild_id = ? AND author_id = ? AND channel_id = ?
                        ORDER BY deleted_at DESC
                        LIMIT ? OFFSET ?;
                        """,
                        (guild_id, author_id, channel_id, limit, offset),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, message_id, guild_id, channel_id, author_id, author_name,
                               author_display_name, author_avatar_url, content, attachments,
                               stickers, created_at, deleted_at
                        FROM deleted_messages
                        WHERE guild_id = ? AND author_id = ?
                        ORDER BY deleted_at DESC
                        LIMIT ? OFFSET ?;
                        """,
                        (guild_id, author_id, limit, offset),
                    )
                rows = cur.fetchall()
                return [self._row_to_deleted_dict(r) for r in rows]
            finally:
                conn.close()

        return await asyncio.to_thread(_fetch)

    async def search_deleted(
        self, guild_id: int, query: str, channel_id: Optional[int] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Search deleted messages by content text."""
        def _search() -> List[Dict[str, Any]]:
            conn = sqlite3.connect(str(self.db_path))
            try:
                cur = conn.cursor()
                like_pattern = f"%{query}%"
                if channel_id:
                    cur.execute(
                        """
                        SELECT id, message_id, guild_id, channel_id, author_id, author_name,
                               author_display_name, author_avatar_url, content, attachments,
                               stickers, created_at, deleted_at
                        FROM deleted_messages
                        WHERE guild_id = ? AND channel_id = ? AND content LIKE ?
                        ORDER BY deleted_at DESC
                        LIMIT ?;
                        """,
                        (guild_id, channel_id, like_pattern, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, message_id, guild_id, channel_id, author_id, author_name,
                               author_display_name, author_avatar_url, content, attachments,
                               stickers, created_at, deleted_at
                        FROM deleted_messages
                        WHERE guild_id = ? AND content LIKE ?
                        ORDER BY deleted_at DESC
                        LIMIT ?;
                        """,
                        (guild_id, like_pattern, limit),
                    )
                rows = cur.fetchall()
                return [self._row_to_deleted_dict(r) for r in rows]
            finally:
                conn.close()

        return await asyncio.to_thread(_search)

    async def get_edited(self, channel_id: int, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        """Fetch edited messages for a channel ordered by edited_at descending."""
        def _fetch() -> List[Dict[str, Any]]:
            conn = sqlite3.connect(str(self.db_path))
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id, message_id, guild_id, channel_id, author_id, author_name,
                           author_display_name, author_avatar_url, before_content,
                           after_content, jump_url, created_at, edited_at
                    FROM edited_messages
                    WHERE channel_id = ?
                    ORDER BY edited_at DESC
                    LIMIT ? OFFSET ?;
                    """,
                    (channel_id, limit, offset),
                )
                rows = cur.fetchall()
                return [self._row_to_edited_dict(r) for r in rows]
            finally:
                conn.close()

        return await asyncio.to_thread(_fetch)

    async def get_edited_by_author(
        self, guild_id: int, author_id: int, channel_id: Optional[int] = None, limit: int = 20, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Fetch edited messages by author in a guild or specific channel."""
        def _fetch() -> List[Dict[str, Any]]:
            conn = sqlite3.connect(str(self.db_path))
            try:
                cur = conn.cursor()
                if channel_id:
                    cur.execute(
                        """
                        SELECT id, message_id, guild_id, channel_id, author_id, author_name,
                               author_display_name, author_avatar_url, before_content,
                               after_content, jump_url, created_at, edited_at
                        FROM edited_messages
                        WHERE guild_id = ? AND author_id = ? AND channel_id = ?
                        ORDER BY edited_at DESC
                        LIMIT ? OFFSET ?;
                        """,
                        (guild_id, author_id, channel_id, limit, offset),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, message_id, guild_id, channel_id, author_id, author_name,
                               author_display_name, author_avatar_url, before_content,
                               after_content, jump_url, created_at, edited_at
                        FROM edited_messages
                        WHERE guild_id = ? AND author_id = ?
                        ORDER BY edited_at DESC
                        LIMIT ? OFFSET ?;
                        """,
                        (guild_id, author_id, limit, offset),
                    )
                rows = cur.fetchall()
                return [self._row_to_edited_dict(r) for r in rows]
            finally:
                conn.close()

        return await asyncio.to_thread(_fetch)

    async def search_edited(
        self, guild_id: int, query: str, channel_id: Optional[int] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Search edited messages by before or after content."""
        def _search() -> List[Dict[str, Any]]:
            conn = sqlite3.connect(str(self.db_path))
            try:
                cur = conn.cursor()
                like_pattern = f"%{query}%"
                if channel_id:
                    cur.execute(
                        """
                        SELECT id, message_id, guild_id, channel_id, author_id, author_name,
                               author_display_name, author_avatar_url, before_content,
                               after_content, jump_url, created_at, edited_at
                        FROM edited_messages
                        WHERE guild_id = ? AND channel_id = ? AND (before_content LIKE ? OR after_content LIKE ?)
                        ORDER BY edited_at DESC
                        LIMIT ?;
                        """,
                        (guild_id, channel_id, like_pattern, like_pattern, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, message_id, guild_id, channel_id, author_id, author_name,
                               author_display_name, author_avatar_url, before_content,
                               after_content, jump_url, created_at, edited_at
                        FROM edited_messages
                        WHERE guild_id = ? AND (before_content LIKE ? OR after_content LIKE ?)
                        ORDER BY edited_at DESC
                        LIMIT ?;
                        """,
                        (guild_id, like_pattern, like_pattern, limit),
                    )
                rows = cur.fetchall()
                return [self._row_to_edited_dict(r) for r in rows]
            finally:
                conn.close()

        return await asyncio.to_thread(_search)

    async def get_edit_history(self, message_id: int) -> List[Dict[str, Any]]:
        """Fetch complete edit timeline for a specific message."""
        def _fetch() -> List[Dict[str, Any]]:
            conn = sqlite3.connect(str(self.db_path))
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id, message_id, guild_id, channel_id, author_id, author_name,
                           author_display_name, author_avatar_url, before_content,
                           after_content, jump_url, created_at, edited_at
                    FROM edited_messages
                    WHERE message_id = ?
                    ORDER BY edited_at ASC;
                    """,
                    (message_id,),
                )
                rows = cur.fetchall()
                return [self._row_to_edited_dict(r) for r in rows]
            finally:
                conn.close()

        return await asyncio.to_thread(_fetch)

    async def prune_older_than(self, days: int) -> Tuple[int, int]:
        """Prunes messages older than the given number of days."""
        if days <= 0:
            return (0, 0)

        cutoff = time.time() - (days * 86400)

        def _prune() -> Tuple[int, int]:
            conn = sqlite3.connect(str(self.db_path))
            try:
                cur = conn.cursor()
                cur.execute("DELETE FROM deleted_messages WHERE deleted_at < ?;", (cutoff,))
                del_count = cur.rowcount
                cur.execute("DELETE FROM edited_messages WHERE edited_at < ?;", (cutoff,))
                edit_count = cur.rowcount
                conn.commit()
                return (del_count, edit_count)
            finally:
                conn.close()

        return await asyncio.to_thread(_prune)

    async def get_stats(self, guild_id: Optional[int] = None) -> Dict[str, Any]:
        """Get database storage statistics."""
        def _stats() -> Dict[str, Any]:
            conn = sqlite3.connect(str(self.db_path))
            try:
                cur = conn.cursor()
                if guild_id:
                    cur.execute("SELECT COUNT(*) FROM deleted_messages WHERE guild_id = ?;", (guild_id,))
                    deleted_count = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM edited_messages WHERE guild_id = ?;", (guild_id,))
                    edited_count = cur.fetchone()[0]
                else:
                    cur.execute("SELECT COUNT(*) FROM deleted_messages;")
                    deleted_count = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM edited_messages;")
                    edited_count = cur.fetchone()[0]

                size_bytes = 0
                if self.db_path.exists():
                    size_bytes = self.db_path.stat().st_size

                return {
                    "deleted_count": deleted_count,
                    "edited_count": edited_count,
                    "size_bytes": size_bytes,
                }
            finally:
                conn.close()

        stats = await asyncio.to_thread(_stats)
        stats["queue_size"] = self.queue.qsize()
        return stats
