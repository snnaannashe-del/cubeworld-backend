"""
CubeWorld - SQLite database layer
Auto-creates all tables on first run
"""

import sqlite3, os, time

DB_PATH = os.environ.get("DB_PATH", "cubeworld.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_key    TEXT PRIMARY KEY,
        tier        TEXT NOT NULL DEFAULT 'free',
        balance     INTEGER NOT NULL DEFAULT 250,
        xp          INTEGER NOT NULL DEFAULT 0,
        streak      INTEGER NOT NULL DEFAULT 0,
        last_checkin TEXT DEFAULT '',
        created_at  INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS posts (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_key     TEXT NOT NULL,
        author       TEXT NOT NULL,
        text         TEXT NOT NULL,
        post_type    TEXT NOT NULL DEFAULT 'text',
        react_fire      INTEGER NOT NULL DEFAULT 0,
        react_rocket    INTEGER NOT NULL DEFAULT 0,
        react_like      INTEGER NOT NULL DEFAULT 0,
        react_heart     INTEGER NOT NULL DEFAULT 0,
        react_eyes      INTEGER NOT NULL DEFAULT 0,
        react_thinking  INTEGER NOT NULL DEFAULT 0,
        created_at   INTEGER NOT NULL,
        FOREIGN KEY (user_key) REFERENCES users(user_key)
    );
    CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at DESC);
    CREATE TABLE IF NOT EXISTS messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        room        TEXT NOT NULL,
        user_key    TEXT NOT NULL,
        text        TEXT NOT NULL,
        created_at  INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room, created_at DESC);
    CREATE TABLE IF NOT EXISTS wallet_txs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_key    TEXT NOT NULL,
        dir         TEXT NOT NULL,
        amount      INTEGER NOT NULL,
        desc        TEXT NOT NULL,
        icon        TEXT NOT NULL DEFAULT 'coin',
        created_at  INTEGER NOT NULL,
        FOREIGN KEY (user_key) REFERENCES users(user_key)
    );
    CREATE INDEX IF NOT EXISTS idx_txs_user ON wallet_txs(user_key, created_at DESC);
    CREATE TABLE IF NOT EXISTS signals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_key    TEXT NOT NULL,
        author      TEXT NOT NULL,
        pair        TEXT NOT NULL,
        direction   TEXT NOT NULL,
        entry       TEXT NOT NULL,
        tp          TEXT NOT NULL,
        sl          TEXT NOT NULL,
        react_rocket INTEGER NOT NULL DEFAULT 0,
        react_fire   INTEGER NOT NULL DEFAULT 0,
        created_at  INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at DESC);
    CREATE TABLE IF NOT EXISTS groups (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        icon        TEXT NOT NULL DEFAULT 'group',
        group_type  TEXT NOT NULL DEFAULT 'public',
        created_by  TEXT NOT NULL,
        member_count INTEGER NOT NULL DEFAULT 1,
        created_at  INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS group_members (
        group_id    INTEGER NOT NULL,
        user_key    TEXT NOT NULL,
        joined_at   INTEGER NOT NULL,
        PRIMARY KEY (group_id, user_key),
        FOREIGN KEY (group_id) REFERENCES groups(id),
        FOREIGN KEY (user_key) REFERENCES users(user_key)
    );
    CREATE TABLE IF NOT EXISTS referrals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        inviter_key TEXT NOT NULL,
        invited_key TEXT NOT NULL,
        rewarded    INTEGER NOT NULL DEFAULT 0,
        created_at  INTEGER NOT NULL,
        FOREIGN KEY (inviter_key) REFERENCES users(user_key)
    );
    """)
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    if count == 0:
        _seed_demo_data(conn)

    conn.close()
    print(f"DB ready: {DB_PATH}")


def _seed_demo_data(conn):
    now = int(time.time())
    demo_users = [
        ("7A2F-CUBE-DEMO-0001", "premium", 1250),
        ("NN90-CUBE-DEMO-0002", "free",    420),
        ("ANON-CUBE-DEMO-0003", "free",    88),
        ("G057-CUBE-DEMO-0004", "premium", 3400),
        ("A1PH-CUBE-DEMO-0005", "free",    210),
    ]
    for key, tier, bal in demo_users:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_key, tier, balance, created_at) VALUES (?,?,?,?)",
            (key, tier, bal, now - 86400 * 7)
        )

    demo_posts = [
        ("7A2F-CUBE-DEMO-0001", "7A2F-CUBE", "Crypto market is waking up. BTC broke 85k. Get ready for volatility.", "text", 42, 18, 0, 0, 7, 0),
        ("NN90-CUBE-DEMO-0002", "NN90-CUBE", "Ran a neural network on local hardware. GPT-4 level without the cloud is real.", "text", 91, 34, 22, 0, 0, 0),
        ("ANON-CUBE-DEMO-0003", "ANON-????", "BTC/USDT Long @ 84,200 | TP: 87,000 | SL: 82,000", "signal", 89, 156, 0, 0, 0, 0),
        ("G057-CUBE-DEMO-0004", "G057-CUBE", "New exclusive NFT drop. 48 hours only. After - deleted.", "media", 67, 0, 0, 203, 0, 0),
        ("A1PH-CUBE-DEMO-0005", "A1PH-CUBE", "ETH being accumulated by large wallets. Whale Alert: +12,400 ETH in 6 hours", "text", 144, 312, 0, 0, 56, 0),
    ]
    for i, (key, author, text, ptype, fire, rocket, like, heart, eyes, thinking) in enumerate(demo_posts):
        conn.execute(
            """INSERT INTO posts
               (user_key,author,text,post_type,react_fire,react_rocket,react_like,react_heart,react_eyes,react_thinking,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (key, author, text, ptype, fire, rocket, like, heart, eyes, thinking, now - 3600 * (i + 1))
        )

    demo_signals = [
        ("7A2F-CUBE-DEMO-0001", "7A2F-CUBE", "BTC/USDT", "long",  "84200", "87000", "82000", 156, 89),
        ("A1PH-CUBE-DEMO-0005", "A1PH-CUBE", "ETH/USDT", "long",  "3180",  "3450",  "3050",  89,  44),
        ("NN90-CUBE-DEMO-0002", "NN90-CUBE", "SOL/USDT", "short", "142.5", "128.0", "150.0", 34,  18),
        ("G057-CUBE-DEMO-0004", "G057-CUBE", "XRP/USDT", "long",  "0.892", "0.980", "0.840", 45,  22),
    ]
    for i, (key, author, pair, direction, entry, tp, sl, rocket, fire) in enumerate(demo_signals):
        conn.execute(
            """INSERT INTO signals
               (user_key,author,pair,direction,entry,tp,sl,react_rocket,react_fire,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (key, author, pair, direction, entry, tp, sl, rocket, fire, now - 1800 * (i + 1))
        )

    conn.commit()
    print("Demo data seeded")
