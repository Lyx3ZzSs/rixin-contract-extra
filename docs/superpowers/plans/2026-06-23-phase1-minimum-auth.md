# Phase 1 · Minimum Auth (API Key) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a minimal API-Key gate so the commercial product isn't running fully open, replacing the cosmetic hardcoded `admin/123456` login with a real key. No multi-tenant, no JWT, no user system (spec D5).

**Architecture:** Backend — a `verify_api_key` FastAPI dependency attached router-level to `/api/v1`, configured via `APP_API_KEYS`. **Open mode when unset** (keeps dev/tests green), **locked mode when set** (production). Frontend — store the key in localStorage, inject `X-API-Key` on every request, validate on login via a cheap authenticated probe, bounce to login on 401.

**Tech Stack:** FastAPI + Pydantic-settings (backend, pytest-tested); React/TS (frontend, `tsc -b` + manual verification, no test framework).

**Spec:** `docs/superpowers/specs/2026-06-23-phase1-review-loop-and-accuracy-foundation-design.md` item ⑧. Branches from the Plan 2 branch.

## Global Constraints

- **Open mode when `APP_API_KEYS` is empty** — existing dev/tests pass unchanged; production MUST set it to lock down (loud startup warning). This is a deliberate transition choice, not a bug.
- **`/health` stays unauthenticated** (defined on `app`, not under `api_router`).
- **No user system / JWT / RBAC / rate-limiting** (→ Phase 3).
- **Key validated on login by probing an authenticated endpoint** (`listFieldDefinitions`) — no dedicated `/auth/check` endpoint (YAGNI).
- **On 401, frontend clears the key and reloads to the login page** (minimum-auth simplicity).
- **CORS**: when `ALLOWED_ORIGINS` is set, use that explicit list with credentials; when unset, `["*"]` without credentials (the current `["*"]` + `credentials=True` is an invalid combo per spec).
- **Commit style:** conventional commits.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `backend/app/config.py` | Add `app_api_keys`, `allowed_origins` | 1 |
| `backend/app/api/deps.py` (new) | `verify_api_key` dependency | 1 |
| `backend/app/api/router.py` | Attach dependency router-level | 1 |
| `backend/app/main.py` | Startup warning + CORS tightening | 1 |
| `backend/.env.example` | Document `APP_API_KEYS`, `ALLOWED_ORIGINS` | 1 |
| `backend/tests/test_auth.py` (new) | TDD for the dependency (open/locked/valid/invalid) | 2 |
| `frontend/src/lib/api.ts` | `authHeaders()` + inject at all call sites + 401 handling | 3 |
| `frontend/src/lib/state.tsx` | Store API key in localStorage | 4 |
| `frontend/src/pages/LoginPage.tsx` | API-key login form | 4 |
| `frontend/src/App.tsx` | `handleLogin` validates key via probe | 4 |

---

## Task 1: Backend API-Key dependency + config + CORS

**Files:**
- Modify: `backend/app/config.py`, `backend/app/api/router.py`, `backend/app/main.py`, `backend/.env.example`
- Create: `backend/app/api/deps.py`

**Interfaces:**
- Consumes: `settings` (`app.config`); `Header`/`HTTPException` (FastAPI).
- Produces: `verify_api_key` async dependency; `settings.app_api_keys` / `settings.allowed_origins`; `/api/v1` routes require a valid key when keys are configured.

- [ ] **Step 1: Write the failing tests first (TDD)**

Create `backend/tests/test_auth.py`:

```python
"""Tests for the API-Key auth dependency on /api/v1."""
import pytest
from app.config import settings


@pytest.fixture
def open_mode(monkeypatch):
    monkeypatch.setattr(settings, "app_api_keys", "")


@pytest.fixture
def locked_mode(monkeypatch):
    monkeypatch.setattr(settings, "app_api_keys", "secret-1, secret-2")


async def test_open_mode_allows_request_without_key(open_mode, client):
    resp = await client.get("/api/v1/field-definitions")
    assert resp.status_code != 401


async def test_locked_mode_rejects_missing_key(locked_mode, client):
    resp = await client.get("/api/v1/field-definitions")
    assert resp.status_code == 401


async def test_locked_mode_rejects_wrong_key(locked_mode, client):
    resp = await client.get("/api/v1/field-definitions", headers={"X-API-Key": "nope"})
    assert resp.status_code == 401


async def test_locked_mode_accepts_valid_key(locked_mode, client):
    resp = await client.get("/api/v1/field-definitions", headers={"X-API-Key": "secret-2"})
    assert resp.status_code != 401


async def test_health_is_unauthenticated(locked_mode, client):
    resp = await client.get("/health")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_auth.py -v`
