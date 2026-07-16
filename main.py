import os
import secrets
import string
import hashlib
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

import database as db

SECRET_KEY   = os.getenv("JWT_SECRET", "changeme-very-secret-key-cubeworld")
JWT_ALG      = "HS256"
ACCESS_TTL   = int(os.getenv("ACCESS_TTL_MINUTES", "60"))
REFRESH_TTL  = int(os.getenv("REFRESH_TTL_DAYS", "30"))
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")

app = FastAPI(title="CubeWorld API", version="4.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

db.init_db()
bearer_scheme = HTTPBearer(auto_error=False)

_CHARS = string.ascii_uppercase + string.digits

def _random_segment(n=4):
    return "".join(secrets.choice(_CHARS) for _ in range(n))

def generate_cube_key():
    return f"CUBE-{_random_segment()}-{_random_segment()}-{_random_segment()}"

def create_access_token(user_id, key_type):
    payload = {"sub": str(user_id), "key_type": key_type,
                "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TTL),
                "iat": datetime.utcnow(), "type": "access"}
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALG)

def create_refresh_token():
    return secrets.token_hex(32)

def decode_access_token(token):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALG])
        if payload.get("type") != "access":
            raise ValueError("not access token")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except Exception:
        raise HTTPException(401, "Invalid token")

async def get_current_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)):
    if not creds:
        raise HTTPException(401, "No token")
    payload = decode_access_token(creds.credentials)
    user = db.get_user_by_id(int(payload["sub"]))
    if not user:
        raise HTTPException(401, "User not found")
    db.update_last_seen(user["id"])
    return user

# ── Pydantic models ───────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    key: str

class RefreshRequest(BaseModel):
    refresh_token: str

class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None

class WalletLinkRequest(BaseModel):
    address: str
    chain_id: int = 137
    signature: Optional[str] = None  # proof of ownership

class RewardClaimRequest(BaseModel):
    month: Optional[str] = None   # YYYY-MM, defaults to current
    wallet_address: str

class PremiumActivateRequest(BaseModel):
    months: int = 1
    payment_method: Optional[str] = None
    tx_hash: Optional[str] = None

class CreateCubeRequest(BaseModel):
    name: str
    description: str = ""
    icon: str = "📦"
    color: str = "#0095F6"
    type: str = "public"
    life_hours: int = 24

class JoinCubeRequest(BaseModel):
    cube_key: str

class SetUsernameRequest(BaseModel):
    username: str

class SetCubeHandleRequest(BaseModel):
    handle: str

class CreateGroupRequest(BaseModel):
    name: str
    description: Optional[str] = ''
    icon: Optional[str] = '👥'
    type: Optional[str] = 'public'  # public | private

class SetGroupHandleRequest(BaseModel):
    handle: str

class CreatePostRequest(BaseModel):
    content: str

class CreateSignalRequest(BaseModel):
    ticker: str
    direction: str = "LONG"
    entry_price: float = 0.0
    tp_price: float = 0.0
    sl_price: float = 0.0
    content: str = ""

class ReactRequest(BaseModel):
    emoji: str

# ── Auth ──────────────────────────────────────────────────────────────────────

@app.post("/auth/generate")
async def generate_key(request: Request):
    for _ in range(10):
        raw_key = generate_cube_key()
        key_hash = db.hash_key(raw_key)
        if not db.get_user_by_key_hash(key_hash):
            break
    else:
        raise HTTPException(500, "Key generation failed")

    key_prefix = raw_key[:9]
    user_id = db.create_user(key_hash, key_prefix, key_type="free")
    db.add_cube_balance(user_id, 100.0, "mint", description="Welcome bonus")

    access_token = create_access_token(user_id, "free")
    refresh_raw   = create_refresh_token()
    refresh_hash  = db.hash_token(refresh_raw)
    expires_at    = (datetime.utcnow() + timedelta(days=REFRESH_TTL)).strftime("%Y-%m-%d %H:%M:%S")

    ua      = request.headers.get("user-agent", "")
    ip_raw  = request.client.host if request.client else ""
    ip_hash = hashlib.sha256(ip_raw.encode()).hexdigest() if ip_raw else None
    db.create_session(user_id, refresh_hash, expires_at, user_agent=ua, ip_hash=ip_hash)

    return {"key": raw_key, "key_prefix": key_prefix, "key_type": "free",
            "access_token": access_token, "refresh_token": refresh_raw,
            "cube_balance": 100.0, "message": "Save your key - it cannot be recovered!"}

