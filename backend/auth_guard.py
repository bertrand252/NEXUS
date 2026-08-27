"""
Gerbang auth buat semua endpoint backend — dipasang sekali di main.py lewat
dependencies=[Depends(require_auth)] per router, bukan diulang di tiap route.

Terima salah satu dari:
  1. Authorization: Bearer <jwt> — token dari Supabase Auth (user login lewat
     frontend), divalidasi via supabase.auth.get_user().
  2. X-Service-Key: <key> — buat caller mesin-ke-mesin (WhatsApp listener,
     cron, dst) yang gak login lewat browser. Cocokin ke SERVICE_API_KEY.
"""
from fastapi import Header, HTTPException
from config import supabase, SERVICE_API_KEY


def require_auth(authorization: str | None = Header(None), x_service_key: str | None = Header(None)) -> None:
    if SERVICE_API_KEY and x_service_key == SERVICE_API_KEY:
        return

    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        try:
            res = supabase.auth.get_user(token)
            if res and res.user:
                return
        except Exception:
            pass

    raise HTTPException(status_code=401, detail="Belum login / API key gak valid")
