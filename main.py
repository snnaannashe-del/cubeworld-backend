"""
CubeWorld Backend — FastAPI + WebSocket + SQLite + Web3 (Polygon CUBE Token)
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

# ────────────────────────────────────────────────────────────
#  Web3 / Polygon setup  (optional — runs fine without it)
# ─────────────────────────────────────────────────────────────
POLYGON_RPC          = os.environ.get("POLYGON_RPC", "https://polygon-rpc.com")
CUBE_CONTRACT_ADDR   = os.environ.get("CUBE_CONTRACT_ADDRESS", "")
MINTER_PRIVATE_KEY   = os.environ.get("MINTER_PRIVATE_KEY", "")

CUBE_ABI = [
    {
        "name": "mint", "type": "function", "stateMutability": "nonpayable",
        "inputs": [
            {"name": "to",     "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "reason", "type": "string"}
        ],
        "outputs": []
    },
    {
        "name": "balanceOf", "type": "function", "stateMutability": "view",
        "inputs":  [{"name": "account", "type": "address"}],
        "outputs": [{"type": "uint256"}]
    },
    {
        "name": "totalSupply", "type": "function", "stateMutability": "view",
        "inputs": [], "outputs": [{"type": "uint256"}]
    },
    {
        "name": "transfer", "type": "function", "stateMutability": "nonpayable",
        "inputs": [{"name": "to", "type": "address"}, {"name": "value", "type": "uint256"}],
        "outputs": [{"type": "bool"}]
    }
]

w3 = None
cube_contract = None
minter_account = None

def init_web3():
    global w3, cube_contract, minter_account
    try:
        from web3 import Web3
        from eth_account import Account

        if not CUBE_CONTRACT_ADDR or not MINTER_PRIVATE_KEY:
            print("[Web3] Disabled: set CUBE_CONTRACT_ADDRESS + MINTER_PRIVATE_KEY env vars")
            return

        w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
        if not w3.is_connected():
            print(f"[Web3] Cannot connect to {POLYGON_RPC}")
            w3 = None
            return

        cube_contract = w3.eth.contract(
            address=Web3.to_checksum_address(CUBE_CONTRACT_ADDR),
            abi=CUBE_ABI
        )
        minter_account = Account.from_key(MINTER_PRIVATE_KEY)
        print(f"[Web3] Connected to Polygon | CUBE: {CUBE_CONTRACT_ADDR} | Minter: {minter_account.address}")

    except ImportError:
        print("[Web3] web3 not installed — blockchain features disabled")
    except Exception as e:
        print(f"[Web3] Init error: {e}")

# ─────────────────────────────────────────────────────────────
#  FastAPI app
# ─────────────────────────────────────────────────────────────
app = FastAPI(title="CubeWorld API", version="2.0.0")

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
    init_web3()
    print("CubeWorld server v2 started")

FRONTEND = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(FRONTEND):
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

@app.get("/")
def root():
    index = os.path.join(FRONTEND, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {
        "status": "CubeWorld API works",
        "version": "2.0.0",
        "web3": cube_contract is not None,
        "cube_contract": CUBE_CONTRACT_ADDR or "not configured",
        "docs": "/docs"
    }

# ─────────────────────────────────────────────────────────────
#  WebSocket Chat
# ─────────────────────────────────────────────────────────────
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
    await manager.broadcast(room, {
        "type": "system", "text": f"{short} joined",
        "time": _now(), "online": manager.online_count(room)
    })
    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            if data.get("type") == "message":
                text = data.get("text", "").strip()[:1000]
                if not text:
                    continue
                db.execute(
                    "INSERT INTO messages (room, user_key, text, created_at) VALUES (?,?,?,?)",
                    (room, user_key, text, int(time.time()))
                )
                db.commit()
                await manager.broadcast(room, {
                    "type": "message", "author": short, "text": text,
                    "time": _now(), "online": manager.online_count(room)
                })
            elif data.get("type") == "typing":
                await manager.broadcast(room, {"type": "typing", "author": short})
    except WebSocketDisconnect:
        manager.disconnect(ws, room)
        await manager.broadcast(room, {
            "type": "system", "text": f"{short} left",
            "time": _now(), "online": manager.online_count(room)
        })
    finally:
        db.close()

# ─────────────────────────────────────────────────────────────
#  Key / Auth
# ─────────────────────────────────────────────────────────────
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
    db.execute(
        "INSERT OR IGNORE INTO users (user_key, tier, balance, created_at) VALUES (?,?,?,?)",
        (key, tier, 250, int(time.time()))
    )
    db.execute(
        "INSERT OR IGNORE INTO wallets (user_key, linked_at) VALUES (?,?)",
        (key, 0)
    )
    db.commit()
    db.close()
    return {"key": key, "tier": tier, "balance": 250}

@app.get("/api/key/validate/{key}")
def validate_key(key: str):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE user_key=?", (key,)).fetchone()
    wallet = db.execute("SELECT * FROM wallets WHERE user_key=?", (key,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "Key not found")
    return {
        "key": row["user_key"],
        "tier": row["tier"],
        "balance": row["balance"],
        "xp": row["xp"],
        "streak": row["streak"],
        "eth_address": wallet["eth_address"] if wallet else None,
        "hd_address": wallet["hd_address"] if wallet else None,
    }

# ─────────────────────────────────────────────────────────────
#  Feed
# ─────────────────────────────────────────────────────────────
class PostRequest(BaseModel):
    user_key: str
    text: str
    post_type: str = "text"

@app.get("/api/feed")
def get_feed(limit: int = 30, offset: int = 0):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM posts ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset)
    ).fetchall()
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
    cur = db.execute(
        "INSERT INTO posts (user_key, author, text, post_type, created_at) VALUES (?,?,?,?,?)",
        (req.user_key, short, text, req.post_type, int(time.time()))
    )
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
    db.execute(
        f"UPDATE posts SET react_{req.reaction} = react_{req.reaction} + 1 WHERE id=?",
        (post_id,)
    )
    db.commit()
    db.close()
    return {"ok": True}

# ─────────────────────────────────────────────────────────────
#  Wallet — off-chain CUBE ledger
# ─────────────────────────────────────────────────────────────
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
    wallet = db.execute("SELECT * FROM wallets WHERE user_key=?", (user_key,)).fetchone()
    txs = db.execute(
        "SELECT * FROM wallet_txs WHERE user_key=? ORDER BY created_at DESC LIMIT 30",
        (user_key,)
    ).fetchall()
    db.close()

    result = {
        "balance": user["balance"],
        "xp": user["xp"],
        "txs": [dict(t) for t in txs],
        "eth_address": None,
        "hd_address": None,
        "cube_on_chain": "0",
    }
    if wallet:
        result["eth_address"]   = wallet["eth_address"]
        result["hd_address"]    = wallet["hd_address"]
        result["cube_on_chain"] = wallet["cube_balance"] or "0"
    return result

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
    db.execute(
        "INSERT INTO wallet_txs (user_key, dir, amount, desc, icon, created_at) VALUES (?,?,?,?,?,?)",
        (req.from_key, "out", req.amount, "Transfer out", "out", now)
    )
    db.execute(
        "INSERT INTO wallet_txs (user_key, dir, amount, desc, icon, created_at) VALUES (?,?,?,?,?,?)",
        (req.to_key, "in", req.amount, "Transfer in", "in", now)
    )
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

# ─────────────────────────────────────────────────────────────
#  ETH Wallet Linking (WalletConnect / MetaMask)
# ─────────────────────────────────────────────────────────────
class LinkWalletRequest(BaseModel):
    user_key: str
    eth_address: str
    hd_address: Optional[str] = None   # derived from CW key on frontend

@app.post("/api/wallet/link")
def link_wallet(req: LinkWalletRequest):
    """Link an external ETH address (MetaMask / WalletConnect) to a CubeWorld key."""
    _require_user(req.user_key)

    # Basic checksum validation
    eth = req.eth_address.strip()
    if not eth.startswith("0x") or len(eth) != 42:
        raise HTTPException(400, "Invalid ETH address")

    db = get_db()
    # Check if this ETH address is already linked to another key
    existing = db.execute(
        "SELECT user_key FROM wallets WHERE eth_address=? AND user_key != ?",
        (eth.lower(), req.user_key)
    ).fetchone()
    if existing:
        db.close()
        raise HTTPException(409, "ETH address already linked to another key")

    now = int(time.time())
    db.execute("""
        INSERT INTO wallets (user_key, eth_address, hd_address, linked_at)
        VALUES (?,?,?,?)
        ON CONFLICT(user_key) DO UPDATE SET
            eth_address = excluded.eth_address,
            hd_address  = COALESCE(excluded.hd_address, hd_address),
            linked_at   = excluded.linked_at
    """, (req.user_key, eth.lower(), req.hd_address, now))

    # Reward for linking wallet (one-time)
    wallet_row = db.execute("SELECT linked_at FROM wallets WHERE user_key=?", (req.user_key,)).fetchone()
    db.commit()
    db.close()

    # Fetch on-chain CUBE balance in background
    cube_balance = _get_cube_balance_onchain(eth)

    return {
        "ok": True,
        "eth_address": eth.lower(),
        "cube_on_chain": cube_balance,
        "linked_at": now,
    }

@app.delete("/api/wallet/link/{user_key}")
def unlink_wallet(user_key: str):
    """Unlink external ETH address from CubeWorld key."""
    _require_user(user_key)
    db = get_db()
    db.execute(
        "UPDATE wallets SET eth_address=NULL, linked_at=0 WHERE user_key=?",
        (user_key,)
    )
    db.commit()
    db.close()
    return {"ok": True}

# ─────────────────────────────────────────────────────────────
#  CUBE Token — on-chain (Polygon)
# ─────────────────────────────────────────────────────────────
class MintRequest(BaseModel):
    user_key: str
    reason: str = "reward"
    amount_cube: float = 10.0   # human-readable CUBE (not wei)
    secret: str = ""            # simple server-to-server auth

MINT_SECRET = os.environ.get("MINT_SECRET", "cubeworld_mint_2025")

@app.post("/api/cube/mint")
def mint_cube_onchain(req: MintRequest):
    """
    Mint real CUBE tokens on Polygon to the user's linked ETH address.
    Called internally after user earns rewards.
    Protected by MINT_SECRET.
    """
    if req.secret != MINT_SECRET:
        raise HTTPException(403, "Invalid mint secret")

    _require_user(req.user_key)
    db = get_db()
    wallet = db.execute("SELECT eth_address FROM wallets WHERE user_key=?", (req.user_key,)).fetchone()
    db.close()

    if not wallet or not wallet["eth_address"]:
        raise HTTPException(400, "No ETH address linked — user must connect wallet first")

    eth_address = wallet["eth_address"]
    amount_cube = max(0.1, min(req.amount_cube, 10000.0))  # cap 10k per call

    if not cube_contract or not w3 or not minter_account:
        return {
            "ok": False,
            "reason": "Web3 not configured on server",
            "queued": True,
            "amount": amount_cube
        }

    try:
        from web3 import Web3
        amount_wei = int(amount_cube * 10**18)
        nonce = w3.eth.get_transaction_count(minter_account.address)

        tx = cube_contract.functions.mint(
            Web3.to_checksum_address(eth_address),
            amount_wei,
            req.reason
        ).build_transaction({
            "from": minter_account.address,
            "nonce": nonce,
            "gas": 100000,
            "gasPrice": w3.eth.gas_price,
        })

        signed = w3.eth.account.sign_transaction(tx, MINTER_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        tx_hash_hex = tx_hash.hex()

        # Save to blockchain_txs
        db = get_db()
        db.execute("""
            INSERT INTO blockchain_txs
            (tx_hash, user_key, eth_address, tx_type, amount_wei, amount_cube, reason, status, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (tx_hash_hex, req.user_key, eth_address, "mint_reward",
              str(amount_wei), amount_cube, req.reason, "pending", int(time.time())))
        db.commit()
        db.close()

        return {
            "ok": True,
            "tx_hash": tx_hash_hex,
            "amount": amount_cube,
            "eth_address": eth_address,
            "explorer": f"https://polygonscan.com/tx/{tx_hash_hex}",
        }

    except Exception as e:
        raise HTTPException(500, f"Mint failed: {str(e)}")

