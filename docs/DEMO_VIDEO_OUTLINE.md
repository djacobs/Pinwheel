# Pinwheel Fates — Demo Video Outline (3 minutes)

> **Production principle: Show, don't tell.** Every claim in the voiceover must be accompanied by something visible on screen. If we say "multilingual," we show a proposal in Spanish. If we say "tight feedback loop," we show a rule passing and games changing. The voiceover narrates what the viewer is already watching — it never substitutes for it.

---

## Opening Hook (0:00–0:15)

**On screen:** Arena page — live games in progress, AI commentary scrolling.

**Voiceover:**
> "This is Pinwheel Fates — a basketball league where humans don't play basketball. They govern it. Rule changes in plain language, interpreted by AI, cascading through dozens of simulated games. A governance lab disguised as a sport."

---

## Section 1: Working Demo (0:15–1:15) — *Judging: Demo*

Walk through one complete **Govern → Simulate → Observe → Reflect** cycle.

### Govern (0:15–0:35)
**On screen:** Discord — a governor types `/propose Make three-pointers worth 5 points`

- Show the AI interpretation step: Opus reads the natural language, classifies the governance tier, explains what the rule means mechanically, and asks the governor to confirm.
- Governor confirms. The proposal enters the voting floor. Other governors vote. The rule passes.

**Then — the multilingual moment:**
**On screen:** A second governor types `/propose` *in Spanish* (or another non-English language). The AI interprets it correctly, responds with the structured rule in English, and asks for confirmation.

**Key line:** *"The AI interprets what you mean — in whatever language you say it. You decide whether that interpretation is right. Then the community votes."*

> **Why the multilingual beat matters:** This is 5 seconds of screen time that demonstrates linguistic accessibility, the interpreter's flexibility, and the "human in the loop" confirmation step — all at once. Show, don't tell.

### Simulate + Observe (0:35–0:55)
**On screen:** Arena page — games running under the new rule. Cut to a game detail page showing box scores with the rule context panel.

- Highlight the rule context sidebar: "Active rules affecting this game" — so players see causation, not just correlation.
- Show standings shifting. A team that relied on three-point shooting now dominates.

**Key line:** *"Propose a rule at noon, watch it reshape the league by 1pm. The feedback loop is tight enough to feel in your gut."*

### Reflect (0:55–1:15)
**On screen:** A private report snippet (DM to a governor). Then the shared governance report.

- The private report tells a governor something only the AI can see: *"You've voted with the same coalition on 4 of 5 proposals. Your governance pattern suggests alliance formation."*
- The shared report surfaces league-wide dynamics: power concentration, emerging coalitions, rule drift.

**Key line:** *"The AI never decides. It illuminates. Each governor gets a private mirror — honest feedback delivered directly, visible only to them."*

---

## Section 2: Architecture + Opus 4.6 (1:15–1:55) — *Judging: Depth/Execution + Opus Usage*

### Agent-Native Architecture (1:15–1:35)
**On screen:** Architecture diagram. Dev log scrolling. Test output.

- Agent-native means the AI is a first-class participant in every layer — not a bolted-on feature. The interpreter, the reporter, the broadcaster, and the build process itself all run through Opus.
- API-first: Discord is the chat interface today, but the API and CLI exist as proof that any chat app with persistent memory can be a point of entry. The architecture is designed to outlive any single client.
- 16 days. 83 sessions. 1515 tests. Every session documented in the dev log. The prompts that drive gameplay were iterated alongside the code that executes them — prompts treated as code.

**Key line:** *"Opus 4.6 isn't just inside the game — it built the game. Agent-native from the ground up: 83 sessions, 1515 tests, every decision traceable."*

### Four Roles for Opus 4.6 (1:35–1:55)
**On screen:** Quick cuts between each role in action — code snippets from `ai/interpreter.py`, `ai/report.py`, `ai/commentary.py`, then the dev log.

1. **Build Partner** — The codebase was pair-programmed with Opus across 83 sessions. Architecture decisions, test strategies, and the AI prompts themselves were co-developed.

2. **Constitutional Interpreter** — Reads natural language proposals in a sandboxed context. Classifies governance tier. Produces structured rule objects validated against a schema before they can touch the simulation. The AI interprets intent — it doesn't legislate.

3. **Social Reporter** — Three report types per round: simulation (what happened on the court), governance (what happened on the floor), and private (what *you* did that you might not have noticed). Behavioral profiling, coalition detection, leverage analysis.

