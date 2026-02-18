# Plan: Local Mode Safe by Default

## Context

TK's server has a `TK_LOCAL_MODE=1` default that silently bypasses all authentication. This is fine when bound to `127.0.0.1` (the default), but if the server is bound to `0.0.0.0` — or deployed to Fly.io — every protected route is wide open. A deep audit found:

- **Critical:** `GET /files/read` accepts any absolute path with no auth — `GET /files/read?path=/etc/passwd` works today
- **Critical:** `client.ts:172` logs the full LLM API key to the browser console on every request
- **High:** 40+ routes have no auth dependency (fine for localhost, dangerous when exposed)
- **High:** `auth_required` config field exists but is completely disconnected from enforcement
- **High:** `fly.toml` + Dockerfile don't set `TK_LOCAL_MODE=0` — production inherits the permissive default
- **Medium:** `service.py:77-78` logs verification tokens at INFO level with no production guard

This plan fixes all of these while keeping localhost usage completely frictionless.

---

## Phase 1: Tests First (~35 new tests)

### 1A — `core/tests/test_local_guard.py` (new, ~10 tests)

Unit tests for the bind-address detection module (created in Phase 2):

```
test_127_0_0_1_is_loopback
test_localhost_is_loopback
test_ipv6_loopback_is_loopback
test_empty_string_is_loopback
test_0_0_0_0_is_not_loopback
test_lan_ip_is_not_loopback
test_public_ip_is_not_loopback
test_env_TK_LOCAL_MODE_0_overrides_loopback
test_env_TK_AUTH_REQUIRED_true_overrides_loopback
test_is_local_mode_defaults_true_before_configure
test_configure_local_mode_sets_state
```

### 1B — `core/tests/test_auth_enforcement.py` (new, ~20 tests)

Integration tests using `TestClient` with local mode toggled:

```
# Public routes — accessible regardless of mode
test_health_always_accessible
test_llm_providers_always_accessible
test_roles_list_always_accessible
test_story_types_list_always_accessible

# Protected routes — require auth when not local
test_suggestions_requires_auth_when_exposed
test_suggestions_open_in_local_mode
test_roles_create_requires_auth_when_exposed
test_roles_ask_requires_auth_when_exposed
test_upload_requires_auth_when_exposed
test_context_folders_requires_auth_when_exposed
test_context_summary_requires_auth_when_exposed
test_bookmarks_requires_auth_when_exposed
test_index_path_requires_auth_when_exposed
test_files_read_requires_auth_when_exposed
test_files_read_rejects_paths_outside_projects
test_files_read_denies_etc_passwd
test_files_read_denies_ssh_keys
test_files_read_denies_dotenv
test_files_read_denies_var_log
test_files_read_allows_project_scoped_path

# Structural — verify no route was accidentally left unprotected
test_all_non_public_routes_have_auth_dependency

# Regression
test_local_mode_full_roundtrip_no_token_needed
test_options_preflight_never_blocked
```

### 1C — `web/src/lib/api/__tests__/client-logging.test.ts` (new, ~5 tests)

```
test_llm_config_log_does_not_contain_api_key
test_llm_config_log_contains_provider_and_model
test_no_console_log_contains_sk_ant_prefix
test_no_console_log_contains_sk_prefix
test_readFileDirect_removed_or_absent
```

### 1D — Update `core/tests/conftest.py`

Add fixtures for toggling local mode cleanly (replaces monkey-patching in `test_projects.py`):

```python
@pytest.fixture
def local_mode_on(monkeypatch):
    from tk.auth import local_guard
    monkeypatch.setattr(local_guard, "_is_local", True)

@pytest.fixture
def local_mode_off(monkeypatch):
    from tk.auth import local_guard
    monkeypatch.setattr(local_guard, "_is_local", False)
```

Update `test_projects.py:129-136` to use the `local_mode_off` fixture instead of direct monkey-patching.

---

## Phase 2: Bind-Address-Aware Local Guard

