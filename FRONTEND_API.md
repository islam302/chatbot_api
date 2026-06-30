# ChatBot API — Frontend Integration Guide

Everything a frontend developer needs to build against this API. Mirrors
`api-docs.html` in a frontend-friendly form. Examples use `fetch`; adapt to axios.

---

## 1. Base URL & conventions

```
Base URL:  https://una-ai-tools-apis.una-oic.org/chatbot-api/api/v1
```

- Requests/responses are **JSON** unless uploading files (then `multipart/form-data`).
- Most endpoints require **authentication** (§2). Public ones are marked.
- Trailing slashes are required (`/chat/`, not `/chat`).
- Timestamps are ISO-8601 UTC. All IDs are UUIDs.
- List endpoints are **paginated** (20/page): `?page=2` →
  `{ "count", "next", "previous", "results": [...] }`.
- List endpoints accept `?search=<q>`, `?ordering=<field>` (`-` = desc), and per-endpoint filters.

> **CORS:** your origin must be whitelisted server-side (`CORS_ALLOWED_ORIGINS`).
> `http://localhost:5173` and `:3000` are already allowed for dev.

---

## 2. Authentication

Two interchangeable schemes — pick one per request:
- **JWT**: `Authorization: Bearer <access>` (user-facing apps)
- **API key**: `x-api-key: <key>` or `Authorization: ApiKey <key>` (embedding a single tenant's bot)

> Multi-tenant: the token/key identifies the tenant; all data is auto-scoped to them.
> You never pass a user/tenant id in the body.

**Login** — `POST /auth/login/` (public)
```js
const res = await fetch(`${BASE}/auth/login/`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ username, password }),
});
const data = await res.json();
// data.access (3-day), data.refresh (30-day), data.user { id, username, email, role, api_key, ... }
```
> Login is rate-limited (10/min per IP) → `429` if exceeded.

**Refresh** — `POST /auth/refresh/` (public): `{ "refresh": "..." }` → `{ "access", "refresh" }`
**Verify** — `POST /auth/verify/` (public): `{ "token": "..." }` → `200` valid / `401` invalid

**Current user** — `GET /users/me/` → the `user` object (incl. `api_key`).
**Change password** — `POST /users/change-password/`
```json
{ "old_password": "...", "new_password": "...", "new_password_confirm": "..." }
```
**My API key** — `GET /users/api-key/` → `{ id, key, is_active, last_used_at, created_at }`.
Rotate: `POST /users/regenerate-api-key/`.

---

## 3. Chat (the main endpoint)

### `POST /chat/`
```json
{
  "question": "ما هي المنتجات المتوفرة لديكم؟",
  "history": [
    { "role": "user",      "content": "السلام عليكم" },
    { "role": "assistant", "content": "وعليكم السلام! كيف أقدر أساعدك؟" }
  ]
}
```
| Field | Required | Notes |
|-------|----------|-------|
| `question` | yes | Any language; the bot replies in the **same language and dialect**. |
| `history` | no | Prior turns. Keep the **last 10 turns** (20 messages) — older → `400`. |
| `language` | no | Optional hint (`"ar"`, `"en"`); omit and the server auto-detects. |

**Response — 200:**
```json
{
  "answer": "أهلاً! عندنا ... بسعر 12,000 دينار...",
  "source": "rag",
  "source_id": "",
  "confident": true,            // false = weak/fallback answer
  "response_time_ms": 2643,
  "prompt_tokens": 2503,
  "completion_tokens": 144,
  "cost_usd": 0.007698,
  "sources": [
    { "filename": "products", "document_id": "42b7…", "chunk_id": "d6e3…", "position": 11, "score": 0.81 }
  ]
}
```
- `answer` may contain **Markdown** (render links/lists).
- `confident: false` → optionally show a subtle "not sure" hint.
- Errors: `401` auth · `403` suspended/expired · `429` rate/quota · `503` LLM down.

```js
async function ask(question, history) {
  const res = await fetch(`${BASE}/chat/`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
    body: JSON.stringify({ question, history: history.slice(-20) }),
  });
  if (!res.ok) throw await res.json();
  return res.json();
}
```
> **Streaming:** not yet — the full answer returns in one response (~1–4 s). Show a typing indicator.

### `POST /chat/feedback/` — thumbs up/down
```json
{ "question": "...", "answer": "...", "source": "rag", "rating": "up", "comment": "" }
```
`rating`: `up | down`. Returns `201`.

---

## 4. Documents (knowledge management)

Uploads process in the **background**; poll until `processing_status === "completed"`.

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/documents/` | List (filters: `?processing_status=`, `?is_active=`, `?search=<filename>`, `?ordering=-created_at`) |
| `POST` | `/documents/` | Upload any supported file (`multipart`, field `file`) |
| `POST` | `/documents/upload-word/` | `.docx`-only upload |
| `GET/PATCH/DELETE` | `/documents/{id}/` | Retrieve / toggle `is_active` / delete (`204`) |
| `POST` | `/documents/{id}/reindex/` | Re-embed (`202`) |

**Object:**
```json
{
  "id": "f0e1…", "filename": "about.docx", "file_size_mb": 0.04, "chunk_count": 6,
  "processing_status": "completed",   // pending | processing | completed | failed
  "error_message": "", "is_active": true, "source_type": "file",   // file | api
  "uploaded_by_username": "alice", "created_at": "…"
}
```
**Upload (multipart):**
```js
const fd = new FormData();
fd.append("file", fileInput.files[0]);   // .docx/.txt/.md, max 20 MB
await fetch(`${BASE}/documents/upload-word/`, {
  method: "POST",
  headers: { "Authorization": `Bearer ${token}` },   // do NOT set Content-Type for FormData
  body: fd,
});
```
Then poll `GET /documents/{id}/` (~2 s) until `completed`.
> Default limits: **100 docs / 200 MB / 20 MB per file** → `413` when exceeded.

---

## 5. Sync content from an external API — `POST /sync-api-content/`
```json
{ "api_url": "https://api.example.com/products", "items_key": "results", "full_refresh": false }
```
Returns `{ "status", "processed", "chunks_created", "items_new", "items_updated", "items_unchanged", "items_removed", "errors" }`. Incremental: re-running only re-embeds changed items. (Internal/private URLs are rejected.)

---

## 6. Bot configuration (optional) — `GET` / `PATCH /chatbot-config/`
Works with **zero config** (identity inferred from uploaded data). Use to customise presentation:
```json
{
  "assistant_name": "مساعد يونا", "company_name": "يونا",
  "tone": "friendly",            // friendly | formal | concise
  "default_language": "auto",
  "no_answer_message": "",       // optional static reply when nothing matches
  "top_k": null, "similarity_threshold": null   // retrieval overrides (null = default)
}
```
Grounding/safety rules are server-enforced and **not** configurable. `PATCH` only the fields you change.

---

## 7. Knowledge gaps (unanswered questions) — `/unanswered-questions/`
Questions the bot couldn't answer, **AI-filtered** to keep only in-domain, meaningful ones (greetings/tests/off-topic dropped). Scoped to your tenant. Use it to show "questions to add answers for".

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/unanswered-questions/` | List. `?status=new\|reviewed\|answered\|dismissed`, `?search=`, `?ordering=-occurrences` |
| `GET` | `/unanswered-questions/{id}/` | One |
| `PATCH` | `/unanswered-questions/{id}/` | Update `status` only |
| `DELETE` | `/unanswered-questions/{id}/` | Remove |

**Object:**
```json
{
  "id": "…", "question": "Do you support PostgreSQL backups?", "language": "en",
  "reason": "In-domain technical question worth answering",
  "status": "new",            // new | reviewed | answered | dismissed
  "occurrences": 3, "last_asked_at": "…", "created_at": "…"
}
```
Rows are created by the system (no `POST` → `405`).

---

## 8. Usage analytics — `GET /analytics/usage/`
```json
{
  "tenant": { "id": "...", "username": "original_software" },
  "totals":     { "requests": 1280, "total_tokens": 3904000, "cost_usd": 12.64, "avg_response_ms": 1180, "confident_rate": 0.86 },
  "this_month": { ... },
  "today":      { ... },
  "quota": { "documents_used": 2, "max_documents": 100, "storage_mb_used": 0.06, "max_total_mb": 200,
             "tokens_this_month": 410000, "monthly_token_cap": 0, "max_requests_per_min": 60, "is_suspended": false }
}
```
Admins: `?scope=all` → `{ "tenants": [ ... ] }` (non-admins `403`).

---

## 9. Subscriptions & plans

**My subscription + remaining usage** — `GET /my-subscription/`
```json
{
  "subscription": {            // null on the free tier
    "plan": { "name": "Starter", "monthly_questions": 1000, "llm_model": "gpt-4o", ... },
    "status": "active", "current_period_start": "…", "current_period_end": "…", "is_current": true
  },
  "on_free_tier": false,
  "usage": {
    "questions_used": 134, "questions_limit": 1000, "questions_remaining": 866,   // *_limit 0 = unlimited; *_remaining null = unlimited
    "tokens_used": 410000, "tokens_limit": 5000000, "tokens_remaining": 4590000,
    "documents_used": 12, "documents_limit": 25, "documents_remaining": 13,
    "storage_mb_used": 31.4, "storage_mb_limit": 50, "storage_mb_remaining": 18.6,
    "requests_per_min": 60
  }
}
```
Use `*_remaining` for "how much is left" widgets / upgrade prompts.

**Browse plans (catalog)** — `GET /plans/` → paginated plan objects
```json
{ "id": "…", "name": "Growth", "price_usd": "149.00", "monthly_questions": 5000,
  "max_documents": 100, "llm_model": "gpt-4o", ... }
```
> When a tenant runs out: chat returns `429` (questions/tokens) or `402` (subscription expired).

---

## 10. WhatsApp (overview)

Incoming WhatsApp messages are answered by the **tenant linked to the receiving business number** (multi-tenant). Frontend rarely calls these directly; relevant endpoints:
- `POST /whatsapp/send/` (auth) — send a text: `{ "to_number": "2012…", "message": "…" }` → `{ message_id, status }`.
- Read-only logs (auth): `GET /whatsapp/messages/`, `/whatsapp/sessions/`, `/whatsapp/users/`, `/whatsapp/analytics/`.
- Linking numbers to tenants is **admin** (`/whatsapp/accounts/`, see §12).

---

## 11. Error handling

Errors return `{ "detail": "..." }` (or per-field map for `400`).

| Status | Meaning | Frontend action |
|--------|---------|-----------------|
| `400` | Validation | Show field message(s). |
| `401` | Missing/expired token | Refresh (§2) or re-login. |
| `402` | Subscription expired | Prompt to renew/upgrade. |
| `403` | Not allowed / suspended | Show "no access / suspended". |
| `404` | Not found | — |
| `405` | Method not allowed | — |
| `413` | Document/storage quota exceeded | Tell user to delete or upgrade. |
| `429` | Rate limit / question or token quota / login throttle | Back off; show "try again / upgrade". |
| `503` | LLM/RAG provider down | "service busy, retry". |

```js
const res = await fetch(...);
if (!res.ok) {
  const err = await res.json().catch(() => ({}));
  if (res.status === 401) { /* refresh or re-login */ }
  else if (res.status === 402) { /* show upgrade modal */ }
  else if (res.status === 429) { /* rate-limit/quota toast */ }
  else { showError(err.detail || "Something went wrong"); }
  return;
}
```

---

## 12. Admin endpoints (only for an admin panel)

Require an **admin** token (`is_staff`).

**Users & keys**
- `POST /users/` — create a tenant (+ `api_key`). Optional `plan` + `plan_duration_days` to subscribe at creation:
  ```json
  { "username": "client1", "email": "c1@x.com", "password": "…", "password_confirm": "…",
    "plan": "starter", "plan_duration_days": 30 }
  ```
  → response includes `api_key` and (if plan given) `subscription`.
- `GET /users/` — list tenants (each includes its `api_key`).
- `POST /users/{id}/set-password/` — reset a user's password.
- `GET/POST /users/{id}/api-key/`, `/users/{id}/regenerate-api-key/` — read/rotate a user's key.
- `/api-keys/` — full key control: `GET`, `POST {user}`, `revoke/`, `activate/`, `regenerate/`.

**Plans** — `/plans/` (read open; write admin)
- `POST /plans/` (slug auto-derived from name), `PATCH/DELETE /plans/{id}/`.
- Object: `{ name, slug, price_usd, monthly_questions, monthly_token_cap, max_documents, max_total_mb, max_requests_per_min, llm_model, is_active, sort_order }` (0 = unlimited for `monthly_questions`/`monthly_token_cap`).

**Subscriptions** — `/subscriptions/` (admin)
- `POST /subscriptions/` `{ "user": "<id>", "plan": "<id|slug>", "duration_days": 30, "auto_renew": true }`.
- `GET /subscriptions/`, `GET /subscriptions/{id}/`.

**WhatsApp accounts** — `/whatsapp/accounts/` (admin): link a number to a tenant
```json
{ "tenant": "<user_id>", "phone_number_id": "1234567890", "display_name": "Acme Bot", "access_token": "EAAG…" }
```
`access_token` is optional (write-only; falls back to env). `GET` to list, `PATCH/DELETE` to edit/unlink.

**All-tenant analytics** — `GET /analytics/usage/?scope=all`.

---

## 13. Minimal client wrapper

```js
const BASE = "https://una-ai-tools-apis.una-oic.org/chatbot-api/api/v1";
let token = localStorage.getItem("access");

async function api(path, { method = "GET", body, isForm } = {}) {
  const headers = { "Authorization": `Bearer ${token}` };
  if (!isForm) headers["Content-Type"] = "application/json";
  const res = await fetch(`${BASE}${path}`, {
    method, headers,
    body: isForm ? body : body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.detail || "API error"), { status: res.status, data });
  return data;
}

// Usage:
await api("/auth/login/", { method: "POST", body: { username, password } });
await api("/chat/", { method: "POST", body: { question: "مرحبا", history: [] } });
await api("/my-subscription/");
await api("/unanswered-questions/?status=new");
```
