import asyncio, hashlib, json, logging, time, uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import delete, or_, select, update as sql_update
from sqlalchemy.exc import IntegrityError
import httpx
from firebase_admin import auth as firebase_auth
from .config import settings
from .auth import current_user, firebase_app, verify_id_token
from .db import engine, SessionLocal
from .models import Document, DocumentMember, DocumentSnapshot, Update, Revision
from y_py import YDoc, apply_update, encode_state_as_update
from . import presence

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("workspace")
# Ephemeral connection/presence state only. Production multi-instance fanout uses Redis.
rooms: dict[str, set[WebSocket]] = defaultdict(set)
connection_ids: dict[WebSocket, str] = {}
connection_users: dict[WebSocket, str] = {}
redis_client: Redis | None = None
redis_listener_task: asyncio.Task | None = None
ai_requests_by_user: dict[str, deque[float]] = defaultdict(deque)

async def broadcast_local(document_id: str, origin_id: str | None, message: dict[str, Any]):
    stale=[]
    for peer in tuple(rooms[document_id]):
        if origin_id and connection_ids.get(peer) == origin_id: continue
        try: await peer.send_json(message)
        except Exception: stale.append(peer)
    for peer in stale:
        rooms[document_id].discard(peer)
        connection_ids.pop(peer, None)
        connection_users.pop(peer, None)

async def emit(document_id: str, sender: WebSocket | None, message: dict[str, Any]):
    origin_id = connection_ids.get(sender) if sender else None
    if redis_client:
        try:
            await redis_client.publish(f"nexsync:documents:{document_id}", json.dumps({"origin": origin_id, "message": message}))
            return
        except Exception:
            log.exception("Redis publish failed; relaying within this API process only")
    await broadcast_local(document_id, origin_id, message)

async def consume_redis():
    if not redis_client: return
    while True:
        pubsub = redis_client.pubsub()
        try:
            await pubsub.psubscribe("nexsync:documents:*")
            async for event in pubsub.listen():
                if event.get("type") not in ("pmessage", "message"): continue
                try:
                    channel = event.get("channel", b"")
                    channel = channel.decode() if isinstance(channel, bytes) else channel
                    doc_id = channel.rsplit(":", 1)[-1]
                    envelope = json.loads(event["data"])
                    await broadcast_local(doc_id, envelope.get("origin"), envelope["message"])
                except Exception:
                    log.exception("Invalid Redis collaboration event")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Redis subscription interrupted; retrying")
            await asyncio.sleep(2)
        finally:
            await pubsub.aclose()

def compact_document(document_id: str) -> bool:
    """Merge durable Yjs updates into a recoverable state update and prune covered rows."""
    with SessionLocal() as db:
        snapshot = db.scalar(select(DocumentSnapshot).where(DocumentSnapshot.document_id == document_id).order_by(DocumentSnapshot.watermark_id.desc()).limit(1))
        watermark = snapshot.watermark_id if snapshot else 0
        updates = db.scalars(select(Update).where(Update.document_id == document_id, Update.id > watermark).order_by(Update.id)).all()
        if len(updates) < max(settings.snapshot_interval, 1): return False
        ydoc = YDoc()
        try:
            if snapshot: apply_update(ydoc, snapshot.payload)
            for row in updates: apply_update(ydoc, row.payload)
            new_watermark = updates[-1].id
            payload = bytes(encode_state_as_update(ydoc))
            db.add(DocumentSnapshot(document_id=document_id, watermark_id=new_watermark, payload=payload))
            db.flush()
            db.execute(delete(Update).where(Update.document_id == document_id, Update.id <= new_watermark))
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            return False
        except Exception:
            db.rollback()
            log.exception("Could not compact Yjs update log for document %s", document_id)
            return False

@asynccontextmanager
async def lifespan(app):
    global redis_client, redis_listener_task
    if settings.redis_url:
        try:
            redis_client = Redis.from_url(settings.redis_url, decode_responses=False)
            await redis_client.ping()
            redis_listener_task = asyncio.create_task(consume_redis())
            log.info("Redis collaboration fanout enabled")
        except Exception:
            log.exception("Redis unavailable; using local single-process WebSocket fanout")
            redis_client = None
    yield
    if redis_listener_task:
        redis_listener_task.cancel()
        try: await redis_listener_task
        except asyncio.CancelledError: pass
    if redis_client:
        await redis_client.aclose()
    redis_client = None
    redis_listener_task = None

app = FastAPI(title="Shared Workspace API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.cors_origins.split(",")], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class DocumentIn(BaseModel):
    title: str = Field(default="Untitled document", max_length=240)

class DocumentPatch(BaseModel):
    title: str = Field(min_length=1, max_length=240)

class ShareIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)

class AIRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=2000)
    section_id: str = Field(min_length=1, max_length=120)
    content: str = Field(max_length=30000)
    base_version: int = Field(ge=0)

@app.get("/health")
def health(): return {"status": "ok"}

