"""FastAPI server — connects the dashboard to the trading bots."""

import hashlib
import hmac
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from api.bot_manager import BotManager
from data.storage import close_db

# Auth config
AUTH_USERNAME = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "admin")
AUTH_SECRET = os.getenv("AUTH_SECRET", "default-secret-change-me")
TOKEN_EXPIRY = 86400  # 24h

# Singleton bot manager
manager = BotManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    yield
    # Graceful shutdown — stop all bots, join threads, close DB
    manager.kill_all()
    close_db()


app = FastAPI(title="Polymarket RBI Bot API", version="1.0.0", lifespan=lifespan)

# CORS — restricted to localhost by default, configurable via CORS_ORIGINS env var
ALLOWED_ORIGINS = os.getenv(
    "CORS_ORIGINS", "http://localhost:1818,http://127.0.0.1:1818"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type"],
)

# Serve dashboard static files
dashboard_dir = Path(__file__).parent.parent / "dashboard"
if dashboard_dir.exists():
    app.mount("/static", StaticFiles(directory=str(dashboard_dir)), name="static")


# ========== Auth ==========

def _make_token(username: str) -> str:
    """Create a signed token: base64(payload).signature"""
    payload = json.dumps({"user": username, "exp": int(time.time()) + TOKEN_EXPIRY})
    sig = hmac.new(AUTH_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}|{sig}"


def _verify_token(token: str) -> bool:
    """Verify token signature and expiry."""
    try:
        payload, sig = token.rsplit("|", 1)
        expected = hmac.new(AUTH_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return False
        data = json.loads(payload)
        return data.get("exp", 0) > time.time()
    except Exception:
        return False


# Public paths that don't require auth
PUBLIC_PATHS = {"/api/auth/login", "/api/auth/check", "/login.html", "/docs"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # Allow public paths and static files
    if path in PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)

    # Check token from Authorization header or cookie
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""
    if not token:
        token = request.cookies.get("auth_token", "")

    if token and _verify_token(token):
        return await call_next(request)

    # Not authenticated: redirect pages to login, return 401 for API
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Non authentifie"}, status_code=401)
    # Serve login page for all other requests
    login_page = dashboard_dir / "login.html"
    if login_page.exists():
        return FileResponse(str(login_page))
    return JSONResponse({"detail": "Non authentifie"}, status_code=401)


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
async def auth_login(body: LoginRequest):
    if body.username == AUTH_USERNAME and body.password == AUTH_PASSWORD:
        token = _make_token(body.username)
        response = JSONResponse({"token": token})
        response.set_cookie("auth_token", token, max_age=TOKEN_EXPIRY, httponly=True, samesite="lax")
        return response
    return JSONResponse({"detail": "Identifiants incorrects"}, status_code=401)


@app.get("/api/auth/check")
async def auth_check(request: Request):
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""
    if _verify_token(token):
        return {"status": "ok"}
    return JSONResponse({"detail": "Token invalide"}, status_code=401)


# ========== Routes ==========

@app.get("/")
async def root(request: Request):
    """Serve the dashboard."""
    index = dashboard_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "Dashboard not found — place index.html in dashboard/"}


@app.get("/docs.html")
async def docs_page():
    """Serve the documentation page."""
    docs = dashboard_dir / "docs.html"
    if docs.exists():
        return FileResponse(str(docs))
    return {"message": "Documentation not found"}


# --- Bots ---

@app.get("/api/bots")
async def get_bots():
    return manager.get_all_bots()


@app.post("/api/bots/{key}/start")
async def start_bot(key: str, token_id: str = ""):
    return manager.start_bot(key, token_id=token_id)


@app.post("/api/bots/{key}/stop")
async def stop_bot(key: str):
    return manager.stop_bot(key)


@app.post("/api/bots/kill-all")
async def kill_all():
    return manager.kill_all()


# --- Metrics ---

@app.get("/api/metrics")
async def get_metrics():
    return manager.get_metrics()


# --- Trades ---

@app.get("/api/trades")
async def get_trades(limit: int = 50):
    return manager.get_trades(limit=limit)


# --- Risk ---

@app.get("/api/risk")
async def get_risk():
    return manager.get_risk()


# --- Logs ---

@app.get("/api/logs")
async def get_logs(limit: int = 100, level: str = ""):
    return manager.get_logs(limit=limit, level=level)


# --- Alerts ---

@app.get("/api/alerts/status")
async def alerts_status():
    return {"enabled": manager.alerter.enabled}


@app.post("/api/alerts/test")
async def alerts_test():
    ok = manager.alerter.send_test()
    return {"status": "sent" if ok else "failed", "enabled": manager.alerter.enabled}


# --- Settings ---

class SettingsUpdate(BaseModel):
    position_size: float | None = Field(None, gt=0, le=1000, description="Position size in USD")
    stop_loss_pct: float | None = Field(None, gt=0, le=100, description="Stop loss percentage (1-100)")
    take_profit_pct: float | None = Field(None, gt=0, le=100, description="Take profit percentage (1-100)")
    dry_run: bool | None = None
    account: str | None = Field(None, min_length=1, max_length=50)


@app.get("/api/settings")
async def get_settings():
    return manager.get_settings()


@app.put("/api/settings")
async def update_settings(body: SettingsUpdate):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    return manager.update_settings(updates)


# ========== Entry point ==========

if __name__ == "__main__":
    import uvicorn
    print("\n  Polymarket RBI Bot — API Server")
    print("  Dashboard: http://localhost:1818")
    print("  API docs:  http://localhost:1818/docs\n")
    uvicorn.run(app, host="0.0.0.0", port=1818)
