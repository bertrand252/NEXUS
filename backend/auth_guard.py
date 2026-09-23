"""
Gerbang auth buat semua endpoint backend — dipasang sekali di main.py lewat
dependencies=[Depends(require_auth)] per router, bukan diulang di tiap route.

Terima salah satu dari:
  1. Authorization: Bearer <jwt> — token dari Supabase Auth (user login lewat
     frontend), divalidasi via supabase.auth.get_user(). WAJIB udah lolos MFA
     (claim `aal` = "aal2") — token yang cuma modal password (aal1) ditolak,
     jadi password bocor/ke-brute-force doang gak cukup buat akses API.
  2. X-Service-Key: <key> — buat caller mesin-ke-mesin (WhatsApp listener,
     cron, dst) yang gak login lewat browser. Cocokin ke SERVICE_API_KEY.
"""
import base64
import hmac
import json
from fastapi import Header, HTTPException
from config import supabase, SERVICE_API_KEY


def jwt_aal(token: str) -> str | None:
    # Decode payload doang TANPA verifikasi signature — aman karena cuma
    # dipanggil SETELAH get_user() (server Supabase) udah validasi token-nya.
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("aal")
    except Exception:
        return None


def require_auth(authorization: str | None = Header(None), x_service_key: str | None = Header(None)) -> None:
    # hmac.compare_digest — perbandingan constant-time, `==` biasa buat secret
    # rawan timing attack (durasi compare bocorin berapa karakter awal yang
    # udah cocok). Guard x_service_key None dulu, compare_digest gak terima None.
    if SERVICE_API_KEY and x_service_key and hmac.compare_digest(x_service_key, SERVICE_API_KEY):
        return

    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        try:
            res = supabase.auth.get_user(token)
            if res and res.user:
                if jwt_aal(token) != "aal2":
                    raise HTTPException(status_code=401, detail="Butuh verifikasi MFA (kode authenticator)")
                return
        except HTTPException:
            raise
        except Exception:
            pass

    raise HTTPException(status_code=401, detail="Belum login / API key gak valid")
