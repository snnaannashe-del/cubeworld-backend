import os
import secrets
import string
import hashlib
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request, BackgroundTasks, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import shutil
import uuid
import httpx

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

# Serve uploaded files statically
UPLOAD_DIR = "/tmp/cw_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

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

# ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ Pydantic models ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ

class LoginRequest(BaseModel):
    key: str

class RefreshRequest(BaseModel):
    refresh_token: str

class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    account_type: Optional[str] = None  # 'public' | 'hidden'
    bio: Optional[str] = None

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
    icon: str = "ÃÂÃÂ°ÃÂÃÂÃÂÃÂÃÂÃÂ¦"
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
    icon: Optional[str] = 'ÃÂÃÂ°ÃÂÃÂÃÂÃÂÃÂÃÂ¥'
    type: Optional[str] = 'public'  # public | private

class SetGroupHandleRequest(BaseModel):
    handle: str

class CreatePostRequest(BaseModel):
    content: str

class CreateVideoPostRequest(BaseModel):
    cube_id: Optional[int] = 1
    video_url: Optional[str] = ""
    description: Optional[str] = ""
    music: Optional[str] = ""
    post_type: Optional[str] = "short"   # "short" | "ball"
    image_url: Optional[str] = ""
    file_url: Optional[str] = ""
    title: Optional[str] = ""
    tags: Optional[Any] = None  # list or comma-string

class GroupMessageRequest(BaseModel):
    content: str
    msg_type: Optional[str] = "text"

class JoinGroupByKeyRequest(BaseModel):
    key: str

class SetGroupKeyRequest(BaseModel):
    key: str

class AddCommentRequest(BaseModel):
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

class SendDmRequest(BaseModel):
    content: str = ""
    msg_type: str = "text"
    file_name: Optional[str] = None
    file_size: Optional[str] = None
    file_url: Optional[str] = None

# ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ Auth ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ

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

# ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ User ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ

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
    if body.account_type in ('public', 'hidden'):
        db.set_account_type(user["id"], body.account_type)
    # bio stored via avatar_url prefix or ignored if DB doesn't support it yet
    return {"ok": True}

@app.post("/signals")
async def create_signal_standalone(body: CreateSignalRequest, user=Depends(get_current_user)):
    """Standalone signal ÃÂÃÂ¢ÃÂÃÂÃÂÃÂ not tied to a specific cube."""
    try:
        cube_id = 1  # default global cube
        sid = db.create_signal(cube_id, user["id"], body.ticker, body.direction,
                               body.entry_price, body.tp_price, body.sl_price, body.content)
        return {"ok": True, "id": sid}
    except Exception:
        return {"ok": False}

@app.post("/me/username")
async def set_username(body: SetUsernameRequest, user=Depends(get_current_user)):
    """Set @username. 4-24 chars, a-z 0-9 underscore only."""
    import re
    uname = body.username.strip().lower().lstrip('@')
    if not re.match(r'^[a-z0-9_]{4,24}$', uname):
        raise HTTPException(400, "ÃÂÃÂÃÂÃÂ®ÃÂÃÂÃÂÃÂ·ÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂ¹ÃÂÃÂÃÂÃÂ¼: 4-24 ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¸ÃÂÃÂÃÂÃÂ¼ÃÂÃÂÃÂÃÂ²ÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂ°, ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂºÃÂÃÂÃÂÃÂ¾ a-z 0-9 _")
    ok = db.set_username(user["id"], uname)
    if not ok:
        raise HTTPException(409, "ÃÂÃÂÃÂÃÂ­ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ @username ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¶ÃÂÃÂÃÂÃÂµ ÃÂÃÂÃÂÃÂ·ÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ")
    return {"ok": True, "username": uname}

# ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ User Search ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ

@app.get("/users/search")
async def search_users(q: str = "", limit: int = 20, user=Depends(get_current_user)):
    """Search users by @username, display_name, key_prefix, or #ID."""
    if not q.strip():
        raise HTTPException(400, "ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ¹ ÃÂÃÂÃÂÃÂ·ÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂ¿ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ")
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
        raise HTTPException(400, "ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ¹ ÃÂÃÂÃÂÃÂ·ÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂ¿ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ")
    results = db.search_cubes(q.strip(), min(limit, 50))
    return [
        {
            "id":     r["id"],
            "uid":    f"#G{r['id']}",
            "name":   r["name"],
            "handle": f"@{r['handle']}" if r.get("handle") else f"#G{r['id']}",
            "icon":   r.get("icon","ÃÂÃÂ°ÃÂÃÂÃÂÃÂ§ÃÂÃÂ"),
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
        raise HTTPException(400, "Handle: 3-24 ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¸ÃÂÃÂÃÂÃÂ¼ÃÂÃÂÃÂÃÂ²ÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂ°, ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂºÃÂÃÂÃÂÃÂ¾ a-z 0-9 _")
    ok = db.set_cube_handle(cube_id, user["id"], handle)
    if not ok:
        raise HTTPException(409, "ÃÂÃÂÃÂÃÂ­ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ @handle ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¶ÃÂÃÂÃÂÃÂµ ÃÂÃÂÃÂÃÂ·ÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ ÃÂÃÂÃÂÃÂ¸ÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂ¸ ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ ÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂµ ÃÂÃÂÃÂÃÂ²ÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂ´ÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂ")
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

# ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ Premium ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ

@app.get("/premium/status")
async def premium_status(user=Depends(get_current_user)):
    info = db.get_premium_info(user["id"])
    is_active = db.is_premium(user["id"])
    return {"is_premium": is_active, "info": info}

@app.post("/premium/activate")
async def activate_premium(body: PremiumActivateRequest, user=Depends(get_current_user)):
    db.activate_premium(user["id"], body.months, body.payment_method, body.tx_hash)
    db.record_activity(user["id"], "invite")  # reward for upgrading
    return {"ok": True, "message": f"Premium ÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂºÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¸ÃÂÃÂÃÂÃÂ²ÃÂÃÂÃÂÃÂ¸ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ²ÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂ½ ÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂ° {body.months} ÃÂÃÂÃÂÃÂ¼ÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂ."}

# ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ Activity tracking ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ

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
    # Milestone rewards ÃÂÃÂ¢ÃÂÃÂÃÂÃÂ award CUBE at activity milestones and notify via WS
    stats = db.get_my_activity_stats(user["id"])
    bonus = 0
    reason = ""
    if body.event == "message":
        msgs = stats.get("messages", 0)
        if msgs > 0 and msgs % 10 == 0:
            bonus, reason = 5, f"+5 CUBE ÃÂÃÂÃÂÃÂ· {msgs} ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ±ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂ¸ÃÂÃÂÃÂÃÂ¹"
    elif body.event == "post":
        posts = stats.get("posts", 0)
        if posts > 0 and posts % 5 == 0:
            bonus, reason = 10, f"+10 CUBE ÃÂÃÂÃÂÃÂ· {posts} ÃÂÃÂÃÂÃÂ¿ÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ²"
    elif body.event == "invite":
        invites = stats.get("invites", 0)
        if invites > 0 and invites % 1 == 0:
            bonus, reason = 50, "+50 CUBE ÃÂÃÂÃÂÃÂ· ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂ»"
    if bonus:
        db.add_cube_balance(user["id"], float(bonus), "activity", description=reason)
        new_bal = db.get_cube_balance(user["id"])
        await _notify_user(user["id"], {"type": "balance_update", "balance": new_bal, "reason": reason})
    return {"ok": True, "bonus": bonus}

# ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ Rewards ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ

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
            "message": f"ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ²ÃÂÃÂÃÂÃÂºÃÂÃÂÃÂÃÂ° ÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂ° ${amount:.2f} ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ·ÃÂÃÂÃÂÃÂ´ÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂ°. ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¿ÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ° ÃÂÃÂÃÂÃÂ² ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂ¸ÃÂÃÂÃÂÃÂµ 24ÃÂÃÂÃÂÃÂ."}

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

# ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ Groups ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ

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
    gid = db.create_group(user["id"], body.name.strip(), body.description or '', body.icon or 'ÃÂÃÂ°ÃÂÃÂÃÂÃÂÃÂÃÂ¥', gtype)
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

@app.get("/groups/{group_id}/messages")
async def get_group_messages(group_id: int, user=Depends(get_current_user)):
    info = db.get_group_info(group_id, user["id"])
    if not info:
        raise HTTPException(404, "Group not found")
    if not info.get("is_member"):
        raise HTTPException(403, "Not a member")
    msgs = db.get_group_messages(group_id, limit=80)
    return msgs

@app.post("/groups/{group_id}/messages")
async def send_group_message(group_id: int, body: GroupMessageRequest, user=Depends(get_current_user)):
    info = db.get_group_info(group_id, user["id"])
    if not info:
        raise HTTPException(404, "Group not found")
    if not info.get("is_member"):
        raise HTTPException(403, "Not a member")
    content = body.content.strip()[:2000]
    if not content:
        raise HTTPException(400, "Empty message")
    display_name = db.get_display_name(user["id"])
    mid = db.add_group_message(group_id, user["id"], display_name, content, body.msg_type or "text")
    msg = {"id": mid, "group_id": group_id, "user_id": user["id"],
           "display_name": display_name, "content": content,
           "msg_type": body.msg_type or "text",
           "created_at": datetime.utcnow().isoformat()}
    # Broadcast to group members in group_ws rooms
    for ws in list(_group_ws.get(str(group_id), {}).values()):
        try:
            await ws.send_json({"type": "group_msg", "group_id": group_id, **msg})
        except Exception:
            pass
    return msg

@app.put("/groups/{group_id}/messages/{msg_id}")
async def edit_group_message(group_id: int, msg_id: int, body: dict, user=Depends(get_current_user)):
    """Edit own group message."""
    info = db.get_group_info(group_id, user["id"])
    if not info or not info.get("is_member"):
        raise HTTPException(403, "Not a member")
    new_content = (body.get("content") or "").strip()
    if not new_content:
        raise HTTPException(400, "Content required")
    conn = db.get_db(); c = conn.cursor()
    if db._PG:
        c.execute("SELECT user_id FROM group_messages WHERE id=%s AND group_id=%s", (msg_id, group_id))
    else:
        c.execute("SELECT user_id FROM group_messages WHERE id=? AND group_id=?", (msg_id, group_id))
    row = c.fetchone()
    if not row:
        conn.close(); raise HTTPException(404, "Message not found")
    msg_owner = row[0] if isinstance(row, (list, tuple)) else row.get("user_id")
    if str(msg_owner) != str(user["id"]):
        conn.close(); raise HTTPException(403, "Cannot edit other's message")
    if db._PG:
        c.execute("UPDATE group_messages SET content=%s WHERE id=%s", (new_content, msg_id))
    else:
        c.execute("UPDATE group_messages SET content=? WHERE id=?", (new_content, msg_id))
    conn.commit(); conn.close()
    return {"ok": True, "id": msg_id, "content": new_content}

@app.delete("/groups/{group_id}/messages/{msg_id}")
async def delete_group_message(group_id: int, msg_id: int, user=Depends(get_current_user)):
    """Delete a group message (own message, or admin)."""
    info = db.get_group_info(group_id, user["id"])
    if not info or not info.get("is_member"):
        raise HTTPException(403, "Not a member")
    conn = db.get_db(); c = conn.cursor()
    # Check ownership
    if db._PG:
        c.execute("SELECT user_id FROM group_messages WHERE id=%s AND group_id=%s", (msg_id, group_id))
    else:
        c.execute("SELECT user_id FROM group_messages WHERE id=? AND group_id=?", (msg_id, group_id))
    row = c.fetchone()
    if not row:
        conn.close(); raise HTTPException(404, "Message not found")
    msg_owner = row[0] if isinstance(row, (list, tuple)) else row.get("user_id")
    is_admin = (str(info.get("owner_id", "")) == str(user["id"]))
    if str(msg_owner) != str(user["id"]) and not is_admin:
        conn.close(); raise HTTPException(403, "Cannot delete other's message")
    if db._PG:
        c.execute("DELETE FROM group_messages WHERE id=%s", (msg_id,))
    else:
        c.execute("DELETE FROM group_messages WHERE id=?", (msg_id,))
    conn.commit(); conn.close()
    return {"deleted": True}

@app.get("/groups/{group_id}/info")
async def get_group_info(group_id: int, creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)):
    uid = None
    if creds:
        try:
            payload = decode_access_token(creds.credentials)
            uid = int(payload["sub"])
        except Exception:
            pass
    info = db.get_group_info(group_id, uid)
    if not info:
        raise HTTPException(404, "Group not found")
    return info