@app.get("/api/cube/balance/{eth_address}")
def cube_balance_onchain(eth_address: str):
    """Get on-chain CUBE token balance for an ETH address."""
    balance = _get_cube_balance_onchain(eth_address)
    # Also update cached value in DB if address is linked
    if balance != "0":
        db = get_db()
        db.execute(
            "UPDATE wallets SET cube_balance=?, synced_at=? WHERE eth_address=?",
            (balance, int(time.time()), eth_address.lower())
        )
        db.commit()
        db.close()
    return {
        "eth_address": eth_address,
        "cube_balance_wei": balance,
        "cube_balance": str(int(balance) / 10**18) if balance != "0" else "0",
        "contract": CUBE_CONTRACT_ADDR,
        "network": "polygon" if "amoy" not in POLYGON_RPC else "amoy_testnet",
    }

@app.get("/api/cube/txs/{user_key}")
def cube_txs(user_key: str, limit: int = 20):
    """Get on-chain CUBE transaction history for a user."""
    _require_user(user_key)
    db = get_db()
    rows = db.execute(
        "SELECT * FROM blockchain_txs WHERE user_key=? ORDER BY created_at DESC LIMIT ?",
        (user_key, limit)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]

@app.post("/api/cube/confirm/{tx_hash}")
def confirm_tx(tx_hash: str):
    """Update transaction status after confirmation (called by webhook or frontend)."""
    if not w3:
        raise HTTPException(503, "Web3 not configured")
    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
        status = "confirmed" if receipt and receipt["status"] == 1 else "failed"
        block = receipt["blockNumber"] if receipt else 0
        gas = receipt["gasUsed"] if receipt else 0

        db = get_db()
        db.execute("""
            UPDATE blockchain_txs
            SET status=?, block_number=?, gas_used=?, confirmed_at=?
            WHERE tx_hash=?
        """, (status, block, gas, int(time.time()), tx_hash))

        if status == "confirmed":
            # Update cached balance
            row = db.execute("SELECT * FROM blockchain_txs WHERE tx_hash=?", (tx_hash,)).fetchone()
            if row:
                new_bal = _get_cube_balance_onchain(row["eth_address"])
                db.execute(
                    "UPDATE wallets SET cube_balance=?, synced_at=? WHERE eth_address=?",
                    (new_bal, int(time.time()), row["eth_address"])
                )
        db.commit()
        db.close()
        return {"ok": True, "status": status, "block": block}
    except Exception as e:
        raise HTTPException(500, str(e))