@app.get("/api/documents")
def list_documents(user_id: str = Depends(current_user)):
    with SessionLocal() as db:
        rows = db.scalars(select(Document).outerjoin(DocumentMember, DocumentMember.document_id == Document.id).where(or_(Document.owner_id == user_id, DocumentMember.user_id == user_id)).distinct().order_by(Document.created_at.desc())).all()
        return [{"id": d.id, "title": d.title, "owner_id": d.owner_id, "version": d.version, "created_at": d.created_at.isoformat()} for d in rows]

@app.post("/api/documents", status_code=201)
def create_document(payload: DocumentIn, user_id: str = Depends(current_user)):
    with SessionLocal() as db:
        doc = Document(id=str(uuid.uuid4()), title=payload.title, owner_id=user_id)
        db.add(doc); db.commit()
        try: presence.grant_document_access(doc.id, user_id)
        except Exception: log.exception("Could not register Firebase presence access; local presence fallback remains active")
        return {"id": doc.id, "title": doc.title, "owner_id": doc.owner_id, "version": 0}

def owned_document(db, document_id: str, user_id: str):
    doc = db.get(Document, document_id)
    if not doc: raise HTTPException(404, "Document not found")
    if doc.owner_id != user_id: raise HTTPException(403, "You do not have access to this document")
    return doc

def accessible_document(db, document_id: str, user_id: str):
    doc = db.get(Document, document_id)
    if not doc: raise HTTPException(404, "Document not found")
    if doc.owner_id != user_id and not db.get(DocumentMember, (document_id, user_id)):
        raise HTTPException(403, "You do not have access to this document")
    return doc

@app.post("/api/documents/{document_id}/share", status_code=201)
def share_document(document_id: str, payload: ShareIn, user_id: str = Depends(current_user)):
    with SessionLocal() as db:
        owned_document(db, document_id, user_id)
    try:
        invited = firebase_auth.get_user_by_email(payload.email.strip(), app=firebase_app())
    except Exception as exc:
        raise HTTPException(404, "No Firebase user exists with that email") from exc
    if invited.uid == user_id: raise HTTPException(400, "You already own this document")
    with SessionLocal() as db:
        owned_document(db, document_id, user_id)
        db.merge(DocumentMember(document_id=document_id, user_id=invited.uid, role="editor"))
        db.commit()
        try: presence.grant_document_access(document_id, invited.uid)
        except Exception: log.exception("Could not add Firebase presence access for invited user")
    return {"status": "shared", "email": invited.email, "role": "editor"}

@app.patch("/api/documents/{document_id}")
def update_document(document_id: str, payload: DocumentPatch, user_id: str = Depends(current_user)):
    with SessionLocal() as db:
        doc = owned_document(db, document_id, user_id)
        doc.title = payload.title.strip()
        db.commit()
        return {"id": doc.id, "title": doc.title, "owner_id": doc.owner_id, "version": doc.version}

@app.get("/api/documents/{document_id}")
def get_document(document_id: str, user_id: str = Depends(current_user)):
    with SessionLocal() as db:
        doc = accessible_document(db, document_id, user_id)
        snapshot = db.scalar(select(DocumentSnapshot).where(DocumentSnapshot.document_id == document_id).order_by(DocumentSnapshot.watermark_id.desc()).limit(1))
        updates = db.scalars(select(Update).where(Update.document_id == document_id, Update.id > (snapshot.watermark_id if snapshot else 0)).order_by(Update.id)).all()
        return {"id": doc.id, "title": doc.title, "owner_id": doc.owner_id, "version": doc.version, "snapshot": snapshot.payload.hex() if snapshot else None, "updates": [u.payload.hex() for u in updates]}

@app.get("/api/documents/{document_id}/revisions")
def revisions(document_id: str, user_id: str = Depends(current_user)):
    with SessionLocal() as db:
        accessible_document(db, document_id, user_id)
        return [{"id": r.id, "author_id": r.author_id, "kind": r.kind, "summary": r.summary, "snapshot": r.snapshot, "created_at": r.created_at.isoformat()} for r in db.scalars(select(Revision).where(Revision.document_id == document_id).order_by(Revision.id.desc()).limit(50))]

@app.post("/api/documents/{document_id}/ai-suggestions")
async def ai_suggestion(document_id: str, payload: AIRequest, user_id: str = Depends(current_user)):
    """Return a reviewable proposal; the browser applies it only if source text is unchanged."""
    with SessionLocal() as db:
        accessible_document(db, document_id, user_id)
    api_key = settings.ai_api_key or settings.groq_api_key
    if not api_key:
        raise HTTPException(503, "AI is not configured; set GROQ_API_KEY on the backend")
    current = time.time()
    if redis_client:
        bucket = int(current // 60)
        key = f"nexsync:ai-rate:{user_id}:{bucket}"
        count = await redis_client.incr(key)
        if count == 1: await redis_client.expire(key, 65)
    else:
        recent = ai_requests_by_user[user_id]
        while recent and recent[0] < current - 60: recent.popleft()
        recent.append(current)
        count = len(recent)
    if count > 10:
        raise HTTPException(429, "AI request limit reached; try again in a minute")
    prompt = f"Instruction: {payload.instruction}\nRewrite only this section and return only replacement text:\n{payload.content}"
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                f"{settings.ai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": settings.ai_model or settings.groq_model,
                      "messages": [{"role": "system", "content": "You are a careful writing assistant. Return only the requested replacement text."},
                                   {"role": "user", "content": prompt}],
                      "temperature": 0.4, "max_completion_tokens": 4096})
            response.raise_for_status()
            suggestion = response.json()["choices"][0]["message"]["content"].strip()
            if not suggestion: raise ValueError("Empty AI response")
        return {"status": "proposal", "suggestion": suggestion, "section_id": payload.section_id,
                "base_version": payload.base_version, "base_content": payload.content}
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        log.warning("AI suggestion request failed: %s", type(exc).__name__)
        raise HTTPException(503, "AI suggestion service is unavailable") from exc

