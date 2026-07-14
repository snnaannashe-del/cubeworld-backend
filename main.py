import os
import secrets
import string
import hashlib
import jwt
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

import database as db

SECRET_KEY   = os.getenv("JWT_SECRET", "changeme-very-secret-key-cubeworld")
JWT_ALG      = "HS256"
ACCESS_TTL   = int(os.getenv("ACCESS_TTL_MINUTES", "60"))
REFRESH_TTL  = int(os.getenv("REFRESH_TTL_DAYS",   "30"))

CUBE_CONTRACT = os.getenv("CUBE_CONTRACT_ADDRESS", "")
POLYGON_RPC   = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
MINTER_KEY    = os.getenv("MINTER_PRIVATE_KEY", "")
MINTER_ADDR   = os.getenv("MINTER_ADDRESS", "")

app = FastAPI(title="CubeWorld API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

db.init_db()

bearer_scheme = HTTPBearer(auto_error=False)

_CHARS = string.ascii_uppercase + string.digits

def _random_segment(n: int = 4) -> str:
    return "".join(secrets.choice(_CHARS) for _ in range(n))

def generate_cube_key() -> str:
    return f"CUBE-{_random_segment()}-{_random_segment()}-{_random_segment()}"

def create_access_token(user_id: int, key_type: str) -> str:
    payload = {
        "sub": str(user_id),
        "key_type": key_type,
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TTL),
        "iat": datetime.utcnow(),
        "type": "access",
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALG)

def create_refresh_token() -> str:
    return secrets.token_hex(32)

def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALG])
        if payload.get("type") != "access":
            raise ValueError("not an access token")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except Exception:
        raise HTTPException(401, "Invalid token")

async def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    if not creds:
        raise HTTPException(401, "No token")
    payload = decode_access_token(creds.credentials)
    user = db.get_user_by_id(int(payload["sub"]))
    if not user:
        raise HTTPException(401, "User not found")
    db.update_last_seen(user["id"])
    return user

class LoginRequest(BaseModel):
    key: str

class RefreshRequest(BaseModel):
    refresh_token: str

class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None
    avatar_url:   Optional[str] = None

class WalletLinkRequest(BaseModel):
    address: str
    chain_id: int = 137

@app.post("/auth/generate")
async def generate_key(request: Request):
    for _ in range(10):
        raw_key    = generate_cube_key()
        key_hash   = db.hash_key(raw_key)
        existing   = db.get_user_by_key_hash(key_hash)
        if not existing:
            break
    else:
        raise HTTPException(500, "Key generation failed")

    key_prefix = raw_key[:9]
    user_id    = db.create_user(key_hash, key_prefix, key_type="free")
    db.add_cube_balance(user_id, 100.0, "mint", description="Welcome bonus")

    access_token  = create_access_token(user_id, "free")
    refresh_raw   = create_refresh_token()
    refresh_hash  = db.hash_token(refresh_raw)
    expires_at    = (datetime.utcnow() + timedelta(days=REFRESH_TTL)).strftime("%Y-%m-%d %H:%M:%S")

    user_agent = request.headers.get("user-agent", "")
    ip_raw     = request.client.host if request.client else ""
    ip_hash    = hashlib.sha256(ip_raw.encode()).hexdigest() if ip_raw else None
    db.create_session(user_id, refresh_hash, expires_at, user_agent=user_agent, ip_hash=ip_hash)

    return {
        "key":           raw_key,
        "key_prefix":    key_prefix,
        "key_type":      "free",
        "access_token":  access_token,
        "refresh_token": refresh_raw,
        "cube_balance":  100.0,
        "message":       "Save your key - it cannot be recovered!",
    }


@app.post("/auth/login")
async def login_with_key(body: LoginRequest, request: Request):
    raw_key  = body.key.strip().upper()
    key_hash = db.hash_key(raw_key)
    user     = db.get_user_by_key_hash(key_hash)
    if not user:
        raise HTTPException(401, "Key not found or invalid")

    user_id  = user["id"]
    key_type = user["key_type"]

    access_token = create_access_token(user_id, key_type)
    refresh_raw  = create_refresh_token()
    refresh_hash = db.hash_token(refresh_raw)
    expires_at   = (datetime.utcnow() + timedelta(days=REFRESH_TTL)).strftime("%Y-%m-%d %H:%M:%S")

    user_agent = request.headers.get("user-agent", "")
    ip_raw     = request.client.host if request.client else ""
    ip_hash    = hashlib.sha256(ip_raw.encode()).hexdigest() if ip_raw else None
    db.create_session(user_id, refresh_hash, expires_at, user_agent=user_agent, ip_hash=ip_hash)
    db.update_last_seen(user_id)

    return {
        "key_prefix":    user["key_prefix"],
        "key_type":      key_type,
        "access_token":  access_token,
        "refresh_token": refresh_raw,
        "cube_balance":  user["cube_balance"],
        "display_name":  user["display_name"],
    }


@app.post("/auth/refresh")
async def refresh_tokens(body: RefreshRequest):
    token_hash = db.hash_token(body.refresh_token)
    session    = db.get_session_by_token_hash(token_hash)
    if not session:
        raise HTTPException(401, "Invalid or expired refresh token")
    access_token = create_access_token(session["user_id"], session["key_type"])
    return {"access_token": access_token, "key_type": session["key_type"]}


@app.post("/auth/logout")
async def logout(body: RefreshRequest):
    db.delete_session(db.hash_token(body.refresh_token))
    return {"ok": True}


@app.get("/me")
async def get_me(user=Depends(get_current_user)):
    return {
        "id":           user["id"],
        "key_prefix":   user["key_prefix"],
        "key_type":     user["key_type"],
        "display_name": user["display_name"],
        "avatar_url":   user["avatar_url"],
        "cube_balance": user["cube_balance"],
        "created_at":   user["created_at"],
        "last_seen":    user["last_seen"],
    }


@app.patch("/me")
async def update_me(body: UpdateProfileRequest, user=Depends(get_current_user)):
    conn = db.get_db()
    if body.display_name is not None:
        conn.execute("UPDATE users SET display_name=? WHERE id=?", (body.display_name, user["id"]))
    if body.avatar_url is not None:
        conn.execute("UPDATE users SET avatar_url=? WHERE id=?", (body.avatar_url, user["id"]))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/wallet/link")
async def link_wallet(body: WalletLinkRequest, user=Depends(get_current_user)):
    db.link_wallet(user["id"], body.address, body.chain_id)
    return {"ok": True, "address": body.address}


@app.get("/cube/balance")
async def cube_balance(user=Depends(get_current_user)):
    return {"balance": db.get_cube_balance(user["id"]), "key_type": user["key_type"]}


@app.get("/stats")
async def stats():
    return db.get_stats()


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


connected_users: dict[str, WebSocket] = {}

@app.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
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
            elif data.get("type") == "broadcast":
                out = {"type": "message", "from": user_id, "data": data.get("data")}
                for uid, ws in list(connected_users.items()):
                    if uid != user_id:
                        try: await ws.send_json(out)
                        except: pass
    except WebSocketDisconnect:
        pass
    except HTTPException:
        await websocket.close(code=4001)
    finally:
        if user_id and user_id in connected_users:
            del connected_users[user_id]
