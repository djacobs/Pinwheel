# Proposal Pipeline Performance Hardening

## Context
Proposals are the core gameplay feature and are timing out in production. The pipeline currently runs classifier (Haiku, 15s timeout) + interpreter v2 (Sonnet, 30s timeout) in parallel, but worst-case latency is 61s (30s + 1s sleep + 30s retry). The v2 interpreter has an oversized `max_tokens=2000` and creates a new HTTP client per call.

## Changes

### 1. Reduce `max_tokens` on interpreter v2: 2000 → 1000
**File:** `src/pinwheel/ai/interpreter.py:587`

Even a complex multi-effect JSON with all fields populated is ~800 tokens. Lowering `max_tokens` directly reduces generation time since the model can stop earlier.

### 2. Shared client singleton for connection reuse
**File:** `src/pinwheel/ai/interpreter.py` + `src/pinwheel/ai/classifier.py`

Create module-level `_get_client(api_key)` functions that reuse a cached `AsyncAnthropic` instance instead of constructing a new one (and new httpx connection pool) per call. The timeout is set on the client, so the singleton carries the timeout config too.

### 3. Tighten timeouts
**Files:** `src/pinwheel/ai/interpreter.py`, `src/pinwheel/ai/classifier.py`

- Interpreter: 30s → 20s (both v1 and v2)
- Classifier: 15s → 10s
- Connect timeout stays proportional: 10s → 5s for interpreter, 5s → 3s for classifier

### 4. Remove retry sleep
**File:** `src/pinwheel/ai/interpreter.py`

Remove the `await asyncio.sleep(1)` between retries. If the API timed out at 20s, waiting another second adds latency without helping.

### Summary of worst-case latency improvement
- Before: max(15, 30) + 1 + max(15, 30) = 61s
- After: max(10, 20) + max(10, 20) = 40s
- Common case (no retry): max(10, 20) = 20s (vs 30s before)

## Verification
1. `uv run pytest -x -q` — all tests pass
2. `uv run ruff check src/ tests/` — lint clean
