# Feedly OAuth Refresh Token Implementation Plan

## Problem
Feedly access tokens expire, causing silent sync failures. Currently there's no refresh mechanism.

## Solution Overview
Add automatic token refresh by:
1. Storing OAuth credentials (refresh_token, client_id, client_secret) in the database
2. Creating a token manager that refreshes tokens automatically on 401 or before expiry
3. Maintaining backward compatibility with existing `LINKBLOG_FEEDLY_TOKEN` env var

## Files to Modify

### 1. `linkblog/db.py` - Add token storage
Add `FeedlyTokenRepository` class with schema:
```sql
CREATE TABLE IF NOT EXISTS feedly_tokens (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- Singleton
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    client_id TEXT NOT NULL,
    client_secret TEXT NOT NULL,
    expires_at TEXT,
    updated_at TEXT NOT NULL
);
```

Methods: `get_token()`, `save_token()`, `update_access_token()`

### 2. `linkblog/feedly_auth.py` (new) - Token manager
Create `FeedlyTokenManager` class:
- `get_valid_token()` - Returns valid token, refreshing if needed
- `refresh_token()` - POST to `https://cloud.feedly.com/v3/auth/token` with `grant_type=refresh_token`
- Auto-refresh 5 minutes before expiry
- Lock to prevent concurrent refresh attempts

### 3. `linkblog/sources/feedly.py` - Use token manager
- Accept optional `db` parameter in `__init__`
- Add `_get_access_token()` method that uses token manager
- Add retry logic on 401: refresh token and retry once
- Fall back to `LINKBLOG_FEEDLY_TOKEN` env var if no DB credentials

### 4. `linkblog/cli.py` - Add credential commands
- `feedly-auth` - Store OAuth credentials interactively
- `feedly-status` - Show token status and expiration

### 5. `linkblog/scheduler.py` - Pass DB to adapter
Update `_sync_all_sources()` to pass `db` to `FeedlyAdapter`

### 6. `linkblog/recommendations.py` - Use token manager
Update `FeedlyFeedSearch` to use token manager for refresh support

## New Environment Variables (optional, for bootstrap)
- `LINKBLOG_FEEDLY_CLIENT_ID`
- `LINKBLOG_FEEDLY_CLIENT_SECRET`
- `LINKBLOG_FEEDLY_REFRESH_TOKEN`

## Migration Path
1. Existing `LINKBLOG_FEEDLY_TOKEN` continues to work (backward compatible)
2. Run `uv run linkblog feedly-auth` to store refresh credentials
3. Or set new env vars and credentials auto-migrate to DB on first sync

## How to Obtain Feedly Credentials
1. Go to https://feedly.com/i/team/api (Feedly developer page)
2. Generate a developer access token
3. Copy the client_id and client_secret provided for token renewal
4. The refresh_token is provided when you generate the developer token

## Verification
```bash
# Set up credentials
uv run linkblog feedly-auth

# Check status
uv run linkblog feedly-status

# Test sync
uv run linkblog sync --source feedly

# Check logs for refresh events
fly logs | grep -i "feedly\|refresh\|token"
```
