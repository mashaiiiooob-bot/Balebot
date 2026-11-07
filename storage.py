import sqlite3
from pathlib import Path

class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True) if str(Path(path).parent) not in ("", ".") else None
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS sent (
                source_id TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                PRIMARY KEY (source_id, message_id)
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                source_id TEXT PRIMARY KEY,
                last_message_id INTEGER NOT NULL
            )
        """)
        self.db.commit()

    def was_sent(self, source_id, message_id):
        row = self.db.execute(
            "SELECT 1 FROM sent WHERE source_id=? AND message_id=?",
            (str(source_id), int(message_id))
        ).fetchone()
        return row is not None

    def mark_sent(self, source_id, message_id):
        self.db.execute(
            "INSERT OR IGNORE INTO sent(source_id,message_id) VALUES(?,?)",
            (str(source_id), int(message_id))
        )
        self.db.commit()

    def last_id(self, source_id):
        row = self.db.execute(
            "SELECT last_message_id FROM checkpoints WHERE source_id=?",
            (str(source_id),)
        ).fetchone()
        return int(row[0]) if row else 0

    def set_last_id(self, source_id, message_id):
        self.db.execute("""
            INSERT INTO checkpoints(source_id,last_message_id) VALUES(?,?)
            ON CONFLICT(source_id) DO UPDATE SET last_message_id=MAX(last_message_id, excluded.last_message_id)
        """, (str(source_id), int(message_id)))
        self.db.commit()

    def close(self):
        self.db.close()