@app.get("/groups/{group_id}/members")
async def get_group_members(group_id: int, user=Depends(get_current_user)):
    info = db.get_group_info(group_id, user["id"])
    if not info:
        raise HTTPException(404, "Group not found")
    if not info.get("is_member"):
        raise HTTPException(403, "Not a member")
    return db.get_group_members(group_id)

@app.post("/groups/join-key")
async def join_group_by_key(body: JoinGroupByKeyRequest, user=Depends(get_current_user)):
    g = db.join_group_by_key(body.key.strip(), user["id"])
    if not g:
        raise HTTPException(404, "ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ ÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂµ ÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂ¹ÃÂÃÂÃÂÃÂ´ÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂ½ ÃÂÃÂÃÂÃÂ¸ÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂ¸ ÃÂÃÂÃÂÃÂ³ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¿ÃÂÃÂÃÂÃÂ¿ÃÂÃÂÃÂÃÂ° ÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂ´ÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¿ÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂ°")
    return {"joined": True, "group": g}

@app.post("/groups/{group_id}/set-key")
async def set_group_key(group_id: int, body: SetGroupKeyRequest, user=Depends(get_current_user)):
    ok = db.set_group_key(group_id, user["id"], body.key.strip())
    if not ok:
        raise HTTPException(403, "ÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂºÃÂÃÂÃÂÃÂ¾ ÃÂÃÂÃÂÃÂ²ÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂ´ÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂ ÃÂÃÂÃÂÃÂ¼ÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ¶ÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂ ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ²ÃÂÃÂÃÂÃÂ¸ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ ÃÂÃÂÃÂÃÂºÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ")
    return {"ok": True}

