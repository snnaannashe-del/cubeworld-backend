"""
CubeWorld Database — dual-mode: PostgreSQL (Render) or SQLite (local)
  - DATABASE_URL env var  → psycopg2 / PostgreSQL
  - Otherwise             → sqlite3 (local dev)
"""
import os
import hashlib
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL")  # set automatically by Render

# ─────────────────────────────── connection ───────────────────────────────────

if DATABASE_URL:
    import psycopg2
    import psycopg2.extras

    def get_db():
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = False
        return conn

    # PostgreSQL: placeholder is %s, no row_factory needed
    _PH = "%s"
    _PG = True
else:
    import sqlite3
    DB_PATH = os.getenv("DB_PATH", "cubeworld.db")

    def get_db():
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    _PH = "?"
    _PG = False


def _q(sql: str) -> str:
    """Replace ? with %s for PostgreSQL."""
    return sql.replace("?", "%s") if _PG else sql


def _row(r):
    return dict(r) if r else None


def _fetchall(rows):
    return [dict(r) for r in rows]


def _now_expr():
    return "NOW()" if _PG else "datetime('now')"


def _now_plus_hours(h_param):
    """SQL expression for NOW + h hours (h_param is SQL parameter %s / ?)."""
    if _PG:
        return f"NOW() + ({h_param} || ' hours')::INTERVAL"
    return f"datetime('now','+'||{h_param}||' hours')"


def _execute_returning(conn, sql, params=()):
    """INSERT ... RETURNING id  (PG) or lastrowid (SQLite)."""
    if _PG:
        c = conn.cursor()
        c.execute(sql + " RETURNING id", params)
        rid = c.fetchone()["id"]
        return rid
    else:
        c = conn.cursor()
        c.execute(sql, params)
        return c.lastrowid


# ──────────────────────────────── schema ──────────────────────────────────────

