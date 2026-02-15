# Newsletter Ring Implementation Plan

## Overview

Build the MVP (Phase 1) of Newsletter Ring: a cross-promotion network for newsletters with embeddable widgets.

## Project Structure

```
EmailRing/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Settings/configuration
│   ├── database.py          # SQLAlchemy async setup
│   ├── models/
│   │   ├── __init__.py
│   │   ├── newsletter.py    # Newsletter model
│   │   └── recommendation.py # Recommendation model
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── newsletter.py    # Pydantic schemas
│   │   └── recommendation.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── newsletter.py    # Newsletter business logic
│   │   ├── recommendation.py # Recommendation business logic
│   │   └── embed.py         # Widget/snippet generation
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── api.py           # REST API routes
│   │   ├── embed.py         # Widget endpoints
│   │   └── pages.py         # HTML pages (landing, management)
│   ├── templates/
│   │   ├── base.html
│   │   ├── landing.html     # Registration form
│   │   ├── manage.html      # Newsletter management page
│   │   └── partials/
│   │       └── email_fallback.html
│   └── static/
│       └── css/
│           └── style.css
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 001_initial_schema.py
├── tests/
│   ├── conftest.py
│   ├── test_api.py
│   └── test_services.py
├── alembic.ini
├── pyproject.toml
├── Dockerfile
├── .env.example
└── .gitignore
```

## Implementation Steps

### Step 1: Project Setup
- Create `pyproject.toml` with dependencies (FastAPI, SQLAlchemy, uvicorn, Jinja2, alembic, pytest)
- Create `.env.example` and `.gitignore`
- Create `app/config.py` with Pydantic settings

### Step 2: Database Layer
- Create `app/database.py` with async SQLAlchemy setup
- Create `app/models/newsletter.py` with Newsletter model
- Create `app/models/recommendation.py` with Recommendation model
- Set up Alembic with `alembic.ini` and initial migration

### Step 3: Schemas
- Create Pydantic schemas for Newsletter (create, read, update)
- Create Pydantic schemas for Recommendation (create, read, update)

### Step 4: Services
- `app/services/newsletter.py` - CRUD operations, URL normalization
- `app/services/recommendation.py` - CRUD with max-3 enforcement
- `app/services/embed.py` - JS widget and HTML fallback generation

### Step 5: API Routes
- POST `/api/v1/newsletters` - Register newsletter
- GET `/api/v1/newsletters/{id}` - Get newsletter details
- POST `/api/v1/newsletters/{id}/recommendations` - Add recommendation
- PATCH `/api/v1/recommendations/{id}` - Update recommendation
- DELETE `/api/v1/recommendations/{id}` - Remove recommendation
- GET `/api/v1/newsletters/{id}/recommendations` - Get recommendations (JSON)

### Step 6: Embed Routes
- GET `/embed/{id}.js` - JavaScript widget
- GET `/embed/{id}/fallback` - Static HTML fallback

### Step 7: Web Pages
- GET `/` - Landing page with registration form
- GET `/manage/{id}` - Newsletter management page

### Step 8: Templates
- Base template with minimal CSS
- Landing page with newsletter registration form
- Management page with recommendation CRUD forms
- Copy-to-clipboard for embed snippet

### Step 9: Deployment Files
- `Dockerfile` for containerized deployment
- Update `fly.toml.example` if needed

### Step 10: Tests
- API endpoint tests
- Service layer tests
- Embed generation tests

## Key Dependencies

```toml
[project]
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "sqlalchemy[asyncio]>=2.0.0",
    "aiosqlite>=0.20.0",
    "alembic>=1.14.0",
    "pydantic>=2.10.0",
    "pydantic-settings>=2.6.0",
    "jinja2>=3.1.0",
    "python-multipart>=0.0.12",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "httpx>=0.28.0",
    "ruff>=0.8.0",
]
```

## Verification

1. Run `uv sync` to install dependencies
2. Run `uv run alembic upgrade head` to create database
3. Run `uv run uvicorn app.main:app --reload --port 8000`
4. Test registration at `http://localhost:8000/`
5. Test management page after registration
6. Test embed endpoints
7. Run `uv run pytest` for automated tests

## Notes

- P0 security: Management URLs use unguessable UUIDs (no auth)
- Max 3 recommendations per newsletter enforced at service layer
- Widget JS < 5KB gzipped target
- Email fallback uses inline CSS only
