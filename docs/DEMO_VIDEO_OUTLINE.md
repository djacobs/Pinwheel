# Pinwheel Fates — Demo Video Outline (3 minutes)

> **Production principle: Show, don't tell.** Every claim in the voiceover must be accompanied by something visible on screen.

**Judging criteria and target allocation:**
| Criterion | Weight | Target Time |
|-----------|--------|-------------|
| Demo | 30% | 35s |
| Opus 4.6 Use | 25% | 40s |
| Impact | 25% | 40s |
| Depth & Execution | 20% | 25s |

---

## Hook (0:00–0:25)

**On screen:** Arena page — live games in progress, commentary scrolling.

**Voiceover:**
> Pinwheel Fates — a basketball simulation game where players choose teams and govern the rules together. Sports drives fierce opinions and loyalty — the perfect arena to test whether AI can help groups make better decisions together. They play through Discord and on the web. After each round of games, players propose and vote on rule changes — and Opus interprets the proposals, simulates consequences, and transparently shares what it sees.

---

## Why a Game (0:25–0:55)

**On screen:** Standings page, governance page — showing the patterns in action.

**Voiceover:**
> Games are where humanity prototypes its next societies — low stakes, high reps, fast feedback. Coalition detection, power concentration, participation gaps — these are the same patterns that matter in newsrooms, neighborhood associations, and city councils.

> Pinwheel is a place to experiment with direct democracy and understand what AI-augmented decision-making can actually do. Not a finished handbook — a step.

---

## Demo: Propose (0:55–1:05)

**On screen:** Discord `/propose` flow — a player proposes a rule change.

**Voiceover:**
> Here, a player proposes a rule change. Opus interprets the proposal, and confirms with the player. The community votes on rules between rounds.

---

## Demo: Simulate + Reflect (1:05–1:30)

**On screen:** Arena with games under new rules. Game detail with rule context. Reports page.

**Voiceover:**
> The rule proposed at noon impacts the next round of games, starting at 1pm. Opus reports feedback to the league about the impact of the rules, and gives direct, private feedback to players — visible only to them — surfacing patterns in their governance behavior. The shared report surfaces league-wide dynamics: coalitions forming, power concentrating, voices going silent.

---

## Impact (1:30–1:40)

**On screen:** Cut from governance page (passed proposal) to standings page (shifted rankings).

**Voiceover:**
> Opus helps to illuminate hidden dynamics, amplifying human judgment by making collective decisions legible. Human players always have the last word.

---

## Why Discord (1:40–1:55)

**On screen:** Rules page — showing how governance shapes the system.

**Voiceover:**
> On top of a basketball simulator and Opus-powered rules engine, I chose Discord for user interaction. Any chat app with persistent memory can sit on the same stack. Discord is only the proof of concept, and different communities will choose different tools.

---

## Opus: Four Roles (1:55–2:10)

**On screen:** Team page showing AI-interpreted strategy. Quick cuts of code files.

**Voiceover:**
> Opus played four roles. First, build partner — 200 commits over six days. Constitutional interpreter, social reporter — behavioral profiling, coalition detection, private reflections, and broadcaster — game commentary woven with league context.

---

## Opus: Agent-Native (2:10–2:30)

**On screen:** Test suite output as evidence of depth.

**Voiceover:**
> Nearly 2,000 tests measure how much code exists — and that is too many. The vision: ship narrative, not code. Replace classes and validators with a product document describing input and output. The dev environment looks less like an IDE and more like the narrative design tools game makers use.

> With the model of six months from now, each component shrinks from hundreds of lines to a prompt.

---

## Depth: Make It Take It (2:30–2:55)

**On screen:** Evals dashboard — measurement infrastructure.

**Voiceover:**
> Our biggest challenge was convincing Opus to expand its scope: a player proposed "make it take it" — a real basketball rule meaning the scoring team keeps possession. Opus knows this, and in open conversation, it explains the rule perfectly. But our structured interpreter was unable to modify the game, because the pipeline was optimized for schema-compatible fields. The model knew the answer. Our code prevented it from using what it knew.

