---
title: "feat: Season Lifecycle State Machine"
type: feat
date: 2026-02-11
---

# Season Lifecycle State Machine

## Overview

A season progresses through defined phases. The state machine governs what's allowed in each phase, what triggers transitions, and what happens at each boundary.

## States

```
SETUP → REGULAR_SEASON → TIEBREAKER_CHECK → TIEBREAKERS → PLAYOFFS → CHAMPIONSHIP → OFFSEASON → (next season SETUP)
```

```python
class SeasonPhase(str, Enum):
    SETUP = "setup"                    # League seeded, schedule generated, not yet started
    REGULAR_SEASON = "regular_season"  # Rounds in progress, governance active
    TIEBREAKER_CHECK = "tiebreaker_check"  # Regular season complete, checking for ties
    TIEBREAKERS = "tiebreakers"        # Tiebreaker games + governance rounds
    PLAYOFFS = "playoffs"              # Semifinal and final series
    CHAMPIONSHIP = "championship"      # Finals complete, awards and narrative generation
    OFFSEASON = "offseason"            # Extended governance window between seasons
    COMPLETE = "complete"              # Season archived
```

## State Machine

```
┌─────────┐
│  SETUP  │
│         │──── admin starts season ────┐
└─────────┘                             │
                                        ▼
                              ┌──────────────────┐
                              │ REGULAR_SEASON    │◄──────────────┐
                              │                   │               │
                              │ Round N:          │  governance   │
                              │  simulate 4 games │  window       │
                              │  present results  │  (if interval │
                              │  mirrors          │   allows)     │
                              │                   ├───────────────┘
                              │ N < 21 → next round
                              │ N == 21 → check ties
                              └────────┬──────────┘
                                       │
                                       ▼
                              ┌──────────────────┐
                              │ TIEBREAKER_CHECK  │
                              │                   │
                              │ Compute standings │
                              │ Detect ties       │
                              │                   │
                              │ No ties → playoffs│
                              │ Ties → tiebreakers│
                              └──┬────────────┬───┘
                                 │            │
                    ┌────────────┘            │
                    ▼                         │
          ┌──────────────────┐               │
          │ TIEBREAKERS      │               │
          │                  │               │
          │ Extra governance │               │
          │ round            │               │
          │ Tiebreaker game  │               │
          │ Mirror           │               │
          │                  │               │
          │ Resolved → check │               │
          │ again (3+ teams  │               │
          │ may need multiple│               │
          │ rounds)          │               │
          └────────┬─────────┘               │
                   │                         │
                   ▼                         ▼
          ┌──────────────────────────────────┐
          │ PLAYOFFS                          │
          │                                   │
          │ Semifinal 1: #1 vs #4 (best-of-5)│
          │ Semifinal 2: #2 vs #3 (best-of-5)│
          │   governance window between games │
          │   series mirror after series ends │
          │                                   │
          │ Finals: winners (best-of-7)       │
          │   governance window between games │
          │   series mirror after series ends │
          └──────────┬────────────────────────┘
                     │
                     ▼
          ┌──────────────────┐
          │ CHAMPIONSHIP     │
          │                  │
          │ Season mirror    │
          │ Awards           │
          │ Stats compilation│
          │ Narrative        │
          └──────────┬───────┘
                     │
                     ▼
          ┌──────────────────┐
          │ OFFSEASON        │
          │                  │
          │ Extended gov     │
          │ window           │
          │ Carry-forward    │
          │ vote             │
          │ Roster changes   │
          │ Offseason mirror │
          └──────────┬───────┘
                     │
                     ▼
          ┌──────────────────┐
          │ COMPLETE         │
          │ Season archived  │
          └──────────────────┘
```

## Transition Logic

### SETUP → REGULAR_SEASON

**Trigger:** Admin command or API call (`POST /admin/seasons/{id}/start`).

**Preconditions:**
- All 8 teams created with 4 agents each
- Schedule generated (21 rounds)
- Starting ruleset configured
- Governance config set

**Actions:**
- Season status → `regular_season`
- Round counter → 1
- Publish `season.started` event

### REGULAR_SEASON: Round Cycle

Each round follows the heartbeat:

```python
async def run_round(season, round_number):
    # 1. Simulate
    results = simulate_round(season, round_number)
    store_results(results)

    # 2. Present (runs in background, 20-30 min)
    start_presentation(results)

    # 3. Simulation mirror
    generate_simulation_mirror(results)

    # 4. Governance (if interval allows)
    if round_number % season.config.governance_rounds_interval == 0:
        open_governance_window(season, round_number)
        # Window stays open for PINWHEEL_GOV_WINDOW seconds
        # On close: resolve votes, enact rules, governance mirror, private mirrors

    # 5. Advance
    if round_number < 21:
        season.current_round = round_number + 1
        # Schedule next round (on game clock cron)
    else:
        transition_to(SeasonPhase.TIEBREAKER_CHECK)
```

### REGULAR_SEASON → TIEBREAKER_CHECK

**Trigger:** Round 21 completes.

**Actions:**
- Compute final regular season standings
- Publish `season.regular_season_complete` event

### TIEBREAKER_CHECK → TIEBREAKERS or PLAYOFFS

```python
async def check_tiebreakers(season):
    standings = compute_standings(season)

    # Check for ties at playoff cutoff (top 4)
    ties = detect_ties(standings, cutoff=4)

    if not ties:
        # Clean seeding
        seed_playoffs(standings[:4])
        transition_to(SeasonPhase.PLAYOFFS)
    else:
        # Need tiebreaker games
        schedule_tiebreakers(ties)
        transition_to(SeasonPhase.TIEBREAKERS)
```