@app.post("/auth/login")
async def login_with_key(body: LoginRequest, request: Request):
    raw_key  = body.key.strip().upper()
    key_hash = db.hash_key(raw_key)
    user     = db.get_user_by_key_hash(key_hash)
    if not user:
        raise HTTPException(401, "Key not found or invalid")

    user_id   = user["id"]
    key_type  = user["key_type"]
    access_token = create_access_token(user_id, key_type)
    refresh_raw  = create_refresh_token()
    refresh_hash = db.hash_token(refresh_raw)
    expires_at   = (datetime.utcnow() + timedelta(days=REFRESH_TTL)).strftime("%Y-%m-%d %H:%M:%S")

    ua      = request.headers.get("user-agent", "")
    ip_raw  = request.client.host if request.client else ""
    ip_hash = hashlib.sha256(ip_raw.encode()).hexdigest() if ip_raw else None
    db.create_session(user_id, refresh_hash, expires_at, user_agent=ua, ip_hash=ip_hash)
    db.update_last_seen(user_id)

    return {"key_prefix": user["key_prefix"], "key_type": key_type,
            "access_token": access_token, "refresh_token": refresh_raw,
            "cube_balance": user["cube_balance"], "display_name": user["display_name"],
            "user_id": user_id}

@app.post("/auth/refresh")
async def refresh_tokens(body: RefreshRequest):
    session = db.get_session_by_token_hash(db.hash_token(body.refresh_token))
    if not session:
        raise HTTPException(401, "Invalid or expired refresh token")
    access_token = create_access_token(session["user_id"], session["key_type"])
    return {"access_token": access_token, "key_type": session["key_type"]}

@app.post("/auth/logout")
async def logout(body: RefreshRequest):
    db.delete_session(db.hash_token(body.refresh_token))
    return {"ok": True}

# ── User ──────────────────────────────────────────────────────────────────────

@app.get("/me")
async def get_me(user=Depends(get_current_user)):
    return {"id": user["id"], "key_prefix": user["key_prefix"], "key_type": user["key_type"],
            "display_name": user["display_name"], "avatar_url": user["avatar_url"],
            "username": user.get("username"), "uid": f"#{user['id']}",
            "cube_balance": user["cube_balance"], "created_at": user["created_at"],
            "last_seen": user["last_seen"]}

@app.patch("/me")
async def update_me(body: UpdateProfileRequest, user=Depends(get_current_user)):
    db.update_profile(user["id"], body.display_name, body.avatar_url)
    return {"ok": True}

@app.post("/me/username")
async def set_username(body: SetUsernameRequest, user=Depends(get_current_user)):
    """Set @username. 4-24 chars, a-z 0-9 underscore only."""
    import re
    uname = body.username.strip().lower().lstrip('@')
    if not re.match(r'^[a-z0-9_]{4,24}$', uname):
        raise HTTPException(400, "Юзернейм: 4-24 символа, только a-z 0-9 _")
    ok = db.set_username(user["id"], uname)
    if not ok:
        raise HTTPException(409, "Этот @username уже занят")
    return {"ok": True, "username": uname}

# ── User Search ────────────────────────────────────────────────────────────────

@app.get("/users/search")
async def search_users(q: str = "", limit: int = 20, user=Depends(get_current_user)):
    """Search users by @username, display_name, key_prefix, or #ID."""
    if not q.strip():
        raise HTTPException(400, "Пустой запрос")
    results = db.search_users(q.strip(), min(limit, 50), exclude_id=user["id"])
    return [
        {
            "id":           r["id"],
            "uid":          f"#{r['id']}",
            "username":     r.get("username"),
            "display_name": r.get("display_name") or r.get("key_prefix",""),
            "avatar_url":   r.get("avatar_url"),
            "key_type":     r.get("key_type","free"),
            "handle":       f"@{r['username']}" if r.get("username") else f"#{r['id']}",
        }
        for r in results
    ]