### 2A — `core/tk/auth/local_guard.py` (new)

Single-responsibility module that determines whether the server is local-only:

```python
_is_local: bool | None = None  # None = not yet configured

def _is_loopback(host: str) -> bool:
    """127.0.0.1, ::1, localhost, empty string → True. Everything else → False."""

def configure_local_mode(host: str) -> None:
    """Called once at startup. Rules:
    1. TK_LOCAL_MODE=0 explicitly → auth enforced
    2. TK_AUTH_REQUIRED=true → auth enforced
    3. Binding to loopback → local mode ON
    4. Binding to non-loopback → local mode OFF + warning
    """

def is_local_mode() -> bool:
    """Dynamic check. Returns True before configure() is called (safe default)."""
```

### 2B — Modify `core/tk/auth/dependencies.py`

- Delete line 24: `LOCAL_MODE = os.environ.get("TK_LOCAL_MODE", "1") == "1"`
- Add: `from tk.auth.local_guard import is_local_mode`
- Replace all 4 references to `LOCAL_MODE` (lines 72, 111, 125, 137) with `is_local_mode()`

### 2C — Call `configure_local_mode(host)` at startup

**`core/tk/cli/__init__.py`** — in `serve()` before `uvicorn.run()`:
```python
from tk.auth.local_guard import configure_local_mode
configure_local_mode(host)
```

**`core/tk/api/server.py`** — in `create_app()`, read host from env for non-CLI startup (Dockerfile):
```python
from tk.auth.local_guard import configure_local_mode, is_local_mode
host = os.environ.get("TK_HOST", "127.0.0.1")
configure_local_mode(host)
```

### 2D — Wire `auth_required` config field

The field exists in `schema.py:48` and env var mapping exists in `loader.py:68` (`TK_AUTH_REQUIRED`). `configure_local_mode()` reads `TK_AUTH_REQUIRED` directly from `os.environ` (Phase 2A above), which means the env var now has a real effect. The config-file path (`auth_required: true` in config.json) needs one bridge:

In `create_app()`, after loading config:
```python
config = get_config()
if config.auth_required and not os.environ.get("TK_AUTH_REQUIRED"):
    os.environ["TK_AUTH_REQUIRED"] = "true"
```

This ensures both the config file and the env var paths feed into `configure_local_mode()`.

---

## Phase 3: Auth Enforcement

### 3A — Auth middleware (defense-in-depth)

Add to `create_app()` in `core/tk/api/server.py`, after CORS middleware:

```python
PUBLIC_PATHS = frozenset({
    "/health", "/docs", "/openapi.json",
    "/llm/providers", "/llm/health", "/llm/models",
    "/roles", "/roles/enums",
    "/story-types", "/intent/types",
    "/auth/register", "/auth/login", "/auth/verify-email", "/auth/refresh",
})

@app.middleware("http")
async def auth_guard(request, call_next):
    if is_local_mode():
        return await call_next(request)
    if request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    if path in PUBLIC_PATHS:
        return await call_next(request)
    # Allow GET on /story-types/{id} and /roles/{id} (not /roles/{id}/ask)
    if request.method == "GET" and (path.startswith("/story-types/") or
            (path.startswith("/roles/") and "/ask" not in path)):
        return await call_next(request)
    # Allow static assets in production
    if path.startswith("/_app/") or path.startswith("/static/"):
        return await call_next(request)
    # Require Bearer token
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse(status_code=401,
            content={"detail": "Not authenticated"},
            headers={"WWW-Authenticate": "Bearer"})
    return await call_next(request)
```

This catches any route that accidentally forgets `CurrentUser`.

### 3B — Add `CurrentUser` to all sensitive routes

Add `current_user: CurrentUser` parameter to these 29 route handlers in `server.py`. In local mode this resolves to `LOCAL_USER` silently — zero friction. In exposed mode this does full JWT validation.

