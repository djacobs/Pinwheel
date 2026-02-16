# Pinwheel Fates -- Video Demo Storyboard

*2026-02-16T19:11:15Z by Showboat 0.5.0*

Visual storyboard for the 3-minute hackathon video. Each beat maps to `demo/teleprompter.md`. Every screenshot was captured live from a running instance.

**Judging criteria:** Demo 30% | Opus 4.6 Use 25% | Impact 25% | Depth & Execution 20%

## Setup: Seed the League

```bash
uv run python scripts/demo_seed.py seed
```

```output
League seeded: 4 teams, 18 scheduled games
Season ID: f4b6ab98-ed21-45cd-922e-7e496ecd654a
  Rose City Thorns: 2dd1d97c-efcb-4be4-a8f1-957c135b472e
  Burnside Breakers: d099e4ff-bc20-4d99-aba6-4560b547547a
  St. Johns Herons: faa415ad-1329-458e-a6c3-2c90331af84f
  Hawthorne Hammers: 9527328c-68ac-40de-85ec-925b4d859dbd
```

```bash
uv run python scripts/demo_seed.py step 2
```

```output
Round 1: 2 games, 2 reports
  Rose City Thorns 55 - 60 Burnside Breakers (AWAY) [ELAM]
  St. Johns Herons 34 - 54 Hawthorne Hammers (AWAY) [ELAM]
  Report (simulation): Hawthorne Hammers demolished St. Johns Herons 54-34. The 20-point margin speaks ...
  Report (governance): Round 1 was quiet on the governance front -- no proposals filed....
Round 2: 2 games, 2 reports
  Rose City Thorns 52 - 51 Hawthorne Hammers (HOME) [ELAM]
  Burnside Breakers 48 - 57 St. Johns Herons (AWAY) [ELAM]
  Report (simulation): Rose City Thorns survived Hawthorne Hammers 52-51 in a thriller — just 1 points ...
  Report (governance): Round 2 was quiet on the governance front -- no proposals filed....
```

## Hook (0:00–0:25)

**Voiceover:** *Pinwheel Fates — a basketball simulation game where players choose teams and govern the rules together. Sports drives fierce opinions and loyalty — the perfect arena to test whether AI can help groups make better decisions together. They play through Discord and on the web. After each round of games, players propose and vote on rule changes — and Opus interprets the proposals, simulates consequences, and transparently shares what it sees.*

**Visual:** Arena page — live games in progress, commentary scrolling.

```bash {image}
demo/video_01_arena_hook.png
```

![6195f3c3-2026-02-16](6195f3c3-2026-02-16.png)

## Why a Game (0:25–0:55)

**Voiceover:** *Games are where humanity prototypes its next societies — low stakes, high reps, fast feedback. Coalition detection, power concentration, participation gaps — these are the same patterns that matter in newsrooms, neighborhood associations, and city councils.*

*Pinwheel is a place to experiment with direct democracy and understand what AI-augmented decision-making can actually do. Not a finished handbook — a step.*

**Visual:** Standings page, governance page — showing the patterns in action.

```bash {image}
demo/video_02_standings.png
```

![143dd194-2026-02-16](143dd194-2026-02-16.png)

```bash {image}
demo/video_03_governance.png
```

![52e9498c-2026-02-16](52e9498c-2026-02-16.png)

## Demo: Propose (0:55–1:05)

**Voiceover:** *Here, a player proposes a rule change. Opus interprets the proposal, and confirms with the player. The community votes on rules between rounds.*

**Visual:** Discord `/propose` flow. Here we capture the governance page after a proposal.

```bash
uv run python scripts/demo_seed.py propose Make three-pointers worth 5 points
```

```output
Proposal submitted: p-808acfa2
  Text: Make three-pointers worth 5 points
```

```bash {image}
demo/video_04_governance_propose.png
```

![309e7ab6-2026-02-16](309e7ab6-2026-02-16.png)

## Demo: Simulate + Reflect (1:05–1:30)

**Voiceover:** *The rule proposed at noon impacts the next round of games, starting at 1pm. Opus reports feedback to the league about the impact of the rules, and gives direct, private feedback to players — visible only to them — surfacing patterns in their governance behavior. The shared report surfaces league-wide dynamics: coalitions forming, power concentrating, voices going silent.*

