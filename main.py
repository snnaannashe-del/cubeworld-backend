import os
import secrets
import string
import hashlib
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

import database as db

SECRET_KEY   = os.getenv("JWT_SECRET", "changeme-very-secret-key-cubeworld")
JWT_ALG      = "HS256"
ACCESS_TTL   = int(os.getenv("ACCESS_TTL_MINUTES", "60"))
REFRESH_TTL  = int(os.getenv("REFRESH_TTL_DAYS", "30"))
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")

app = FastAPI(title="CubeWorld API", version="3.0.0")
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

class CreateCubeRequest(BaseModel):
    name: str
    description: str = ""
    icon: str = "📦"
    color: str = "#7c6fcd"
    type: str = "public"
    life_hours: int = 24

class CreatePostRequest(BaseModel):
    content: str

class CreateSignalRequest(BaseModel):
    ticker: str
    direction: str = "LONG"
    entry_price: float = 0.0
    tp_price: float = 0.0
    sl_price: float = 0.0
    content: str = ""

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
            "cube_balance": user["cube_balance"], "display_name": user["display_name"]}

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
            "cube_balance": user["cube_balance"], "created_at": user["created_at"],
            "last_seen": user["last_seen"]}

@app.patch("/me")
async def update_me(body: UpdateProfileRequest, user=Depends(get_current_user)):
    conn = db.get_db()
    if body.display_name is not None:
        conn.execute("UPDATE users SET display_name=? WHERE id=?", (body.display_name, user["id"]))
    if body.avatar_url is not None:
        conn.execute("UPDATE users SET avatar_url=? WHERE id=?", (body.avatar_url, user["id"]))
    conn.commit(); conn.close()
    return {"ok": True}

@app.post("/wallet/link")
async def link_wallet(body: WalletLinkRequest, user=Depends(get_current_user)):
    db.link_wallet(user["id"], body.address, body.chain_id)
    return {"ok": True, "address": body.address}

@app.get("/cube/balance")
async def cube_balance(user=Depends(get_current_user)):
    return {"balance": db.get_cube_balance(user["id"]), "key_type": user["key_type"]}

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
    cube_id   = db.create_cube(user["id"], name, desc, icon, color, ctype, life_h)
    return {"id": cube_id, "name": name, "type": ctype, "life_hours": life_h, "ok": True}

@app.get("/cubes/{cube_id}/messages")
async def get_messages(cube_id: int, limit: int = 50):
    return db.get_messages(cube_id, min(limit, 100))

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

@app.get("/cubes/{cube_id}/online")
async def cube_online_count(cube_id: str):
    return {"online": len(cube_rooms.get(cube_id, {}))}

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
    # Add realtime WS connected count
    s["connected_ws"] = sum(len(v) for v in cube_rooms.values())
    return s

@app.get("/health")
async def health():
    return {"status": "ok", "version": "3.0.0"}

# ── WebSocket: per-cube rooms ─────────────────────────────────────────────────
# cube_rooms: { cube_id_str: { user_id_str: (websocket, display_name) } }

cube_rooms: Dict[str, Dict[str, tuple]] = {}

async def _broadcast(cube_id: str, msg: dict, exclude: str = None):
    for uid, (ws, _) in list(cube_rooms.get(cube_id, {}).items()):
        if uid == exclude:
            continue
        try:
            await ws.send_json(msg)
        except Exception:
            pass

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
        cube_rooms[cube_id][user_id] = (websocket, display_name)

        online = len(cube_rooms[cube_id])

        await websocket.send_json({
            "type": "joined", "user_id": user_id,
            "display_name": display_name, "online": online
        })
        await _broadcast(cube_id, {
            "type": "user_joined", "user_id": user_id,
            "display_name": display_name, "online": online
        }, exclude=user_id)

        while True:
            data     = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            elif msg_type == "message":
                content = str(data.get("content", "")).strip()[:500]
                if not content:
                    continue
                # Save to DB if cube_id is numeric
                msg_id = None
                if cube_id.isdigit():
                    msg_id = db.save_message(int(cube_id), int(user_id), display_name, content)

                out = {
                    "type": "message",
                    "id": msg_id,
                    "user_id": user_id,
                    "display_name": display_name,
                    "content": content,
                    "created_at": datetime.utcnow().isoformat()
                }
                await _broadcast(cube_id, out)  # include sender

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
            if not cube_rooms[cube_id]:
                del cube_rooms[cube_id]
            else:
                await _broadcast(cube_id, {
                    "type": "user_left", "user_id": user_id,
                    "online": len(cube_rooms.get(cube_id, {}))
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
