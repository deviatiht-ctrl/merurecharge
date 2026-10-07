"""Meru Auto Recharge — FastAPI app.

Lokal:  uvicorn app.main:app --reload --port 8000
Vercel: detekte otomatikman kòm entrypoint (framework preset FastAPI).
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from . import config
from .db import SessionLocal, init_db
from .routers import auth, float as float_router, orders, settings, system, webhook
from .worker import resume_sending, start_background_worker


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        return resp


class VercelPathFixMiddleware:
    """Sou Vercel, kèk rewrite ka rive ak path='/api/index' —
    restore path orijinal la via headers yo si li disponib."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            if path.startswith("/api/index"):
                headers = dict(scope.get("headers") or [])
                for name in (b"x-vercel-original-path", b"x-matched-path",
                             b"x-vercel-rewrite"):
                    orig = headers.get(name)
                    if orig:
                        scope["path"] = orig.decode().split("?")[0]
                        break
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pa janm fè cold start la krache — si baz done a pa disponib,
    # /api/health rapòte erè a olye FUNCTION_INVOCATION_FAILED.
    from .db import engine as _engine
    try:
        init_db()
        if _engine is not None:
            db = SessionLocal()
            try:
                resume_sending(db)
            except Exception:
                pass
            finally:
                db.close()
    except Exception:
        pass
    if not config.IS_VERCEL:
        start_background_worker()
    yield


app = FastAPI(title="Meru Auto Recharge", lifespan=lifespan)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(VercelPathFixMiddleware)

app.include_router(auth.router)
app.include_router(orders.router)
app.include_router(float_router.router)
app.include_router(settings.router)
app.include_router(webhook.router)
app.include_router(system.router)


@app.exception_handler(Exception)
async def unhandled(request, exc):
    """Retounen erè a an JSON olye HTML 500 — frontend la ka montre l."""
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"},
                        status_code=500)

# Frontend — 'public/' la: Vercel sèvi l natif, lokalman StaticFiles.
public_dir = config.BASE_DIR / "public"
if public_dir.exists():
    app.mount("/", StaticFiles(directory=str(public_dir), html=True),
              name="public")
