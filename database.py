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

    c.execute("""CREATE TABLE IF NOT EXISTS users (
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
        color TEXT DEFAULT '#7c6fcd',
        type TEXT NOT NULL DEFAULT 'public',
        life_hours INTEGER NOT NULL DEFAULT 24,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        expires_at TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1
    )""")
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
    # Safe migration: add new columns to existing table
    for col_sql in [
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

    # ── Activity tracking ─────────────────────────────────────────────────────
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

    # ── Premium subscriptions ─────────────────────────────────────────────────
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

    # ── Reward pool & claims ──────────────────────────────────────────────────
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

    # Safe migrations
    for col_sql in [
        "ALTER TABLE users ADD COLUMN premium_expires_at TEXT",
        "ALTER TABLE users ADD COLUMN referrer_id INTEGER",
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

def save_message(cube_id, user_id, display_name, content,
                 msg_type='text', reply_to_id=None, expires_at=None,
                 file_name=None, file_size=None, file_data=None, duration=None):
    conn = get_db(); c = conn.cursor()
    c.execute(
        """INSERT INTO messages
           (cube_id,user_id,display_name,content,msg_type,reply_to_id,expires_at,file_name,file_size,file_data,duration)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (cube_id, user_id, display_name, content, msg_type,
         reply_to_id, expires_at, file_name, file_size, file_data, duration))
    mid = c.lastrowid; conn.commit(); conn.close(); return mid

def get_messages(cube_id, limit=50):
    conn = get_db()
    rows = conn.execute(
        """SELECT id,cube_id,user_id,display_name,content,msg_type,reply_to_id,
                  file_name,file_size,duration,created_at
           FROM messages
           WHERE cube_id=? AND (expires_at IS NULL OR expires_at>datetime('now'))
           ORDER BY created_at DESC LIMIT ?""",
        (cube_id, limit)).fetchall()
    conn.close(); return list(reversed([dict(r) for r in rows]))

def get_message_by_id(msg_id):
    conn = get_db()
    row = conn.execute("SELECT id,display_name,content,msg_type FROM messages WHERE id=?",(msg_id,)).fetchone()
    conn.close(); return dict(row) if row else None

def delete_expired_messages():
    conn = get_db()
    conn.execute("DELETE FROM messages WHERE expires_at IS NOT NULL AND expires_at<=datetime('now')")
    conn.execute("DELETE FROM direct_messages WHERE expires_at IS NOT NULL AND expires_at<=datetime('now')")
    conn.commit(); conn.close()

# ── Reactions ─────────────────────────────────────────────────────────────────

def toggle_reaction(message_id, user_id, display_name, emoji):
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM message_reactions WHERE message_id=? AND user_id=? AND emoji=?",
        (message_id, user_id, emoji)).fetchone()
    if existing:
        conn.execute("DELETE FROM message_reactions WHERE id=?",(existing["id"],))
        added = False
    else:
        conn.execute(
            "INSERT INTO message_reactions (message_id,user_id,display_name,emoji) VALUES (?,?,?,?)",
            (message_id, user_id, display_name, emoji))
        added = True
    conn.commit()
    rows = conn.execute(
        "SELECT emoji, COUNT(*) as cnt FROM message_reactions WHERE message_id=? GROUP BY emoji",
        (message_id,)).fetchall()
    conn.close()
    return {"added": added, "counts": {r["emoji"]: r["cnt"] for r in rows}}

def get_reactions(message_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT emoji, COUNT(*) as cnt FROM message_reactions WHERE message_id=? GROUP BY emoji",
        (message_id,)).fetchall()
    conn.close()
    return {r["emoji"]: r["cnt"] for r in rows}

# ── Direct Messages ────────────────────────────────────────────────────────────

def save_dm(from_user_id, to_user_id, content, msg_type='text',
            file_name=None, file_size=None, file_data=None, duration=None,
            reply_to_id=None, expires_at=None):
    conn = get_db(); c = conn.cursor()
    c.execute(
        """INSERT INTO direct_messages
           (from_user_id,to_user_id,content,msg_type,file_name,file_size,file_data,duration,reply_to_id,expires_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (from_user_id, to_user_id, content, msg_type,
         file_name, file_size, file_data, duration, reply_to_id, expires_at))
    mid = c.lastrowid; conn.commit(); conn.close(); return mid

def get_dm_history(user1_id, user2_id, limit=50):
    conn = get_db()
    rows = conn.execute(
        """SELECT id,from_user_id,to_user_id,content,msg_type,file_name,file_size,duration,reply_to_id,created_at
           FROM direct_messages
           WHERE ((from_user_id=? AND to_user_id=?) OR (from_user_id=? AND to_user_id=?))
             AND (expires_at IS NULL OR expires_at>datetime('now'))
           ORDER BY created_at DESC LIMIT ?""",
        (user1_id, user2_id, user2_id, user1_id, limit)).fetchall()
    conn.close(); return list(reversed([dict(r) for r in rows]))

def mark_dm_read(from_user_id, to_user_id):
    conn = get_db()
    conn.execute(
        "UPDATE direct_messages SET read_at=datetime('now') WHERE from_user_id=? AND to_user_id=? AND read_at IS NULL",
        (from_user_id, to_user_id))
    conn.commit(); conn.close()

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

# ── Premium ───────────────────────────────────────────────────────────────────

def is_premium(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM premium_subscriptions WHERE user_id=? AND status='active' AND expires_at>datetime('now')",
        (user_id,)).fetchone()
    conn.close(); return row is not None

def activate_premium(user_id, months=1, payment_method=None, tx_hash=None):
    conn = get_db()
    expires = conn.execute(
        "SELECT datetime('now','+'||?||' months')", (months,)).fetchone()[0]
    conn.execute(
        """INSERT OR REPLACE INTO premium_subscriptions
           (user_id,expires_at,price_usd,payment_method,tx_hash,status)
           VALUES (?,?,?,?,?,'active')""",
        (user_id, expires, 6.99*months, payment_method, tx_hash))
    conn.execute("UPDATE users SET key_type='premium', premium_expires_at=? WHERE id=?",(expires,user_id))
    conn.commit(); conn.close()

def get_premium_info(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM premium_subscriptions WHERE user_id=? ORDER BY id DESC LIMIT 1",(user_id,)).fetchone()
    conn.close(); return dict(row) if row else None

# ── Activity tracking ─────────────────────────────────────────────────────────

SCORE_WEIGHTS = {
    # 🗨️ Chatting
    'online_minutes':     1.0,   # passive — being present
    'messages_sent':      3.0,   # active chat
    'voice_messages':     10.0,  # voice > text
    # 🎨 Art / Content
    'posts_created':      25.0,  # original content
    'reactions_received': 6.0,   # audience engagement on your content
    # 💻 Coding / Signals (tracked via posts_created for signals & API)
    # 📢 Advertising / Referrals
    'invites_converted':  250.0, # highest value — bringing real users
}

def _calc_score(row):
    return sum(row.get(k, 0) * w for k, w in SCORE_WEIGHTS.items())

def ping_activity(user_id):
    """Call every ~60s while user is online. Increments online_minutes."""
    today = datetime.utcnow().strftime('%Y-%m-%d')
    conn = get_db()
    conn.execute(
        """INSERT INTO user_activity (user_id,date,online_minutes) VALUES (?,?,1)
           ON CONFLICT(user_id,date) DO UPDATE SET online_minutes=online_minutes+1""",
        (user_id, today))
    # Recalculate score
    row = conn.execute("SELECT * FROM user_activity WHERE user_id=? AND date=?",(user_id,today)).fetchone()
    if row:
        score = _calc_score(dict(row))
        conn.execute("UPDATE user_activity SET score=? WHERE user_id=? AND date=?",(score,user_id,today))
    conn.commit(); conn.close()

def record_activity(user_id, event):
    """event: 'message'|'post'|'reaction_received'|'voice'|'invite'"""
    col_map = {
        'message':           'messages_sent',
        'post':              'posts_created',
        'reaction_received': 'reactions_received',
        'voice':             'voice_messages',
        'invite':            'invites_converted',
    }
    col = col_map.get(event)
    if not col: return
    today = datetime.utcnow().strftime('%Y-%m-%d')
    conn = get_db()
    conn.execute(
        f"""INSERT INTO user_activity (user_id,date,{col}) VALUES (?,?,1)
            ON CONFLICT(user_id,date) DO UPDATE SET {col}={col}+1""",
        (user_id, today))
    row = conn.execute("SELECT * FROM user_activity WHERE user_id=? AND date=?",(user_id,today)).fetchone()
    if row:
        score = _calc_score(dict(row))
        conn.execute("UPDATE user_activity SET score=? WHERE user_id=? AND date=?",(score,user_id,today))
    conn.commit(); conn.close()

def get_my_activity_stats(user_id, month=None):
    """Get activity stats for current or given month (YYYY-MM)."""
    if not month:
        month = datetime.utcnow().strftime('%Y-%m')
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM user_activity WHERE user_id=? AND date LIKE ?",
        (user_id, month+'%')).fetchall()
    conn.close()
    if not rows:
        return {'month': month, 'days_active': 0, 'total_score': 0,
                'online_hours': 0, 'messages': 0, 'posts': 0,
                'reactions_received': 0, 'voice': 0, 'invites': 0}
    totals = {k: sum(r[k] for r in rows) for k in
              ['online_minutes','messages_sent','posts_created',
               'reactions_received','voice_messages','invites_converted']}
    return {
        'month': month,
        'days_active': len(rows),
        'total_score': round(sum(r['score'] for r in rows), 2),
        'online_hours': round(totals['online_minutes'] / 60, 1),
        'messages': totals['messages_sent'],
        'posts': totals['posts_created'],
        'reactions_received': totals['reactions_received'],
        'voice': totals['voice_messages'],
        'invites': totals['invites_converted'],
    }

def estimate_reward(user_id, month=None):
    """Estimate USD reward for this month. Only premium users earn."""
    if not is_premium(user_id):
        return {'eligible': False, 'reason': 'Premium required', 'usd': 0}
    if not month:
        month = datetime.utcnow().strftime('%Y-%m')
    conn = get_db()
    # My score this month
    my_rows = conn.execute(
        "SELECT score FROM user_activity WHERE user_id=? AND date LIKE ?",
        (user_id, month+'%')).fetchall()
    my_score = sum(r['score'] for r in my_rows)
    # Total score of all premium users this month
    total_score = conn.execute(
        """SELECT COALESCE(SUM(a.score),0) FROM user_activity a
           JOIN premium_subscriptions p ON p.user_id=a.user_id
           WHERE a.date LIKE ? AND p.status='active' AND p.expires_at>datetime('now')""",
        (month+'%',)).fetchone()[0]
    # Pool
    pool_row = conn.execute("SELECT monthly_usd,total_usd,used_usd FROM reward_pool WHERE id=1").fetchone()
    monthly = pool_row['monthly_usd'] if pool_row else 83333
    conn.close()
    if total_score <= 0 or my_score <= 0:
        return {'eligible': True, 'usd': 0, 'score': 0, 'rank_pct': 0,
                'pool_month': monthly, 'message': 'Набери очки активности'}
    share = my_score / total_score
    raw_usd = share * monthly
    # Cap: $5000 max, $1 min threshold (score > 500)
    usd = min(raw_usd, 5000.0) if my_score >= 500 else 0
    return {
        'eligible': True, 'usd': round(usd, 2),
        'score': round(my_score, 0),
        'total_score': round(total_score, 0),
        'share_pct': round(share * 100, 4),
        'pool_month': monthly,
        'message': f'Твоя доля пула: {share*100:.3f}%'
    }

def claim_reward(user_id, month, wallet_address):
    if not is_premium(user_id):
        return None, "Требуется Premium"
    est = estimate_reward(user_id, month)
    if not est['eligible'] or est['usd'] < 1:
        return None, "Минимальная выплата $1"
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO reward_claims (user_id,month,score,usd_amount,wallet_address,status)
               VALUES (?,?,?,?,?,'pending')""",
            (user_id, month, est['score'], est['usd'], wallet_address))
        conn.execute("UPDATE reward_pool SET used_usd=used_usd+? WHERE id=1",(est['usd'],))
        conn.commit(); conn.close()
        return est['usd'], None
    except Exception as e:
        conn.close()
        return None, "Награда уже запрошена за этот месяц"
