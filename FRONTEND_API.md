# ChatBot API — Frontend Integration Guide

Everything a frontend developer needs to build against this API. All examples
use `fetch`; adapt freely to axios/your client.

---

## 1. Base URL & conventions

```
Base URL:  https://una-ai-tools-apis.una-oic.org/chatbot-api/api/v1
```

- All requests/responses are **JSON** unless uploading files (then `multipart/form-data`).
- Every endpoint below requires **authentication** (see §2) except where noted.
- Trailing slashes are required (`/chat/`, not `/chat`).
- Timestamps are ISO-8601 UTC.
- List endpoints are **paginated** (20/page): `?page=2`. Response shape:
  `{ "count", "next", "previous", "results": [...] }`.

> **CORS:** your site's origin must be whitelisted server-side
> (`CORS_ALLOWED_ORIGINS`). Give the backend team your frontend URL(s).

---

## 2. Authentication

Two interchangeable ways to authenticate. Pick one per request.

### Sign up — `POST /auth/register/`  (public, no auth)
Anyone can create an account:
```js
await fetch(`${BASE}/auth/register/`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    username, email, first_name, last_name, password, password_confirm,
  }),
});
// 201 -> { detail, username, email, email_verification: "activation_sent" | "send_failed" }
```
`email` is **required and unique**. The account is created **inactive** and an
activation link is emailed — the user must verify (see "New accounts must verify"
below) before they can log in. Rate-limited to 5/min per IP. `400` on validation
errors (duplicate email/username, password mismatch/too weak).

### Option A — JWT (recommended for user-facing apps)

**Login:**
```js
const res = await fetch(`${BASE}/auth/login/`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ username, password }),
});
const data = await res.json();
// data.access   -> short-lived access token (send on every request)
// data.refresh  -> use to get a new access token
// data.user     -> { id, username, email, role, api_key, ... }
```

**Use the token on every request:**
```js
headers: { "Authorization": `Bearer ${accessToken}` }
```

**Refresh when the access token expires (401):**
```js
const res = await fetch(`${BASE}/auth/refresh/`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ refresh: refreshToken }),
});
const { access } = await res.json();
```

Access token lifetime: 3 days. Refresh token: 30 days.

### Option B — API key (good for embedding a single tenant's bot)

Send the tenant's key as a header — no login needed:
```js
headers: { "x-api-key": "427a48c9...." }
```

> The whole API is **multi-tenant**: the token/key identifies the tenant, and
> all data (documents, chat answers) is automatically scoped to them. You never
> pass a user/tenant id in the body.

### New accounts must verify their email before they can log in

A newly created account is **inactive** until its email is verified — login
returns `401` and the API key won't authenticate. The user gets an email with a
link to `FRONTEND_URL/verify-email?uid=...&token=...`. Your frontend page reads
`uid` and `token` from the query string and POSTs them to activate (public, no
auth):

```js
await fetch(`${BASE}/auth/verify-email/`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ uid, token }),
});
// 200 -> account is active + email_verified; the user can now log in.
// 400 -> invalid/expired link (ask an admin to re-send).
```

A direct `GET /auth/verify-email/?uid=...&token=...` also works (for clicking the
link without a dedicated page). Links are **single-use** and expire (~3 days); an
admin can re-send via `POST /users/{id}/resend-activation/`.

---

## 3. Chat (the main endpoint)

### `POST /chat/`

**Request:**
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
| `question` | yes | Any language; the bot replies in the same language **and dialect**. |
| `history` | no | Prior turns for context. Keep the **last 10 turns max** (20 messages) — older ones rejected with 400. |
| `language` | no | Optional hint (`"ar"`, `"en"`, …). Omit it — the server auto-detects. |

**Response — 200:**
```json
{
  "answer": "أهلاً! عندنا انترنت داونلود مانجر (IDM) لمدة سنة بسعر 12,000 دينار...",
  "source": "rag",
  "source_id": "",
  "confident": true,
  "response_time_ms": 2643,
  "sources": [
    {
      "filename": "products",
      "document_id": "42b71e56-...",
      "chunk_id": "d6e3e001-...",
      "position": 11,
      "score": 0.81
    }
  ],
  "prompt_tokens": 2503,
  "completion_tokens": 144,
  "cost_usd": 0.007698
}
```

| Field | Meaning |
|-------|---------|
| `answer` | The text to render (may contain **Markdown** — render links/lists). |
| `confident` | `true` when a strong match was found; `false` = weaker/fallback answer. Optionally show a subtle "not sure" hint when false. |
| `sources` | Which knowledge chunks were used. Optional to display (e.g. "Sources"). |
| `prompt_tokens` / `completion_tokens` / `cost_usd` | Usage of this call (for your own metering/UI; safe to ignore). |