| Route | Line |
|-------|------|
| `POST /suggestions` | ~742 |
| `POST /suggestions/batch` | ~806 |
| `POST /suggestions/quick` | ~890 |
| `POST /suggestions/apply` | ~910 |
| `POST /suggestions/review` | ~968 |
| `POST /suggestions/council` | ~1042 |
| `POST /roles` | ~517 |
| `DELETE /roles/{role_id}` | ~572 |
| `POST /roles/{role_id}/ask` | ~591 |
| `POST /story-types/assemble` | ~1115 |
| `POST /index/path` | ~1173 |
| `POST /index/refresh` | ~1188 |
| `POST /upload` | ~1618 |
| `POST /intent/detect` | ~1676 |
| `POST /bookmarks/sync` | ~1203 |
| `GET /bookmarks` | ~1220 |
| `GET /stats` | ~288 |
| `POST /llm/ollama/refresh` | ~441 |
| `GET /system/folder-picker` | ~2072 |
| `GET /context/summary` | ~1807 |
| `GET /context/stats` | ~1833 |
| `GET /context/folders` | ~1842 |
| `POST /context/folders` | ~1851 |
| `DELETE /context/folders` | ~1868 |
| `POST /context/folders/reindex` | ~1880 |
| `GET /context/files` | ~1893 |
| `POST /context/files/toggle` | ~1902 |
| `GET /context/progress` | ~1911 |
| `GET /context/attribution/{id}` | ~1920 |

### 3C — Restrict `/files/read` (auth-gate + path constraint + system path deny list)

The endpoint has 3 active frontend callers (`+page.ts:103`, `DraftStage.svelte:231`, `client.ts:822`), so we can't delete it outright. Instead, three layers of protection:

1. **Add `CurrentUser` dependency** — no anonymous access when exposed
2. **Deny list for system/sensitive paths** — reject before any filesystem access
3. **Project-root allowlist** — only paths within the user's own projects

```python
# Paths that should NEVER be readable, regardless of project membership
DENIED_PATH_PREFIXES = (
    "/etc", "/var", "/usr", "/sys", "/proc", "/dev",
    "/boot", "/sbin", "/bin", "/lib",
    "/private/etc", "/private/var",  # macOS equivalents
)

DENIED_PATH_PATTERNS = (
    "/.ssh/", "/.aws/", "/.gnupg/", "/.config/",
    "/.env", "/credentials", "/secrets",
    "/.git/config",  # can contain tokens
)

@app.get("/files/read")
async def read_file_direct(path: str, current_user: CurrentUser):
    file_path = Path(path).resolve()  # resolve symlinks
    path_str = str(file_path)

    # Layer 1: Deny system paths
    if any(path_str.startswith(p) for p in DENIED_PATH_PREFIXES):
        raise HTTPException(status_code=403, detail="Access denied: system path")
    if any(pattern in path_str for pattern in DENIED_PATH_PATTERNS):
        raise HTTPException(status_code=403, detail="Access denied: sensitive path")

    # Layer 2: Must be within a known project root
    allowed = any(
        path_str.startswith(str(Path(p.path).resolve()))
        for p in _projects.values()
        if p.user_id == current_user.id
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Path not within any project")

    # Layer 3: Verify resolved path didn't escape via symlink
    # (resolve() above handles this — if a symlink in the project
    # points outside, the resolved path won't match the project root)

    # ... existing read logic
```

This means:
- `/files/read?path=/etc/passwd` → 403 (system path)
- `/files/read?path=/Users/djacobs/.ssh/id_rsa` → 403 (sensitive pattern)
- `/files/read?path=/Users/djacobs/random/file.txt` → 403 (not in a project)
- `/files/read?path=/Users/djacobs/Documents/GitHub/TK/docs/file.md` → 200 (if TK folder is a project)
- Symlink inside project pointing to `/etc/shadow` → 403 (resolved path is `/etc/shadow`, caught by deny list)

---

## Phase 4: Logging Fixes

### 4A — `web/src/lib/api/client.ts:172`