**Visual:** Arena with games under new rules. Game detail with rule context. Reports page.

```bash
uv run python scripts/demo_seed.py step 1
```

```output
Round 3: 2 games, 5 reports
  Rose City Thorns 56 - 46 St. Johns Herons (HOME) [ELAM]
  Hawthorne Hammers 46 - 55 Burnside Breakers (AWAY) [ELAM]
  Report (simulation): Round 3. Rose City Thorns beat St. Johns Herons 56-46. Rose City Thorns rolled p...
  Report (governance): Round 3 was quiet on the governance front -- no proposals filed....
  Report (leverage): **Influence Analysis for demo-governor**

You haven't cast any votes yet this se...
  Report (behavioral): **Season Arc for demo-governor**

Your engagement has been consistent throughout...
```

```bash {image}
demo/video_05_arena_games.png
```

![fa56b9cd-2026-02-16](fa56b9cd-2026-02-16.png)

```bash {image}
demo/video_06_game_detail.png
```

![b06ecd7d-2026-02-16](b06ecd7d-2026-02-16.png)

```bash {image}
demo/video_07_reports.png
```

![d7db58a8-2026-02-16](d7db58a8-2026-02-16.png)

## Impact (1:30–1:40)

**Voiceover:** *Opus helps to illuminate hidden dynamics, amplifying human judgment by making collective decisions legible. Human players always have the last word.*

**Visual:** Reuse governance → standings cut from earlier screenshots.

## Why Discord (1:40–1:55)

**Voiceover:** *On top of a basketball simulator and Opus-powered rules engine, I chose Discord for user interaction. Any chat app with persistent memory can sit on the same stack. Discord is only the proof of concept, and different communities will choose different tools.*

**Visual:** Rules page — showing how governance shapes the system.

```bash {image}
demo/video_08_rules.png
```

![cab4ee64-2026-02-16](cab4ee64-2026-02-16.png)

## Opus: Four Roles (1:55–2:10)

**Voiceover:** *Opus played four roles. First, build partner — 200 commits over six days. Constitutional interpreter, social reporter — behavioral profiling, coalition detection, private reflections, and broadcaster — game commentary woven with league context.*

**Visual:** Team page showing AI-interpreted strategy. Quick cuts of code files.

```bash {image}
demo/video_09_team.png
```

![a94ea1de-2026-02-16](a94ea1de-2026-02-16.png)

## Opus: Agent-Native (2:10–2:30)

**Voiceover:** *Nearly 2,000 tests measure how much code exists — and that is too many. The vision: ship narrative, not code. Replace classes and validators with a product document describing input and output. The dev environment looks less like an IDE and more like the narrative design tools game makers use.*

*With the model of six months from now, each component shrinks from hundreds of lines to a prompt.*

**Visual:** Test suite output as evidence of depth.

```bash
uv run pytest --tb=short -q 2>&1 | tail -5
```

```output
........................................................................ [ 91%]
........................................................................ [ 95%]
........................................................................ [ 98%]
.......................                                                  [100%]
1967 passed in 76.22s (0:01:16)
```

## Depth: Make It Take It (2:30–2:55)

**Voiceover:** *Our biggest challenge was convincing Opus to expand its scope: a player proposed 'make it take it' — a real basketball rule meaning the scoring team keeps possession. Opus knows this, and in open conversation, it explains the rule perfectly. But our structured interpreter was unable to modify the game, because the pipeline was optimized for schema-compatible fields. The model knew the answer. Our code prevented it from using what it knew.*

**Visual:** Evals dashboard — measurement infrastructure.

```bash {image}
demo/video_10_evals.png
```

![1b3d812e-2026-02-16](1b3d812e-2026-02-16.png)

## Close (2:55–3:05)

**Voiceover:** *Pinwheel is my expression of Opus helping groups make better decisions together by amplifying what we already do well: negotiate, change minds, and form coalitions.*

**Visual:** Home page with league activity. URL: **pinwheel.fly.dev**

```bash {image}
demo/video_11_home_close.png
```

![a556303f-2026-02-16](a556303f-2026-02-16.png)
