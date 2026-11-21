import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.auth import current_user
from app.db import engine
from app.models import Base, Document, DocumentMember
from app.db import SessionLocal
import uuid
from app.config import settings

@pytest.fixture
def client():
    Base.metadata.create_all(engine)
    app.dependency_overrides[current_user] = lambda: "test-user"
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()

def test_health(client):
    assert client.get('/health').json() == {'status':'ok'}

def test_document_create_and_updates_listing(client):
    created = client.post('/api/documents', json={'title':'Test document'})
    assert created.status_code == 201
    doc_id = created.json()['id']
    assert client.get(f'/api/documents/{doc_id}').json()['updates'] == []
    assert any(row['id'] == doc_id for row in client.get('/api/documents').json())

def test_missing_document(client):
    assert client.get('/api/documents/no-such-document').status_code == 404

def test_api_requires_identity_without_override():
    app.dependency_overrides.clear()
    with TestClient(app) as unauthenticated:
        assert unauthenticated.get('/api/documents').status_code == 401

def test_document_access_is_bound_to_verified_identity(client):
    doc = client.post('/api/documents', json={'title':'Private'}).json()
    app.dependency_overrides[current_user] = lambda: "different-user"
    assert client.get(f"/api/documents/{doc['id']}").status_code == 403

def test_ai_reports_missing_provider_key(client, monkeypatch):
    doc = client.post('/api/documents', json={'title':'AI'}).json()
    monkeypatch.setattr(settings, 'ai_api_key', '')
    monkeypatch.setattr(settings, 'groq_api_key', '')
    response = client.post(f"/api/documents/{doc['id']}/ai-suggestions", json={"instruction":"Rewrite this", "section_id":"whole-document", "content":"Some text", "base_version":0})
    assert response.status_code == 503
    assert "GROQ_API_KEY" in response.json()["detail"]

def test_document_member_can_read_shared_document(client):
    doc_id = str(uuid.uuid4())
    with SessionLocal() as db:
        db.add(Document(id=doc_id, title='Shared', owner_id='owner-uid'))
        db.add(DocumentMember(document_id=doc_id, user_id='test-user', role='editor'))
        db.commit()
    response = client.get(f'/api/documents/{doc_id}')
    assert response.status_code == 200