def init_db():
    conn = get_db()
    c = conn.cursor()

    if _PG:
        # PostgreSQL schema
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            key_hash TEXT NOT NULL UNIQUE,
            key_prefix TEXT NOT NULL,
            key_type TEXT NOT NULL DEFAULT 'free',
            display_name TEXT,
            avatar_url TEXT,
            cube_balance REAL NOT NULL DEFAULT 0.0,
            premium_expires_at TEXT,
            referrer_id INTEGER,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            last_seen TIMESTAMP NOT NULL DEFAULT NOW(),
            is_active INTEGER NOT NULL DEFAULT 1,
            account_type TEXT NOT NULL DEFAULT 'public'
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS sessions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMP NOT NULL,
            last_used TIMESTAMP NOT NULL DEFAULT NOW(),
            user_agent TEXT,
            ip_hash TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS cube_transactions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            amount REAL NOT NULL,
            tx_type TEXT NOT NULL,
            description TEXT,
            tx_hash TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS wallets (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            address TEXT NOT NULL UNIQUE,
            chain_id INTEGER NOT NULL DEFAULT 137,
            linked_at TIMESTAMP NOT NULL DEFAULT NOW()
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS key_upgrades (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            from_type TEXT NOT NULL,
            to_type TEXT NOT NULL,
            cube_spent REAL NOT NULL DEFAULT 0.0,
            tx_hash TEXT,
            upgraded_at TIMESTAMP NOT NULL DEFAULT NOW()
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS cubes (
            id SERIAL PRIMARY KEY,
            owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            icon TEXT DEFAULT '📦',
            color TEXT DEFAULT '#0095F6',
            type TEXT NOT NULL DEFAULT 'public',
            life_hours INTEGER NOT NULL DEFAULT 24,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMP NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            cube_key TEXT UNIQUE
        )""")
        # Migrations: add columns if missing (PG-safe)
        for tbl, col, defn in [
            ('cubes', 'cube_key', 'TEXT'),
            ('users', 'username', 'TEXT UNIQUE'),
            ('cubes', 'handle', 'TEXT UNIQUE'),
            ('users', 'account_type', "TEXT NOT NULL DEFAULT 'public'"),
        ]:
            c.execute("""SELECT column_name FROM information_schema.columns
                         WHERE table_name=%s AND column_name=%s""", (tbl, col))
            if not c.fetchone():
                c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {defn}")
        # Add group_key + post_type columns
        for tbl, col, defn in [
            ('groups', 'group_key', 'TEXT UNIQUE'),
            ('posts', 'post_type', "TEXT NOT NULL DEFAULT 'short'"),
            ('posts', 'image_url', 'TEXT'),
            ('posts', 'view_count', 'INTEGER NOT NULL DEFAULT 0'),
        ]:
            c.execute("""SELECT column_name FROM information_schema.columns
                         WHERE table_name=%s AND column_name=%s""", (tbl, col))
            if not c.fetchone():
                try: c.execute(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col} {defn}")
                except Exception: pass
        conn.commit()
        c.execute("""CREATE TABLE IF NOT EXISTS groups (
            id SERIAL PRIMARY KEY,
            owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            icon TEXT DEFAULT '👥',
            type TEXT NOT NULL DEFAULT 'public',
            handle TEXT UNIQUE,
            group_key TEXT UNIQUE,
            member_count INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS group_messages (
            id SERIAL PRIMARY KEY,
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            display_name TEXT,
            content TEXT NOT NULL,
            msg_type TEXT NOT NULL DEFAULT 'text',
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS group_members (
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            joined_at TIMESTAMP NOT NULL DEFAULT NOW(),
            PRIMARY KEY (group_id, user_id)
        )""")
        conn.commit()
        c.execute("""CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            cube_id INTEGER NOT NULL,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            display_name TEXT,
            content TEXT NOT NULL,
            msg_type TEXT NOT NULL DEFAULT 'text',
            reply_to_id INTEGER,
            expires_at TIMESTAMP,
            file_name TEXT,
            file_size TEXT,
            file_data TEXT,
            duration INTEGER,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS message_reactions (
            id SERIAL PRIMARY KEY,
            message_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            display_name TEXT,
            emoji TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(message_id, user_id, emoji)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS direct_messages (
            id SERIAL PRIMARY KEY,
            from_user_id INTEGER NOT NULL,
            to_user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            msg_type TEXT NOT NULL DEFAULT 'text',
            file_name TEXT,
            file_size TEXT,
            file_data TEXT,
            duration INTEGER,
            reply_to_id INTEGER,
            expires_at TIMESTAMP,
            read_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS posts (
            id SERIAL PRIMARY KEY,
            cube_id INTEGER NOT NULL,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            display_name TEXT,
            content TEXT NOT NULL,
            likes INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS post_likes (
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (post_id, user_id)
        )""")
        # Video feed columns (PG safe ADD IF NOT EXISTS)
        for _col, _def in [('video_url','TEXT'), ('description','TEXT'), ('music','TEXT'),
                            ('views','INTEGER NOT NULL DEFAULT 0'), ('comment_count','INTEGER NOT NULL DEFAULT 0')]:
            try: c.execute(f"ALTER TABLE posts ADD COLUMN IF NOT EXISTS {_col} {_def}")
            except: pass
        c.execute("""CREATE TABLE IF NOT EXISTS follows (
            follower_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            following_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            PRIMARY KEY (follower_id, following_id)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS post_comments (
            id SERIAL PRIMARY KEY,
            post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            display_name TEXT,
            content TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS signals (
            id SERIAL PRIMARY KEY,
            cube_id INTEGER NOT NULL,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            display_name TEXT,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'LONG',
            entry_price REAL,
            tp_price REAL,
            sl_price REAL,
            content TEXT DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS user_activity (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            date TEXT NOT NULL,
            online_minutes INTEGER NOT NULL DEFAULT 0,
            messages_sent INTEGER NOT NULL DEFAULT 0,
            posts_created INTEGER NOT NULL DEFAULT 0,
            reactions_received INTEGER NOT NULL DEFAULT 0,
            voice_messages INTEGER NOT NULL DEFAULT 0,
            invites_converted INTEGER NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            UNIQUE(user_id, date)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS premium_subscriptions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            started_at TIMESTAMP NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMP NOT NULL,
            price_usd REAL NOT NULL DEFAULT 6.99,
            payment_method TEXT,
            tx_hash TEXT,
            status TEXT NOT NULL DEFAULT 'active'
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS reward_pool (
            id INTEGER PRIMARY KEY,
            total_usd REAL NOT NULL DEFAULT 1000000,
            used_usd REAL NOT NULL DEFAULT 0,
            monthly_usd REAL NOT NULL DEFAULT 83333
        )""")
        c.execute("""INSERT INTO reward_pool (id,total_usd,used_usd,monthly_usd)
                     VALUES (1,1000000,0,83333)
                     ON CONFLICT (id) DO NOTHING""")
        c.execute("""CREATE TABLE IF NOT EXISTS reward_claims (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            month TEXT NOT NULL,
            score REAL NOT NULL DEFAULT 0,
            usd_amount REAL NOT NULL DEFAULT 0,
            wallet_address TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            paid_at TIMESTAMP,
            UNIQUE(user_id, month)
        )""")
    else:
        # SQLite schema (original)
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT NOT NULL UNIQUE,
            key_prefix TEXT NOT NULL,
            key_type TEXT NOT NULL DEFAULT 'free',
            display_name TEXT,
            avatar_url TEXT,
            cube_balance REAL NOT NULL DEFAULT 0.0,
            premium_expires_at TEXT,
            referrer_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_seen TEXT NOT NULL DEFAULT (datetime('now')),
            is_active INTEGER NOT NULL DEFAULT 1,
            account_type TEXT NOT NULL DEFAULT 'public'
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            last_used TEXT NOT NULL DEFAULT (datetime('now')),
            user_agent TEXT,
            ip_hash TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS cube_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            amount REAL NOT NULL,
            tx_type TEXT NOT NULL,
            description TEXT,
            tx_hash TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            address TEXT NOT NULL UNIQUE,
            chain_id INTEGER NOT NULL DEFAULT 137,
            linked_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS key_upgrades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            from_type TEXT NOT NULL,
            to_type TEXT NOT NULL,
            cube_spent REAL NOT NULL DEFAULT 0.0,
            tx_hash TEXT,
            upgraded_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS cubes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            icon TEXT DEFAULT '📦',
            color TEXT DEFAULT '#0095F6',
            type TEXT NOT NULL DEFAULT 'public',
            life_hours INTEGER NOT NULL DEFAULT 24,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            cube_key TEXT UNIQUE
        )""")
        # Migrations: add missing columns (SQLite)
        def _sqlite_add_col(table, col, defn):
            cols = [row[1] for row in c.execute(f"PRAGMA table_info({table})").fetchall()]
            if col not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
        _sqlite_add_col('cubes', 'cube_key', 'TEXT UNIQUE')
        _sqlite_add_col('users', 'username', 'TEXT UNIQUE')
        _sqlite_add_col('cubes', 'handle', 'TEXT UNIQUE')
        _sqlite_add_col('users', 'account_type', "TEXT NOT NULL DEFAULT 'public'")
        _sqlite_add_col('groups', 'group_key', 'TEXT UNIQUE')
        _sqlite_add_col('posts', 'post_type', "TEXT NOT NULL DEFAULT 'short'")
        _sqlite_add_col('posts', 'image_url', 'TEXT')
        _sqlite_add_col('posts', 'view_count', 'INTEGER NOT NULL DEFAULT 0')
        conn.commit()
        c.execute("""CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            icon TEXT DEFAULT '👥',
            type TEXT NOT NULL DEFAULT 'public',
            handle TEXT UNIQUE,
            group_key TEXT UNIQUE,
            member_count INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS group_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            display_name TEXT,
            content TEXT NOT NULL,
            msg_type TEXT NOT NULL DEFAULT 'text',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS group_members (
            group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            joined_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (group_id, user_id)
        )""")
        conn.commit()
        c.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cube_id INTEGER NOT NULL,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            display_name TEXT,
            content TEXT NOT NULL,
            msg_type TEXT NOT NULL DEFAULT 'text',
            reply_to_id INTEGER,
            expires_at TEXT,
            file_name TEXT,
            file_size TEXT,
            file_data TEXT,
            duration INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS message_reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            display_name TEXT,
            emoji TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(message_id, user_id, emoji)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS direct_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user_id INTEGER NOT NULL,
            to_user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            msg_type TEXT NOT NULL DEFAULT 'text',
            file_name TEXT,
            file_size TEXT,
            file_data TEXT,
            duration INTEGER,
            reply_to_id INTEGER,
            expires_at TEXT,
            read_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cube_id INTEGER NOT NULL,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            display_name TEXT,
            content TEXT NOT NULL,
            likes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS post_likes (
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (post_id, user_id)
        )""")
        # Video feed extras (migrations so existing DBs get new columns)
        _sqlite_add_col('posts', 'video_url', 'TEXT DEFAULT NULL')
        _sqlite_add_col('posts', 'description', 'TEXT DEFAULT NULL')
        _sqlite_add_col('posts', 'music', 'TEXT DEFAULT NULL')
        _sqlite_add_col('posts', 'views', 'INTEGER NOT NULL DEFAULT 0')
        _sqlite_add_col('posts', 'comment_count', 'INTEGER NOT NULL DEFAULT 0')
        c.execute("""CREATE TABLE IF NOT EXISTS follows (
            follower_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            following_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (follower_id, following_id)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS post_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            display_name TEXT,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        conn.commit()
        c.execute("""CREATE TABLE IF NOT EXISTS signals (
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
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS user_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            date TEXT NOT NULL,
            online_minutes INTEGER NOT NULL DEFAULT 0,
            messages_sent INTEGER NOT NULL DEFAULT 0,
            posts_created INTEGER NOT NULL DEFAULT 0,
            reactions_received INTEGER NOT NULL DEFAULT 0,
            voice_messages INTEGER NOT NULL DEFAULT 0,
            invites_converted INTEGER NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            UNIQUE(user_id, date)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS premium_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            price_usd REAL NOT NULL DEFAULT 6.99,
            payment_method TEXT,
            tx_hash TEXT,
            status TEXT NOT NULL DEFAULT 'active'
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS reward_pool (
            id INTEGER PRIMARY KEY,
            total_usd REAL NOT NULL DEFAULT 1000000,
            used_usd REAL NOT NULL DEFAULT 0,
            monthly_usd REAL NOT NULL DEFAULT 83333
        )""")
        c.execute("INSERT OR IGNORE INTO reward_pool (id,total_usd,used_usd,monthly_usd) VALUES (1,1000000,0,83333)")
        c.execute("""CREATE TABLE IF NOT EXISTS reward_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            month TEXT NOT NULL,
            score REAL NOT NULL DEFAULT 0,
            usd_amount REAL NOT NULL DEFAULT 0,
            wallet_address TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            paid_at TEXT,
            UNIQUE(user_id, month)
        )""")
        # Safe migrations for existing SQLite DBs
        for col_sql in [
            "ALTER TABLE users ADD COLUMN premium_expires_at TEXT",
            "ALTER TABLE users ADD COLUMN referrer_id INTEGER",
            "ALTER TABLE messages ADD COLUMN msg_type TEXT NOT NULL DEFAULT 'text'",
            "ALTER TABLE messages ADD COLUMN reply_to_id INTEGER",
            "ALTER TABLE messages ADD COLUMN expires_at TEXT",
            "ALTER TABLE messages ADD COLUMN file_name TEXT",
            "ALTER TABLE messages ADD COLUMN file_size TEXT",
            "ALTER TABLE messages ADD COLUMN file_data TEXT",
            "ALTER TABLE messages ADD COLUMN duration INTEGER",
        ]:
            try: c.execute(col_sql)
            except Exception: pass

    conn.commit()
    conn.close()


# ── Auth ──────────────────────────────────────────────────────────────────────

def hash_key(raw_key):
    return hashlib.sha256(raw_key.encode()).hexdigest()

def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()

def get_user_by_key_hash(key_hash):
    conn = get_db()
    c = conn.cursor()
    c.execute(_q("SELECT * FROM users WHERE key_hash=? AND is_active=1"), (key_hash,))
    user = _row(c.fetchone())
    conn.close(); return user

def get_user_by_id(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute(_q("SELECT * FROM users WHERE id=? AND is_active=1"), (user_id,))
    user = _row(c.fetchone())
    conn.close(); return user

def create_user(key_hash, key_prefix, key_type="free"):
    conn = get_db()
    uid = _execute_returning(
        conn,
        _q("INSERT INTO users (key_hash,key_prefix,key_type) VALUES (?,?,?)"),
        (key_hash, key_prefix, key_type))
    conn.commit(); conn.close(); return uid

def update_last_seen(user_id):
    conn = get_db(); c = conn.cursor()
    c.execute(_q(f"UPDATE users SET last_seen={_now_expr()} WHERE id=?"), (user_id,))
    conn.commit(); conn.close()

def update_profile(user_id, display_name=None, avatar_url=None):
    conn = get_db(); c = conn.cursor()
    if display_name is not None:
        c.execute(_q("UPDATE users SET display_name=? WHERE id=?"), (display_name, user_id))
    if avatar_url is not None:
        c.execute(_q("UPDATE users SET avatar_url=? WHERE id=?"), (avatar_url, user_id))
    conn.commit(); conn.close()

def get_display_name(user_id):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT display_name,key_prefix FROM users WHERE id=?"), (user_id,))
    row = c.fetchone()
    conn.close()
    if not row: return "Unknown"
    row = dict(row)
    return row["display_name"] or f"CUBE-{row['key_prefix']}"

def search_users(query: str, limit: int = 20, exclude_id: int = None):
    """
    Search users by @username, display_name, key_prefix, or #ID.
    #42  → direct numeric UID lookup
    @ann → search username field
    ann  → search display_name + key_prefix
    """
    conn = get_db(); c = conn.cursor()

    excl = f"AND id != {int(exclude_id)}" if exclude_id else ""

    # #42 — direct UID lookup
    q = query.strip()
    if q.startswith('#') and q[1:].isdigit():
        uid = int(q[1:])
        c.execute(_q(f"SELECT id,display_name,username,key_prefix,avatar_url,key_type,last_seen FROM users WHERE id=? AND is_active=1 {excl}"), (uid,))
        row = c.fetchone()
        conn.close()
        return [dict(row)] if row else []

    # Remove @ prefix for username search
    if q.startswith('@'):
        q = q[1:]

    if not q:
        conn.close()
        return []

    if _PG:
        pattern = f"%{q}%"
        c.execute(f"""SELECT id,display_name,username,key_prefix,avatar_url,key_type,last_seen
                      FROM users
                      WHERE is_active=1
                        AND (username ILIKE %s OR display_name ILIKE %s OR key_prefix ILIKE %s)
                        {excl}
                      ORDER BY last_seen DESC LIMIT %s""",
                  (pattern, pattern, pattern, limit))
    else:
        pattern = f"%{q}%"
        c.execute(f"""SELECT id,display_name,username,key_prefix,avatar_url,key_type,last_seen
                      FROM users
                      WHERE is_active=1
                        AND display_name IS NOT NULL AND display_name != ''
                        AND last_seen >= datetime('now', '-30 days')
                        AND (username LIKE ? OR display_name LIKE ? OR key_prefix LIKE ?)
                        {excl}
                      ORDER BY last_seen DESC LIMIT ?""",
                  (pattern, pattern, pattern, limit))

    rows = c.fetchall()
    conn.close()
    return _fetchall(rows)

def search_cubes(query: str, limit: int = 20):
    """Search cubes by @handle, name. #G42 = cube id lookup."""
    conn = get_db(); c = conn.cursor()
    q = query.strip()

    # #G42 or #42 — direct ID lookup
    if q.startswith('#'):
        uid_str = q.lstrip('#Gg')
        if uid_str.isdigit():
            c.execute(_q("""SELECT id,name,description,icon,color,type,handle,cube_key,
                                   life_hours,expires_at FROM cubes WHERE id=? AND is_active=1"""), (int(uid_str),))
            row = c.fetchone()
            conn.close()
            return [dict(row)] if row else []

    if q.startswith('@'):
        q = q[1:]
    if not q:
        conn.close()
        return []

    if _PG:
        pattern = f"%{q}%"
        c.execute("""SELECT id,name,description,icon,color,type,handle,cube_key,life_hours,
                            CAST(EXTRACT(EPOCH FROM (expires_at - NOW())) AS INTEGER) as life_left_seconds
                     FROM cubes WHERE is_active=1 AND expires_at>NOW()
                       AND (handle ILIKE %s OR name ILIKE %s)
                     ORDER BY created_at DESC LIMIT %s""", (pattern, pattern, limit))
    else:
        pattern = f"%{q}%"
        c.execute("""SELECT id,name,description,icon,color,type,handle,cube_key,life_hours,
                            CAST((julianday(expires_at)-julianday('now'))*86400 AS INTEGER) as life_left_seconds
                     FROM cubes WHERE is_active=1 AND expires_at>datetime('now')
                       AND (handle LIKE ? OR name LIKE ?)
                     ORDER BY created_at DESC LIMIT ?""", (pattern, pattern, limit))

    rows = c.fetchall()
    conn.close()
    return _fetchall(rows)

# ── Groups ───────────────────────────────────────────────────────────────────

def create_group(owner_id: int, name: str, description: str, icon: str, gtype: str):
    """Create a group and auto-add owner as member. Returns group_id."""
    conn = get_db(); c = conn.cursor()
    try:
        gid = _execute_returning(
            conn,
            _q("INSERT INTO groups (owner_id,name,description,icon,type) VALUES (?,?,?,?,?)"),
            (owner_id, name, description, icon, gtype)
        )
        c.execute(_q("INSERT INTO group_members (group_id,user_id) VALUES (?,?)"), (gid, owner_id))
        conn.commit(); conn.close()
        return gid
    except Exception:
        if _PG: conn.rollback()
        conn.close(); return None

def get_groups(limit: int = 50, user_id: int = None):
    """List public groups, with is_member flag if user_id given."""
    conn = get_db(); c = conn.cursor()
    if _PG:
        if user_id:
            c.execute("""SELECT g.id,g.owner_id,g.name,g.description,g.icon,g.type,g.handle,g.member_count,g.created_at,
                                EXISTS(SELECT 1 FROM group_members gm WHERE gm.group_id=g.id AND gm.user_id=%s) as is_member
                         FROM groups g WHERE g.type='public' ORDER BY g.member_count DESC LIMIT %s""", (user_id, limit))
        else:
            c.execute("""SELECT id,owner_id,name,description,icon,type,handle,member_count,created_at,false as is_member
                         FROM groups WHERE type='public' ORDER BY member_count DESC LIMIT %s""", (limit,))
    else:
        if user_id:
            c.execute("""SELECT g.id,g.owner_id,g.name,g.description,g.icon,g.type,g.handle,g.member_count,g.created_at,
                                CASE WHEN gm.user_id IS NOT NULL THEN 1 ELSE 0 END as is_member
                         FROM groups g LEFT JOIN group_members gm ON gm.group_id=g.id AND gm.user_id=?
                         WHERE g.type='public' ORDER BY g.member_count DESC LIMIT ?""", (user_id, limit))
        else:
            c.execute("""SELECT id,owner_id,name,description,icon,type,handle,member_count,created_at,0 as is_member
                         FROM groups WHERE type='public' ORDER BY member_count DESC LIMIT ?""", (limit,))
    rows = _fetchall(c.fetchall()); conn.close()
    return rows

def get_my_groups(user_id: int):
    """Groups where user is a member (including private ones)."""
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute("""SELECT g.id,g.owner_id,g.name,g.description,g.icon,g.type,g.handle,g.member_count,g.created_at,
                            true as is_member
                     FROM groups g JOIN group_members gm ON gm.group_id=g.id
                     WHERE gm.user_id=%s ORDER BY gm.joined_at DESC""", (user_id,))
    else:
        c.execute("""SELECT g.id,g.owner_id,g.name,g.description,g.icon,g.type,g.handle,g.member_count,g.created_at,
                            1 as is_member
                     FROM groups g JOIN group_members gm ON gm.group_id=g.id
                     WHERE gm.user_id=? ORDER BY gm.joined_at DESC""", (user_id,))
    rows = _fetchall(c.fetchall()); conn.close()
    return rows

def join_group(group_id: int, user_id: int):
    """Join a public group. Returns True/False."""
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(_q("SELECT id,type FROM groups WHERE id=?"), (group_id,))
        g = c.fetchone()
        if not g: conn.close(); return False
        if _PG:
            c.execute("INSERT INTO group_members (group_id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                      (group_id, user_id))
            c.execute("UPDATE groups SET member_count = (SELECT COUNT(*) FROM group_members WHERE group_id=%s) WHERE id=%s",
                      (group_id, group_id))
        else:
            c.execute("INSERT OR IGNORE INTO group_members (group_id,user_id) VALUES (?,?)", (group_id, user_id))
            c.execute("UPDATE groups SET member_count = (SELECT COUNT(*) FROM group_members WHERE group_id=?) WHERE id=?",
                      (group_id, group_id))
        conn.commit(); conn.close(); return True
    except Exception:
        if _PG: conn.rollback()
        conn.close(); return False

def leave_group(group_id: int, user_id: int):
    """Leave a group (owner cannot leave)."""
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(_q("SELECT owner_id FROM groups WHERE id=?"), (group_id,))
        g = c.fetchone()
        if not g: conn.close(); return False
        if dict(g).get('owner_id') == user_id: conn.close(); return False  # owner can't leave
        c.execute(_q("DELETE FROM group_members WHERE group_id=? AND user_id=?"), (group_id, user_id))
        if _PG:
            c.execute("UPDATE groups SET member_count = (SELECT COUNT(*) FROM group_members WHERE group_id=%s) WHERE id=%s",
                      (group_id, group_id))
        else:
            c.execute("UPDATE groups SET member_count = (SELECT COUNT(*) FROM group_members WHERE group_id=?) WHERE id=?",
                      (group_id, group_id))
        conn.commit(); conn.close(); return True
    except Exception:
        if _PG: conn.rollback()
        conn.close(); return False

def search_groups(query: str, limit: int = 20, user_id: int = None):
    """Search groups by name, description, or #G42 / @handle."""
    conn = get_db(); c = conn.cursor()
    q = query.strip()
    uid_col = "EXISTS(SELECT 1 FROM group_members gm WHERE gm.group_id=g.id AND gm.user_id=%s) as is_member" if _PG else \
              "CASE WHEN gm2.user_id IS NOT NULL THEN 1 ELSE 0 END as is_member"

    if q.startswith('#'):
        raw = q.lstrip('#Gg')
        if raw.isdigit():
            gid = int(raw)
            if _PG:
                c.execute(f"""SELECT g.id,g.owner_id,g.name,g.description,g.icon,g.type,g.handle,g.member_count,g.created_at,
                                     {uid_col}
                              FROM groups g WHERE g.id=%s""",
                           (user_id, gid) if user_id else (gid,))
            else:
                c.execute("""SELECT g.id,g.owner_id,g.name,g.description,g.icon,g.type,g.handle,g.member_count,g.created_at,
                                    CASE WHEN gm2.user_id IS NOT NULL THEN 1 ELSE 0 END as is_member
                             FROM groups g LEFT JOIN group_members gm2 ON gm2.group_id=g.id AND gm2.user_id=?
                             WHERE g.id=?""", (user_id or 0, gid))
            row = c.fetchone(); conn.close()
            return [dict(row)] if row else []

    if q.startswith('@'):
        q = q[1:]
    if not q:
        conn.close(); return []

    pattern = f"%{q}%"
    if _PG:
        c.execute(f"""SELECT g.id,g.owner_id,g.name,g.description,g.icon,g.type,g.handle,g.member_count,g.created_at,
                             {uid_col}
                      FROM groups g WHERE g.name ILIKE %s OR g.description ILIKE %s OR g.handle ILIKE %s
                      ORDER BY g.member_count DESC LIMIT %s""",
                  (user_id, pattern, pattern, pattern, limit) if user_id else (pattern, pattern, pattern, limit))
    else:
        c.execute("""SELECT g.id,g.owner_id,g.name,g.description,g.icon,g.type,g.handle,g.member_count,g.created_at,
                            CASE WHEN gm2.user_id IS NOT NULL THEN 1 ELSE 0 END as is_member
                     FROM groups g LEFT JOIN group_members gm2 ON gm2.group_id=g.id AND gm2.user_id=?
                     WHERE g.name LIKE ? OR g.description LIKE ? OR g.handle LIKE ?
                     ORDER BY g.member_count DESC LIMIT ?""",
                  (user_id or 0, pattern, pattern, pattern, limit))
    rows = _fetchall(c.fetchall()); conn.close()
    return rows

def set_group_handle(group_id: int, owner_id: int, handle: str):
    """Set @handle for a group. Only owner."""
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(_q("UPDATE groups SET handle=? WHERE id=? AND owner_id=?"),
                  (handle.lower(), group_id, owner_id))
        conn.commit(); conn.close(); return True
    except Exception:
        if _PG: conn.rollback()
        conn.close(); return False

def set_account_type(user_id: int, account_type: str):
    """Set account_type: 'public' or 'hidden'."""
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(_q("UPDATE users SET account_type=? WHERE id=?"), (account_type, user_id))
        conn.commit(); conn.close(); return True
    except Exception:
        if _PG: conn.rollback()
        conn.close(); return False

def set_username(user_id: int, username: str):
    """Set or update @username. Returns True on success, False if taken."""
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(_q("UPDATE users SET username=? WHERE id=?"), (username.lower(), user_id))
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.rollback(); conn.close()
        return False

def set_cube_handle(cube_id: int, owner_id: int, handle: str):
    """Set @handle for a cube. Only owner can set it."""
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(_q("UPDATE cubes SET handle=? WHERE id=? AND owner_id=?"),
                  (handle.lower(), cube_id, owner_id))
        conn.commit(); conn.close()
        return True
    except Exception:
        conn.rollback(); conn.close()
        return False

def create_session(user_id, token_hash, expires_at, user_agent=None, ip_hash=None):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("INSERT INTO sessions (user_id,token_hash,expires_at,user_agent,ip_hash) VALUES (?,?,?,?,?)"),
              (user_id, token_hash, expires_at, user_agent, ip_hash))
    conn.commit(); conn.close()

def get_session_by_token_hash(token_hash):
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute("""SELECT s.*,u.key_type,u.key_prefix,u.cube_balance,u.display_name,u.id as user_id
                     FROM sessions s JOIN users u ON u.id=s.user_id
                     WHERE s.token_hash=%s AND s.expires_at>NOW()""", (token_hash,))
    else:
        c.execute("""SELECT s.*,u.key_type,u.key_prefix,u.cube_balance,u.display_name,u.id as user_id
                     FROM sessions s JOIN users u ON u.id=s.user_id
                     WHERE s.token_hash=? AND s.expires_at>datetime('now')""", (token_hash,))
    row = _row(c.fetchone())
    conn.close(); return row

def delete_session(token_hash):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("DELETE FROM sessions WHERE token_hash=?"), (token_hash,))
    conn.commit(); conn.close()

def add_cube_balance(user_id, amount, tx_type, description=None, tx_hash=None):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("UPDATE users SET cube_balance=cube_balance+? WHERE id=?"), (amount, user_id))
    c.execute(_q("INSERT INTO cube_transactions (user_id,amount,tx_type,description,tx_hash) VALUES (?,?,?,?,?)"),
              (user_id, amount, tx_type, description, tx_hash))
    conn.commit(); conn.close()

def get_cube_balance(user_id):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT cube_balance FROM users WHERE id=?"), (user_id,))
    row = c.fetchone()
    conn.close(); return (dict(row)["cube_balance"] if row else 0.0)

def upgrade_key(user_id, to_type, cube_spent=0.0, tx_hash=None):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT key_type FROM users WHERE id=?"), (user_id,))
    cur = c.fetchone()
    from_type = dict(cur)["key_type"] if cur else "free"
    c.execute(_q("UPDATE users SET key_type=?,cube_balance=cube_balance-? WHERE id=?"), (to_type, cube_spent, user_id))
    c.execute(_q("INSERT INTO key_upgrades (user_id,from_type,to_type,cube_spent,tx_hash) VALUES (?,?,?,?,?)"),
              (user_id, from_type, to_type, cube_spent, tx_hash))
    conn.commit(); conn.close()

def link_wallet(user_id, address, chain_id=137):
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute("""INSERT INTO wallets (user_id,address,chain_id) VALUES (%s,%s,%s)
                     ON CONFLICT (address) DO UPDATE SET user_id=EXCLUDED.user_id, chain_id=EXCLUDED.chain_id""",
                  (user_id, address.lower(), chain_id))
    else:
        c.execute("INSERT OR REPLACE INTO wallets (user_id,address,chain_id) VALUES (?,?,?)",
                  (user_id, address.lower(), chain_id))
    conn.commit(); conn.close()

def get_stats():
    conn = get_db(); c = conn.cursor()
    now_expr = _now_expr()
    c.execute("SELECT COUNT(*) as n FROM users WHERE is_active=1"); total_users = c.fetchone()["n"]
    c.execute("SELECT COUNT(*) as n FROM users WHERE key_type='premium' AND is_active=1"); premium = c.fetchone()["n"]
    if _PG:
        c.execute("SELECT COUNT(*) as n FROM users WHERE last_seen>NOW()-INTERVAL '5 minutes'")
    else:
        c.execute("SELECT COUNT(*) as n FROM users WHERE last_seen>datetime('now','-5 minutes')")
    online = c.fetchone()["n"]
    c.execute("SELECT COUNT(*) as n FROM cubes WHERE is_active=1"); total_cubes = c.fetchone()["n"]
    conn.close()
    return {"total_users": total_users, "premium_users": premium,
            "online_users": online, "total_cubes": total_cubes}


# ── Cubes ─────────────────────────────────────────────────────────────────────

def _gen_cube_key():
    """Generate a unique cube invite key: CK-XXXX-XXXX-XXXX"""
    import secrets, string
    chars = string.ascii_uppercase + string.digits
    seg = lambda: ''.join(secrets.choice(chars) for _ in range(4))
    return f"CK-{seg()}-{seg()}-{seg()}"

def create_cube(owner_id, name, description, icon, color, cube_type, life_hours):
    conn = get_db()
    # Generate unique cube key
    cube_key = _gen_cube_key()
    if _PG:
        sql = """INSERT INTO cubes (owner_id,name,description,icon,color,type,life_hours,expires_at,cube_key)
                 VALUES (%s,%s,%s,%s,%s,%s,%s, NOW() + (%s || ' hours')::INTERVAL, %s)"""
    else:
        sql = """INSERT INTO cubes (owner_id,name,description,icon,color,type,life_hours,expires_at,cube_key)
                 VALUES (?,?,?,?,?,?,?,datetime('now','+'||?||' hours'),?)"""
    cid = _execute_returning(conn, sql,
          (owner_id, name, description, icon, color, cube_type, life_hours, str(life_hours), cube_key))
    conn.commit(); conn.close()
    return cid, cube_key

def get_cube_by_key(cube_key: str):
    """Resolve a cube invite key → cube info (if still active/not expired)."""
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute("""SELECT id,owner_id,name,description,icon,color,type,life_hours,expires_at,cube_key,
                            CAST(EXTRACT(EPOCH FROM (expires_at - NOW())) AS INTEGER) as life_left_seconds
                     FROM cubes WHERE cube_key=%s AND is_active=1 AND expires_at>NOW()""", (cube_key,))
    else:
        c.execute("""SELECT id,owner_id,name,description,icon,color,type,life_hours,expires_at,cube_key,
                            CAST((julianday(expires_at)-julianday('now'))*86400 AS INTEGER) as life_left_seconds
                     FROM cubes WHERE cube_key=? AND is_active=1 AND expires_at>datetime('now')""", (cube_key,))
    row = c.fetchone(); conn.close()
    return dict(row) if row else None

def get_cube_key(cube_id: int, owner_id: int):
    """Return cube_key only to the cube owner."""
    conn = get_db(); c = conn.cursor()
    ph = '%s' if _PG else '?'
    c.execute(f"SELECT cube_key FROM cubes WHERE id={ph} AND owner_id={ph}", (cube_id, owner_id))
    row = c.fetchone(); conn.close()
    if not row: return None
    return row[0] if not _PG else row['cube_key']

def deactivate_expired_cubes():
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute("UPDATE cubes SET is_active=0 WHERE is_active=1 AND expires_at<=NOW()")
    else:
        c.execute("UPDATE cubes SET is_active=0 WHERE is_active=1 AND expires_at<=datetime('now')")
    conn.commit(); conn.close()

def list_cubes():
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute("""SELECT id,owner_id,name,description,icon,color,type,life_hours,created_at,expires_at,
                            CAST(EXTRACT(EPOCH FROM (expires_at - NOW())) AS INTEGER) as life_left_seconds
                     FROM cubes WHERE is_active=1 AND expires_at>NOW()
                     ORDER BY created_at DESC LIMIT 100""")
    else:
        c.execute("""SELECT id,owner_id,name,description,icon,color,type,life_hours,created_at,expires_at,
                            CAST((julianday(expires_at)-julianday('now'))*86400 AS INTEGER) as life_left_seconds
                     FROM cubes WHERE is_active=1 AND expires_at>datetime('now')
                     ORDER BY created_at DESC LIMIT 100""")
    rows = c.fetchall()
    conn.close(); return _fetchall(rows)


# ── Messages ──────────────────────────────────────────────────────────────────

def save_message(cube_id, user_id, display_name, content,
                 msg_type='text', reply_to_id=None, expires_at=None,
                 file_name=None, file_size=None, file_data=None, duration=None):
    conn = get_db()
    sql = _q("""INSERT INTO messages
       (cube_id,user_id,display_name,content,msg_type,reply_to_id,expires_at,file_name,file_size,file_data,duration)
       VALUES (?,?,?,?,?,?,?,?,?,?,?)""")
    mid = _execute_returning(conn, sql,
          (cube_id, user_id, display_name, content, msg_type,
           reply_to_id, expires_at, file_name, file_size, file_data, duration))
    conn.commit(); conn.close(); return mid

def get_messages(cube_id, limit=50):
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute("""SELECT id,cube_id,user_id,display_name,content,msg_type,reply_to_id,
                            file_name,file_size,duration,created_at
                     FROM messages
                     WHERE cube_id=%s AND (expires_at IS NULL OR expires_at>NOW())
                     ORDER BY created_at DESC LIMIT %s""", (cube_id, limit))
    else:
        c.execute("""SELECT id,cube_id,user_id,display_name,content,msg_type,reply_to_id,
                            file_name,file_size,duration,created_at
                     FROM messages
                     WHERE cube_id=? AND (expires_at IS NULL OR expires_at>datetime('now'))
                     ORDER BY created_at DESC LIMIT ?""", (cube_id, limit))
    rows = c.fetchall()
    conn.close(); return list(reversed(_fetchall(rows)))

def get_message_by_id(msg_id):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT id,display_name,content,msg_type FROM messages WHERE id=?"), (msg_id,))
    row = c.fetchone()
    conn.close(); return _row(row)

def delete_expired_messages():
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute("DELETE FROM messages WHERE expires_at IS NOT NULL AND expires_at<=NOW()")
        c.execute("DELETE FROM direct_messages WHERE expires_at IS NOT NULL AND expires_at<=NOW()")
    else:
        c.execute("DELETE FROM messages WHERE expires_at IS NOT NULL AND expires_at<=datetime('now')")
        c.execute("DELETE FROM direct_messages WHERE expires_at IS NOT NULL AND expires_at<=datetime('now')")
    conn.commit(); conn.close()


# ── Reactions ─────────────────────────────────────────────────────────────────

def toggle_reaction(message_id, user_id, display_name, emoji):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT id FROM message_reactions WHERE message_id=? AND user_id=? AND emoji=?"),
              (message_id, user_id, emoji))
    existing = c.fetchone()
    if existing:
        c.execute(_q("DELETE FROM message_reactions WHERE id=?"), (dict(existing)["id"],))
        added = False
    else:
        c.execute(_q("INSERT INTO message_reactions (message_id,user_id,display_name,emoji) VALUES (?,?,?,?)"),
                  (message_id, user_id, display_name, emoji))
        added = True
    conn.commit()
    c.execute(_q("SELECT emoji, COUNT(*) as cnt FROM message_reactions WHERE message_id=? GROUP BY emoji"),
              (message_id,))
    rows = c.fetchall()
    conn.close()
    return {"added": added, "counts": {r["emoji"]: r["cnt"] for r in _fetchall(rows)}}

def get_reactions(message_id):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT emoji, COUNT(*) as cnt FROM message_reactions WHERE message_id=? GROUP BY emoji"),
              (message_id,))
    rows = c.fetchall()
    conn.close(); return {r["emoji"]: r["cnt"] for r in _fetchall(rows)}


# ── Direct Messages ───────────────────────────────────────────────────────────

def save_dm(from_user_id, to_user_id, content, msg_type='text',
            file_name=None, file_size=None, file_data=None, duration=None,
            reply_to_id=None, expires_at=None):
    conn = get_db()
    sql = _q("""INSERT INTO direct_messages
       (from_user_id,to_user_id,content,msg_type,file_name,file_size,file_data,duration,reply_to_id,expires_at)
       VALUES (?,?,?,?,?,?,?,?,?,?)""")
    mid = _execute_returning(conn, sql,
          (from_user_id, to_user_id, content, msg_type,
           file_name, file_size, file_data, duration, reply_to_id, expires_at))
    conn.commit(); conn.close(); return mid

def get_dm_history(user1_id, user2_id, limit=50):
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute("""SELECT id,from_user_id,to_user_id,content,msg_type,file_name,file_size,duration,reply_to_id,created_at
                     FROM direct_messages
                     WHERE ((from_user_id=%s AND to_user_id=%s) OR (from_user_id=%s AND to_user_id=%s))
                       AND (expires_at IS NULL OR expires_at>NOW())
                     ORDER BY created_at DESC LIMIT %s""",
                  (user1_id, user2_id, user2_id, user1_id, limit))
    else:
        c.execute("""SELECT id,from_user_id,to_user_id,content,msg_type,file_name,file_size,duration,reply_to_id,created_at
                     FROM direct_messages
                     WHERE ((from_user_id=? AND to_user_id=?) OR (from_user_id=? AND to_user_id=?))
                       AND (expires_at IS NULL OR expires_at>datetime('now'))
                     ORDER BY created_at DESC LIMIT ?""",
                  (user1_id, user2_id, user2_id, user1_id, limit))
    rows = c.fetchall()
    conn.close(); return list(reversed(_fetchall(rows)))

def get_dm_inbox(user_id):
    """Return list of DM conversations: {peer_id, display_name, last_content, last_time, unread}"""
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute("""
            SELECT peer_id, display_name, last_content, last_time,
                   (SELECT COUNT(*) FROM direct_messages
                    WHERE from_user_id=peer_id AND to_user_id=%s AND read_at IS NULL) AS unread
            FROM (
                SELECT CASE WHEN from_user_id=%s THEN to_user_id ELSE from_user_id END AS peer_id,
                       CASE WHEN from_user_id=%s THEN (SELECT display_name FROM users WHERE id=to_user_id)
                            ELSE (SELECT display_name FROM users WHERE id=from_user_id) END AS display_name,
                       content AS last_content, created_at AS last_time,
                       ROW_NUMBER() OVER (PARTITION BY
                           CASE WHEN from_user_id=%s THEN to_user_id ELSE from_user_id END
                           ORDER BY created_at DESC) AS rn
                FROM direct_messages
                WHERE (from_user_id=%s OR to_user_id=%s)
                  AND (expires_at IS NULL OR expires_at>NOW())
            ) sub WHERE rn=1
            ORDER BY last_time DESC LIMIT 50
        """, (user_id, user_id, user_id, user_id, user_id, user_id))
    else:
        c.execute("""
            SELECT peer_id, display_name, last_content, last_time,
                   (SELECT COUNT(*) FROM direct_messages
                    WHERE from_user_id=peer_id AND to_user_id=? AND read_at IS NULL) AS unread
            FROM (
                SELECT CASE WHEN from_user_id=? THEN to_user_id ELSE from_user_id END AS peer_id,
                       CASE WHEN from_user_id=? THEN (SELECT display_name FROM users WHERE id=to_user_id)
                            ELSE (SELECT display_name FROM users WHERE id=from_user_id) END AS display_name,
                       content AS last_content, created_at AS last_time,
                       ROW_NUMBER() OVER (PARTITION BY
                           CASE WHEN from_user_id=? THEN to_user_id ELSE from_user_id END
                           ORDER BY created_at DESC) AS rn
                FROM direct_messages
                WHERE (from_user_id=? OR to_user_id=?)
                  AND (expires_at IS NULL OR expires_at>datetime('now'))
            ) sub WHERE rn=1
            ORDER BY last_time DESC LIMIT 50
        """, (user_id, user_id, user_id, user_id, user_id, user_id))
    rows = c.fetchall(); conn.close()
    result = []
    for r in _fetchall(rows):
        t = r.get('last_time','')
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(str(t).replace('Z','+00:00')) if t else None
            time_str = dt.strftime('%H:%M') if dt else ''
        except Exception:
            time_str = str(t)[:5] if t else ''
        result.append({
            'peer_id': r['peer_id'],
            'display_name': r.get('display_name') or f'User#{r["peer_id"]}',
            'last_msg': (r.get('last_content') or '')[:40],
            'time': time_str,
            'unread': r.get('unread') or 0
        })
    return result

def mark_dm_read(from_user_id, to_user_id):
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute("""UPDATE direct_messages SET read_at=NOW()
                     WHERE from_user_id=%s AND to_user_id=%s AND read_at IS NULL""",
                  (from_user_id, to_user_id))
    else:
        c.execute("""UPDATE direct_messages SET read_at=datetime('now')
                     WHERE from_user_id=? AND to_user_id=? AND read_at IS NULL""",
                  (from_user_id, to_user_id))
    conn.commit(); conn.close()


# ── Posts ─────────────────────────────────────────────────────────────────────

def create_post(cube_id, user_id, display_name, content):
    conn = get_db()
    sql = _q("INSERT INTO posts (cube_id,user_id,display_name,content) VALUES (?,?,?,?)")
    pid = _execute_returning(conn, sql, (cube_id, user_id, display_name, content))
    conn.commit(); conn.close(); return pid

def get_posts(cube_id, limit=50):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT id,cube_id,user_id,display_name,content,likes,created_at FROM posts WHERE cube_id=? ORDER BY created_at DESC LIMIT ?"),
              (cube_id, limit))
    rows = c.fetchall()
    conn.close(); return _fetchall(rows)

def like_post(post_id, user_id):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT 1 FROM post_likes WHERE post_id=? AND user_id=?"), (post_id, user_id))
    exists = c.fetchone()
    if exists:
        c.execute(_q("DELETE FROM post_likes WHERE post_id=? AND user_id=?"), (post_id, user_id))
        c.execute(_q("UPDATE posts SET likes=MAX(0,likes-1) WHERE id=?"), (post_id,))
        liked = False
    else:
        try:
            c.execute(_q("INSERT INTO post_likes (post_id,user_id) VALUES (?,?)"), (post_id, user_id))
            c.execute(_q("UPDATE posts SET likes=likes+1 WHERE id=?"), (post_id,))
            liked = True
        except Exception:
            if _PG: conn.rollback()
            liked = False
    conn.commit()
    c.execute(_q("SELECT likes FROM posts WHERE id=?"), (post_id,))
    row = c.fetchone()
    conn.close(); return (dict(row)["likes"] if row else 0, liked)


# ── Video Feed ────────────────────────────────────────────────────────────────

def create_video_post(cube_id, user_id, display_name, video_url, description, music):
    conn = get_db()
    sql = _q("INSERT INTO posts (cube_id,user_id,display_name,content,video_url,description,music) VALUES (?,?,?,?,?,?,?)")
    pid = _execute_returning(conn, sql, (cube_id, user_id, display_name, description or '', video_url or '', description or '', music or ''))
    conn.commit(); conn.close(); return pid

def _ensure_post_columns(conn):
    """Add new columns to posts table if missing (safe to call multiple times)."""
    c = conn.cursor()
    for col_sql in [
        "ALTER TABLE posts ADD COLUMN post_type TEXT DEFAULT 'short'",
        "ALTER TABLE posts ADD COLUMN image_url TEXT DEFAULT ''",
        "ALTER TABLE posts ADD COLUMN title TEXT DEFAULT ''",
        "ALTER TABLE posts ADD COLUMN tags TEXT DEFAULT '[]'",
    ]:
        try: c.execute(col_sql)
        except Exception: pass
    conn.commit()

def get_global_feed(limit=30, offset=0):
    import json as _json
    conn = get_db(); _ensure_post_columns(conn); c = conn.cursor()
    sql = _q("SELECT * FROM posts ORDER BY created_at DESC LIMIT ? OFFSET ?")
    c.execute(sql, (limit, offset))
    rows = _fetchall(c.fetchall()); conn.close()
    for r in rows:
        try: r['tags'] = _json.loads(r.get('tags') or '[]')
        except Exception: r['tags'] = []
        r.setdefault('image_url', ''); r.setdefault('post_type', 'short')
        r.setdefault('title', ''); r['view_count'] = r.get('views', 0)
    return rows

def get_following_feed(user_id, limit=30):
    import json as _json
    conn = get_db(); _ensure_post_columns(conn); c = conn.cursor()
    sql = _q("""SELECT p.* FROM posts p
                JOIN follows f ON f.following_id=p.user_id
                WHERE f.follower_id=?
                ORDER BY p.created_at DESC LIMIT ?""")
    c.execute(sql, (user_id, limit))
    rows = _fetchall(c.fetchall()); conn.close()
    for r in rows:
        try: r['tags'] = _json.loads(r.get('tags') or '[]')
        except Exception: r['tags'] = []
        r.setdefault('image_url', ''); r.setdefault('post_type', 'short')
        r.setdefault('title', ''); r['view_count'] = r.get('views', 0)
    return rows

def like_feed_post(post_id, user_id):
    return like_post(post_id, user_id)  # reuse existing

def get_post_comments(post_id, limit=100):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT id,post_id,user_id,display_name,content,created_at FROM post_comments WHERE post_id=? ORDER BY created_at ASC LIMIT ?"), (post_id, limit))
    rows = c.fetchall(); conn.close(); return _fetchall(rows)

def add_post_comment(post_id, user_id, display_name, content):
    conn = get_db()
    sql = _q("INSERT INTO post_comments (post_id,user_id,display_name,content) VALUES (?,?,?,?)")
    cid = _execute_returning(conn, sql, (post_id, user_id, display_name, content))
    # Increment comment_count
    conn2 = get_db(); c2 = conn2.cursor()
    c2.execute(_q("UPDATE posts SET comment_count=comment_count+1 WHERE id=?"), (post_id,))
    conn2.commit(); conn2.close()
    conn.commit(); conn.close(); return cid

def follow_user(follower_id, following_id):
    if follower_id == following_id: return False
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(_q("INSERT INTO follows (follower_id,following_id) VALUES (?,?)"), (follower_id, following_id))
        conn.commit(); conn.close(); return True
    except Exception:
        if _PG: conn.rollback()
        conn.close(); return False

def unfollow_user(follower_id, following_id):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("DELETE FROM follows WHERE follower_id=? AND following_id=?"), (follower_id, following_id))
    conn.commit(); conn.close(); return True

def is_following(follower_id, following_id):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT 1 FROM follows WHERE follower_id=? AND following_id=?"), (follower_id, following_id))
    row = c.fetchone(); conn.close(); return row is not None

def get_follow_counts(user_id):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT COUNT(*) as cnt FROM follows WHERE following_id=?"), (user_id,))
    followers = dict(c.fetchone())["cnt"]
    c.execute(_q("SELECT COUNT(*) as cnt FROM follows WHERE follower_id=?"), (user_id,))
    following = dict(c.fetchone())["cnt"]
    conn.close()
    return {"followers_count": followers, "following_count": following}

def reset_all_data():
    """Truncate all user data tables, keep schema. Then seed 8 default cubes."""
    conn = get_db(); c = conn.cursor()
    tables = [
        'reward_claims', 'premium_subscriptions', 'user_activity',
        'signals', 'post_comments', 'follows', 'post_likes', 'posts',
        'direct_messages', 'message_reactions', 'messages',
        'group_members', 'group_messages', 'groups',
        'cube_transactions', 'key_upgrades', 'wallets',
        'cubes', 'sessions', 'users',
    ]
    for t in tables:
        try:
            if _PG:
                c.execute(f"TRUNCATE TABLE {t} CASCADE")
            else:
                c.execute(f"DELETE FROM {t}")
        except Exception:
            if _PG: conn.rollback()
    if not _PG:
        try: c.execute("DELETE FROM sqlite_sequence")
        except Exception: pass
    # Reset reward pool
    if _PG:
        c.execute("DELETE FROM reward_pool")
        c.execute("INSERT INTO reward_pool (id,total_usd,used_usd,monthly_usd) VALUES (1,1000000,0,83333)")
    else:
        c.execute("DELETE FROM reward_pool")
        c.execute("INSERT OR IGNORE INTO reward_pool (id,total_usd,used_usd,monthly_usd) VALUES (1,1000000,0,83333)")
    conn.commit(); conn.close()
    seed_default_cubes()

def seed_default_cubes():
    """Insert 8 permanent system cubes (owner_id=NULL). Safe to call multiple times."""
    default_cubes = [
        ("CUBE·GLOBAL",   "🌐", "#0095F6", "Главный куб CubeWorld"),
        ("CUBE·CRYPTO",   "₿",  "#F7931A", "Крипто-сигналы и аналитика"),
        ("CUBE·TECH",     "🖥",  "#4CC9F0", "Технологии и AI"),
        ("CUBE·ART",      "🎨", "#D65DB1", "Искусство и дизайн"),
        ("CUBE·MUSIC",    "🎵", "#845EC2", "Музыка и культура"),
        ("CUBE·SPORTS",   "⚽", "#4ade80", "Спорт и здоровье"),
        ("CUBE·BUSINESS", "💼", "#F9C74F", "Бизнес и стартапы"),
        ("CUBE·GAMING",   "🎮", "#FF6B6B", "Игры и e-sports"),
    ]
    conn = get_db(); c = conn.cursor()
    for (name, icon, color, description) in default_cubes:
        key = "SYS-" + name.replace("·", "-")
        if _PG:
            c.execute("""INSERT INTO cubes
                         (owner_id,name,description,icon,color,type,life_hours,expires_at,cube_key,is_active)
                         VALUES (NULL,%s,%s,%s,%s,'public',876000,NOW()+'876000 hours'::interval,%s,1)
                         ON CONFLICT (cube_key) DO NOTHING""",
                      (name, description, icon, color, key))
        else:
            c.execute("""INSERT OR IGNORE INTO cubes
                         (owner_id,name,description,icon,color,type,life_hours,expires_at,cube_key,is_active)
                         VALUES (NULL,?,?,?,?,'public',876000,datetime('now','+876000 hours'),?,1)""",
                      (name, description, icon, color, key))
    conn.commit(); conn.close()

# ── Signals ───────────────────────────────────────────────────────────────────

def create_signal(cube_id, user_id, display_name, ticker, direction, entry_price, tp_price, sl_price, content):
    conn = get_db()
    sql = _q("""INSERT INTO signals (cube_id,user_id,display_name,ticker,direction,entry_price,tp_price,sl_price,content)
                VALUES (?,?,?,?,?,?,?,?,?)""")
    sid = _execute_returning(conn, sql,
          (cube_id, user_id, display_name, ticker, direction, entry_price, tp_price, sl_price, content))
    conn.commit(); conn.close(); return sid

def get_signals(cube_id, limit=30):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("""SELECT id,cube_id,user_id,display_name,ticker,direction,entry_price,tp_price,sl_price,content,created_at
                    FROM signals WHERE cube_id=? ORDER BY created_at DESC LIMIT ?"""),
              (cube_id, limit))
    rows = c.fetchall()
    conn.close(); return _fetchall(rows)


# ── Premium ───────────────────────────────────────────────────────────────────

def is_premium(user_id):
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute("""SELECT id FROM premium_subscriptions
                     WHERE user_id=%s AND status='active' AND expires_at>NOW()""", (user_id,))
    else:
        c.execute("""SELECT id FROM premium_subscriptions
                     WHERE user_id=? AND status='active' AND expires_at>datetime('now')""", (user_id,))
    row = c.fetchone()
    conn.close(); return row is not None

def activate_premium(user_id, months=1, payment_method=None, tx_hash=None):
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute("""SELECT NOW() + (%s || ' months')::INTERVAL AS exp""", (str(months),))
        expires = dict(c.fetchone())["exp"]
        c.execute("""INSERT INTO premium_subscriptions (user_id,expires_at,price_usd,payment_method,tx_hash,status)
                     VALUES (%s,%s,%s,%s,%s,'active')
                     ON CONFLICT (user_id) DO UPDATE
                       SET expires_at=EXCLUDED.expires_at, price_usd=EXCLUDED.price_usd,
                           payment_method=EXCLUDED.payment_method, tx_hash=EXCLUDED.tx_hash, status='active'""",
                  (user_id, expires, 6.99*months, payment_method, tx_hash))
        c.execute("UPDATE users SET key_type='premium', premium_expires_at=%s WHERE id=%s",
                  (str(expires), user_id))
    else:
        expires = conn.execute("SELECT datetime('now','+'||?||' months')", (months,)).fetchone()[0]
        conn.execute("""INSERT OR REPLACE INTO premium_subscriptions
                        (user_id,expires_at,price_usd,payment_method,tx_hash,status)
                        VALUES (?,?,?,?,?,'active')""",
                     (user_id, expires, 6.99*months, payment_method, tx_hash))
        conn.execute("UPDATE users SET key_type='premium', premium_expires_at=? WHERE id=?", (expires, user_id))
    conn.commit(); conn.close()

def get_premium_info(user_id):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT * FROM premium_subscriptions WHERE user_id=? ORDER BY id DESC LIMIT 1"), (user_id,))
    row = c.fetchone()
    conn.close(); return _row(row)


# ── Activity tracking ─────────────────────────────────────────────────────────

SCORE_WEIGHTS = {
    'online_minutes':     1.0,
    'messages_sent':      3.0,
    'voice_messages':     10.0,
    'posts_created':      25.0,
    'reactions_received': 6.0,
    'invites_converted':  250.0,
}

def _calc_score(row):
    return sum(row.get(k, 0) * w for k, w in SCORE_WEIGHTS.items())

def ping_activity(user_id):
    today = datetime.utcnow().strftime('%Y-%m-%d')
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute("""INSERT INTO user_activity (user_id,date,online_minutes) VALUES (%s,%s,1)
                     ON CONFLICT (user_id,date) DO UPDATE SET online_minutes=user_activity.online_minutes+1""",
                  (user_id, today))
        c.execute("SELECT * FROM user_activity WHERE user_id=%s AND date=%s", (user_id, today))
    else:
        c.execute("""INSERT INTO user_activity (user_id,date,online_minutes) VALUES (?,?,1)
                     ON CONFLICT(user_id,date) DO UPDATE SET online_minutes=online_minutes+1""",
                  (user_id, today))
        c.execute("SELECT * FROM user_activity WHERE user_id=? AND date=?", (user_id, today))
    row = c.fetchone()
    if row:
        score = _calc_score(dict(row))
        c.execute(_q("UPDATE user_activity SET score=? WHERE user_id=? AND date=?"),
                  (score, user_id, today))
    conn.commit(); conn.close()

def record_activity(user_id, event):
    col_map = {
        'message': 'messages_sent', 'post': 'posts_created',
        'reaction_received': 'reactions_received', 'voice': 'voice_messages',
        'invite': 'invites_converted',
    }
    col = col_map.get(event)
    if not col: return
    today = datetime.utcnow().strftime('%Y-%m-%d')
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute(f"""INSERT INTO user_activity (user_id,date,{col}) VALUES (%s,%s,1)
                      ON CONFLICT (user_id,date) DO UPDATE SET {col}=user_activity.{col}+1""",
                  (user_id, today))
        c.execute("SELECT * FROM user_activity WHERE user_id=%s AND date=%s", (user_id, today))
    else:
        c.execute(f"""INSERT INTO user_activity (user_id,date,{col}) VALUES (?,?,1)
                      ON CONFLICT(user_id,date) DO UPDATE SET {col}={col}+1""",
                  (user_id, today))
        c.execute("SELECT * FROM user_activity WHERE user_id=? AND date=?", (user_id, today))
    row = c.fetchone()
    if row:
        score = _calc_score(dict(row))
        c.execute(_q("UPDATE user_activity SET score=? WHERE user_id=? AND date=?"),
                  (score, user_id, today))
    conn.commit(); conn.close()

def get_my_activity_stats(user_id, month=None):
    if not month:
        month = datetime.utcnow().strftime('%Y-%m')
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT * FROM user_activity WHERE user_id=? AND date LIKE ?"),
              (user_id, month+'%'))
    rows = _fetchall(c.fetchall())
    conn.close()
    if not rows:
        return {'month': month, 'days_active': 0, 'total_score': 0,
                'online_hours': 0, 'messages': 0, 'posts': 0,
                'reactions_received': 0, 'voice': 0, 'invites': 0}
    totals = {k: sum(r.get(k, 0) for r in rows) for k in
              ['online_minutes','messages_sent','posts_created',
               'reactions_received','voice_messages','invites_converted']}
    return {
        'month': month, 'days_active': len(rows),
        'total_score': round(sum(r.get('score', 0) for r in rows), 2),
        'online_hours': round(totals['online_minutes'] / 60, 1),
        'messages': totals['messages_sent'], 'posts': totals['posts_created'],
        'reactions_received': totals['reactions_received'],
        'voice': totals['voice_messages'], 'invites': totals['invites_converted'],
    }

def estimate_reward(user_id, month=None):
    if not is_premium(user_id):
        return {'eligible': False, 'reason': 'Premium required', 'usd': 0}
    if not month:
        month = datetime.utcnow().strftime('%Y-%m')
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT score FROM user_activity WHERE user_id=? AND date LIKE ?"),
              (user_id, month+'%'))
    my_score = sum(dict(r).get('score', 0) for r in c.fetchall())
    if _PG:
        c.execute("""SELECT COALESCE(SUM(a.score),0) as total FROM user_activity a
                     JOIN premium_subscriptions p ON p.user_id=a.user_id
                     WHERE a.date LIKE %s AND p.status='active' AND p.expires_at>NOW()""", (month+'%',))
    else:
        c.execute("""SELECT COALESCE(SUM(a.score),0) as total FROM user_activity a
                     JOIN premium_subscriptions p ON p.user_id=a.user_id
                     WHERE a.date LIKE ? AND p.status='active' AND p.expires_at>datetime('now')""", (month+'%',))
    total_score = dict(c.fetchone()).get('total', 0) or 0
    c.execute("SELECT monthly_usd,total_usd,used_usd FROM reward_pool WHERE id=1")
    pool_row = c.fetchone()
    monthly = dict(pool_row)['monthly_usd'] if pool_row else 83333
    conn.close()
    if total_score <= 0 or my_score <= 0:
        return {'eligible': True, 'usd': 0, 'score': 0, 'rank_pct': 0,
                'pool_month': monthly, 'message': 'Набери очки активности'}
    share = my_score / total_score
    raw_usd = share * monthly
    usd = min(raw_usd, 5000.0) if my_score >= 500 else 0
    return {
        'eligible': True, 'usd': round(usd, 2),
        'score': round(my_score, 0), 'total_score': round(total_score, 0),
        'share_pct': round(share * 100, 4), 'pool_month': monthly,
        'message': f'Твоя доля пула: {share*100:.3f}%'
    }

def claim_reward(user_id, month, wallet_address):
    if not is_premium(user_id):
        return None, "Требуется Premium"
    est = estimate_reward(user_id, month)
    if not est['eligible'] or est['usd'] < 1:
        return None, "Минимальная выплата $1"
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(_q("""INSERT INTO reward_claims (user_id,month,score,usd_amount,wallet_address,status)
                        VALUES (?,?,?,?,?,'pending')"""),
                  (user_id, month, est['score'], est['usd'], wallet_address))
        c.execute(_q("UPDATE reward_pool SET used_usd=used_usd+? WHERE id=1"), (est['usd'],))
        conn.commit(); conn.close()
        return est['usd'], None
    except Exception:
        if _PG: conn.rollback()
        conn.close()
        return None, "Награда уже запрошена за этот месяц"

# ══════════════════════════════════════════
# GROUP MESSAGES
# ══════════════════════════════════════════

def get_group_messages(group_id: int, limit: int = 80):
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute("""SELECT id,group_id,user_id,display_name,content,msg_type,created_at
                     FROM group_messages WHERE group_id=%s ORDER BY id DESC LIMIT %s""", (group_id, limit))
    else:
        c.execute("""SELECT id,group_id,user_id,display_name,content,msg_type,created_at
                     FROM group_messages WHERE group_id=? ORDER BY id DESC LIMIT ?""", (group_id, limit))
    rows = list(reversed(_fetchall(c.fetchall()))); conn.close()
    return rows

def add_group_message(group_id: int, user_id: int, display_name: str, content: str, msg_type: str = 'text'):
    conn = get_db(); c = conn.cursor()
    try:
        mid = _execute_returning(
            conn,
            _q("INSERT INTO group_messages (group_id,user_id,display_name,content,msg_type) VALUES (?,?,?,?,?)"),
            (group_id, user_id, display_name, content, msg_type)
        )
        conn.commit(); conn.close(); return mid
    except Exception:
        if _PG: conn.rollback()
        conn.close(); return None

def join_group_by_key(group_key: str, user_id: int):
    """Join a private group by its key. Returns group dict or None."""
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(_q("SELECT id,name,icon,type FROM groups WHERE group_key=?"), (group_key,))
        g = c.fetchone()
        if not g: conn.close(); return None
        gd = dict(g)
        gid = gd['id']
        if _PG:
            c.execute("INSERT INTO group_members (group_id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (gid, user_id))
            c.execute("UPDATE groups SET member_count=(SELECT COUNT(*) FROM group_members WHERE group_id=%s) WHERE id=%s", (gid, gid))
        else:
            c.execute("INSERT OR IGNORE INTO group_members (group_id,user_id) VALUES (?,?)", (gid, user_id))
            c.execute("UPDATE groups SET member_count=(SELECT COUNT(*) FROM group_members WHERE group_id=?) WHERE id=?", (gid, gid))
        conn.commit(); conn.close(); return gd
    except Exception:
        if _PG: conn.rollback()
        conn.close(); return None

def set_group_key(group_id: int, owner_id: int, key: str):
    """Owner sets a key for private group access."""
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(_q("UPDATE groups SET group_key=? WHERE id=? AND owner_id=?"), (key, group_id, owner_id))
        conn.commit(); conn.close(); return True
    except Exception:
        if _PG: conn.rollback()
        conn.close(); return False

def increment_post_views(post_id: int):
    conn = get_db(); c = conn.cursor()
    try:
        c.execute(_q("UPDATE posts SET view_count=COALESCE(view_count,0)+1 WHERE id=?"), (post_id,))
        conn.commit(); conn.close()
    except Exception:
        if _PG: conn.rollback()
        conn.close()

def get_group_info(group_id: int, user_id: int = None):
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute("""SELECT g.id,g.owner_id,g.name,g.description,g.icon,g.type,g.handle,g.member_count,g.created_at,
                            COALESCE(gm.user_id IS NOT NULL, false) as is_member
                     FROM groups g LEFT JOIN group_members gm ON gm.group_id=g.id AND gm.user_id=%s
                     WHERE g.id=%s""", (user_id, group_id))
    else:
        c.execute("""SELECT g.id,g.owner_id,g.name,g.description,g.icon,g.type,g.handle,g.member_count,g.created_at,
                            CASE WHEN gm.user_id IS NOT NULL THEN 1 ELSE 0 END as is_member
                     FROM groups g LEFT JOIN group_members gm ON gm.group_id=g.id AND gm.user_id=?
                     WHERE g.id=?""", (user_id or 0, group_id))
    row = c.fetchone(); conn.close()
    return dict(row) if row else None

def get_group_members(group_id: int, limit: int = 50):
    conn = get_db(); c = conn.cursor()
    if _PG:
        c.execute("""SELECT u.id,u.display_name,gm.joined_at
                     FROM group_members gm JOIN users u ON u.id=gm.user_id
                     WHERE gm.group_id=%s ORDER BY gm.joined_at DESC LIMIT %s""", (group_id, limit))
    else:
        c.execute("""SELECT u.id,u.display_name,gm.joined_at
                     FROM group_members gm JOIN users u ON u.id=gm.user_id
                     WHERE gm.group_id=? ORDER BY gm.joined_at DESC LIMIT ?""", (group_id, limit))
    rows = _fetchall(c.fetchall()); conn.close()
    return rows
