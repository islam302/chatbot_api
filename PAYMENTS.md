# Payments & Billing

Everything about how tenants pay, how credits work, and how to set up / operate
Paddle. The **API surface** is in `FRONTEND_API.md` (§7); this doc is the setup +
operations guide (backend/ops).

---

## 1. The model in one minute

- **Credits are the currency.** Every chat question costs `CREDITS_PER_QUESTION`
  credits (**default 2**). Out of credits → `POST /chat/` returns **`402`**.
  Enforcement is **race-safe**: the cost is atomically reserved *before*
  answering (a single conditional DB update), so parallel requests can never
  spend more than the balance; a failed answer (`503`) auto-refunds.
- **Free tier** (a signup with no paid plan): a **one-time grant** of
  `FREE_TIER_CREDITS` (**default 100 = 50 questions**), plus tight data limits
  (**1 document, ~0.5 MB**, no external-API import).
- **Paid plans** top up credits and lift limits. A plan carries
  `included_credits` (granted per payment) and a `paddle_price_id` (the Paddle
  price it maps to).
- **Money → credits via Paddle.** The frontend opens Paddle Checkout; Paddle
  charges the card and calls our **webhook**; we grant credits / set the plan.
- **Manual top-ups** (bank transfer / Instapay / wallet) are supported with **zero
  Paddle** via the admin endpoint — good for day-one selling.

---

## 2. Flow

```
Frontend (Paddle.js)                 Paddle                 Our backend
  Checkout.open({                                            
    priceId, customData:{user_id} }) ──▶ hosted card UI
                                     charges customer
                                     ──▶ POST /api/v1/billing/paddle/webhook/
                                          (Paddle-Signature header)
                                                             verify signature
                                                             match plan (price_id)
                                                             match user (user_id)
                                                             grant credits / set plan
  re-fetch GET /my-subscription/  ◀── new balance shown
```

The backend **never sees card data**. It only trusts a **signature-verified**
webhook.

---

## 3. One-time setup

### 3.1 Paddle dashboard (do it in **sandbox** first)

1. **Products & Prices** → create one Price per plan you sell. Copy each
   `pri_...` id.
2. **Developer Tools → Authentication** → create an **API key** (`pdl_sdbx_...`
   in sandbox). Used for outbound calls (optional today).
3. **Developer Tools → Notifications → New destination**:
   - Type: **Webhook**
   - URL: `https://<your-domain>/chatbot-api/api/v1/billing/paddle/webhook/`
   - Events: at minimum `transaction.completed`, `subscription.created`,
     `subscription.updated`, `subscription.canceled`
   - Save, then open the destination and copy its **Secret key** (`pdl_ntfset_...`).
4. **Client-side token** (Developer Tools → Authentication) for Paddle.js on the
   frontend.

### 3.2 Environment (`.env`)

```
PADDLE_ENV=sandbox                 # switch to "production" only when going live
PADDLE_API_KEY=pdl_sdbx_apikey_... # sandbox key while testing
PADDLE_WEBHOOK_SECRET=pdl_ntfset_...   # the destination's Secret key (REQUIRED)
CREDITS_PER_QUESTION=2
FREE_TIER_CREDITS=100
```

Without `PADDLE_WEBHOOK_SECRET`, every webhook is rejected with **403**. Restart
the server after editing `.env`.

### 3.3 Plans

Create a Plan per Paddle price and set the mapping + credits (admin API or Django
admin):

```json
POST /api/v1/plans/
{
  "name": "Pro",
  "price_usd": "49.00",
  "included_credits": 4000,        // credits granted on each payment
  "paddle_price_id": "pri_...",    // the Paddle price this maps to
  "monthly_questions": 0,          // 0 = unlimited (credits are the real gate)
  "max_documents": 100,
  "max_total_mb": 500,
  "allow_api_sync": true,
  "llm_model": "gpt-4o"
}
```

The **free tier needs no plan** — it's the default for signups. Optionally create
a "Free" plan for display, but limits/credits already apply without it.

---

## 4. What each webhook event does

Endpoint: `POST /api/v1/billing/paddle/webhook/` (public, signature-verified,
idempotent per `event_id`).

| Event | Action |
|-------|--------|
| `transaction.completed` | **Grants credits** = matched plan's `included_credits`. Fires on the initial payment AND every renewal, and on one-time credit-pack buys. This is the ONLY place credits are granted. |
| `subscription.created` / `subscription.activated` / `subscription.updated` | Sets/refreshes the tenant's Subscription (plan, period, status, Paddle ids). Does **not** grant credits. |
| `subscription.canceled` / `subscription.paused` | Marks the subscription canceled. |
| anything else | Ignored (200). |