# ─────────────────────────────────────────────────────────────
#  Signals
# ─────────────────────────────────────────────────────────────
@app.get("/api/signals")
def get_signals(limit: int = 20):
    db = get_db()
    rows = db.execute("SELECT * FROM signals ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    db.close()
    return [dict(r) for r in rows]

class SignalRequest(BaseModel):
    user_key: str
    pair: str
    direction: str
    entry: str
    tp: str
    sl: str

@app.post("/api/signals")
def create_signal(req: SignalRequest):
    _require_user(req.user_key)
    if req.direction not in ("long", "short"):
        raise HTTPException(400, "direction must be long or short")
    short = req.user_key[:4] + "CUBE"
    db = get_db()
    cur = db.execute("""
        INSERT INTO signals (user_key,author,pair,direction,entry,tp,sl,created_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (req.user_key, short, req.pair[:20], req.direction,
          req.entry[:20], req.tp[:20], req.sl[:20], int(time.time())))
    _earn(db, req.user_key, 25, f"Signal: {req.pair} {req.direction.upper()}", "chart")
    db.commit()
    db.close()
    return {"id": cur.lastrowid, "ok": True, "earned": 25}

# ─────────────────────────────────────────────────────────────
#  Leaderboard / Chat history / Online
# ─────────────────────────────────────────────────────────────
@app.get("/api/leaderboard")
def leaderboard():
    db = get_db()
    rows = db.execute(
        "SELECT user_key, balance, xp FROM users ORDER BY balance DESC LIMIT 10"
    ).fetchall()
    db.close()
    return [
        {"rank": i+1, "author": r["user_key"][:4]+"CUBE", "balance": r["balance"], "xp": r["xp"]}
        for i, r in enumerate(rows)
    ]

@app.get("/api/chat/{room}")
def chat_history(room: str, limit: int = 50):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM messages WHERE room=? ORDER BY created_at DESC LIMIT ?",
        (room, limit)
    ).fetchall()
    db.close()
    return [
        {"author": r["user_key"][:4]+"CUBE", "text": r["text"], "time": _ts_to_rel(r["created_at"])}
        for r in reversed(rows)
    ]

@app.get("/api/online")
def online_count():
    return {
        "total": sum(len(v) for v in manager.rooms.values()),
        "rooms": {k: len(v) for k, v in manager.rooms.items()}
    }

@app.get("/api/stats")
def stats():
    db = get_db()
    users  = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    posts  = db.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    msgs   = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    minted = db.execute("SELECT COUNT(*) FROM blockchain_txs WHERE status='confirmed'").fetchone()[0]
    db.close()
    return {
        "users": users, "posts": posts, "messages": msgs,
        "minted_txs": minted,
        "web3_enabled": cube_contract is not None,
        "cube_contract": CUBE_CONTRACT_ADDR or None,
    }

# ─────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────
def _require_user(key: str):
    db = get_db()
    row = db.execute("SELECT 1 FROM users WHERE user_key=?", (key,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(401, "Key not registered")

def _earn(db, user_key, amount, desc, icon):
    db.execute(
        "UPDATE users SET balance=balance+?, xp=xp+? WHERE user_key=?",
        (amount, amount * 10, user_key)
    )
    db.execute(
        "INSERT INTO wallet_txs (user_key,dir,amount,desc,icon,created_at) VALUES (?,?,?,?,?,?)",
        (user_key, "in", amount, desc, icon, int(time.time()))
    )

def _get_cube_balance_onchain(eth_address: str) -> str:
    """Query on-chain CUBE balance; returns wei as string. Returns '0' if web3 not available."""
    if not cube_contract or not w3:
        return "0"
    try:
        from web3 import Web3
        balance = cube_contract.functions.balanceOf(
            Web3.to_checksum_address(eth_address)
        ).call()
        return str(balance)
    except Exception as e:
        print(f"[Web3] balanceOf error: {e}")
        return "0"

def _now():
    return time.strftime("%H:%M")

def _ts_to_rel(ts):
    d = int(time.time()) - ts
    if d < 60:   return "just now"
    if d < 3600: return f"{d//60}m"
    if d < 86400: return f"{d//3600}h"
    return f"{d//86400}d"

def _post_to_dict(r):
    return {
        "id": r["id"], "author": r["author"], "text": r["text"],
        "type": r["post_type"], "time": _ts_to_rel(r["created_at"]),
        "reactions": {
            "fire": r["react_fire"], "rocket": r["react_rocket"],
            "like": r["react_like"], "heart": r["react_heart"],
            "eyes": r["react_eyes"], "thinking": r["react_thinking"]
        }
    }