---

## Close (2:55–3:05)

**On screen:** Home page with league activity. URL: **pinwheel.fly.dev**

**Voiceover:**
> Pinwheel is my expression of Opus helping groups make better decisions together by amplifying what we already do well: negotiate, change minds, and form coalitions.

---

## Addendum: What Could Have Gone Better

> *This section is not in the 3-minute video. It belongs in a companion document, a slide deck appendix, or a Q&A response.*

### The "Make It Take It" Problem — Full Detail

The most revealing challenge was getting Claude to treat human proposals as genuine free text rather than strings to pattern-match against a database schema.

"Make it take it" is a real basketball rule — the scoring team keeps possession. Claude *knows* this. During development, Claude Code correctly identified it: "Make it take it is a real basketball rule. With the custom_mechanic effect type, this should get interpreted as a custom mechanic at ~0.75 confidence."

But when the interpreter actually ran the string, it returned zero signals and fell through to the "uninterpretable" path. Four words, no explicit game vocabulary, no structural patterns to match. Claude's proposed fix was to add common basketball idioms to a lookup table — exactly the wrong answer. The whole point of using a frontier model as an interpreter is that it should not need special-casing for knowledge it already possesses.

**The gap:** There is a difference between what the model *knows* and what the model *does* when constrained by a structured interpretation pipeline. The interpreter's system prompt was optimized for decomposing proposals into schema-compatible fields. That optimization made it excellent at parsing "make three-pointers worth 5 points" and blind to "make it take it" — even though the underlying model understands both equally well.

**The lesson:** Agent-native architecture means trusting the model's knowledge, not just its ability to fill structured templates. The interpreter needs a path that says: "I recognize this as a known game concept, even though it doesn't decompose neatly into my schema. Here's what it means, and here's how it should affect gameplay." That path does not exist yet. Building it is the next step.

### Agent-Native Is the Destination, Not the Current State

Pinwheel today is a traditional codebase with AI deeply integrated into it. That is not the same thing as agent-native. The proof is in the numbers: nearly 2,000 tests. Each test validates a code path, and each code path represents a decision we made in Python rather than trusting to the model. That is too many. The goal for a future model is to ship refined ideas well-specified in narrative, not code.

Agent-native means replacing the interpreter's Python classes, schema validators, and pattern matchers with a product document that tells the model what input it will receive and what output to return. The development environment for an agent-native system looks less like VS Code and more like the narrative design tools that game studios use to author branching dialogue and decision trees. The operational overhead — tests, deploys, debugging structured pipelines — drops dramatically because the model handles the ambiguity that code was written to constrain.

The productive tension is this: **an agent-native system should degrade gracefully toward the model's general knowledge, not toward a fallback lookup table.** Achieving that with a future model means progressively replacing code with narrative — and trusting the model to handle the ambiguity that code was written to eliminate.

### Keeping Claude Expansive — Full Detail

Claude was a positive partner throughout — eager, productive, reliable — but not a true peer or owner of the product vision. Its default across dozens of sessions was to build its way through ambiguity: cap the number of active effects, add validators for unknown parameters, write pattern matchers for edge cases.

Every time, the answer was no. The whole point is that a governor should be able to propose something the system has never seen before — and the AI should be able to interpret it using its own knowledge rather than rejecting it for falling outside a predefined schema.

**The design principle:** An agent-native governance system must default to openness. When a proposal does not fit the schema, the correct response is to widen the model's aperture — not to write more code that constrains it.

### Discord Is a Bridge, Not a Destination

Discord was the right choice for the hackathon — it is where communities already gather, and the bot framework is mature. But the API-first architecture exists precisely because Discord should not be the only entry point. Any chat app with persistent memory can serve as a front end to the governance engine.