Expected: FAIL — routes don't enforce a key yet (the "rejects" tests will pass vacuously only if 401 already happens, which it won't; the `accepts_valid_key`/`open_mode` pass since no auth; `rejects_missing_key`/`rejects_wrong_key` FAIL because the endpoint returns 200, not 401).

- [ ] **Step 3: Add config fields**

In `backend/app/config.py`, inside `Settings` (after `llm_chunk_pages` or near the upload block), add:

```python
    # Minimum auth: comma-separated valid API keys. Empty = open mode (dev);
    # set in production to require an X-API-Key header on all /api/v1 routes.
    app_api_keys: str = ""
    # CORS: comma-separated allowed origins. Empty = allow any (credentials off).
    allowed_origins: str = ""
```

- [ ] **Step 4: Create the dependency**

Create `backend/app/api/deps.py`:

```python
"""Auth dependencies for the /api/v1 router."""

from __future__ import annotations

from fastapi import Header, HTTPException

from app.config import settings


def valid_api_keys() -> set[str]:
    """Configured API keys. Empty set means open mode (no key required)."""
    return {k.strip() for k in settings.app_api_keys.split(",") if k.strip()}


async def verify_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    """Require a valid X-API-Key when keys are configured.

    Open mode (APP_API_KEYS unset) lets dev/tests run without a key; production
    must set APP_API_KEYS to lock the API down.
    """
    keys = valid_api_keys()
    if not keys:
        return  # open mode
    if not x_api_key or x_api_key not in keys:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
```

- [ ] **Step 5: Attach the dependency router-level**

In `backend/app/api/router.py`, change the `api_router` construction (line 8) to:

```python
from fastapi import APIRouter, Depends

from app.api.contract import router as contract_router
from app.api.deps import verify_api_key
from app.api.field_definition import router as field_def_router
from app.api.review import router as review_router
from app.api.task import router as task_router

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_key)])
api_router.include_router(contract_router)
api_router.include_router(field_def_router)
api_router.include_router(task_router)
api_router.include_router(review_router)
```

- [ ] **Step 6: Tighten CORS + startup warning**

In `backend/app/main.py`, replace the CORS middleware block (~:105-111) with:

```python
_origins = (
    [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
    if settings.allowed_origins else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=bool(settings.allowed_origins),  # credentials only with explicit origins
    allow_methods=["*"],
    allow_headers=["*"],
)
```

In the `lifespan` startup section (after table creation, before `yield`), add a warning when auth is open:

```python
if not settings.app_api_keys:
    import logging
    logging.getLogger(__name__).warning(
        "AUTH DISABLED: APP_API_KEYS is empty — all /api/v1 routes are open. "
        "Set APP_API_KEYS in production to require an X-API-Key header."
    )
```

- [ ] **Step 7: Document in `.env.example`**

Append to `backend/.env.example`:

```ini
# Minimum auth: comma-separated valid API keys. Empty = open (dev). Set in production.
APP_API_KEYS=
# CORS allowed origins (comma-separated). Empty = allow any.
ALLOWED_ORIGINS=
```

- [ ] **Step 8: Run auth tests + full suite**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_auth.py -v` → all 5 pass.
Run: `cd backend && ../.venv/bin/python -m pytest -q` → full suite green (existing tests pass because conftest leaves `app_api_keys` empty = open mode).

- [ ] **Step 9: Commit**

```bash
git add backend/app/config.py backend/app/api/deps.py backend/app/api/router.py backend/app/main.py backend/.env.example backend/tests/test_auth.py
git commit -m "feat(auth): add API-Key dependency on /api/v1 (open-when-unset, locked-when-configured)"
```

---

## Task 2 — folded into Task 1

(The TDD tests in Task 1 Step 1 ARE the auth test task. No separate task needed — Task 1 covers implementation + tests together, since they're one cohesive deliverable.)

---

## Task 3: Frontend API client — inject X-API-Key + 401 handling

**Files:**
- Modify: `frontend/src/lib/api.ts` (header helper + all ~10 fetch call sites + response parsers)

**Interfaces:**
- Consumes: localStorage key `rixin_contract_api_key` (set by Task 4).
- Produces: every request carries `X-API-Key` when a key is stored; 401 clears the key and reloads to login.

- [ ] **Step 1: Add the header helper + 401 handling**

Near the top of `frontend/src/lib/api.ts` (after `toApiUrl`), add:

```ts
const API_KEY_STORAGE = "rixin_contract_api_key";

