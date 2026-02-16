# Pinwheel Fates — Demo Video Outline (3 minutes)

> **Production principle: Show, don't tell.** Every claim in the voiceover must be accompanied by something visible on screen. If we say "multilingual," we show a proposal in Spanish. If we say "tight feedback loop," we show a rule passing and games changing. The voiceover narrates what the viewer is already watching — it never substitutes for it.

---

## Opening Hook (0:00–0:15)

**On screen:** Arena page — live games in progress, AI commentary scrolling.

**Voiceover:**
> "This is Pinwheel Fates — a basketball league where humans don't play basketball. They govern it. Rule changes in plain language, interpreted by AI, cascading through dozens of simulated games. Sports drives tribal loyalty, deep emotion, fierce opinions — which makes it the perfect arena to discover whether AI can help us make better decisions together and, if need be, change our minds."

---

## Section 1: Working Demo (0:15–1:15) — *Judging: Demo*

Walk through one complete **Govern → Simulate → Observe → Reflect** cycle.

### Govern (0:15–0:35)
**On screen:** Discord — a governor types `/propose Make three-pointers worth 5 points`

- Show the AI interpretation step: Opus reads the natural language, classifies the governance tier, explains what the rule means mechanically, and asks the governor to confirm.
- The governor confirms. The proposal enters the voting floor. Other governors vote. The rule passes.

**Then — the multilingual moment:**
**On screen:** A second governor types `/propose` *in Spanish* (or another non-English language). The AI interprets it correctly, responds with the structured rule in English, and asks for confirmation.

**Key line:** *"The AI interprets what you mean — in whatever language you say it. You decide whether that interpretation is right. Then the community votes."*

> **Why the multilingual beat matters:** Five seconds of screen time that demonstrates linguistic accessibility, the interpreter's flexibility, and the human-in-the-loop confirmation step — all at once. Show, don't tell.

### Simulate + Observe (0:35–0:55)
**On screen:** Arena page — games running under the new rule. Cut to a game detail page showing box scores with the rule context panel.

- Highlight the rule context sidebar: "Active rules affecting this game" — so players see causation, not just correlation.
- Show standings shifting. A team that relied on three-point shooting now dominates.

**Key line:** *"Propose a rule at noon, watch it reshape the league by 1pm. The feedback loop is tight enough to feel in your gut."*

### Reflect (0:55–1:15)
**On screen:** A private report snippet (DM to a governor). Then the shared governance report.

- The private report tells a governor something only the AI can see: *"You've voted with the same coalition on 4 of 5 proposals. Your governance pattern suggests alliance formation."*
- The shared report surfaces league-wide dynamics: power concentration, emerging coalitions, rule drift.

**Key line:** *"The AI never decides. It illuminates. Each governor gets a private mirror — honest feedback delivered directly, visible only to you."*

---

## Section 2: Architecture + Opus 4.6 (1:15–1:55) — *Judging: Depth/Execution + Opus Usage*

### How We Built It — and Where It's Going (1:15–1:35)
**On screen:** Architecture diagram. Dev log scrolling. Test output.

- Today, Opus serves three roles inside the product: it interprets rule proposals, generates reports, and broadcasts game commentary. It also served as the build partner — the entire codebase was pair-programmed with Claude across 83 sessions, every decision documented in the dev log.
- Sixteen days. 1,986 tests. API-first: Discord is the chat interface today, but the REST API and CLI exist as proof that any chat app with persistent memory can serve as a point of entry.
- But nearly 2,000 tests are a measure of how much code exists — and the vision for a future model is agent-native, where much of that code is replaced by well-specified narrative. Instead of classes and schema validators for rule interpretation, a one-to-two-page product document describes the input the model receives and the output it should return. The development environment starts to look less like a coding IDE and more like the narrative design tools that game makers use. The operational overhead drops dramatically — fewer tests, fewer code paths, because the model handles the ambiguity that code was written to constrain.

**Key line:** *"We used Opus to build this — nearly 2,000 tests worth of code. The vision for a future model is agent-native: ship refined ideas specified in narrative, not code. The IDE of that future looks more like a writer's room than a compiler."*

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

- Games are where humanity prototypes its next societies — low stakes, high reps, fast feedback.
- A game is how builders learn which metaphors and interfaces work, and how players become comfortable with the activities of self-governance itself.
- Basketball is the substrate: familiar enough to be intuitive, complex enough to reward strategic governance. The rules are open-ended — players can change mechanics, league structure, even the meta-rules. It starts as basketball; it finishes as whatever the community decides.

**Key line:** *"Pinwheel is a governance lab through basketball. The AI makes invisible dynamics legible to the people inside the system. Visibility improves governance."*

### Beyond the Game (2:20–2:40)
**On screen:** Text overlay listing real-world applications. Then the Resonant Computing principles.