@app.post("/feed/{post_id}/view")
async def record_post_view(post_id: int):
    db.increment_post_views(post_id)
    return {"ok": True}

@app.post("/upload")
async def upload_file(file: UploadFile = File(...),
                      creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)):
    """Upload image/video/document. Returns public URL.
    Uses Cloudinary if CLOUDINARY_CLOUD_NAME + CLOUDINARY_UPLOAD_PRESET env vars are set,
    otherwise falls back to local temp storage.
    """
    MAX_SIZE = 100 * 1024 * 1024  # 100 MB
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(413, "File too large (max 100 MB)")

    cld_cloud  = os.getenv("CLOUDINARY_CLOUD_NAME", "")
    cld_preset = os.getenv("CLOUDINARY_UPLOAD_PRESET", "")

    # ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ Cloudinary upload (permanent) ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ
    if cld_cloud and cld_preset:
        try:
            import io
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"https://api.cloudinary.com/v1_1/{cld_cloud}/auto/upload",
                    files={"file": (file.filename or "upload", content, file.content_type or "application/octet-stream")},
                    data={"upload_preset": cld_preset}
                )
            j = resp.json()
            if "secure_url" in j:
                return {"url": j["secure_url"], "filename": file.filename,
                        "size": len(content), "ok": True, "storage": "cloudinary"}
        except Exception as e:
            # fall through to local storage if Cloudinary fails
            pass

    # ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ Local temp storage (fallback) ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ
    ext = os.path.splitext(file.filename or "file")[1].lower() or ".bin"
    allowed = {".jpg",".jpeg",".png",".gif",".webp",".mp4",".webm",".mov",
               ".pdf",".zip",".txt",".doc",".docx",".csv",".xls",".xlsx"}
    if ext not in allowed:
        ext = ".bin"
    fname = f"{uuid.uuid4().hex}{ext}"
    fpath = os.path.join(UPLOAD_DIR, fname)
    with open(fpath, "wb") as f:
        f.write(content)
    base_url = os.getenv("API_BASE_URL", "https://cubeworld-backend.onrender.com")
    url = f"{base_url}/uploads/{fname}"
    return {"url": url, "filename": file.filename, "size": len(content),
            "ok": True, "storage": "local_temp"}

# ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ Cubes CRUD ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ

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
    """Resolve a cube invite key ÃÂÃÂ¢ÃÂÃÂÃÂÃÂ returns cube info if valid."""
    key = body.cube_key.strip().upper()
    cube = db.get_cube_by_key(key)
    if not cube:
        raise HTTPException(status_code=404, detail="ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ ÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂµ ÃÂÃÂÃÂÃÂ½ÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂ¹ÃÂÃÂÃÂÃÂ´ÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂ½ ÃÂÃÂÃÂÃÂ¸ÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂ¸ ÃÂÃÂÃÂÃÂºÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ± ÃÂÃÂÃÂÃÂ¸ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂº")
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
        raise HTTPException(status_code=403, detail="ÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂºÃÂÃÂÃÂÃÂ¾ ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ·ÃÂÃÂÃÂÃÂ´ÃÂÃÂÃÂÃÂ°ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂ ÃÂÃÂÃÂÃÂ¼ÃÂÃÂÃÂÃÂ¾ÃÂÃÂÃÂÃÂ¶ÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂ ÃÂÃÂÃÂÃÂ²ÃÂÃÂÃÂÃÂ¸ÃÂÃÂÃÂÃÂ´ÃÂÃÂÃÂÃÂµÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ ÃÂÃÂÃÂÃÂºÃÂÃÂÃÂÃÂ»ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ")
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

@app.get("/dm/inbox")
async def get_dm_inbox(user=Depends(get_current_user)):
    """Return all DM conversations for the current user (inbox)."""
    return db.get_dm_inbox(user["id"])

@app.post("/dm/{other_user_id}")
async def send_dm_http(other_user_id: int, body: SendDmRequest, user=Depends(get_current_user)):
    """HTTP fallback for sending DMs (WS is preferred, this ensures delivery even if WS fails)."""
    content = (body.content or "").strip()[:4000]
    if not content:
        raise HTTPException(400, "content required")
    display_name = db.get_display_name(user["id"])
    dm_id = db.save_dm(user["id"], other_user_id, content, msg_type=body.msg_type,
                        file_name=body.file_name, file_size=body.file_size)
    dm_out = {
        "type": "dm", "id": dm_id,
        "from_user_id": user["id"], "to_user_id": other_user_id,
        "display_name": display_name, "content": content,
        "msg_type": body.msg_type, "file_name": body.file_name, "file_size": body.file_size,
        "file_url": body.file_url or (content if body.msg_type in ("image","video","file") else None),
        "created_at": datetime.utcnow().isoformat(), "ok": True
    }
    # Real-time delivery to recipient via WS if connected
    await _notify_user(other_user_id, dm_out)
    return dm_out

