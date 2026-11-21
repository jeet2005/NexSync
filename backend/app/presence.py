from __future__ import annotations

from firebase_admin import db

from .auth import firebase_app
from .config import settings


def grant_document_access(document_id: str, user_id: str) -> None:
    if not settings.firebase_database_url:
        return
    db.reference(f"documentAccess/{document_id}/{user_id}", app=firebase_app()).set(True)