export function getApiKey(): string | null {
  return localStorage.getItem(API_KEY_STORAGE);
}

export function setApiKey(key: string): void {
  localStorage.setItem(API_KEY_STORAGE, key);
}

export function clearApiKey(): void {
  localStorage.removeItem(API_KEY_STORAGE);
}

function authHeaders(): Record<string, string> {
  const key = getApiKey();
  return key ? { "X-API-Key": key } : {};
}
```

Then add 401 handling inside the response parsers. In `parseJsonResponse` (before the `!response.ok` branch or within it), add: if `response.status === 401`, clear the key and reload:

```ts
async function parseJsonResponse<T>(response: Response): Promise<T> {
  if (response.status === 401) {
    clearApiKey();
    if (window.location.pathname !== "/") window.location.assign("/");
    else window.location.reload();
    throw new Error("未授权，请重新登录");
  }
  if (!response.ok) {
    // ... existing error handling ...
  }
  return (await response.json()) as T;
}
```

(Do the same 401 check at the top of `parseApiResponse`.)

- [ ] **Step 2: Inject headers at all call sites**

For every `fetch(toApiUrl(X), { method, headers: {...}, body })`, merge `authHeaders()`:
- `prepareContract` (~:62): headers none (FormData) → add `headers: authHeaders()` (FormData must NOT set Content-Type; X-API-Key is fine).
- `startContractExtraction` (~:83): `headers: { "Content-Type": "application/json", ...authHeaders() }`.
- JSON-body calls (`updateFieldDefinition`, `createFieldDefinition`, `deleteFieldDefinition`, `resetFieldDefinitions`, and the new review calls): merge `...authHeaders()` into the existing headers object.
- Bare GETs (`getTask`, `getContractDetail`, `listContracts`, `listFieldDefinitions`, `listReviewRecords`): change `fetch(toApiUrl(X))` → `fetch(toApiUrl(X), { headers: authHeaders() })`.

Example transformation for a GET:
```ts
const response = await fetch(toApiUrl(`/api/v1/contracts/${contractId}`), { headers: authHeaders() });
```
Example for a JSON POST:
```ts
headers: { "Content-Type": "application/json", ...authHeaders() },
```

- [ ] **Step 3: Handle the file-download URL caveat**

`downloadContractFileUrl` (~:126) returns a bare URL used in `<a href>`/`window.open`, which can't carry headers. Add the key as a query param when present so downloads work in locked mode:

```ts
export function downloadContractFileUrl(contractId: string): string {
  let url = toApiUrl(`/api/v1/contracts/${contractId}/files/download`);
  const key = getApiKey();
  if (key) url += `?api_key=${encodeURIComponent(key)}`;
  return url;
}
```

Then in `deps.py` (Task 1), ALSO accept the key from a query param so this works:

```python
async def verify_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    api_key: str | None = None,
) -> None:
    keys = valid_api_keys()
    if not keys:
        return
    provided = x_api_key or api_key
    if not provided or provided not in keys:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
```

Add a test in `tests/test_auth.py`: `test_locked_mode_accepts_query_param_key` (GET with `?api_key=secret-1`).

- [ ] **Step 4: Type-check + build + manual verification**

Run: `cd frontend && npx tsc -b && npm run build` (succeeds). Manual: with backend `APP_API_KEYS` unset (open mode), the app still works (no key needed). With it set, requests without the key 401 and bounce to login.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts backend/app/api/deps.py backend/tests/test_auth.py
git commit -m "feat(frontend): inject X-API-Key on all requests, 401 → login, query-param for downloads"
```

---

## Task 4: API-Key login flow

**Files:**
- Modify: `frontend/src/lib/state.tsx` (key storage), `frontend/src/pages/LoginPage.tsx` (form), `frontend/src/App.tsx` (validate)

**Interfaces:**
- Consumes: `setApiKey`/`getApiKey`/`clearApiKey` (Task 3), `listFieldDefinitions` (probe).
- Produces: a single-field API-key login that validates against the backend before entering the app.

- [ ] **Step 1: Update login to store the key**