**Frontend flow for a chat UI:**
1. Keep an array of `{role, content}` messages in state.
2. On send: push the user message, POST `{ question, history: lastMessages }`.
3. Append `answer` as an assistant message. Render with a Markdown renderer.
4. Trim history to the last ~20 messages before sending.

```js
async function ask(question, history) {
  const res = await fetch(`${BASE}/chat/`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
    body: JSON.stringify({ question, history: history.slice(-20) }),
  });
  if (!res.ok) throw await res.json();   // see §10 for error shapes
  return res.json();
}
```

> **Streaming:** not supported yet — the full answer returns in one response
> (typically 1–4 s). Show a typing indicator while awaiting.

### `POST /chat/feedback/` — thumbs up/down on an answer
```json
{ "question": "...", "answer": "...", "source": "rag", "rating": "up", "comment": "" }
```
`rating`: `"up"` | `"down"`. Returns `201` with the saved row.

---

## 4. Documents (knowledge management UI)

Uploads are processed in the **background**. After upload, poll the document
until `processing_status` is `completed`.

### List — `GET /documents/`
Paginated. Each item:
```json
{
  "id": "677f38e1-...",
  "filename": "about.docx",
  "file_size_mb": 0.01,
  "chunk_count": 2,
  "processing_status": "completed",   // pending | processing | completed | failed
  "error_message": "",
  "is_active": true,
  "source_type": "file",              // file | api
  "uploaded_by_username": "original_software",
  "created_at": "2026-06-17T01:03:41Z"
}
```
Filters: `?processing_status=completed`, `?search=<filename>`, `?ordering=-created_at`.

### Upload a Word file — `POST /documents/upload-word/`  (multipart)
```js
const fd = new FormData();
fd.append("file", fileInput.files[0]);   // .docx only, max 20 MB
const res = await fetch(`${BASE}/documents/upload-word/`, {
  method: "POST",
  headers: { "Authorization": `Bearer ${token}` },   // do NOT set Content-Type for FormData
  body: fd,
});
```
Returns `201` with the document (`processing_status: "pending"`). Then poll §below.

### Upload any file — `POST /documents/`  (multipart)
Same as above with field `file`; optional `is_active`.

### Poll status — `GET /documents/{id}/`
Poll every ~2 s until `processing_status === "completed"` (or `"failed"` →
show `error_message`). Then `chunk_count` is the number of searchable pieces.

### Delete — `DELETE /documents/{id}/`  → `204`
### Re-index — `POST /documents/{id}/reindex/`  → `202` (re-embeds the document)

> Limits per tenant: **100 documents / 200 MB / 20 MB per file** by default.
> Over the limit returns **413** (see §10). **Free-tier** tenants are much
> tighter — **1 document, ~0.5 MB** (one small file); see §7.

---

## 5. Sync content from an external API — `POST /sync-api-content/`
Pull a JSON list from a URL into the tenant's knowledge.
```json
{ "api_url": "https://api.example.com/products", "items_key": "results", "full_refresh": false }
```
Returns sync stats: `{ "status", "processed", "chunks_created", "items_new", "items_updated", ... }`.
Re-running only re-embeds changed items (incremental).

> **Not on the free tier.** This endpoint returns **`402`** unless the tenant's
> plan enables it (`allow_api_sync`). Free-tier users import knowledge only by
> uploading a small file (§4). Show an upgrade CTA on `402`.

---

## 5b. Crawl a website into knowledge — `POST /crawl-website/`
Give the tenant's own **website URL** and the bot learns the whole site: it
discovers as many pages of the same site as it can (sitemap + internal links),
extracts each page's main readable text (nav/menus/footers stripped), and stores
them as knowledge. Great for onboarding — the client pastes their site instead of
uploading files.
```json
// request
{
  "url": "https://client.com",   // required; bare host is upgraded to https://
  "document_name": "Website: client.com",  // optional (defaults to the host)
  "max_pages": 100,              // optional; clamped to the server cap (300)
  "full_refresh": false          // optional; false = incremental re-crawl
}
```
Crawling a whole site is slow, so it runs in the **background** and returns
**`202`** immediately:
```json
{ "status": "processing", "document_id": "<uuid>", "document_name": "...", "max_pages": 100,
  "detail": "Crawl started. Poll GET /documents/{id}/ for progress ..." }
```
Poll `GET /documents/{document_id}/` and watch `processing_status`:
`processing` → `completed` (or `failed`, with `error_message`). The crawled pages
become one knowledge document (source type `website`); re-crawling only re-embeds
pages whose content changed. Same-site only, honours `robots.txt`, and rejects
private/internal URLs (**`400`**).