@app.websocket("/ws/{document_id}")
async def collaborate(websocket: WebSocket, document_id: str, client_id: str = "anonymous"):
    await websocket.accept()
    try:
        auth_packet = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        if auth_packet.get("type") != "auth": raise ValueError("Auth frame required")
        claims = verify_id_token(auth_packet.get("token", ""))
        user_id = claims.get("uid") or claims.get("user_id") or claims.get("sub")
        if not user_id: raise ValueError("Firebase token is missing UID")
    except Exception:
        await websocket.close(code=4401); return
    client_id = user_id
    connection_id = str(uuid.uuid4())
    connection_ids[websocket] = connection_id
    connection_users[websocket] = user_id
    rooms[document_id].add(websocket)
    with SessionLocal() as db:
        try: doc = accessible_document(db, document_id, user_id)
        except HTTPException as exc:
            rooms[document_id].discard(websocket)
            connection_ids.pop(websocket, None); connection_users.pop(websocket, None)
            await websocket.close(code=4404 if exc.status_code == 404 else 4403); return
        await websocket.send_json({"type":"ready"})
        snapshot = db.scalar(select(DocumentSnapshot).where(DocumentSnapshot.document_id == document_id).order_by(DocumentSnapshot.watermark_id.desc()).limit(1))
        if snapshot:
            await websocket.send_json({"type": "snapshot", "payload": snapshot.payload.hex(), "version": doc.version})
        # Catch-up replays the latest compacted state followed by uncovered operations.
        for row in db.scalars(select(Update).where(Update.document_id == document_id, Update.id > (snapshot.watermark_id if snapshot else 0)).order_by(Update.id)):
            await websocket.send_json({"type": "update", "payload": row.payload.hex(), "client_id": row.client_id, "version": doc.version})
    await emit(document_id, websocket, {"type": "presence", "client_id": client_id, "online": True})
    try:
        while True:
            packet = await websocket.receive_json()
            kind = packet.get("type")
            if kind == "update":
                try:
                    payload = bytes.fromhex(packet["payload"])
                    if len(payload) > 2_000_000: raise ValueError("update too large")
                    digest = hashlib.sha256(payload).hexdigest()
                    duplicate = False
                    with SessionLocal() as db:
                        doc = db.get(Document, document_id)
                        if not doc: await websocket.close(code=4404); return
                        row = Update(document_id=document_id, client_id=client_id, update_id=digest, payload=payload)
                        db.add(row)
                        db.execute(sql_update(Document).where(Document.id == document_id).values(version=Document.version + 1))
                        try: db.commit()
                        except IntegrityError:
                            db.rollback()
                            duplicate = db.scalar(select(Update.id).where(Update.document_id == document_id, Update.update_id == digest)) is not None
                            if not duplicate: raise
                    if duplicate: continue
                    with SessionLocal() as db:
                        current_version = db.scalar(select(Document.version).where(Document.id == document_id)) or 0
                    if current_version and current_version % max(settings.snapshot_interval, 1) == 0:
                        asyncio.create_task(asyncio.to_thread(compact_document, document_id))
                    await websocket.send_json({"type": "saved", "version": current_version})
                    await emit(document_id, websocket, {"type": "update", "payload": payload.hex(), "client_id": client_id, "version": current_version})
                except (ValueError, KeyError):
                    await websocket.send_json({"type": "error", "message": "Malformed or oversized update"})
            elif kind in ("awareness", "presence"):
                packet["client_id"] = client_id
                await emit(document_id, websocket, packet)
            elif kind == "revision":
                with SessionLocal() as db:
                    db.add(Revision(document_id=document_id, author_id=client_id, kind=packet.get("kind", "edit"), summary=str(packet.get("summary", "Update"))[:500], snapshot=json.dumps(packet.get("snapshot", {}))))
                    db.commit()
                await emit(document_id, websocket, {**packet, "client_id": client_id})
    except WebSocketDisconnect:
        pass
    finally:
        rooms[document_id].discard(websocket)
        connection_ids.pop(websocket, None); connection_users.pop(websocket, None)
        local_connection_remains = any(connection_users.get(peer) == user_id for peer in rooms[document_id])
        if not local_connection_remains:
            await emit(document_id, None, {"type": "presence", "client_id": client_id, "online": False})