**Matching:** plan ← item `price.id` → `Plan.paddle_price_id`; user ←
`data.custom_data.user_id` (the frontend must pass it at checkout). If either is
missing, the event is accepted (200) but logged as `unmatched` and nothing is
granted.

**Idempotency:** each `event_id` is recorded in `PaddleWebhookEvent`; a
re-delivered event is a no-op.

---

## 5. Frontend checkout

The plan's `paddle_price_id` comes from `GET /plans/`. Pass the logged-in
**user id** as `custom_data.user_id` — this is how the payment maps back.

```js
Paddle.Checkout.open({
  items: [{ priceId: plan.paddle_price_id, quantity: 1 }],
  customer: { email: user.email },
  customData: { user_id: user.id },   // REQUIRED
});
// After checkout closes, re-fetch GET /my-subscription/ (fulfillment is async).
```

Reading the balance: `GET /my-subscription/` →
`credits: { balance, credits_per_question, questions_left }`.

---

## 6. Manual payments (no Paddle)

You can sell today without any gateway: the customer pays by bank
transfer / Instapay / wallet, you confirm, then credit them (admin token):

```json
POST /api/v1/subscriptions/add-credits/     { "user": "<id>", "amount": 4000 }
POST /api/v1/subscriptions/                  { "user": "<id>", "plan": "<id>", "duration_days": 30 }
```

`add-credits` tops up the wallet; assigning a plan also grants that plan's
`included_credits` and lifts limits.

---

## 7. Testing (sandbox)

1. Keep `PADDLE_ENV=sandbox` and sandbox keys/secret in `.env`.
2. In Paddle → your notification destination → **Simulate** an event (or use a
   sandbox test card in a real checkout). Paddle signs it, so it exercises the
   real verification path.
3. Check **View logs** on the destination: a `200` means verified + processed;
   `403` means the `PADDLE_WEBHOOK_SECRET` in `.env` doesn't match this
   destination.
4. Confirm the wallet moved: `GET /my-subscription/`.

Paddle sandbox test cards: use Paddle's documented sandbox card numbers.

---

## 8. Going live

- [ ] Recreate Products/Prices in the **live** Paddle account; update each
      Plan's `paddle_price_id` to the live `pri_...`.
- [ ] Create a **live** notification destination (same URL); copy its **live**
      secret.
- [ ] `.env`: `PADDLE_ENV=production`, live `PADDLE_API_KEY`, live
      `PADDLE_WEBHOOK_SECRET`. Restart.
- [ ] **Rotate** any key/secret that was ever pasted into chat/email/screenshots.
- [ ] Frontend uses the **live** client-side token.
- [ ] Do one real small purchase end to end and confirm credits land.

---

## 9. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| Webhook returns **403** | `PADDLE_WEBHOOK_SECRET` missing/wrong for this destination, or wrong environment. |
| `200` but no credits (`unmatched` in logs) | `paddle_price_id` not set on any Plan, or checkout didn't send `custom_data.user_id`. |
| Credits granted twice | Shouldn't happen — grants only on `transaction.completed`, deduped by `event_id`. Check you didn't also assign the plan manually. |
| Chat returns **402** | Out of credits (top up / upgrade) or expired subscription. |
| Free user blocked from a 2nd upload (**413**) or API sync (**402**) | Expected free-tier limits (1 doc / 0.5 MB / no `allow_api_sync`). |

---

## 10. Config reference

| Setting | Default | Meaning |
|---------|---------|---------|
| `CREDITS_PER_QUESTION` | `2` | Credits spent per chat answer. |
| `FREE_TIER_CREDITS` | `100` | One-time grant for a new tenant (50 questions). |
| `FREE_TIER_MAX_DOCUMENTS` | `1` | Free-tier document count. |
| `FREE_TIER_MAX_TOTAL_MB` | `0.5` | Free-tier total storage. |
| `FREE_TIER_ALLOW_API_SYNC` | `false` | Free-tier access to sync-api-content. |
| `PADDLE_ENV` | `sandbox` | `sandbox` or `production`. |
| `PADDLE_API_KEY` | — | Paddle server API key (outbound). |
| `PADDLE_WEBHOOK_SECRET` | — | Verifies incoming webhooks (**required**). |

Per-plan: `included_credits`, `paddle_price_id`, `allow_api_sync`,
`max_documents`, `max_total_mb`, `monthly_questions`, `price_usd`, `llm_model`.