@app.get("/cubes/search")
async def search_cubes_endpoint(q: str = "", limit: int = 20):
    """Search cubes by @handle or name."""
    if not q.strip():
        raise HTTPException(400, "Пустой запрос")
    results = db.search_cubes(q.strip(), min(limit, 50))
    return [
        {
            "id":     r["id"],
            "uid":    f"#G{r['id']}",
            "name":   r["name"],
            "handle": f"@{r['handle']}" if r.get("handle") else f"#G{r['id']}",
            "icon":   r.get("icon","🧊"),
            "color":  r.get("color","#0095F6"),
            "type":   r.get("type","public"),
            "life_left": r.get("life_left_seconds",0),
        }
        for r in results
    ]

@app.post("/cubes/{cube_id}/handle")
async def set_cube_handle(cube_id: int, body: SetCubeHandleRequest, user=Depends(get_current_user)):
    """Set @handle for a cube (owner only). 3-24 chars a-z 0-9 _"""
    import re
    handle = body.handle.strip().lower().lstrip('@')
    if not re.match(r'^[a-z0-9_]{3,24}$', handle):
        raise HTTPException(400, "Handle: 3-24 символа, только a-z 0-9 _")
    ok = db.set_cube_handle(cube_id, user["id"], handle)
    if not ok:
        raise HTTPException(409, "Этот @handle уже занят или ты не владелец")
    return {"ok": True, "handle": handle}

@app.post("/wallet/link")
async def link_wallet(body: WalletLinkRequest, user=Depends(get_current_user)):
    db.link_wallet(user["id"], body.address, body.chain_id)
    return {"ok": True, "address": body.address}

@app.get("/wallet/my")
async def my_wallets(user=Depends(get_current_user)):
    conn = db.get_db(); c = conn.cursor()
    c.execute(db._q("SELECT address,chain_id,linked_at FROM wallets WHERE user_id=? ORDER BY id DESC"),
              (user["id"],))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── Premium ───────────────────────────────────────────────────────────────────

@app.get("/premium/status")
async def premium_status(user=Depends(get_current_user)):
    info = db.get_premium_info(user["id"])
    is_active = db.is_premium(user["id"])
    return {"is_premium": is_active, "info": info}

@app.post("/premium/activate")
async def activate_premium(body: PremiumActivateRequest, user=Depends(get_current_user)):
    db.activate_premium(user["id"], body.months, body.payment_method, body.tx_hash)
    db.record_activity(user["id"], "invite")  # reward for upgrading
    return {"ok": True, "message": f"Premium активирован на {body.months} мес."}

# ── Activity tracking ─────────────────────────────────────────────────────────

@app.post("/activity/ping")
async def activity_ping(user=Depends(get_current_user)):
    """Frontend calls this every 60s while tab is active."""
    db.ping_activity(user["id"])
    return {"ok": True}

@app.get("/activity/stats")
async def activity_stats(user=Depends(get_current_user)):
    stats = db.get_my_activity_stats(user["id"])
    is_prem = db.is_premium(user["id"])
    return {**stats, "is_premium": is_prem}

class ActivityEventRequest(BaseModel):
    event: str  # message | post | reaction_received | voice | invite

@app.post("/activity/event")
async def activity_event(body: ActivityEventRequest, user=Depends(get_current_user)):
    db.record_activity(user["id"], body.event)
    # Milestone rewards — award CUBE at activity milestones and notify via WS
    stats = db.get_my_activity_stats(user["id"])
    bonus = 0
    reason = ""
    if body.event == "message":
        msgs = stats.get("messages", 0)
        if msgs > 0 and msgs % 10 == 0:
            bonus, reason = 5, f"+5 CUBE · {msgs} сообщений"
    elif body.event == "post":
        posts = stats.get("posts", 0)
        if posts > 0 and posts % 5 == 0:
            bonus, reason = 10, f"+10 CUBE · {posts} постов"
    elif body.event == "invite":
        invites = stats.get("invites", 0)
        if invites > 0 and invites % 1 == 0:
            bonus, reason = 50, "+50 CUBE · реферал"
    if bonus:
        db.add_cube_balance(user["id"], float(bonus), "activity", description=reason)
        new_bal = db.get_cube_balance(user["id"])
        await _notify_user(user["id"], {"type": "balance_update", "balance": new_bal, "reason": reason})
    return {"ok": True, "bonus": bonus}

# ── Rewards ───────────────────────────────────────────────────────────────────

@app.get("/rewards/estimate")
async def reward_estimate(user=Depends(get_current_user)):
    return db.estimate_reward(user["id"])

