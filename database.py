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

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT NOT NULL UNIQUE,
            key_prefix TEXT NOT NULL,
            key_type TEXT NOT NULL DEFAULT 'free',
            display_name TEXT,
            avatar_url TEXT,
            cube_balance REAL NOT NULL DEFAULT 0.0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_seen TEXT NOT NULL DEFAULT (datetime('now')),
            is_active INTEGER NOT NULL DEFAULT 1
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            last_used TEXT NOT NULL DEFAULT (datetime('now')),
            user_agent TEXT,
            ip_hash TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS cube_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            amount REAL NOT NULL,
            tx_type TEXT NOT NULL,
            description TEXT,
            tx_hash TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            address TEXT NOT NULL UNIQUE,
            chain_id INTEGER NOT NULL DEFAULT 137,
            linked_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS key_upgrades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            from_type TEXT NOT NULL,
            to_type TEXT NOT NULL,
            cube_spent REAL NOT NULL DEFAULT 0.0,
            tx_hash TEXT,
            upgraded_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # NEW: Cubes
    c.execute("""
        CREATE TABLE IF NOT EXISTS cubes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            icon TEXT DEFAULT '📦',
            color TEXT DEFAULT '#7c6fcd',
            type TEXT NOT NULL DEFAULT 'public',
            life_hours INTEGER NOT NULL DEFAULT 24,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
    """)
    # NEW: Messages
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cube_id INTEGER NOT NULL,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            display_name TEXT,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # NEW: Posts
    c.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cube_id INTEGER NOT NULL,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            display_name TEXT,
            content TEXT NOT NULL,
            likes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS post_likes (
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (post_id, user_id)
        )
    """)
    # NEW: Signals
    c.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cube_id INTEGER NOT NULL,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            display_name TEXT,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'LONG',
            entry_price REAL,
            tp_price REAL,
            sl_price REAL,
            content TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()

# ── Auth ──────────────────────────────────────────────────────────────────────

def hash_key(raw_key):
    return hashlib.sha256(raw_key.encode()).hexdigest()

def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()

def get_user_by_key_hash(key_hash):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE key_hash=? AND is_active=1",(key_hash,)).fetchone()
    conn.close(); return user

def get_user_by_id(user_id):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=? AND is_active=1",(user_id,)).fetchone()
    conn.close(); return user

def create_user(key_hash, key_prefix, key_type="free"):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO users (key_hash,key_prefix,key_type) VALUES (?,?,?)",(key_hash,key_prefix,key_type))
    uid = c.lastrowid; conn.commit(); conn.close(); return uid

def update_last_seen(user_id):
    conn = get_db()
    conn.execute("UPDATE users SET last_seen=datetime('now') WHERE id=?",(user_id,))
    conn.commit(); conn.close()

def get_display_name(user_id):
    conn = get_db()
    row = conn.execute("SELECT display_name,key_prefix FROM users WHERE id=?",(user_id,)).fetchone()
    conn.close()
    if not row: return "Unknown"
    return row["display_name"] or f"CUBE-{row['key_prefix']}"

def create_session(user_id, token_hash, expires_at, user_agent=None, ip_hash=None):
    conn = get_db()
    conn.execute("INSERT INTO sessions (user_id,token_hash,expires_at,user_agent,ip_hash) VALUES (?,?,?,?,?)",
                 (user_id,token_hash,expires_at,user_agent,ip_hash))
    conn.commit(); conn.close()

def get_session_by_token_hash(token_hash):
    conn = get_db()
    row = conn.execute(
        """SELECT s.*,u.key_type,u.key_prefix,u.cube_balance,u.display_name,u.id as user_id
           FROM sessions s JOIN users u ON u.id=s.user_id
           WHERE s.token_hash=? AND s.expires_at>datetime('now')""",(token_hash,)).fetchone()
    conn.close(); return row

def delete_session(token_hash):
    conn = get_db()
    conn.execute("DELETE FROM sessions WHERE token_hash=?",(token_hash,))
    conn.commit(); conn.close()

def add_cube_balance(user_id, amount, tx_type, description=None, tx_hash=None):
    conn = get_db()
    conn.execute("UPDATE users SET cube_balance=cube_balance+? WHERE id=?",(amount,user_id))
    conn.execute("INSERT INTO cube_transactions (user_id,amount,tx_type,description,tx_hash) VALUES (?,?,?,?,?)",
                 (user_id,amount,tx_type,description,tx_hash))
    conn.commit(); conn.close()

def get_cube_balance(user_id):
    conn = get_db()
    row = conn.execute("SELECT cube_balance FROM users WHERE id=?",(user_id,)).fetchone()
    conn.close(); return row["cube_balance"] if row else 0.0

def upgrade_key(user_id, to_type, cube_spent=0.0, tx_hash=None):
    conn = get_db()
    cur = conn.execute("SELECT key_type FROM users WHERE id=?",(user_id,)).fetchone()
    from_type = cur["key_type"] if cur else "free"
    conn.execute("UPDATE users SET key_type=?,cube_balance=cube_balance-? WHERE id=?",(to_type,cube_spent,user_id))
    conn.execute("INSERT INTO key_upgrades (user_id,from_type,to_type,cube_spent,tx_hash) VALUES (?,?,?,?,?)",
                 (user_id,from_type,to_type,cube_spent,tx_hash))
    conn.commit(); conn.close()

def link_wallet(user_id, address, chain_id=137):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO wallets (user_id,address,chain_id) VALUES (?,?,?)",
                 (user_id,address.lower(),chain_id))
    conn.commit(); conn.close()

def get_stats():
    conn = get_db()
    total_users  = conn.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
    premium      = conn.execute("SELECT COUNT(*) FROM users WHERE key_type='premium' AND is_active=1").fetchone()[0]
    online       = conn.execute("SELECT COUNT(*) FROM users WHERE last_seen>datetime('now','-5 minutes')").fetchone()[0]
    total_cubes  = conn.execute("SELECT COUNT(*) FROM cubes WHERE is_active=1").fetchone()[0]
    conn.close()
    return {"total_users":total_users,"premium_users":premium,"online_users":online,"total_cubes":total_cubes}

# ── Cubes ─────────────────────────────────────────────────────────────────────

def create_cube(owner_id, name, description, icon, color, cube_type, life_hours):
    conn = get_db(); c = conn.cursor()
    c.execute(
        """INSERT INTO cubes (owner_id,name,description,icon,color,type,life_hours,expires_at)
           VALUES (?,?,?,?,?,?,?,datetime('now','+'||?||' hours'))""",
        (owner_id,name,description,icon,color,cube_type,life_hours,str(life_hours)))
    cid = c.lastrowid; conn.commit(); conn.close(); return cid

def deactivate_expired_cubes():
    conn = get_db()
    conn.execute("UPDATE cubes SET is_active=0 WHERE is_active=1 AND expires_at<=datetime('now')")
    conn.commit(); conn.close()

def list_cubes():
    conn = get_db()
    rows = conn.execute(
        """SELECT id,owner_id,name,description,icon,color,type,life_hours,created_at,expires_at,
                  CAST((julianday(expires_at)-julianday('now'))*86400 AS INTEGER) as life_left_seconds
           FROM cubes WHERE is_active=1 AND expires_at>datetime('now')
           ORDER BY created_at DESC LIMIT 100""").fetchall()
    conn.close(); return [dict(r) for r in rows]

# ── Messages ──────────────────────────────────────────────────────────────────

def save_message(cube_id, user_id, display_name, content):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO messages (cube_id,user_id,display_name,content) VALUES (?,?,?,?)",
              (cube_id,user_id,display_name,content))
    mid = c.lastrowid; conn.commit(); conn.close(); return mid

def get_messages(cube_id, limit=50):
    conn = get_db()
    rows = conn.execute(
        "SELECT id,cube_id,user_id,display_name,content,created_at FROM messages WHERE cube_id=? ORDER BY created_at DESC LIMIT ?",
        (cube_id,limit)).fetchall()
    conn.close(); return list(reversed([dict(r) for r in rows]))

# ── Posts ─────────────────────────────────────────────────────────────────────

def create_post(cube_id, user_id, display_name, content):
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO posts (cube_id,user_id,display_name,content) VALUES (?,?,?,?)",
              (cube_id,user_id,display_name,content))
    pid = c.lastrowid; conn.commit(); conn.close(); return pid

def get_posts(cube_id, limit=50):
    conn = get_db()
    rows = conn.execute(
        "SELECT id,cube_id,user_id,display_name,content,likes,created_at FROM posts WHERE cube_id=? ORDER BY created_at DESC LIMIT ?",
        (cube_id,limit)).fetchall()
    conn.close(); return [dict(r) for r in rows]

def like_post(post_id, user_id):
    conn = get_db()
    try:
        conn.execute("INSERT INTO post_likes (post_id,user_id) VALUES (?,?)",(post_id,user_id))
        conn.execute("UPDATE posts SET likes=likes+1 WHERE id=?",(post_id,))
        conn.commit(); liked=True
    except Exception: liked=False
    row = conn.execute("SELECT likes FROM posts WHERE id=?",(post_id,)).fetchone()
    conn.close(); return (row["likes"] if row else 0, liked)

# ── Signals ───────────────────────────────────────────────────────────────────

def create_signal(cube_id, user_id, display_name, ticker, direction, entry_price, tp_price, sl_price, content):
    conn = get_db(); c = conn.cursor()
    c.execute(
        """INSERT INTO signals (cube_id,user_id,display_name,ticker,direction,entry_price,tp_price,sl_price,content)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (cube_id,user_id,display_name,ticker,direction,entry_price,tp_price,sl_price,content))
    sid = c.lastrowid; conn.commit(); conn.close(); return sid

def get_signals(cube_id, limit=30):
    conn = get_db()
    rows = conn.execute(
        """SELECT id,cube_id,user_id,display_name,ticker,direction,entry_price,tp_price,sl_price,content,created_at
           FROM signals WHERE cube_id=? ORDER BY created_at DESC LIMIT ?""",
        (cube_id,limit)).fetchall()
    conn.close(); return [dict(r) for r in rows]