> **Not on the free tier.** Same gate as §5 — returns **`402`** unless the plan
> enables it (`allow_api_sync`). Show an upgrade CTA on `402`.

---

## 6. Bot configuration (optional) — `GET` / `PATCH /chatbot-config/`
The bot works with **zero config** (it infers identity from the uploaded data).
Use this only to customise its presentation.
```json
// PATCH body — send only fields you want to change
{
  "assistant_name": "مساعد يونا",
  "company_name": "يونا",
  "tone": "friendly",            // friendly | formal | concise
  "default_language": "auto",
  "no_answer_message": "",       // optional static "no info" reply
  "top_k": null,                 // retrieval overrides (null = server default)
  "similarity_threshold": null
}
```
Grounding/safety rules are server-enforced and **not** configurable.

---

## 7. Usage analytics — `GET /analytics/usage/`
Returns the tenant's own rollups + live quota:
```json
{
  "tenant": { "id": "...", "username": "original_software" },
  "totals":     { "requests": 1280, "total_tokens": 3904000, "cost_usd": 12.64, "avg_response_ms": 1180, "confident_rate": 0.86 },
  "this_month": { ... },
  "today":      { ... },
  "quota": { "documents_used": 2, "max_documents": 100, "storage_mb_used": 0.06, "max_total_mb": 200, "tokens_this_month": 410000, "monthly_token_cap": 0, "max_requests_per_min": 60, "is_suspended": false }
}
```
Admins can pass `?scope=all` for a per-tenant breakdown.

### Credits & plan — `GET /my-subscription/`
Every chat question costs **credits** (default **2 credits/question**). A
free-tier tenant gets **100 credits per month (= 50 questions/month)** — the
balance is automatically topped back up to 100 at the start of each calendar
month. When the balance can't cover a question, `POST /chat/` returns **`402`**
— show an "upgrade or wait for next month" CTA.

```json
{
  "subscription": { ... } | null,
  "on_free_tier": true,
  "credits": { "balance": 96, "credits_per_question": 2, "exempt": false, "questions_left": 48 },
  "usage": { "documents_used": 1, "documents_limit": 1, "storage_mb_limit": 0.5, ... }
}
```

> `credits.exempt: true` means this user (admin/staff) never spends credits —
> their usage is internal testing. `questions_left` is `null` then; show
> "unlimited" instead of a counter.
Read `credits.questions_left` to show "N questions left" and gate the UI before
the user hits the `402`. Credits are granted by the plan (`included_credits`) or
an admin top-up (`POST /subscriptions/add-credits/`, admin only).

Enforcement is **strict and race-safe**: credits are atomically reserved
*before* the answer is generated (parallel requests can never exceed the
balance), and **auto-refunded** if the answer fails (a `503` never costs the
user credits).

**Free-tier limits** (a signup with no paid plan): **1 document, ~0.5 MB total**
(one small Word file), and **no external-API import** (§5 returns `402`).
Uploading a 2nd file or a larger one returns `413`. Paid plans lift these via
their `max_documents` / `max_total_mb` / `allow_api_sync`.

### Paying with Paddle (upgrade / buy credits)
Payment runs through **Paddle Billing**. The **frontend** opens Paddle Checkout;
the **backend** fulfills via a signed webhook — you never post payment data to
this API.

1. Each plan carries a `paddle_price_id` (from `GET /plans/`).
2. On "Upgrade", open Paddle Checkout (Paddle.js) with that price and **pass the
   logged-in user's id as `custom_data.user_id`** so the payment maps back:
   ```js
   Paddle.Checkout.open({
     items: [{ priceId: plan.paddle_price_id, quantity: 1 }],
     customer: { email: user.email },
     customData: { user_id: user.id },   // REQUIRED for fulfillment
   });
   ```
3. Paddle charges the customer, then calls our webhook
   (`/api/v1/billing/paddle/webhook/`). On success the plan/credits are applied
   server-side within seconds.
4. After checkout closes, **re-fetch `GET /my-subscription/`** to show the new
   balance/plan (fulfillment is async — poll once or twice if needed).

You need the Paddle **client-side token** and **price ids** from the backend team
(they live in the Paddle dashboard). Nothing else changes in your API calls.

> Full payment setup, webhook events, testing, and go-live steps are in
> **`PAYMENTS.md`** (backend/ops guide).