@app.post("/rewards/claim")
async def reward_claim(body: RewardClaimRequest, user=Depends(get_current_user)):
    from datetime import datetime as _dt
    month = body.month or _dt.utcnow().strftime('%Y-%m')
    amount, err = db.claim_reward(user["id"], month, body.wallet_address)
    if err:
        raise HTTPException(400, err)
    return {"ok": True, "usd": amount, "wallet": body.wallet_address,
            "message": f"Заявка на ${amount:.2f} создана. Выплата в течение 24ч."}

@app.get("/rewards/history")
async def reward_history(user=Depends(get_current_user)):
    conn = db.get_db(); c = conn.cursor()
    c.execute(db._q("SELECT month,score,usd_amount,wallet_address,status,created_at FROM reward_claims WHERE user_id=? ORDER BY id DESC"),
              (user["id"],))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/rewards/pool")
async def reward_pool_info():
    conn = db.get_db(); c = conn.cursor()
    c.execute("SELECT * FROM reward_pool WHERE id=1")
    row = c.fetchone()
    conn.close()
    if not row: return {"total_usd": 1000000, "used_usd": 0, "remaining_usd": 1000000}
    r = dict(row)
    r["remaining_usd"] = r["total_usd"] - r["used_usd"]
    return r

@app.get("/cube/balance")
async def cube_balance(user=Depends(get_current_user)):
    return {"balance": db.get_cube_balance(user["id"]), "key_type": user["key_type"]}

# ── Groups ────────────────────────────────────────────────────────────────────

import re as _re
_HANDLE_RE = _re.compile(r'^[a-z0-9_]{3,24}$')

@app.get("/groups")
async def list_groups(creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)):
    """List public groups. Optional auth to get is_member flag."""
    uid = None
    if creds:
        try:
            payload = decode_access_token(creds.credentials)
            user = db.get_user_by_id(int(payload["sub"]))
            if user: uid = user["id"]
        except Exception:
            pass
    return db.get_groups(limit=50, user_id=uid)

@app.get("/groups/my")
async def my_groups(user=Depends(get_current_user)):
    return db.get_my_groups(user["id"])

@app.get("/groups/search")
async def search_groups(q: str = "", creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)):
    uid = None
    if creds:
        try:
            payload = decode_access_token(creds.credentials)
            u = db.get_user_by_id(int(payload["sub"]))
            if u: uid = u["id"]
        except Exception:
            pass
    return db.search_groups(q, limit=20, user_id=uid)

@app.post("/groups")
async def create_group(body: CreateGroupRequest, user=Depends(get_current_user)):
    if not body.name.strip():
        raise HTTPException(400, "Name required")
    gtype = body.type if body.type in ('public', 'private') else 'public'
    gid = db.create_group(user["id"], body.name.strip(), body.description or '', body.icon or '👥', gtype)
    if not gid:
        raise HTTPException(500, "Could not create group")
    groups = db.get_my_groups(user["id"])
    created = next((g for g in groups if g["id"] == gid), None)
    return created or {"id": gid}

@app.post("/groups/{group_id}/join")
async def join_group(group_id: int, user=Depends(get_current_user)):
    ok = db.join_group(group_id, user["id"])
    if not ok:
        raise HTTPException(404, "Group not found")
    return {"joined": True, "group_id": group_id}

@app.post("/groups/{group_id}/leave")
async def leave_group(group_id: int, user=Depends(get_current_user)):
    ok = db.leave_group(group_id, user["id"])
    if not ok:
        raise HTTPException(400, "Cannot leave (not a member or you are the owner)")
    return {"left": True, "group_id": group_id}

@app.post("/groups/{group_id}/handle")
async def set_group_handle(group_id: int, body: SetGroupHandleRequest, user=Depends(get_current_user)):
    handle = body.handle.lower().strip()
    if not _HANDLE_RE.match(handle):
        raise HTTPException(400, "Handle must be 3-24 chars: a-z 0-9 _")
    ok = db.set_group_handle(group_id, user["id"], handle)
    if not ok:
        raise HTTPException(400, "Handle taken or not your group")
    return {"handle": handle}

# ── Cubes CRUD ────────────────────────────────────────────────────────────────

@app.get("/cubes")
async def list_cubes():
    db.deactivate_expired_cubes()
    return db.list_cubes()

