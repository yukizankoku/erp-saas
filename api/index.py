"""Entry point fungsi Python Vercel: mengekspos aplikasi FastAPI di backend/ (PRD §6.5, Fase awal).

Semua request /api/* di-rewrite ke fungsi ini (lihat vercel.json); path asli diteruskan ke FastAPI.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.main import app

__all__ = ["app"]
