"""Logging terstruktur (JSON per baris) — Railway nangkep stdout apa adanya,
JSON per baris bikin gampang di-filter/cari kalau ada masalah produksi
(sebelumnya: banyak `except Exception: pass` di scheduler.py yang nelen
error diem-diem, gak ketauan kalau 1 background job gagal terus-terusan).

Dipake di TITIK BOUNDARY loop background (scheduler.py::run_*), BUKAN di
setiap try/except internal — banyak except internal itu emang sengaja diem
buat kegagalan yang udah dijelasin di komentar (misal "1 ticker gagal fetch,
skip aja"), itu bukan masalah operasional yang perlu di-log tiap kejadian."""
import sys
import json
import logging
from datetime import datetime, timezone


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:  # cegah handler dobel kalau get_logger dipanggil berkali-kali
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