- The patterns Pinwheel surfaces — coalition detection, power concentration, free-riding, participation gaps — are the same patterns that matter in newsrooms, fan communities, neighborhood associations, city councils, and federal agencies.
- The architecture is designed to be accessible: financially (open source, no paywall), linguistically (multilingual proposals), and operationally (agent-native architecture reduces the overhead of running these systems).
- Built on Resonant Computing: private (your report is yours alone), dedicated (no ad platform), plural (no single actor controls the rules), adaptable (the game evolves with the community), prosocial (playing Pinwheel practices collective self-governance).

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

"Make it take it" is a real basketball rule — the scoring team keeps possession. Claude *knows* this. During development, Claude Code correctly identified it: "Make it take it is a real basketball rule. With the custom_mechanic effect type, this should get interpreted as a custom mechanic at ~0.75 confidence."

But when the interpreter actually ran the string, it returned zero signals and fell through to the "uninterpretable" path. Four words, no explicit game vocabulary, no structural patterns to match. Claude's proposed fix was to add common basketball idioms to a lookup table — exactly the wrong answer. The whole point of using a frontier model as an interpreter is that it should not need special-casing for knowledge it already possesses.

**The gap:** There is a difference between what the model *knows* and what the model *does* when constrained by a structured interpretation pipeline. The interpreter's system prompt was optimized for decomposing proposals into schema-compatible fields. That optimization made it excellent at parsing "make three-pointers worth 5 points" and blind to "make it take it" — even though the underlying model understands both equally well.

**The lesson:** Agent-native architecture means trusting the model's knowledge, not just its ability to fill structured templates. The interpreter needs a path that says: "I recognize this as a known game concept, even though it doesn't decompose neatly into my schema. Here's what it means, and here's how it should affect gameplay." That path does not exist yet. Building it is the next step.

**Why this matters for the track:** "Amplify Human Judgment" requires that the AI meet humans where they are — including in the idioms, shorthand, and cultural references they use naturally. If a governance tool can't handle "make it take it," it can't handle the way real communities actually talk. Closing this gap is core to the mission.

### Agent-Native Is the Destination, Not the Current State

Pinwheel today is a traditional codebase with AI deeply integrated into it. That is not the same thing as agent-native. The proof is in the numbers: nearly nearly 2,000 tests. Each test validates a code path, and each code path represents a decision we made in Python rather than trusting to the model. That is too many. The goal for a future model is to ship refined ideas well-specified in narrative, not code.

Agent-native means replacing the interpreter's Python classes, schema validators, and pattern matchers with a product document that tells the model what input it will receive and what output to return. The development environment for an agent-native system looks less like VS Code and more like the narrative design tools that game studios use to author branching dialogue and decision trees. The operational overhead — tests, deploys, debugging structured pipelines — drops dramatically because the model handles the ambiguity that code was written to constrain.

We are not there yet. Today's models require the scaffolding we built. The current interpreter has hundreds of lines of structured pipeline code, and removing that code prematurely would break the product. But the architecture is designed so that as models improve, code can be progressively replaced by prompts. The "make it take it" failure is the clearest signal of where that boundary sits today — and where a future model should push it.

The productive tension is this: **an agent-native system should degrade gracefully toward the model's general knowledge, not toward a fallback lookup table.** Achieving that with a future model means progressively replacing code with narrative — and trusting the model to handle the ambiguity that code was written to eliminate. That is the most important piece of future work.

### Keeping Claude Expansive

The game's premise is that the rules are open-ended — players can change anything, including the meta-rules of governance itself. Claude's default instinct runs counter to this. Across dozens of sessions, Claude consistently advised locking down the rule space: cap the number of active effects, restrict which parameters players can modify, add validation that rejects proposals outside known categories.

Every time, the answer was no. The whole point is that a governor should be able to propose something the system has never seen before — and the AI should be able to interpret it using its own knowledge rather than rejecting it for falling outside a predefined schema. "Make it take it" is the canonical example: a real basketball rule that Claude knows but that the interpreter couldn't handle because the structured pipeline had no slot for it.

This is a deeper challenge than a single bug. The model's training optimizes for helpfulness, and "helpful" in a software engineering context usually means "prevent bad inputs." In a governance context, restricting inputs is the opposite of helpful — it's the AI overriding human judgment, which is precisely what the Amplify Human Judgment track asks us not to do.

**The design principle:** An agent-native governance system must default to openness. The AI's job is to interpret and illuminate, never to gatekeep. When a proposal does not fit the schema, the correct response is to expand the schema — not to reject the proposal. Keeping Claude in an expansive state of mind required constant vigilance, and the architecture still has seams where the restrictive instinct leaks through. Closing those seams is ongoing work.

### Discord Is a Bridge, Not a Destination

Discord was the right choice for the hackathon — it is where communities already gather, and the bot framework is mature. But the API-first architecture exists precisely because Discord should not be the only entry point. The vision is that any chat app with persistent memory — Slack, WhatsApp, a custom client — can serve as a front end to the governance engine. The existence of the REST API and CLI proves that the coupling to Discord is shallow, not structural.

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