---

## 8. Unanswered questions (knowledge gaps)

When the bot can't confidently answer (`confident: false`), the question is
captured for that tenant as a **knowledge gap** — after an AI filter drops
noise (greetings, chit-chat) and de-duplicates repeats. Build a review UI so
the tenant can see what customers ask that the bot can't answer yet, and fill
those gaps.

> No create endpoint — rows are produced automatically by the chat pipeline.
> All endpoints are scoped to the authenticated tenant.

### List — `GET /unanswered/`
Paginated, most-frequent first. Each item:
```json
{
  "id": "9c1e...",
  "question": "Do you offer refunds on annual plans?",
  "question_key": "do you offer refunds on annual plans",
  "language": "en",
  "reason": "asks about refund policy",
  "status": "new",              // new | reviewed | answered | dismissed
  "occurrences": 4,
  "last_asked_at": "2026-07-12T09:10:00Z",
  "created_at": "2026-07-10T14:00:00Z"
}
```
| Field | Meaning |
|-------|---------|
| `occurrences` | How many times this gap was asked (dedup counter). Sort by this to prioritise. |
| `reason` | Why the AI filter kept it as a real question. |
| `status` | Review state you control (see below). |

Filters: `?status=new`, `?language=ar`, `?search=<text>`,
`?ordering=-occurrences` (also `last_asked_at`, `created_at`).

### Retrieve — `GET /unanswered/{id}/`

### Re-triage — `PATCH /unanswered/{id}/`
Only `status` is writable:
```json
{ "status": "reviewed" }
```

### Resolve into knowledge — `POST /unanswered/{id}/resolve/`
Provide an answer and it is **embedded into the tenant's knowledge**, so the bot
retrieves it the next time the question is asked; the gap is marked `answered`.
```json
{ "answer": "Yes, we refund annual plans within 14 days of purchase." }
```
Send an **empty body** to just mark it `answered` without adding knowledge.
Returns the updated row. (May return `503` if embedding is temporarily
unavailable — see §10.)

### Dismiss — `POST /unanswered/{id}/dismiss/`
Marks the gap `dismissed` (not worth answering).

### Delete — `DELETE /unanswered/{id}/`  → `204`

---

## 9. Account settings (the logged-in user)

Self-service account management for the authenticated user. Build a "Settings"
page from these.

### Get my profile — `GET /users/me/`
Returns the current user: `{ id, username, email, email_verified, first_name, last_name, role, api_key, is_active, date_joined }`.

> `email_verified` is `false` until the user confirms their email. New accounts
> verify at signup via the activation link (§2, which also flips `is_active` to
> `true`); a logged-in user changing their email verifies the new address via the
> flow below. Show a "verify your email" banner while it's `false`.

### Edit name / username — `PATCH /users/me/`
```json
{ "first_name": "Alice", "last_name": "Smith", "username": "alice" }
```
Send any subset. Username must stay unique (400 if taken). **Email is not
editable here** — it changes only through the verified flow below. The API key
is never editable by the user. Returns the updated user, so refresh whatever
copy of `me` you cache (name/username may have changed).

### Change password (verified, two steps)
A password change requires the current password **and** a code emailed to the
user — a second factor proving they control the account's email.

**Step 1 — request a code:** `POST /users/request-password-code/`  (empty body)
Emails a **6-digit code** to the user's email (valid ~15 min). `400` if the
account has no email.

**Step 2 — change:** `POST /users/change-password/`
```json
{ "old_password": "...", "new_password": "...", "new_password_confirm": "...", "code": "123456" }
```
Requires the current password **and** the code. `200` on success; `400` if the
old password is wrong, the code is missing/wrong/expired, or the new password
fails validation (or after 5 wrong code attempts — request a new one).

```js
// Settings UI: password change flow
await api("/users/request-password-code/", { method: "POST" });
// ...user reads the code from their inbox...
await api("/users/change-password/", { method: "POST",
  body: { old_password, new_password, new_password_confirm, code } });
```

### Change email (verified, two steps)
Email is never set directly — the user must prove they control the new address.

**Step 1 — request a code:** `POST /users/change-email/`
```json
{ "new_email": "new@example.com" }
```
Sends a **6-digit code** to the new address (valid ~15 min). The account email
is **not** changed yet. `400` if the address is already in use or is the current
one. Calling this again (a "resend") sends a fresh code and **invalidates the
previous one** — always verify with the latest code.