**Tie detection:**
- Two teams tied at the 4/5 boundary → 1 tiebreaker game
- Three teams tied → round-robin among tied teams
- Multiple ties possible (e.g., 3-way tie at #3-5 AND 2-way tie at #1-2)

### TIEBREAKERS

```python
async def run_tiebreaker(season, tied_teams):
    # 1. Extra governance round
    open_governance_window(season, round_number="TB")
    await close_governance_window()

    # 2. Tiebreaker games
    if len(tied_teams) == 2:
        # Single game, higher point diff gets home court
        result = simulate_tiebreaker(tied_teams[0], tied_teams[1], rules)
    else:
        # Mini round-robin among tied teams
        results = simulate_tiebreaker_round_robin(tied_teams, rules)

    # 3. Tiebreaker mirror
    generate_tiebreaker_mirror(results)

    # 4. Re-check — might need more tiebreakers
    new_standings = recompute_with_tiebreakers(season)
    remaining_ties = detect_ties(new_standings, cutoff=4)

    if not remaining_ties:
        seed_playoffs(new_standings[:4])
        transition_to(SeasonPhase.PLAYOFFS)
    else:
        schedule_tiebreakers(remaining_ties)
        # Stay in TIEBREAKERS phase
```

### PLAYOFFS

Playoffs are structured as two rounds of series.

```python
class PlayoffSeries(BaseModel):
    series_id: UUID
    round: Literal["semifinal", "final"]
    higher_seed: Team
    lower_seed: Team
    best_of: int  # 5 for semis, 7 for finals
    games: list[GameResult]
    higher_seed_wins: int
    lower_seed_wins: int
    status: Literal["scheduled", "active", "complete"]
    winner: Team | None

async def run_playoff_game(series: PlayoffSeries, game_number: int):
    # Determine home court
    home = determine_home_court(series, game_number)

    # Simulate
    result = simulate_game(home, away, rules, seed)
    store_result(result)
    update_series(series, result)

    # Present
    start_presentation(result)

    # Check if series is over
    wins_needed = (series.best_of // 2) + 1
    if series.higher_seed_wins >= wins_needed or series.lower_seed_wins >= wins_needed:
        series.status = "complete"
        series.winner = ...
        generate_series_mirror(series)
        publish("season.series", series)
    else:
        # Governance window before next game
        open_governance_window(season, round_number=f"PO-{series.series_id}-G{game_number}")

async def run_playoffs(season, seeds):
    # Semifinals
    semi_1 = PlayoffSeries(round="semifinal", higher_seed=seeds[0], lower_seed=seeds[3], best_of=5)
    semi_2 = PlayoffSeries(round="semifinal", higher_seed=seeds[1], lower_seed=seeds[2], best_of=5)

    # Run semis (could be parallel or sequential depending on presentation)
    for game_num in range(1, 6):  # max 5 games
        if not semi_1.status == "complete":
            await run_playoff_game(semi_1, game_num)
        if not semi_2.status == "complete":
            await run_playoff_game(semi_2, game_num)
        if semi_1.status == "complete" and semi_2.status == "complete":
            break

    # Finals
    finals = PlayoffSeries(
        round="final",
        higher_seed=semi_1.winner if semi_1.winner.seed < semi_2.winner.seed else semi_2.winner,
        lower_seed=semi_2.winner if semi_1.winner.seed < semi_2.winner.seed else semi_1.winner,
        best_of=7,
    )

    for game_num in range(1, 8):  # max 7 games
        await run_playoff_game(finals, game_num)
        if finals.status == "complete":
            break

    transition_to(SeasonPhase.CHAMPIONSHIP)
```

**Home court in playoffs:**
- Best-of-5: Games 1, 2, 5 at higher seed's venue
- Best-of-7: Games 1, 2, 5, 7 at higher seed's venue

```python
def determine_home_court(series: PlayoffSeries, game_number: int) -> Team:
    if series.best_of == 5:
        higher_seed_home = {1, 2, 5}
    else:  # best_of == 7
        higher_seed_home = {1, 2, 5, 7}
    return series.higher_seed if game_number in higher_seed_home else series.lower_seed
```

### CHAMPIONSHIP

```python
async def run_championship(season, finals_winner, finals_loser):
    # 1. Season mirror (comprehensive narrative)
    season_mirror = await generate_season_mirror(season)

    # 2. Awards
    awards = compute_awards(season)  # MVP, best defender, most chaotic, etc.
    awards_mirror = await generate_awards_mirror(awards, season)

    # 3. Stats compilation
    compile_season_stats(season)

    # 4. Publish
    publish("season.champion", finals_winner)
    publish("season.awards", awards)

    transition_to(SeasonPhase.OFFSEASON)
```

### OFFSEASON

```python
async def run_offseason(season):
    # Extended governance window (longer than normal)
    open_governance_window(season, round_number="OFFSEASON", duration=3600)  # 1 hour

    # Carry-forward vote: do rules persist?
    # This is a special proposal auto-created by the system
    create_carry_forward_proposal(season)

    # Window closes, votes resolve
    await close_governance_window()

    # Offseason mirror
    generate_offseason_mirror(season)

    # Apply carry-forward decision
    if carry_forward_passed:
        next_season_rules = season.current_ruleset
    else:
        next_season_rules = DEFAULT_RULESET

    transition_to(SeasonPhase.COMPLETE)
```

### COMPLETE

Season archived. Historical data accessible via API. Next season can be created.

## Hackathon Scope

For the 5-day hackathon, the minimum viable season lifecycle:

**Must have:**
- SETUP → REGULAR_SEASON (7 rounds, 1 round-robin, compressed timing)
- Round cycle with governance windows
- Basic standings computation
- Transition to PLAYOFFS (skip tiebreakers — use point differential as tiebreak)

**Nice to have:**
- TIEBREAKER_CHECK and TIEBREAKERS
- Full playoff series with governance between games
- CHAMPIONSHIP (season mirror, awards)
- OFFSEASON

**Post-hackathon:**
- Multi-round-robin regular seasons (21 rounds)
- Full tiebreaker logic
- Offseason governance with carry-forward votes

## Dev/Staging Mode

Compressed season for testing:

```python
DEV_SEASON_CONFIG = SeasonConfig(
    rounds_per_round_robin=7,        # 1 round-robin (not 3)
    governance_rounds_interval=1,     # every round
    playoff_semis_best_of=1,         # single game
    playoff_finals_best_of=3,        # short series
    offseason_enabled=False,          # skip
)
```

A dev season runs: 7 regular season rounds → tiebreaker check → up to 5 playoff games → done. At 2-minute intervals, that's ~25 minutes for a full season arc.

## File Structure

```
core/
├── season.py           # SeasonPhase enum, state machine, transition logic
├── standings.py        # Standings computation, tiebreaker detection
├── playoffs.py         # PlayoffSeries, bracket seeding, home court logic
├── scheduler.py        # Round-robin generation (already planned)
```

## Acceptance Criteria

- [ ] Season progresses through phases correctly
- [ ] Round cycle: simulate → present → mirror → governance → next round
- [ ] Standings computed correctly after each round
- [ ] Tiebreaker detection at playoff cutoff
- [ ] Playoff bracket seeded from standings
- [ ] Home court assignment correct for best-of-5 and best-of-7
- [ ] Governance windows open between playoff games
- [ ] Series ends when a team wins the majority
- [ ] Dev mode runs a compressed season in ~25 minutes
- [ ] Tests: standings computation, tiebreaker scenarios, playoff seeding, home court