@app.get("/dm/{other_user_id}")
async def get_dm_history(other_user_id: int, user=Depends(get_current_user)):
    history = db.get_dm_history(user["id"], other_user_id)
    return history

# ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ Global Video Feed ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ

@app.get("/feed")
async def get_global_feed(limit: int = 30, offset: int = 0):
    return db.get_global_feed(limit=min(limit,100), offset=offset)

@app.get("/feed/following")
async def get_following_feed(user=Depends(get_current_user)):
    return db.get_following_feed(user["id"])

@app.post("/feed/post")
async def create_feed_post(body: CreateVideoPostRequest, user=Depends(get_current_user)):
    import json as _json
    desc = (body.description or '').strip()[:2000]
    video_url = (body.video_url or '').strip()[:500]
    music = (body.music or '').strip()[:200]
    title = (body.title or '').strip()[:300]
    image_url = (body.image_url or body.file_url or '').strip()[:500]
    post_type = (body.post_type or 'short').lower()
    display_name = db.get_display_name(user["id"])
    # Normalize tags to JSON string
    raw_tags = body.tags
    if isinstance(raw_tags, list):
        tags_str = _json.dumps(raw_tags, ensure_ascii=False)
    elif isinstance(raw_tags, str) and raw_tags.strip():
        tags_str = _json.dumps([t.strip() for t in raw_tags.split(',') if t.strip()], ensure_ascii=False)
    else:
        tags_str = '[]'
    # Require at least something
    if not desc and not title and not video_url and not image_url:
        raise HTTPException(400, "Add title, description, or media")
    pid = db.create_video_post(body.cube_id or 1, user["id"], display_name, video_url, desc, music)
    # Store extra fields if columns exist
    try:
        import database as _db2
        conn = _db2.get_db(); cc = conn.cursor()
        # Add missing columns gracefully
        for col_sql in [
            "ALTER TABLE posts ADD COLUMN post_type TEXT DEFAULT 'short'",
            "ALTER TABLE posts ADD COLUMN image_url TEXT DEFAULT ''",
            "ALTER TABLE posts ADD COLUMN title TEXT DEFAULT ''",
            "ALTER TABLE posts ADD COLUMN tags TEXT DEFAULT '[]'",
        ]:
            try: cc.execute(col_sql)
            except Exception: pass
        cc.execute("UPDATE posts SET post_type=?,image_url=?,title=?,tags=? WHERE id=?",
                   (post_type, image_url, title, tags_str, pid))
        conn.commit(); conn.close()
    except Exception:
        pass
    tags_out = []
    try: tags_out = _json.loads(tags_str)
    except Exception: pass
    return {"id": pid, "user_id": user["id"], "display_name": display_name,
            "video_url": video_url, "image_url": image_url, "title": title,
            "description": desc, "music": music, "post_type": post_type,
            "tags": tags_out, "likes": 0, "comment_count": 0, "view_count": 0,
            "created_at": datetime.utcnow().isoformat(), "ok": True}

@app.post("/feed/{post_id}/like")
async def like_feed_post(post_id: int, user=Depends(get_current_user)):
    likes, liked = db.like_post(post_id, user["id"])
    return {"likes": likes, "liked": liked, "ok": True}

@app.get("/feed/{post_id}/comments")
async def get_feed_comments(post_id: int):
    return db.get_post_comments(post_id)

@app.post("/feed/{post_id}/comment")
async def add_feed_comment(post_id: int, body: AddCommentRequest, user=Depends(get_current_user)):
    content = (body.content or '').strip()[:500]
    if not content:
        raise HTTPException(400, "Content required")
    display_name = db.get_display_name(user["id"])
    cid = db.add_post_comment(post_id, user["id"], display_name, content)
    return {"id": cid, "display_name": display_name, "content": content,
            "created_at": datetime.utcnow().isoformat(), "ok": True}

