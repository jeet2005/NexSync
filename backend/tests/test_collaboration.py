from fastapi.testclient import TestClient
from app.main import app
import app.main as main_module
import app.auth as auth_module
from app.db import engine
from app.models import Base
from app.models import Document, DocumentSnapshot, Update
from app.db import SessionLocal
from app.main import compact_document
from app.config import settings
from y_py import YDoc, apply_update, encode_state_as_update
import hashlib, uuid
from sqlalchemy import select

def test_ws_persists_and_relays_opaque_update(monkeypatch):
    Base.metadata.create_all(engine)
    monkeypatch.setattr(auth_module, "firebase_app", lambda: None)
    monkeypatch.setattr(main_module.firebase_auth, "verify_id_token", lambda token, app=None: {"uid": "test-user"})
    with TestClient(app) as client:
        app.dependency_overrides[main_module.current_user] = lambda: "test-user"
        doc=client.post('/api/documents',json={'title':'Realtime'}).json()
        # Minimal bytes are relayed opaquely; Yjs creates valid updates in the browser runtime.
        with client.websocket_connect(f"/ws/{doc['id']}?client_id=alice") as alice:
            with client.websocket_connect(f"/ws/{doc['id']}?client_id=bob") as bob:
                alice.send_json({'type':'auth','token':'fake'})
                assert alice.receive_json()['type']=='ready'
                bob.send_json({'type':'auth','token':'fake'})
                assert bob.receive_json()['type']=='ready'
                alice.send_json({'type':'update','payload':'010203'})
                packet=bob.receive_json()
                while packet.get('type')!='update': packet=bob.receive_json()
                assert packet['payload']=='010203'
        assert client.get(f"/api/documents/{doc['id']}").json()['updates']==['010203']
    app.dependency_overrides.clear()

def test_compacted_snapshot_recovers_document_and_prunes_covered_updates(monkeypatch):
    monkeypatch.setattr(settings, 'snapshot_interval', 1)
    source=YDoc(); source.get_text('content').insert(source.begin_transaction(),0,'persisted Yjs state')
    payload=bytes(encode_state_as_update(source)); doc_id=str(uuid.uuid4())
    with SessionLocal() as db:
        db.add(Document(id=doc_id,title='Snapshot',owner_id='snapshot-user'))
        db.flush()
        db.add(Update(document_id=doc_id,client_id='snapshot-user',update_id=hashlib.sha256(payload).hexdigest(),payload=payload))
        db.commit()
    assert compact_document(doc_id)
    with SessionLocal() as db:
        snapshot=db.scalar(select(DocumentSnapshot).where(DocumentSnapshot.document_id==doc_id))
        remaining=db.scalars(select(Update).where(Update.document_id==doc_id)).all()
    restored=YDoc(); apply_update(restored,snapshot.payload)
    assert str(restored.get_text('content'))=='persisted Yjs state'
    assert remaining==[]