4. **Broadcaster** — Real-time game commentary woven with rule context, rivalry history, and playoff stakes. Contextual narration, not generic play-by-play.

**Key line:** *"Four roles: build partner, interpreter, reporter, broadcaster. It shaped both the code and the gameplay — without making a single governance decision."*

---

## Section 3: Impact — Amplify Human Judgment (1:55–2:40) — *Judging: Impact*

### The Problem (1:55–2:05)
**On screen:** Side-by-side of a proposal and its downstream effects.

> "Most groups have no tools for seeing their own social dynamics while those dynamics are happening. Coalitions form. Power concentrates. Voices go silent. And nobody inside the system can see it."

### Why a Game (2:05–2:20)
**On screen:** Montage of gameplay moments — proposals, votes, games, reports.

- Games are where humanity prototypes its next societies. Low stakes, high reps, fast feedback.
- A game is how builders learn what metaphors and interfaces work — and how players become comfortable with the activities of self-governance itself.
- Basketball is the substrate: familiar enough to be intuitive, complex enough to reward strategic governance. The rules are open-ended — players can change mechanics, league structure, even the meta-rules. It starts as basketball. It finishes as whatever the community decides.

**Key line:** *"Pinwheel is a governance lab through basketball. The AI makes invisible dynamics legible to the people inside the system. Visibility improves governance."*

### Beyond the Game (2:20–2:40)
**On screen:** Text overlay listing real-world applications. Then the Resonant Computing principles.

- The patterns surfaced in Pinwheel — coalition detection, power concentration, free-riding, participation gaps — are the same patterns that matter in newsrooms, fan communities, neighborhood associations, city councils, and federal agencies.
- The architecture is designed to be accessible: financially (open source, no paywall), linguistically (multilingual proposals), and operationally (agent-native reduces the overhead of running these systems).
- Built on Resonant Computing: private (your report is yours alone), dedicated (no ad platform), plural (no single actor controls the rules), adaptable (the game evolves with the community), prosocial (playing practices collective self-governance).

**Key line:** *"We will need completely new, verified, authentic means of communication and negotiation. Pinwheel is a rehearsal space for that future."*

---

## Close (2:40–3:00)

**On screen:** Home page with league activity. Discord server with active governors.

> "Pinwheel is built for what comes next — a world where AI governance tools aren't toys but rehearsal spaces for the real thing. Where any community can see its own dynamics clearly enough to change them."

> "Pinwheel Fates. The game where AI doesn't play — it helps you see."

Show the URL: **pinwheel.fly.dev**

---

## Addendum: What Could Have Gone Better

> *This section is not in the 3-minute video. It belongs in a companion document, a slide deck appendix, or a Q&A response — wherever honest reflection on the build process is appropriate.*

### The "Make It Take It" Problem

The most revealing challenge was getting Claude to treat human proposals as genuine free text rather than strings to pattern-match against a database schema.

"Make it take it" is a real basketball rule — the scoring team keeps possession. Claude *knows* this. During development, Claude Code correctly identified it: "make it take it is a real basketball rule. With the custom_mechanic effect type, this should get interpreted as a custom mechanic at ~0.75 confidence."

But when the interpreter actually ran the string, it returned zero signals and fell through to the "uninterpretable" path. Four words, no explicit game vocabulary, no structural patterns to match. Claude's proposed fix was to add common basketball idioms to a lookup table — which is exactly the wrong answer. The whole point of using a frontier model as an interpreter is that it shouldn't need special-casing for knowledge it already possesses.

**The gap:** There is a difference between what the model *knows* and what the model *does* when constrained by a structured interpretation pipeline. The interpreter's system prompt was optimized for decomposing proposals into schema-compatible fields. That optimization made it excellent at parsing "make three-pointers worth 5 points" and blind to "make it take it" — even though the underlying model understands both equally well.

**The lesson:** Agent-native architecture means trusting the model's knowledge, not just its ability to fill structured templates. The interpreter needs a path that says: "I recognize this as a known game concept even though it doesn't decompose neatly into my schema. Here's what it means and here's how it should affect gameplay." That path doesn't exist yet. Building it is the next step.

**Why this matters for the track:** "Amplify Human Judgment" requires that the AI meet humans where they are — including in the idioms, shorthand, and cultural references they use naturally. If a governance tool can't handle "make it take it," it can't handle the way real communities actually talk. Closing this gap is core to the mission.

