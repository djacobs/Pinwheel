# Plan: Wire CLI Config into LLM Providers + Add API Key Commands

## Context

The CLI (`tk`) and GUI (`TKEditor`) share TKCore but their config systems are disconnected. `tk council` creates a `ProviderManager()` that reads from Keychain + UserDefaults, completely ignoring `CLIConfig` settings in `~/.config/tk/config.json`. There's also no way to set API keys from the CLI — the error message references a nonexistent `tk providers --setup` command. Users must set keys in the GUI first.

This plan wires CLIConfig into the provider system and adds API key management commands so the CLI is self-sufficient.

---

## Changes

### 1. New: `tk config set-api-key <provider> <key>` command
- Validates provider is "anthropic" or "openai"
- Stores key in Keychain via existing `KeychainService` convenience methods
- Prints confirmation with shell history warning

### 2. New: `tk config delete-api-key <provider>` command
- Removes API key from Keychain for the given provider

### 3. Wire CLIConfig into `tk council`
- After creating `ProviderManager()`, load `CLIConfig` and apply overrides
- `defaultProvider` → `ProviderManager.select(providerId:)`
- `defaultModel` → `ProviderManager.select(modelId:)`
- `ollamaHost` → new `ProviderManager.replaceOllamaProvider(baseURL:)` method

### 4. Fix error message
- `CLIError.noProviderConfigured` → reference `tk config set-api-key`

### 5. Enhance `tk config show` with API key status
- Show "configured" / "(not set)" for each key-based provider (never show actual key)

### 6. Document credential flow in CLAUDE.md

---

## Files (execution order — tests first)

### Tests

| # | File | Action | What |
|---|------|--------|------|
| 1 | `Tests/TKCLITests/APIKeyCommandTests.swift` | NEW | ~15 tests: set/delete API key, provider validation, empty key, round-trip |
| 2 | `Tests/TKCLITests/CLIProviderWiringTests.swift` | NEW | ~12 tests: CLIConfig overrides provider/model/ollamaHost, no-op when unset |
| 3 | `Tests/TKCLITests/ErrorHandlingTests.swift` | MODIFY | 1 test: error message references `tk config set-api-key` |

### Implementation

| # | File | Action | What |
|---|------|--------|------|
| 4 | `Sources/Core/CLI/CLIAPIKeyManager.swift` | NEW | `setAPIKey(_:for:)`, `deleteAPIKey(for:)`, `hasAPIKey(for:)` — wraps KeychainService with validation |
| 5 | `Sources/Core/LLM/ProviderManager.swift` | MODIFY | Add `replaceOllamaProvider(baseURL:)` (~8 lines) |
| 6 | `Sources/Core/CLI/CLIProviderHelper.swift` | NEW | `applyOverrides(from: CLIConfig, to: ProviderManager)` — reads config, calls `select()` and `replaceOllamaProvider()` |
| 7 | `Sources/Core/CLI/CLIError.swift` | MODIFY | Fix `noProviderConfigured` message (1 line) |
| 8 | `Sources/Core/CLI/CLIFormatter.swift` | MODIFY | Add API key status to `formatConfigDisplay()` (~8 lines) |
| 9 | `Sources/CLI/TKCommand.swift` | MODIFY | Add `Config.SetAPIKey` + `Config.DeleteAPIKey` subcommands; wire `CLIProviderHelper.applyOverrides()` into `Council.run()` |
| 10 | `CLAUDE.md` | MODIFY | Add "Credential Flow (CLI)" section under Common Patterns |

---

## Design decisions

**Why Option C (helper in CLI layer)?** `ProviderManager` lives in TKCore shared with GUI. Adding CLI-specific init params leaks concerns. Instead, a thin `CLIProviderHelper.applyOverrides()` function reads CLIConfig and calls existing `ProviderManager` methods. The only TKCore addition is `replaceOllamaProvider(baseURL:)` which is generally useful.

**Why not worry about UserDefaults leakage?** `ProviderManager.select()` writes to UserDefaults, but the CLI binary (`tk`) has a different bundle ID than the GUI (`TKEditor`), so they use separate UserDefaults domains. Writes from CLI don't affect GUI.

**Why argument-based key input (not stdin)?** Simpler for v1. Shell history warning printed. Future: detect `key == "-"` for stdin.

**Reused utilities:**
- `KeychainService.setAnthropicAPIKey()` / `setOpenAIAPIKey()` — existing in `Sources/Core/Services/KeychainService.swift`
- `ProviderManager.select(providerId:modelId:)` — existing in `Sources/Core/LLM/ProviderManager.swift:115`
- `OllamaProvider(baseURL:)` — existing in `Sources/Core/LLM/OllamaProvider.swift:23`
- `CLIConfigManager(configDirectory:)` — existing in `Sources/Core/CLI/CLIConfig.swift:46`
- Test patterns: temp dirs + unique Keychain service IDs from `Tests/TKCLITests/ConfigTests.swift`

---

## Verification

```bash
swift test                           # All 958+ existing tests pass + ~28 new tests
swift build                          # Both targets build
.build/debug/tk config set-api-key anthropic sk-test-key  # Key saved
.build/debug/tk config show          # Shows "anthropic: configured"
.build/debug/tk config set defaultProvider openai
.build/debug/tk config show          # Shows defaultProvider: openai
.build/debug/tk council test.md      # Uses openai provider (from CLIConfig override)
.build/debug/tk config delete-api-key anthropic  # Key removed
```
