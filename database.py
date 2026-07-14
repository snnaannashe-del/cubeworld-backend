import sqlite3
import hashlib
import os
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "cubeworld.db")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    # Users — identified solely by their CUBE key (no email/phone)
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash      TEXT    NOT NULL UNIQUE,
            key_prefix    TEXT    NOT NULL,
            key_type      TEXT    NOT NULL DEFAULT 'free',
            display_name  TEXT,
            avatar_url    TEXT,
            cube_balance  REAL    NOT NULL DEFAULT 0.0,
            created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            last_seen     TEXT    NOT NULL DEFAULT (datetime('now')),
            is_active     INTEGER NOT NULL DEFAULT 1
        )
    """)

    # Sessions / refresh tokens
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash    TEXT    NOT NULL UNIQUE,
            created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            expires_at    TEXT    NOT NULL,
            last_used     TEXT    NOT NULL DEFAULT (datetime('now')),
            user_agent    TEXT,
            ip_hash       TEXT
        )
    """)

    # CUBE token transactions
    c.execute("""
        CREATE TABLE IF NOT EXISTS cube_transactions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            amount        REAL    NOT NULL,
            tx_type       TEXT    NOT NULL,
            description   TEXT,
            tx_hash       TEXT,
            created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Linked wallets
    c.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            address       TEXT    NOT NULL UNIQUE,
            chain_id      INTEGER NOT NULL DEFAULT 137,
            linked_at     TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Key upgrade history
    c.execute("""
        CREATE TABLE IF NOT EXISTS key_upgrades (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            from_type     TEXT    NOT NULL,
            to_type       TEXT    NOT NULL,
            cube_spent    REAL    NOT NULL DEFAULT 0.0,
            tx_hash       TEXT,
            upgraded_at   TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get_user_by_key_hash(key_hash: str):
    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE key_hash = ? AND is_active = 1", (key_hash,)
    ).fetchone()
    conn.close()
    return user


def get_user_by_id(user_id: int):
    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE id = ? AND is_active = 1", (user_id,)
    ).fetchone()
    conn.close()
    return user


def create_user(key_hash: str, key_prefix: str, key_type: str = "free"):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO users (key_hash, key_prefix, key_type) VALUES (?, ?, ?)",
        (key_hash, key_prefix, key_type),
    )
    user_id = c.lastrowid
    conn.commit()
    conn.close()
    return user_id


def update_last_seen(user_id: int):
    conn = get_db()
    conn.execute("UPDATE users SET last_seen = datetime('now') WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def create_session(user_id: int, token_hash: str, expires_at: str,
                   user_agent: str = None, ip_hash: str = None):
    conn = get_db()
    conn.execute(
        "INSERT INTO sessions (user_id, token_hash, expires_at, user_agent, ip_hash) VALUES (?, ?, ?, ?, ?)",
        (user_id, token_hash, expires_at, user_agent, ip_hash),
    )
    conn.commit()
    conn.close()


def get_session_by_token_hash(token_hash: str):
    conn = get_db()
    session = conn.execute(
        """SELECT s.*, u.key_type, u.key_prefix, u.cube_balance, u.display_name, u.id as user_id
           FROM sessions s
           JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = ? AND s.expires_at > datetime('now')""",
        (token_hash,),
    ).fetchone()
    conn.close()
    return session


def delete_session(token_hash: str):
    conn = get_db()
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
    conn.commit()
    conn.close()


def add_cube_balance(user_id: int, amount: float, tx_type: str,
                     description: str = None, tx_hash: str = None):
    conn = get_db()
    conn.execute(
        "UPDATE users SET cube_balance = cube_balance + ? WHERE id = ?",
        (amount, user_id),
    )
    conn.execute(
        "INSERT INTO cube_transactions (user_id, amount, tx_type, description, tx_hash) VALUES (?, ?, ?, ?, ?)",
        (user_id, amount, tx_type, description, tx_hash),
    )
    conn.commit()
    conn.close()


def get_cube_balance(user_id: int) -> float:
    conn = get_db()
    row = conn.execute("SELECT cube_balance FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row["cube_balance"] if row else 0.0


def upgrade_key(user_id: int, to_type: str, cube_spent: float = 0.0, tx_hash: str = None):
    conn = get_db()
    current = conn.execute("SELECT key_type FROM users WHERE id = ?", (user_id,)).fetchone()
    from_type = current["key_type"] if current else "free"
    conn.execute(
        "UPDATE users SET key_type = ?, cube_balance = cube_balance - ? WHERE id = ?",
        (to_type, cube_spent, user_id),
    )
    conn.execute(
        "INSERT INTO key_upgrades (user_id, from_type, to_type, cube_spent, tx_hash) VALUES (?, ?, ?, ?, ?)",
        (user_id, from_type, to_type, cube_spent, tx_hash),
    )
    conn.commit()
    conn.close()


def link_wallet(user_id: int, address: str, chain_id: int = 137):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO wallets (user_id, address, chain_id) VALUES (?, ?, ?)",
        (user_id, address.lower(), chain_id),
    )
    conn.commit()
    conn.close()


def get_stats() -> dict:
    conn = get_db()
    total_users  = conn.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
    premium_users = conn.execute("SELECT COUNT(*) FROM users WHERE key_type='premium' AND is_active=1").fetchone()[0]
    online_users = conn.execute(
        "SELECT COUNT(*) FROM users WHERE last_seen > datetime('now', '-5 minutes')"
    ).fetchone()[0]
    conn.close()
    return {"total_users": total_users, "premium_users": premium_users, "online_users": online_users}