@app.get("/follow/{target_id}")
async def get_follow_status(target_id: int, user=Depends(get_current_user)):
    following = db.is_following(user["id"], target_id)
    counts = db.get_follow_counts(target_id)
    return {"following": following, **counts}

@app.post("/follow/{target_id}")
async def follow(target_id: int, user=Depends(get_current_user)):
    ok = db.follow_user(user["id"], target_id)
    counts = db.get_follow_counts(target_id)
    return {"ok": ok, "following": True, **counts}

@app.delete("/follow/{target_id}")
async def unfollow(target_id: int, user=Depends(get_current_user)):
    db.unfollow_user(user["id"], target_id)
    counts = db.get_follow_counts(target_id)
    return {"ok": True, "following": False, **counts}

# ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ Cube Posts (legacy) ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ

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

# ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ Admin / Stats ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ

@app.post("/admin/reset")
async def admin_reset(request: Request):
    secret = request.headers.get("X-Admin-Secret", "")
    # Allow hardcoded one-time reset token OR env secret
    _allowed = secret == "cw-reset-2025-x9z" or (ADMIN_SECRET and secret == ADMIN_SECRET)
    if not _allowed:
        raise HTTPException(403, "Forbidden")
    db.reset_all_data()
    return {"ok": True, "message": "All data cleared. 8 default cubes seeded."}

@app.post("/admin/seed-cubes")
async def admin_seed_cubes(request: Request):
    secret = request.headers.get("X-Admin-Secret", "")
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(403, "Forbidden")
    db.seed_default_cubes()
    return {"ok": True, "message": "8 default cubes seeded."}

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

# ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ WebSocket: per-cube rooms ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ
# cube_rooms: { cube_id_str: { user_id_str: {"ws": websocket, "display_name": str} } }
# user_ws: { user_id_str: websocket }  ÃÂÃÂ¢ÃÂÃÂÃÂÃÂ tracks the most recent WS for each user (for direct notifications)

cube_rooms: Dict[str, Dict[str, dict]] = {}
user_ws: Dict[str, object] = {}
_group_ws: Dict[str, Dict[str, object]] = {}  # group_id_str -> {user_id_str: websocket}

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
                # {type:"react", msg_id:X, emoji:"ÃÂÃÂ°ÃÂÃÂÃÂÃÂÃÂÃÂ"}
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

# ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ WebSocket: group chat room ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ
@app.websocket("/ws/group/{token}/{group_id}")
async def ws_group(websocket: WebSocket, token: str, group_id: str):
    """Real-time WebSocket for group chat ÃÂÃÂ¢ÃÂÃÂÃÂÃÂ receives group_msg broadcasts."""
    await websocket.accept()
    user_id = None
    try:
        payload      = decode_access_token(token)
        user_id      = payload["sub"]
        display_name = db.get_display_name(int(user_id))
        db.update_last_seen(int(user_id))

        gid = str(group_id)
        if gid not in _group_ws:
            _group_ws[gid] = {}
        _group_ws[gid][user_id] = websocket
        user_ws[user_id] = websocket

        await websocket.send_json({
            "type": "group_joined",
            "group_id": group_id,
            "user_id": user_id,
            "display_name": display_name,
            "online": len(_group_ws[gid])
        })

        while True:
            data = await websocket.receive_json()
            t = data.get("type", "")
            if t == "ping":
                await websocket.send_json({"type": "pong"})
            elif t == "typing":
                # Broadcast typing indicator to other members
                for uid, ws in list(_group_ws.get(gid, {}).items()):
                    if uid != user_id:
                        try:
                            await ws.send_json({
                                "type": "typing",
                                "user_id": user_id,
                                "display_name": display_name,
                                "group_id": group_id
                            })
                        except Exception:
                            pass

    except WebSocketDisconnect:
        pass
    except HTTPException:
        await websocket.close(code=4001)
    finally:
        _group_ws.get(str(group_id), {}).pop(user_id, None)
        if user_id and user_ws.get(user_id) is websocket:
            user_ws.pop(user_id, None)

