"""
CubeWorld Backend - FastAPI + WebSocket + SQLite
Run: uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import json, secrets, time, os
from database import init_db, get_db

app = FastAPI(title="CubeWorld API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    init_db()
    print("CubeWorld server started")

FRONTEND = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(FRONTEND):
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

@app.get("/")
def root():
    index = os.path.join(FRONTEND, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"status": "CubeWorld API works", "docs": "/docs"}


class ConnectionManager:
    def __init__(self):
        self.rooms: dict = {}

    async def connect(self, ws: WebSocket, room: str, user_key: str):
        await ws.accept()
        if room not in self.rooms:
            self.rooms[room] = []
        self.rooms[room].append((ws, user_key))

    def disconnect(self, ws: WebSocket, room: str):
        if room in self.rooms:
            self.rooms[room] = [(w, k) for w, k in self.rooms[room] if w != ws]

    async def broadcast(self, room: str, message: dict):
        if room not in self.rooms:
            return
        dead = []
        for ws, key in self.rooms[room]:
            try:
                await ws.send_text(json.dumps(message, ensure_ascii=False))
            except Exception:
                dead.append(ws)
        self.rooms[room] = [(w, k) for w, k in self.rooms[room] if w not in dead]

    def online_count(self, room: str) -> int:
        return len(self.rooms.get(room, []))

manager = ConnectionManager()


@app.websocket("/ws/{room}/{user_key}")
async def websocket_endpoint(ws: WebSocket, room: str, user_key: str):
    await manager.connect(ws, room, user_key)
    db = get_db()
    short = user_key[:4] + "CUBE"
    await manager.broadcast(room, {"type": "system", "text": f"{short} joined", "time": _now(), "online": manager.online_count(room)})
    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            if data.get("type") == "message":
                text = data.get("text", "").strip()[:1000]
                if not text:
                    continue
                db.execute("INSERT INTO messages (room, user_key, text, created_at) VALUES (?,?,?,?)", (room, user_key, text, int(time.time())))
                db.commit()
                await manager.broadcast(room, {"type": "message", "author": short, "text": text, "time": _now(), "online": manager.online_count(room)})
            elif data.get("type") == "typing":
                await manager.broadcast(room, {"type": "typing", "author": short})
    except WebSocketDisconnect:
        manager.disconnect(ws, room)
        await manager.broadcast(room, {"type": "system", "text": f"{short} left", "time": _now(), "online": manager.online_count(room)})
    finally:
        db.close()


class KeyRequest(BaseModel):
    tier: str = "free"

@app.post("/api/key/generate")
def generate_key(req: KeyRequest):
    raw = secrets.token_hex(16)
    tier = req.tier if req.tier in ("free", "premium") else "free"
    prefix = f"CW-{'FREE' if tier == 'free' else 'PREM'}"
    parts = [raw[i:i+4].upper() for i in range(0, 16, 4)]
    key = f"{prefix}-{'-'.join(parts)}"
    db = get_db()
    db.execute("INSERT OR IGNORE INTO users (user_key, tier, balance, created_at) VALUES (?,?,?,?)", (key, tier, 250, int(time.time())))
    db.commit()
    db.close()
    return {"key": key, "tier": tier, "balance": 250}

@app.get("/api/key/validate/{key}")
def validate_key(key: str):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE user_key=?", (key,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "Key not found")
    return {"key": row["user_key"], "tier": row["tier"], "balance": row["balance"], "xp": row["xp"], "streak": row["streak"]}


class PostRequest(BaseModel):
    user_key: str
    text: str
    post_type: str = "text"

@app.get("/api/feed")
def get_feed(limit: int = 30, offset: int = 0):
    db = get_db()
    rows = db.execute("SELECT * FROM posts ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    db.close()
    return [_post_to_dict(r) for r in rows]

@app.post("/api/feed")
def create_post(req: PostRequest):
    _require_user(req.user_key)
    text = req.text.strip()[:2000]
    if not text:
        raise HTTPException(400, "Empty post")
    short = req.user_key[:4] + "CUBE"
    db = get_db()
    cur = db.execute("INSERT INTO posts (user_key, author, text, post_type, created_at) VALUES (?,?,?,?,?)", (req.user_key, short, text, req.post_type, int(time.time())))
    _earn(db, req.user_key, 15, "Published post", "post")
    db.commit()
    db.close()
    return {"id": cur.lastrowid, "ok": True, "earned": 15}


class ReactionRequest(BaseModel):
    user_key: str
    reaction: str

@app.post("/api/feed/{post_id}/react")
def react_post(post_id: int, req: ReactionRequest):
    allowed = {"fire", "rocket", "like", "heart", "eyes", "thinking"}
    if req.reaction not in allowed:
        raise HTTPException(400, "Unknown reaction")
    db = get_db()
    db.execute(f"UPDATE posts SET react_{req.reaction} = react_{req.reaction} + 1 WHERE id=?", (post_id,))
    db.commit()
    db.close()
    return {"ok": True}


class TransferRequest(BaseModel):
    from_key: str
    to_key: str
    amount: int

@app.get("/api/wallet/{user_key}")
def get_wallet(user_key: str):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE user_key=?", (user_key,)).fetchone()
    if not user:
        raise HTTPException(404, "Not found")
    txs = db.execute("SELECT * FROM wallet_txs WHERE user_key=? ORDER BY created_at DESC LIMIT 30", (user_key,)).fetchall()
    db.close()
    return {"balance": user["balance"], "xp": user["xp"], "txs": [dict(t) for t in txs]}

@app.post("/api/wallet/transfer")
def transfer(req: TransferRequest):
    if req.amount < 1:
        raise HTTPException(400, "Amount >= 1")
    db = get_db()
    sender = db.execute("SELECT * FROM users WHERE user_key=?", (req.from_key,)).fetchone()
    if not sender or sender["balance"] < req.amount:
        db.close()
        raise HTTPException(400, "Not enough CUBE")
    receiver = db.execute("SELECT * FROM users WHERE user_key=?", (req.to_key,)).fetchone()
    if not receiver:
        db.close()
        raise HTTPException(404, "Receiver not found")
    db.execute("UPDATE users SET balance = balance - ? WHERE user_key=?", (req.amount, req.from_key))
    db.execute("UPDATE users SET balance = balance + ? WHERE user_key=?", (req.amount, req.to_key))
    now = int(time.time())
    db.execute("INSERT INTO wallet_txs (user_key, dir, amount, desc, icon, created_at) VALUES (?,?,?,?,?,?)", (req.from_key, "out", req.amount, "Transfer out", "out", now))
    db.execute("INSERT INTO wallet_txs (user_key, dir, amount, desc, icon, created_at) VALUES (?,?,?,?,?,?)", (req.to_key, "in", req.amount, "Transfer in", "in", now))
    db.commit()
    db.close()
    return {"ok": True}

@app.post("/api/wallet/checkin/{user_key}")
def daily_checkin(user_key: str):
    _require_user(user_key)
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE user_key=?", (user_key,)).fetchone()
    today = time.strftime("%Y-%m-%d")
    if user["last_checkin"] == today:
        db.close()
        raise HTTPException(400, "Already checked in today")
    streak = (user["streak"] or 0) + 1
    bonus = 10 + min(streak * 2, 50)
    db.execute("UPDATE users SET last_checkin=?, streak=? WHERE user_key=?", (today, streak, user_key))
    _earn(db, user_key, bonus, f"Daily bonus day {streak}", "gift")
    db.commit()
    db.close()
    return {"ok": True, "earned": bonus, "streak": streak}


@app.get("/api/signals")
def get_signals(limit: int = 20):
    db = get_db()
    rows = db.execute("SELECT * FROM signals ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    db.close()
    return [dict(r) for r in rows]

@app.get("/api/leaderboard")
def leaderboard():
    db = get_db()
    rows = db.execute("SELECT user_key, balance, xp FROM users ORDER BY balance DESC LIMIT 10").fetchall()
    db.close()
    return [{"rank": i+1, "author": r["user_key"][:4]+"CUBE", "balance": r["balance"], "xp": r["xp"]} for i, r in enumerate(rows)]

@app.get("/api/chat/{room}")
def chat_history(room: str, limit: int = 50):
    db = get_db()
    rows = db.execute("SELECT * FROM messages WHERE room=? ORDER BY created_at DESC LIMIT ?", (room, limit)).fetchall()
    db.close()
    return [{"author": r["user_key"][:4]+"CUBE", "text": r["text"], "time": _ts_to_rel(r["created_at"])} for r in reversed(rows)]

@app.get("/api/online")
def online_count():
    return {"total": sum(len(v) for v in manager.rooms.values()), "rooms": {k: len(v) for k, v in manager.rooms.items()}}


def _require_user(key: str):
    db = get_db()
    row = db.execute("SELECT 1 FROM users WHERE user_key=?", (key,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(401, "Key not registered")

def _earn(db, user_key, amount, desc, icon):
    db.execute("UPDATE users SET balance=balance+?, xp=xp+? WHERE user_key=?", (amount, amount*10, user_key))
    db.execute("INSERT INTO wallet_txs (user_key,dir,amount,desc,icon,created_at) VALUES (?,?,?,?,?,?)", (user_key,"in",amount,desc,icon,int(time.time())))

def _now(): return time.strftime("%H:%M")

def _ts_to_rel(ts):
    d = int(time.time())-ts
    if d<60: return "just now"
    if d<3600: return f"{d//60}m"
    if d<86400: return f"{d//3600}h"
    return f"{d//86400}d"

def _post_to_dict(r):
    return {"id":r["id"],"author":r["author"],"text":r["text"],"type":r["post_type"],"time":_ts_to_rel(r["created_at"]),"reactions":{"fire":r["react_fire"],"rocket":r["react_rocket"],"like":r["react_like"],"heart":r["react_heart"],"eyes":r["react_eyes"],"thinking":r["react_thinking"]}}
