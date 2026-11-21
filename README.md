# NexSync MVP

NexSync is a collaborative writing MVP using React, TipTap, Yjs, FastAPI, PostgreSQL, Firebase Authentication/Realtime Database presence, Redis Pub/Sub, and Groq-compatible chat completions. The editor syncs incremental Yjs updates. AI returns a reviewable suggestion and the UI applies it only while the captured target text is unchanged.

## Run it on a developer PC

### Local services (no Docker required)

1. Copy `.env.example` to `.env`. Set `DATABASE_URL=sqlite:///./nexsync-local.db` for local SQLite (the API reads the root `.env`). The Firebase web configuration is already in the example; it is public client configuration.
2. From `backend`, install `requirements.txt`, run `python -m alembic upgrade head`, then start `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`.
3. From `frontend`, run `npm install` and `npm run dev -- --host 127.0.0.1`.
4. Open [http://localhost:5173](http://localhost:5173), then sign up or sign in with Firebase Auth. Email/password and Google providers must be enabled in the Firebase console.
5. For real AI output, put a newly rotated key in `GROQ_API_KEY` in `.env` and restart the API. `AI_BASE_URL` and `AI_MODEL` can point to another OpenAI-compatible provider. Without a key, the AI endpoint returns an explicit 503 configuration error.

The API verifies Firebase ID tokens with Firebase Admin when service-account/ADC credentials are configured. For local sign-in and document APIs without an Admin credential, it verifies Firebase tokens against Google's public signing certificates and the configured project audience. Admin credentials are required for Firebase Admin operations such as sharing by email. Realtime Database presence is optional: set `FIREBASE_DATABASE_URL`, deploy `firebase.database.rules.json`, and set the matching `VITE_FIREBASE_DATABASE_URL`; otherwise the UI falls back to live socket presence.

### Docker Compose

Copy `.env.example` to `.env`, add a Firebase Admin service account file at `firebase-admin.json`, configure the Firebase Realtime Database URL and Groq key, then run `docker compose up --build`. Docker Compose provides PostgreSQL and Redis. It was not started in this environment because Docker Desktop's Linux engine was unavailable; the local API/UI path is the available way to preview it here.

## MVP architecture and correctness

- **Yjs is document truth.** TipTap edits produce incremental CRDT updates; the custom WebSocket provider also exchanges Yjs awareness for collaborator cursors.
- **PostgreSQL is durable.** Updates are inserted with a per-document SHA-256 idempotency key before acknowledgement or broadcast. Reconnect sends the latest snapshot followed by later updates. A compactor merges update batches into Yjs snapshots and removes only rows covered by the snapshot watermark.
- **Redis is fanout, not storage.** One Pub/Sub subscriber per FastAPI process distributes document events across instances. If Redis is unavailable, the app stays usable with process-local sockets; disconnected clients recover from PostgreSQL.
- **Firebase manages identity/presence.** Verified Firebase UIDs own documents; owners can grant editor access to an existing Firebase account by email. Realtime Database stores one connection record per tab and uses `onDisconnect` cleanup plus a heartbeat. When the database URL is missing, socket presence is the fallback.
- **Groq is independent from editing.** AI calls are asynchronous HTTP requests and do not lock the editor. A suggestion captures a document section (heading number) or the whole document. The client compares current target text with the captured source before applying the result as a Yjs transaction; changed target text leaves the suggestion unapplied.
- **Limits are explicit.** This MVP has no distributed AI worker queue, automatic named-version restore UI, load harness, or production authorization review. Redis/Firebase multi-instance behavior and the 20,000-document/100-editor targets have not been load-tested.

## Tests run during implementation

- Backend: `cd backend; python -m pytest -q`
- Frontend: `cd frontend; npm test` and `npm run build`
- Migration: `cd backend; python -m alembic upgrade head`
- Compose syntax: `docker compose config`

The automated tests cover API validation and identity-bound access, WebSocket update persistence/relay, Yjs concurrent insert/delete convergence, duplicate/reconnect update replay, stale AI-source rejection, and snapshot recovery/compaction. A two-tab localhost edit was manually observed syncing before Firebase authentication was added; repeat that manual check after signing in to both tabs. No live Groq request has been verified unless a key is configured in the local `.env`; no performance numbers are claimed.

## Scale-out plan — six points

1. Scale stateless FastAPI WebSocket instances behind a load balancer; Redis Pub/Sub cross-instance fanout is implemented, while multi-instance load behavior still needs testing.
2. Keep Redis explicitly ephemeral. Commit each Yjs update to PostgreSQL before fanout; on reconnect, use the snapshot plus post-watermark update log rather than Redis replay.
3. Partition/index PostgreSQL update and membership rows by document as traffic grows; the MVP already indexes document/update lookup paths and compacts updates into Yjs snapshots.
4. Limit hot document rooms, apply bounded send queues/backpressure, and test fanout at 100 editors; the current implementation has not been benchmarked at that level.
5. Use Firebase Realtime Database for presence only, one connection record per tab with on-disconnect cleanup; document content remains exclusively in Yjs/PostgreSQL.
6. For larger AI volume, move provider calls to a dedicated durable worker queue with retries and per-user limits; today the async API call is isolated from editor transactions but is not a separate worker service.

## Repository layout

- `backend/app`: FastAPI routes, auth, models, Redis relay, Firebase presence access, Yjs snapshot compaction.
- `backend/alembic`: PostgreSQL/SQLite schema migrations.
- `frontend/src`: Firebase sign-in, workspace, TipTap/Yjs provider, presence, and AI suggestion UI.
- `firebase.database.rules.json`: user-owned presence writes and document-member reads; Firebase Admin manages the access index.