@app.post("/cubes")
async def create_cube(body: CreateCubeRequest, user=Depends(get_current_user)):
    name      = body.name.strip()[:50] or "My Cube"
    desc      = body.description[:200]
    icon      = body.icon[:8]
    color     = body.color[:20]
    ctype     = "public" if body.type == "public" else "private"
    life_h    = max(1, min(body.life_hours, 720))
    cube_id, cube_key = db.create_cube(user["id"], name, desc, icon, color, ctype, life_h)
    return {"id": cube_id, "name": name, "type": ctype, "life_hours": life_h,
            "cube_key": cube_key, "ok": True}

@app.post("/cubes/join")
async def join_cube_by_key(body: JoinCubeRequest):
    """Resolve a cube invite key — returns cube info if valid."""
    key = body.cube_key.strip().upper()
    cube = db.get_cube_by_key(key)
    if not cube:
        raise HTTPException(status_code=404, detail="Ключ не найден или куб истёк")
    return {
        "id":         cube["id"],
        "name":       cube["name"],
        "icon":       cube["icon"],
        "color":      cube["color"],
        "type":       cube["type"],
        "life_left":  cube["life_left_seconds"],
        "cube_key":   cube["cube_key"],
        "ok": True
    }

@app.get("/cubes/{cube_id}/key")
async def get_cube_key(cube_id: int, user=Depends(get_current_user)):
    """Owner-only: retrieve the cube's invite key."""
    key = db.get_cube_key(cube_id, user["id"])
    if key is None:
        raise HTTPException(status_code=403, detail="Только создатель может видеть ключ")
    return {"cube_key": key, "ok": True}

@app.get("/cubes/{cube_id}/messages")
async def get_messages(cube_id: int, limit: int = 50):
    return db.get_messages(cube_id, min(limit, 100))

@app.get("/cubes/{cube_id}/online")
async def cube_online_users(cube_id: str):
    """Return list of online users in this cube's WS room."""
    room = cube_rooms.get(cube_id, {})
    users = [{"user_id": uid, "display_name": info["display_name"]}
             for uid, info in room.items()]
    return {"online": len(users), "users": users}

@app.post("/messages/{msg_id}/react")
async def react_to_message(msg_id: int, body: ReactRequest, user=Depends(get_current_user)):
    emoji = body.emoji[:8]
    display_name = db.get_display_name(user["id"])
    result = db.toggle_reaction(msg_id, user["id"], display_name, emoji)
    return {**result, "message_id": msg_id, "ok": True}

@app.get("/dm/{other_user_id}")
async def get_dm_history(other_user_id: int, user=Depends(get_current_user)):
    history = db.get_dm_history(user["id"], other_user_id)
    return history

@app.get("/cubes/{cube_id}/posts")
async def get_posts(cube_id: int):
    return db.get_posts(cube_id)

@app.post("/cubes/{cube_id}/posts")
async def create_post(cube_id: int, body: CreatePostRequest, user=Depends(get_current_user)):
    content = body.content.strip()[:1000]
    if not content:
        raise HTTPException(400, "Content required")
    display_name = db.get_display_name(user["id"])
    post_id = db.create_post(cube_id, user["id"], display_name, content)
    return {"id": post_id, "display_name": display_name, "content": content,
            "likes": 0, "created_at": datetime.utcnow().isoformat(), "ok": True}

@app.post("/cubes/{cube_id}/posts/{post_id}/like")
async def like_post(cube_id: int, post_id: int, user=Depends(get_current_user)):
    likes, liked = db.like_post(post_id, user["id"])
    return {"likes": likes, "liked": liked, "ok": True}

@app.get("/cubes/{cube_id}/signals")
async def get_signals(cube_id: int):
    return db.get_signals(cube_id)

@app.post("/cubes/{cube_id}/signals")
async def create_signal(cube_id: int, body: CreateSignalRequest, user=Depends(get_current_user)):
    ticker    = body.ticker.upper()[:10]
    direction = "LONG" if body.direction.upper() == "LONG" else "SHORT"
    display_name = db.get_display_name(user["id"])
    sig_id = db.create_signal(cube_id, user["id"], display_name, ticker, direction,
                               body.entry_price, body.tp_price, body.sl_price, body.content[:500])
    return {"id": sig_id, "ok": True}