# Keep old /ws/{token} for backward-compat (global room)
connected_users: dict = {}

@app.websocket("/ws/{token}")
async def websocket_legacy(websocket: WebSocket, token: str):
    """Global user WebSocket ÃÂÃÂ¢ÃÂÃÂÃÂÃÂ for DM delivery when user is not in any cube."""
    await websocket.accept()
    user_id = None
    try:
        payload = decode_access_token(token)
        user_id = payload["sub"]
        connected_users[user_id] = websocket
        user_ws[user_id] = websocket  # register for DM delivery
        await websocket.send_json({"type": "connected", "user_id": user_id})
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif data.get("type") == "dm":
                # Handle DM sending from global WS (same logic as cube WS)
                to_uid = str(data.get("to_user_id", ""))
                dm_content = (data.get("content") or "").strip()
                dm_type = data.get("msg_type", "text")
                if dm_content and to_uid:
                    display_name = db.get_display_name(int(user_id))
                    dm_id = db.save_dm(int(user_id), int(to_uid), dm_content, msg_type=dm_type)
                    dm_out = {
                        "type": "dm", "id": dm_id,
                        "from_user_id": user_id, "to_user_id": to_uid,
                        "display_name": display_name, "content": dm_content,
                        "msg_type": dm_type, "created_at": datetime.utcnow().isoformat()
                    }
                    await _notify_user(int(to_uid), dm_out)
                    await websocket.send_json(dm_out)
    except WebSocketDisconnect:
        pass
    except HTTPException:
        await websocket.close(code=4001)
    finally:
        connected_users.pop(user_id, None)
        user_ws.pop(user_id, None)

# ÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂ Admin: reset DB to clean state ÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂÃÂ¢ÃÂÃÂ
@app.post("/admin/reset-db")
async def reset_database(request: Request):
    conn = db.get_db()
    conn.autocommit = True
    c = conn.cursor()
    ordered = [
        "dm_messages","messages","cube_members","group_messages","group_members",
        "groups","reward_claims","wallets","blockchain_txs","cube_transactions",
        "key_upgrades","signals","posts","cubes","sessions","users"
    ]
    errors = []
    for tbl in ordered:
        try:
            c.execute(f"DELETE FROM {tbl}")
        except Exception as e:
            errors.append(f"{tbl}: {str(e)[:50]}")
    for seq in ["users_id_seq","cubes_id_seq","messages_id_seq","groups_id_seq"]:
        try: c.execute(f"ALTER SEQUENCE {seq} RESTART WITH 1")
        except Exception: pass
    seeds = [
        ("Genesis Cube","The first cube","\U0001f30d","#0095F6"),
        ("Crypto Hub","Trade and discuss crypto","\u20bf","#F7931A"),
        ("Tech Lab","Build and hack together","\u26a1","#00D4AA"),
        ("Art Space","Creative expressions","\U0001f3a8","#FF6B6B"),
        ("Game Zone","Gaming community","\U0001f3ae","#9B59B6"),
        ("Music","Beats and vibes","\U0001f3b5","#1DB954"),
        ("Science","Explore the unknown","\U0001f52c","#E74C3C"),
        ("Social Hub","Meet new people","\U0001f310","#F39C12"),
    ]
    far = "2099-01-01 00:00:00"
    inserted = 0
    for name,desc,icon,color in seeds:
        try:
            c.execute("INSERT INTO cubes (owner_id,name,description,icon,color,type,life_hours,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                      (None,name,desc,icon,color,"public",999999,far))
            inserted += 1
        except Exception as e:
            errors.append(f"seed {name}: {str(e)[:50]}")
    conn.close()
    return {"ok": True, "cubes_inserted": inserted, "errors": errors}

