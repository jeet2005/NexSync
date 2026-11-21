from functools import lru_cache

import firebase_admin
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from .config import settings

bearer = HTTPBearer(auto_error=False)

@lru_cache(maxsize=1)
def firebase_app():
    try:
        return firebase_admin.get_app()
    except ValueError:
        # Uses GOOGLE_APPLICATION_CREDENTIALS / ADC. Verification never trusts client-supplied UIDs.
        try:
            credential = credentials.ApplicationDefault()
            options = {"projectId": settings.firebase_project_id}
            if settings.firebase_database_url:
                options["databaseURL"] = settings.firebase_database_url
            return firebase_admin.initialize_app(credential, options)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Firebase Admin credentials are not configured") from exc

def current_user(credentials_header: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    if credentials_header is None or credentials_header.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Sign in is required")
    try:
        token = verify_id_token(credentials_header.credentials)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired Firebase ID token") from exc
    uid = token.get("uid") or token.get("user_id") or token.get("sub")
    if not uid:
        raise HTTPException(status_code=401, detail="Firebase token has no user ID")
    return uid

def verify_id_token(token: str) -> dict:
    try:
        return firebase_auth.verify_id_token(token, app=firebase_app())
    except HTTPException as exc:
        if exc.status_code != 503: raise
        # Local MVP fallback: Google Auth validates Firebase signature, expiry, issuer and audience
        # using Google's public certificates. Admin credentials remain required for Admin APIs.
        return id_token.verify_firebase_token(token, Request(), audience=settings.firebase_project_id)
