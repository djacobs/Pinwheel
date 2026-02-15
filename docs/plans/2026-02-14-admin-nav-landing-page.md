# Plan: Admin Nav + Landing Page

## Context

Admin nav links (Evals, Season) are currently only visible in dev/staging environments via an env check in `base.html`. They should instead be visible to authenticated admin users (matched by `PINWHEEL_ADMIN_DISCORD_ID`) in **all** environments, including production. The user wants a single "Admin" nav item that leads to a landing page hub for all admin features.

## Changes

### 1. Add `is_admin` to auth context
**File:** `src/pinwheel/api/pages.py`

Add `is_admin` to `_auth_context()` so every template can conditionally render admin UI:
```python
def _auth_context(request, current_user):
    settings = request.app.state.settings
    admin_id = settings.pinwheel_admin_discord_id
    is_admin = (
        current_user is not None
        and bool(admin_id)
        and current_user.discord_id == admin_id
    )
    return {
        "current_user": current_user,
        "oauth_enabled": ...,
        "pinwheel_env": ...,
        "is_admin": is_admin,
        ...
    }
```

### 2. Update nav in base.html
**File:** `templates/base.html`

Replace the env-gated admin links with a single "Admin" link gated on `is_admin`:
```html
<!-- Before (env-gated, multiple links) -->
{% if pinwheel_env in ['development', 'staging'] %}
  <a href="/admin/evals">Evals</a>
  <a href="/admin/season">Season</a>
{% endif %}

<!-- After (auth-gated, single link) -->
{% if is_admin %}
  <a href="/admin">Admin</a>
{% endif %}
```

### 3. Create admin landing page route
**File:** `src/pinwheel/api/pages.py`

Add a `/admin` route following the existing page pattern. Auth-gated: redirects to login if not authenticated, returns 403 if not admin.

### 4. Create admin landing page template
**File:** `templates/pages/admin.html`

A hub page with cards linking to:
- **Season** (`/admin/season`) — Season management, config, history
- **Governors** (`/admin/roster`) — Player roster, tokens, proposals
- **Evals** (`/admin/evals`) — Safety metrics, GQI, flags

Styled consistently with the existing retro/bold aesthetic.

### 5. Register any new router (if needed)
**File:** `src/pinwheel/main.py`

The `/admin` route goes in `pages.py` (existing router), so no new registration needed.

## Files to modify
- `src/pinwheel/api/pages.py` — Add `is_admin` to auth context + `/admin` route
- `templates/base.html` — Replace env-gated admin nav with auth-gated single link
- `templates/pages/admin.html` — New admin landing page template (new file)

## Verification
1. `uv run pytest -x -q` — all tests pass
2. Start dev server, log in as admin → see "Admin" nav link
3. Click "Admin" → see landing page with links to Season, Governors, Evals
4. Log in as non-admin → no "Admin" nav link; direct URL returns 403
5. Not logged in → redirects to login