In `frontend/src/lib/state.tsx`, the existing `LOGIN` action stores `username` to `rixin_contract_auth_user`. Keep that for the display label, but the key is stored separately via `setApiKey` (Task 3) called from the login handler. No state change needed here beyond ensuring `LOGOUT` also clears the API key — update the `LOGOUT` case:

```ts
case "LOGOUT":
  window.localStorage.removeItem(AUTH_STORAGE_KEY);
  clearApiKey();  // import from lib/api
  return { ...state, currentUser: null, isSidebarExpanded: false };
```

- [ ] **Step 2: Change LoginPage to an API-key form**

In `frontend/src/pages/LoginPage.tsx`, replace the username/password form with a single API-key field. Change the props interface and handler:

```tsx
import { useState, type FormEvent } from "react";
import { setApiKey } from "../lib/api";

interface LoginPageProps {
  onLogin: (apiKey: string) => Promise<boolean>;
}

export function LoginPage({ onLogin }: LoginPageProps) {
  const [apiKey, setApiKeyState] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const ok = await onLogin(apiKey.trim());
    if (!ok) setError("API Key 无效，请检查后重试。");
    setBusy(false);
  }

  return (
    <form className="login-form" onSubmit={handleSubmit}>
      {/* keep the existing layout/className structure, replace the two inputs with one: */}
      <label className="field-row">
        <span>API Key</span>
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKeyState(e.target.value)}
          autoFocus
          autoComplete="off"
          disabled={busy}
        />
      </label>
      <button className="primary-action login-action" type="submit" disabled={busy || !apiKey.trim()}>
        {busy ? "验证中…" : "登录系统"}
      </button>
      {error && <p className="login-error">{error}</p>}
    </form>
  );
}
```

(Preserve the existing page chrome/background/className wrappers around the form — only swap the fields and make the handler async.)

- [ ] **Step 3: Validate the key in App.tsx**

In `frontend/src/App.tsx`, replace `handleLogin` (~:18-24):

```tsx
async function handleLogin(apiKey: string): Promise<boolean> {
  setApiKey(apiKey);  // store before the probe so the request carries it
  try {
    await listFieldDefinitions();  // authenticated probe
    dispatch({ type: "LOGIN", username: "API 用户" });
    return true;
  } catch {
    clearApiKey();
    return false;
  }
}
```

(Import `setApiKey`, `clearApiKey`, `listFieldDefinitions` from `lib/api`.) The auth gate (`if (!currentUser) return <LoginPage .../>`) stays; `LoginPage` now passes the async `handleLogin`.

- [ ] **Step 4: Type-check + build + manual verification**

Run: `cd frontend && npx tsc -b && npm run build` (succeeds). Manual end-to-end:
- Backend `APP_API_KEYS=mykey`: login page rejects wrong key ("API Key 无效"), accepts `mykey` → app loads, requests succeed.
- Logout (sidebar) → key cleared → login page returns.
- With `APP_API_KEYS` unset (open mode): any/empty key still logs in (probe succeeds because open mode).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/state.tsx frontend/src/pages/LoginPage.tsx frontend/src/App.tsx
git commit -m "feat(frontend): API-key login validated against backend, logout clears key"
```

---

## Self-Review

**1. Spec coverage (item ⑧):**
- API-Key gate on `/api/v1`, `/health` open: Task 1. ✓
- Replace hardcoded `admin/123456`: Tasks 3-4. ✓
- Frontend stores key, injects header, 401→login: Task 3. ✓
- CORS tightening: Task 1 Step 6. ✓
- No JWT/multi-tenant/RBAC/limit: respected (none added). ✓
- Open-when-unset keeps existing tests green: Task 1 Step 8. ✓

**2. Placeholder scan:** Code is complete. The LoginPage Step 2 says "preserve existing page chrome" — the implementer keeps the existing wrapper markup; only fields/handler swap (honest pointer, logic complete).

**3. Type consistency:** `verify_api_key` signature extended in Task 3 Step 3 (adds `api_key` query param) — the Task 1 tests + new query-param test cover both. `getApiKey/setApiKey/clearApiKey` defined Task 3, consumed Task 4. `handleLogin` async signature matches `LoginPageProps.onLogin`.

No gaps for item ⑧.

---

## Execution Handoff

Plan complete. Execute via superpowers:subagent-driven-development. Backend tasks (1) are pytest-TDD; frontend tasks (3, 4) use `tsc -b` + manual verification. This is the final Phase 1 plan — after it lands, Phase 1 (all 8 items) is complete.