# ── Admin / Stats ─────────────────────────────────────────────────────────────

@app.post("/admin/upgrade")
async def admin_upgrade(request: Request):
    secret = request.headers.get("X-Admin-Secret", "")
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(403, "Forbidden")
    body = await request.json()
    user_id = body.get("user_id")
    to_type = body.get("to_type", "premium")
    if not user_id:
        raise HTTPException(400, "user_id required")
    db.upgrade_key(int(user_id), to_type, cube_spent=0.0)
    return {"ok": True, "user_id": user_id, "key_type": to_type}

@app.get("/stats")
async def stats():
    s = db.get_stats()
    s["connected_ws"] = sum(len(v) for v in cube_rooms.values())
    return s

@app.get("/health")
async def health():
    return {"status": "ok", "version": "4.0.0"}

# ── WebSocket: per-cube rooms ─────────────────────────────────────────────────
# cube_rooms: { cube_id_str: { user_id_str: {"ws": websocket, "display_name": str} } }
# user_ws: { user_id_str: websocket }  — tracks the most recent WS for each user (for direct notifications)

cube_rooms: Dict[str, Dict[str, dict]] = {}
user_ws: Dict[str, object] = {}

async def _notify_user(user_id: int, msg: dict):
    """Send a direct message to a user's current WebSocket, if connected."""
    ws = user_ws.get(str(user_id))
    if ws:
        try:
            await ws.send_json(msg)
        except Exception:
            pass

async def _broadcast(cube_id: str, msg: dict, exclude: str = None):
    for uid, info in list(cube_rooms.get(cube_id, {}).items()):
        if uid == exclude:
            continue
        try:
            await info["ws"].send_json(msg)
        except Exception:
            pass

async def _send_to_user(cube_id: str, user_id: str, msg: dict):
    info = cube_rooms.get(cube_id, {}).get(user_id)
    if info:
        try:
            await info["ws"].send_json(msg)
        except Exception:
            pass

def _online_list(cube_id: str):
    return [{"user_id": uid, "display_name": info["display_name"]}
            for uid, info in cube_rooms.get(cube_id, {}).items()]