**Step 2 — confirm:** `POST /users/verify-email/`
```json
{ "code": "123456" }
```
On success (`200`) the email is updated and the **updated user is returned**
(refresh your cached `me`). `400` for a wrong/expired code, or after **5 wrong
attempts** (the code locks — have the user request a new one via step 1).

```js
// Settings UI: email change flow
await api("/users/change-email/", { method: "POST", body: { new_email } });
// ...user reads the code from their inbox and types it...
const updated = await api("/users/verify-email/", { method: "POST", body: { code } });
// updated.email is now the new address — update your local user state.
```

### View my API key — `GET /users/api-key/`
Read-only. Users **cannot** rotate their own key (only an admin can). Don't show
a "regenerate" button in the user settings UI.

> **Paid feature.** Free-tier users get **`402`** here, and `api_key` is `null`
> in `GET /users/me/` and in the login response. Hide the API-key section for
> them and show an upgrade CTA instead (check `credits`/plan via
> `/my-subscription/`).

---

## 10. Error handling

Errors return JSON `{ "detail": "..." }` (or field errors for 400 validation).

| Status | Meaning | Frontend action |
|--------|---------|-----------------|
| `400` | Bad request / validation | Show the field message(s). |
| `401` | Missing/expired token | Refresh token (§2) or send to login. |
| `402` | Out of credits, or subscription expired | Show an **upgrade / top-up** CTA (see credits below). |
| `403` | Tenant suspended / not allowed | Show "account suspended / no access". |
| `413` | Document/storage quota exceeded | Tell the user to delete docs or upgrade. |
| `429` | Rate limit or monthly token budget hit | Back off / show "try again shortly". |
| `503` | RAG backend unavailable (LLM/provider) | Show "service busy, retry". |

Example handling:
```js
const res = await fetch(...);
if (!res.ok) {
  const err = await res.json().catch(() => ({}));
  if (res.status === 401) { /* refresh or re-login */ }
  else if (res.status === 429) { /* show rate-limit toast */ }
  else { showError(err.detail || "Something went wrong"); }
  return;
}
```

---

## 11. Admin endpoints (only if you build an admin panel)

Require an **admin** token. Highlights:
- `POST /users/` — create a tenant (returns the new user + `api_key`). **Email
  verification is required:** with an email, the account is created **inactive**
  (`is_active: false`, cannot log in) and an activation link is emailed; the
  response includes `email_verification` (`activation_sent` | `send_failed` |
  `not_sent`). The user activates via the public link (§2). A no-email account
  stays active.
- `POST /users/{id}/resend-activation/` — re-send the activation link to an
  inactive user (first email failed, or the link expired).
- `GET /users/` — list tenants (each includes its `api_key`).
- `POST /users/{id}/regenerate-api-key/` — rotate a user's key (admin only; users can't rotate their own).
- `POST /users/{id}/set-password/` — admin resets a user's password.
- `PATCH /users/{id}/` — update a tenant. Deactivating (`is_active: false`) a
  normal user works; deactivating a **staff/admin** account is refused (**`400`**,
  "Admin/staff accounts cannot be deactivated") so an admin can't lock themselves
  out. (Recovery if it ever happens on the server: `python manage.py reactivate_admin`.)
- `GET /api-keys/`, `POST /api-keys/{id}/revoke|activate|regenerate/` — key control.

**Plans & billing (admin):**
- `GET /plans/` — anyone authenticated can list active plans (for a pricing page).
  Each plan includes a read-only `questions` (how many chat questions it sells) —
  use that for the pricing display. Admin `POST` / `PATCH` / `DELETE /plans/`
  manage them: just set `name`, `price_usd`, **`questions`**, `max_documents`,
  `max_total_mb`, `is_active`, `allow_api_sync` (+ `paddle_price_id`) — credits
  and the token cap are **auto-derived** from `questions`.
- `POST /subscriptions/` — assign/move a user to a plan (`{ user, plan, duration_days }`).
- `POST /subscriptions/add-credits/` — top up a wallet (`{ user, amount }`).
- `GET /subscriptions/` — list all subscriptions.
- `POST /billing/paddle/webhook/` — **Paddle calls this, not you** (see §7). Public,
  signature-verified; grants credits / sets the subscription on payment.

---

## 12. Minimal client wrapper (copy-paste starter)

```js
const BASE = "https://una-ai-tools-apis.una-oic.org/chatbot-api/api/v1";
let token = localStorage.getItem("access");

async function api(path, { method = "GET", body, isForm } = {}) {
  const headers = { "Authorization": `Bearer ${token}` };
  if (!isForm) headers["Content-Type"] = "application/json";
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
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
await api("/documents/");
```