Replace:
```typescript
console.log('[API] LLM config for', path, ':', llmConfig);
```
With:
```typescript
console.log('[API] LLM config for', path, ':',
    llmConfig ? `${llmConfig.provider}/${llmConfig.model}` : 'none');
```

### 4B — `core/tk/auth/service.py:77-78`

Replace:
```python
logger.info(f"Verification token for {email}: {verification_token}")
logger.info(f"Verify at: /auth/verify-email?token={verification_token}")
```
With:
```python
if os.environ.get("ENVIRONMENT") != "production":
    logger.debug(f"[DEV] Verification token for {email}: {verification_token}")
    logger.debug(f"[DEV] Verify at: /auth/verify-email?token={verification_token}")
else:
    logger.info(f"Verification email queued for {email}")
```

---

## Phase 5: Deployment Hardening

### 5A — `Dockerfile` (line 48)

Add after existing ENV lines:
```dockerfile
ENV TK_LOCAL_MODE=0
```

Belt-and-suspenders: the `--host 0.0.0.0` in CMD triggers auto-detection, but explicit is better.

### 5B — `fly.toml` [env] section

Add:
```toml
TK_LOCAL_MODE = "0"
```

### 5C — CLI warning for `--host 0.0.0.0`

In `core/tk/cli/__init__.py`, in `serve()` after arg parsing:
```python
if host != "127.0.0.1" and host != "localhost":
    console.print(
        "[bold yellow]Warning:[/bold yellow] Binding to non-loopback address "
        f"'{host}'. Auth will be enforced on protected routes."
    )
```

### 5D — CORS production origin

In `core/tk/api/server.py`, `create_app()`:
```python
origins = [ ... existing localhost origins ... ]
prod_origin = os.environ.get("TK_CORS_ORIGIN")
if prod_origin:
    origins.append(prod_origin)
```

---

## Files Modified (summary)

| Phase | Files | Type |
|-------|-------|------|
| 1A | `core/tests/test_local_guard.py` | New |
| 1B | `core/tests/test_auth_enforcement.py` | New |
| 1C | `web/src/lib/api/__tests__/client-logging.test.ts` | New |
| 1D | `core/tests/conftest.py`, `core/tests/test_projects.py` | Modify |
| 2A | `core/tk/auth/local_guard.py` | New |
| 2B | `core/tk/auth/dependencies.py` | Modify |
| 2C | `core/tk/cli/__init__.py`, `core/tk/api/server.py` | Modify |
| 2D | `core/tk/api/server.py` | Modify |
| 3A | `core/tk/api/server.py` | Modify |
| 3B | `core/tk/api/server.py` | Modify (29 routes) |
| 3C | `core/tk/api/server.py` | Modify |
| 4A | `web/src/lib/api/client.ts` | Modify |
| 4B | `core/tk/auth/service.py` | Modify |
| 5A | `Dockerfile` | Modify |
| 5B | `fly.toml` | Modify |
| 5C | `core/tk/cli/__init__.py` | Modify |
| 5D | `core/tk/api/server.py` | Modify |

**4 new files, 9 modified files.**

---

## Verification

After each phase:
```bash
cd /Users/djacobs/Documents/GitHub/TK/core && uv run pytest tests/ -v
cd /Users/djacobs/Documents/GitHub/TK/web && npx vitest run
```

End-to-end checks:
- **Phase 2:** `tk serve` on `127.0.0.1` → all routes work without token. `tk serve --host 0.0.0.0` → warning printed, `POST /suggestions` returns 401 without token
- **Phase 3:** `GET /files/read?path=/etc/passwd` → 403, `?path=~/.ssh/id_rsa` → 403, `?path=~/.env` → 403, project-scoped path → 200
- **Phase 4:** Open browser DevTools console, trigger a suggestion → no API key visible in logs
- **Phase 5:** `fly deploy` → `TK_LOCAL_MODE=0` in env, auth enforced