@app.websocket("/ws/{token}/{cube_id}")
async def ws_cube(websocket: WebSocket, token: str, cube_id: str):
    await websocket.accept()
    user_id = None
    display_name = "Unknown"
    try:
        payload      = decode_access_token(token)
        user_id      = payload["sub"]
        display_name = db.get_display_name(int(user_id))
        db.update_last_seen(int(user_id))

        if cube_id not in cube_rooms:
            cube_rooms[cube_id] = {}
        cube_rooms[cube_id][user_id] = {"ws": websocket, "display_name": display_name}
        user_ws[user_id] = websocket  # register for direct notifications

        online = len(cube_rooms[cube_id])

        await websocket.send_json({
            "type": "joined", "user_id": user_id,
            "display_name": display_name, "online": online,
            "online_users": _online_list(cube_id)
        })
        await _broadcast(cube_id, {
            "type": "user_joined", "user_id": user_id,
            "display_name": display_name, "online": online,
            "online_users": _online_list(cube_id)
        }, exclude=user_id)

        while True:
            data     = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            elif msg_type == "typing":
                # Broadcast typing event to others
                await _broadcast(cube_id, {
                    "type": "typing",
                    "user_id": user_id,
                    "display_name": display_name
                }, exclude=user_id)

            elif msg_type == "message":
                content      = str(data.get("content", "")).strip()[:2000]
                chat_type    = data.get("msg_type", "text")
                reply_to_id  = data.get("reply_to_id")
                expires_secs = data.get("expires_secs")
                file_name    = data.get("file_name")
                file_size    = data.get("file_size")
                file_data    = data.get("file_data")   # base64, stored only for files/voice
                duration     = data.get("duration")

                if not content:
                    continue

                expires_at = None
                if expires_secs:
                    try:
                        expires_at = (datetime.utcnow() + timedelta(seconds=int(expires_secs))).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass

                # Save to DB if numeric cube_id (not seed cube)
                msg_id = None
                if cube_id.isdigit():
                    # Don't store large binary file_data for voice (keep it transient for now)
                    store_data = file_data if chat_type == 'file' else None
                    msg_id = db.save_message(
                        int(cube_id), int(user_id), display_name, content,
                        msg_type=chat_type, reply_to_id=reply_to_id,
                        expires_at=expires_at, file_name=file_name,
                        file_size=file_size, file_data=store_data, duration=duration)

                # Get reply preview if replying
                reply_preview = None
                if reply_to_id:
                    orig = db.get_message_by_id(reply_to_id)
                    if orig:
                        reply_preview = {"id": orig["id"], "author": orig["display_name"],
                                         "text": (orig["content"] or "")[:80]}

                out = {
                    "type": "message",
                    "id": msg_id,
                    "user_id": user_id,
                    "display_name": display_name,
                    "content": content,
                    "msg_type": chat_type,
                    "reply_to_id": reply_to_id,
                    "reply_preview": reply_preview,
                    "file_name": file_name,
                    "file_size": file_size,
                    "file_data": file_data,   # pass through for voice/image
                    "duration": duration,
                    "expires_secs": expires_secs,
                    "created_at": datetime.utcnow().isoformat()
                }
                # Broadcast to others; send ack (with server ID) only to sender
                await _broadcast(cube_id, out, exclude=user_id)
                await _send_to_user(cube_id, user_id, {**out, "type": "message_ack"})

            elif msg_type == "react":
                # {type:"react", msg_id:X, emoji:"👍"}
                react_msg_id = data.get("msg_id")
                emoji = str(data.get("emoji", ""))[:8]
                if react_msg_id and emoji:
                    result = db.toggle_reaction(int(react_msg_id), int(user_id), display_name, emoji)
                    await _broadcast(cube_id, {
                        "type": "react",
                        "msg_id": react_msg_id,
                        "emoji": emoji,
                        "user_id": user_id,
                        "counts": result["counts"]
                    })

            elif msg_type == "dm":
                # {type:"dm", to_user_id:"X", content:"...", msg_type:"text", ...}
                to_uid = str(data.get("to_user_id", ""))
                dm_content = str(data.get("content", "")).strip()[:2000]
                dm_type    = data.get("msg_type", "text")
                file_name  = data.get("file_name")
                file_size  = data.get("file_size")
                file_data  = data.get("file_data")
                duration   = data.get("duration")
                if not dm_content or not to_uid:
                    continue

                dm_id = None
                try:
                    dm_id = db.save_dm(int(user_id), int(to_uid), dm_content, msg_type=dm_type,
                                       file_name=file_name, file_size=file_size,
                                       file_data=None, duration=duration)
                except Exception:
                    pass

                dm_out = {
                    "type": "dm",
                    "id": dm_id,
                    "from_user_id": user_id,
                    "to_user_id": to_uid,
                    "display_name": display_name,
                    "content": dm_content,
                    "msg_type": dm_type,
                    "file_name": file_name,
                    "file_size": file_size,
                    "file_data": file_data,
                    "duration": duration,
                    "created_at": datetime.utcnow().isoformat()
                }
                # Send to recipient via global user_ws (works across all cubes)
                await _notify_user(int(to_uid), dm_out)
                # Echo to sender
                await websocket.send_json(dm_out)

            elif msg_type == "system":
                pass  # ignore client-side system messages

    except WebSocketDisconnect:
        pass
    except HTTPException:
        try:
            await websocket.close(code=4001)
        except Exception:
            pass
    finally:
        if user_id and cube_id in cube_rooms:
            cube_rooms[cube_id].pop(user_id, None)
            user_ws.pop(user_id, None)  # unregister from direct notifications
            if not cube_rooms[cube_id]:
                del cube_rooms[cube_id]
            else:
                await _broadcast(cube_id, {
                    "type": "user_left",
                    "user_id": user_id,
                    "display_name": display_name,
                    "online": len(cube_rooms.get(cube_id, {})),
                    "online_users": _online_list(cube_id)
                })

# Keep old /ws/{token} for backward-compat (global room)
connected_users: dict = {}

@app.websocket("/ws/{token}")
async def websocket_legacy(websocket: WebSocket, token: str):
    await websocket.accept()
    user_id = None
    try:
        payload = decode_access_token(token)
        user_id = payload["sub"]
        connected_users[user_id] = websocket
        await websocket.send_json({"type": "connected", "user_id": user_id})
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except HTTPException:
        await websocket.close(code=4001)
    finally:
        connected_users.pop(user_id, None)