### Agent-Native Is a Mindset, Not a Feature

The biggest recurring challenge was resisting the instinct to build traditional software patterns around the AI. Every time we defaulted to pattern matching, lookup tables, or hardcoded decision trees, we were undermining the agent-native premise. The model has world knowledge — the architecture should let it use that knowledge directly, not force it through a narrow structured pipeline.

This tension is productive. It surfaced a design principle: **an agent-native system should degrade gracefully toward the model's general knowledge, not toward a fallback lookup table.** We haven't fully achieved that yet, and doing so is the most important piece of future work.

### Keeping Claude Expansive

The game's premise is that the rules are open-ended — players can change anything, including the meta-rules of governance itself. Claude's default instinct runs counter to this. Across dozens of sessions, Claude consistently advised locking down the rule space: cap the number of active effects, restrict which parameters players can modify, add validation that rejects proposals outside known categories.

Every time, the answer was no. The whole point is that a governor should be able to propose something the system has never seen before — and the AI should be able to interpret it using its own knowledge rather than rejecting it for falling outside a predefined schema. "Make it take it" is the canonical example: a real basketball rule that Claude knows but that the interpreter couldn't handle because the structured pipeline had no slot for it.

This is a deeper challenge than a single bug. The model's training optimizes for helpfulness, and "helpful" in a software engineering context usually means "prevent bad inputs." In a governance context, restricting inputs is the opposite of helpful — it's the AI overriding human judgment, which is precisely what the Amplify Human Judgment track asks us not to do.

**The design principle:** An agent-native governance system must default to openness. The AI's job is to interpret and illuminate, never to gatekeep. When a proposal doesn't fit the schema, the correct response is to expand the schema — not to reject the proposal. Keeping Claude in an expansive state of mind required constant vigilance, and the architecture still has seams where the restrictive instinct leaks through. Closing those seams is ongoing work.

### Discord Is a Bridge, Not a Destination

Discord was the right choice for the hackathon — it's where communities already gather, and the bot framework is mature. But the API-first architecture exists precisely because Discord shouldn't be the only entry point. The vision is that any chat app with persistent memory — Slack, WhatsApp, a custom client — can serve as a front end to the governance engine. The existence of the REST API and CLI proves that the coupling to Discord is shallow, not structural.

---

## Production Notes

**Show-don't-tell checklist:**
Every voiceover line below must have a corresponding visual. If the visual isn't ready, cut the line.

| Voiceover Claim | Required Visual |
|----------------|-----------------|
| "Govern it" | Discord `/propose` command being typed |
| "In whatever language" | Non-English proposal being interpreted |
| "Feedback loop" | Rule passing → arena games visibly changing |
| "Private mirror" | Discord DM with a private report |
| "Agent-native" | Architecture diagram or code snippet |
| "83 sessions" | Dev log scrolling |
| "Real-world applications" | Text overlay or diagram |
| "Rehearsal space" | Live community activity |

**Assets to prepare:**
- Screen recording of the Arena page with live games
- Screen recording of a `/propose` flow in Discord (interpret → confirm → vote → pass)
- Screen recording of a non-English `/propose` flow (interpret → confirm)
- Screenshot of a private report DM
- Screenshot of the governance report
- Screenshot of game detail with rule context panel
- Architecture diagram (Discord ↔ FastAPI ↔ Opus 4.6 ↔ Simulation ↔ SQLite, with note: "API-first — any chat client can connect")
- Dev log scroll or montage (83 sessions, 16 days)
- Code snippet screenshots: `ai/interpreter.py`, `ai/report.py`, `ai/commentary.py`

**Timing budget:**
| Section | Duration | Judging Criteria |
|---------|----------|-----------------|
| Hook | 15s | — |
| Demo cycle | 60s | Working demo |
| Architecture + Opus | 40s | Depth + Opus usage |
| Impact | 45s | Impact on problem statement |
| Close | 20s | — |
| **Total** | **3:00** | |

**Screenshot mapping:**
| Section | Demo file |
|---------|-----------|
| Hook / Arena | `02_arena.png` |
| Standings | `03_standings.png` |
| Game detail | `04_game_detail.png` |
| Reports | `06_reports.png` |
| Governance | `07_governance.png` |
| Rules | `08_rules.png` |
| Team | `09_team.png` |
| Home | `01_home.png` |
