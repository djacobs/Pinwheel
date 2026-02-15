# LinkBlog: Link Aggregation & Syndication System

## Overview

A local-first link aggregation system that:
1. Syncs favorited/saved links from multiple sources (BlueSky, Feedly, Raindrop.io)
2. Provides a lightweight web UI for viewing and annotating links
3. Generates both JSON Feed and RSS/Atom output feeds
4. Supports direct link posting

## Tech Stack (per Skeleton preferences)

- **Python 3.12+** with FastAPI
- **SQLite** for local storage
- **uv** for package management
- **Jinja2** for simple web UI templates
- **HTMX** for lightweight interactivity (no heavy JS framework)

## Architecture

```
linkblog/
├── pyproject.toml           # uv/pip config
├── config.yaml              # Source configuration
├── linkblog/
│   ├── __init__.py
│   ├── main.py              # FastAPI app + CLI entry
│   ├── cli.py               # CLI commands (sync, serve)
│   ├── db.py                # SQLite setup + repository
│   ├── models.py            # Pydantic models
│   ├── sources/             # Source adapters
│   │   ├── __init__.py
│   │   ├── base.py          # Abstract base class
│   │   ├── bluesky.py       # BlueSky adapter
│   │   ├── feedly.py        # Feedly adapter
│   │   └── raindrop.py      # Raindrop.io adapter
│   ├── feeds/               # Feed generators
│   │   ├── __init__.py
│   │   ├── json_feed.py     # JSON Feed 1.1
│   │   └── rss.py           # RSS 2.0 / Atom
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── links.py         # Link CRUD API
│   │   ├── feeds.py         # Feed output endpoints
│   │   └── ui.py            # Web UI routes
│   └── templates/           # Jinja2 templates
│       ├── base.html
│       ├── links.html       # List view
│       ├── link_form.html   # Add/edit form
│       └── partials/        # HTMX partials
├── static/
│   └── style.css            # Minimal CSS
└── data/
    └── linkblog.db          # SQLite database
```

## Database Schema

```sql
CREATE TABLE links (
    id TEXT PRIMARY KEY,           -- UUID
    url TEXT NOT NULL UNIQUE,
    title TEXT,
    description TEXT,              -- Original description from source
    comment TEXT,                  -- User's annotation
    source TEXT NOT NULL,          -- 'bluesky', 'feedly', 'raindrop', 'manual'
    source_id TEXT,                -- ID in source system
    author TEXT,                   -- Original author (if applicable)
    tags TEXT,                     -- JSON array
    created_at TEXT NOT NULL,      -- ISO timestamp
    synced_at TEXT,                -- When we pulled it
    published_at TEXT,             -- When to show in feed (null = draft)
    metadata TEXT                  -- JSON blob for source-specific data
);

CREATE INDEX idx_links_published ON links(published_at);
CREATE INDEX idx_links_source ON links(source);
```

## Source Adapters

### BlueSky
- Uses AT Protocol API
- Fetches actor's likes (favorites)
- Filters for posts containing links
- Config: `handle`, `app_password` (or session)

### Feedly
- Uses Feedly API v3
- Fetches starred articles from user's account
- Config: `access_token`, `refresh_token`

### Raindrop.io
- Uses Raindrop API
- Fetches raindrops from specified collection
- Config: `access_token`, `collection_id`

## CLI Commands

```bash
# Sync all configured sources
linkblog sync

# Sync specific source
linkblog sync --source bluesky

# Start local web server
linkblog serve

# Generate feeds to files (for static hosting)
linkblog generate --output ./public

# Add link from CLI
linkblog add "https://example.com" --title "Example" --comment "Great article"

# List recent links
linkblog list

# Add comment to a link
linkblog comment <link-id> "This is my annotation"
```

## Web UI Routes

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | List all links (filterable) |
| `/links/new` | GET | Form to add link manually |
| `/links/{id}` | GET | View/edit single link |
| `/links` | POST | Create new link |
| `/links/{id}` | PUT | Update link (comment, publish) |
| `/links/{id}` | DELETE | Delete link |
| `/feed.json` | GET | JSON Feed output |
| `/feed.xml` | GET | RSS/Atom output |

## Configuration (config.yaml)

```yaml
database:
  path: ./data/linkblog.db

server:
  host: 127.0.0.1
  port: 8080
  auth:
    enabled: false        # Set to true to require password
    # password from environment: LINKBLOG_PASSWORD

sources:
  bluesky:
    enabled: true
    handle: yourhandle.bsky.social
    # app_password from environment: LINKBLOG_BLUESKY_PASSWORD

  feedly:
    enabled: true
    # access_token from environment: LINKBLOG_FEEDLY_TOKEN

  raindrop:
    enabled: true
    collection_id: 12345  # or collection name
    # access_token from environment: LINKBLOG_RAINDROP_TOKEN

feed:
  title: "My Link Blog"
  description: "Interesting links I've found"
  author:
    name: "Your Name"
    url: "https://yoursite.com"
  base_url: "https://links.yoursite.com"  # For feed URLs
```

## Behavior

- **Synced links auto-publish**: Links pulled from sources are immediately visible in feeds
- **Manual links**: Can be saved as drafts or published immediately
- **Comments**: Added via web UI or CLI, included in feed output

## Implementation Order

### Phase 1: Foundation
1. Initialize project with uv
2. Create SQLite database + repository layer
3. Create Pydantic models
4. Basic FastAPI app structure

### Phase 2: Web UI
1. Create Jinja2 templates with HTMX
2. Implement link CRUD routes
3. Manual link posting form
4. Comment editing interface

### Phase 3: Source Adapters
1. Base adapter class with common interface
2. BlueSky adapter (AT Protocol)
3. Raindrop.io adapter
4. Feedly adapter

### Phase 4: Feed Generation
1. JSON Feed 1.1 generator
2. RSS 2.0 generator

### Phase 5: CLI
1. Click-based CLI
2. `sync` command
3. `serve` command
4. `generate` command

## Verification

1. Run `linkblog serve` and visit `http://localhost:8080`
2. Add a link manually, add a comment, verify it appears
3. Configure at least one source with credentials
4. Run `linkblog sync` and verify links are imported
5. Check `/feed.json` and `/feed.xml` for valid output
6. Validate feeds with https://validator.w3.org/feed/

## Dependencies

```toml
[project]
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "jinja2>=3.1",
    "httpx>=0.28",        # For API calls
    "click>=8.1",         # CLI
    "pydantic>=2.10",
    "pydantic-settings>=2.7",
    "python-multipart>=0.0.20",  # Form handling
    "feedgen>=1.0",       # RSS/Atom generation
]
```
