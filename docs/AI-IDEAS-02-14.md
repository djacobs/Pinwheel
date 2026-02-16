

## 5. Deep Dive: AI Integration Ideas (Code-Grounded)

The following ideas emerged from reading the actual database models, repository queries, report prompts, narrative context, governance data, and effects system. Each references real data fields and code paths.

---

### 5.1 Behavioral Pattern Detection — AI as Self-Awareness Mirror

**Data available:** `GovernanceEventRow` stores full event history (`proposal.submitted`, `vote.cast`, `token.spent`, `trade.offered`). `Repository.get_governor_activity()` already returns proposals, votes, and token balance per governor.

**What to compute:**
- **Proposal philosophy drift** — Track proposal themes over time. "You started proposing mechanical tweaks. This round you're proposing meta-governance changes."
- **Risk appetite** — Analyze tier distribution. "You consistently propose Tier 1 changes. This round you took a risk with Tier 4."
- **Coalition detection** — Compute vote correlation between governor pairs. "Your votes align 87% with Governor X but diverge with Governor Y."
- **Token velocity** — Track spending patterns. "You've spent 8 PROPOSE tokens this season; league average is 4."
- **Proposal success rate** — "Your proposals pass 60% of the time. That's above league median. What makes yours resonate?"

**Implementation:** New function `generate_behavioral_profile()` in `ai/report.py`. Query event store for governor's full history, compute metrics, feed to Claude with prompt: "Reflect this governor's observable governance behavior back to them without judgment. What patterns emerge?" Store as `report_type="private"`.

---

### 5.2 Rule Evolution Narrative — AI Understands the Meta-Game

**Data available:** `rule_events` with `event_type="rule.enacted"`, payload includes parameter, old_value, new_value, round_enacted. `NarrativeContext.active_rule_changes` and `rules_narrative` already computed.

**What to produce:**
- "Three-pointers went from 3 to 4 points. Perimeter shooting dominates Round 5. Was that intentional? It worked."
- "The league is tightening defenses — foul_rate_modifier dropped twice in four rounds."
- "Rule changes this season: +3pt value, +elam margin, -quarter minutes. You're making games shorter, higher-scoring, more chaotic. The philosophy is clear."
- **Counterfactual:** "If that three-pointer nerf hadn't passed, Team X would likely have won the championship based on their roster composition."

**Implementation:** New `RULE_EVOLUTION_PROMPT` in `ai/report.py` receiving chronological rule changes + correlation with gameplay outcomes (scoring, pace, win margins) + standings before/after each change. Generate a "League Evolution Report" (public) per round.

---

### 5.3 Matchup-Level Narrative & Counterfactuals

**Data available:** `GameResult` with `box_scores`, `possession_log`, `play_by_play`, `elam_activated`, `elam_target`. `NarrativeContext.head_to_head` history.

**What to produce:**
- "Team A has beaten Team B 3 times this season, but never by more than 2 points. Tonight's 12-point margin is unprecedented."
- "If Rule Change X hadn't passed before Round 3, Team A (paint-heavy) would have probably won instead."
- "Team A is normally defensive-minded but scored 68 points tonight — their highest of the season."
- "The Elam Ending activated at 45-40. Normally Team A's strength is endgame execution. They failed."

**Implementation:** Enhance `_build_game_context()` in `ai/commentary.py` to include head-to-head history with margin patterns, season-long team stats, rule changes since last matchup. Generate both play-by-play and a shorter "What This Game Meant" analysis.

---

### 5.4 Proposal Impact Validation — Did the Prediction Match Reality?

**Data available:** `Proposal` with `interpretation.impact_analysis` (stored at proposal time). `game_result` with `ruleset_snapshot`. `box_scores` with shooting stats.

**What to produce:**
- Proposal predicted: "perimeter-heavy teams will dominate."
- After gameplay: perimeter shooting up 23%, winning margins increased 2 points.
- "Your prediction was correct. Exactly as forecasted."
- Or: "Half-right. Perimeter shooting is up, but Team A still lost because of defense. The meta-game adapted faster than expected."

**Implementation:** New `validate_rule_impact()` function: take a rule change event with `impact_analysis`, look at games played under that rule, compute statistics (shooting % by type, scoring pace, team performance deltas), grade prediction accuracy. New `RULE_VALIDATION_PROMPT` asking Claude: "This proposal predicted X. Actual outcome was Y. How accurate?"

**Why it's special:** Closes the feedback loop. Governors see whether their understanding of the game was correct. The AI becomes a learning partner.

---

### 5.5 Seasonal Memorials & "Hall of Fame" Narratives

**Data available:** `SeasonArchiveRow` with `final_standings`, `rule_change_history`, `total_proposals`, `governor_count`, `memorial` (JSON field, currently underused). Full game history.

**What to produce:**
- "Season 2 will be remembered for the three-pointer arms race. Four proposals doubled or tripled its value."
- **MVP:** "Governor Alice submitted 12 proposals, 11 passed (92%). She shaped the entire governance landscape."
- **Cinderella:** "Team X started 1-5. A rule change in Round 7 favored their strengths. They won 6 straight and made the finals."
- **Greatest game:** "The championship needed overtime. The score was tied 8 times. No playoff game this century has been closer."

**Implementation:** New `ai/memorial.py` with `generate_season_memorial()`. Query season archive + game/proposal data. Compute most impactful proposals, closest games, longest streaks, governor highlights. Feed to Claude: "Write a 3-4 paragraph narrative as a sports almanac entry." Store in `SeasonArchiveRow.memorial`.

---

### 5.6 Proposal Synthesis — "If We Combined These..."

**Data available:** Full proposal history with `raw_text`, `interpretation`, `status`. `repository.get_all_proposals()` returns full list.

**What to produce:**
- Governor A proposed: "Make free throws worth 2 points."
- Governor B proposed: "Reduce personal foul limit from 5 to 4."
- AI: "These two proposals have natural synergy. Combined, they create a more disciplined, high-stakes game. Would you like me to draft a synthesis?"
- "Three governors independently proposed tweaks to the three-point rule. Here's a unified proposal."

**Implementation:** New `synthesize_proposals()` in `ai/interpreter.py`. Query pending/recent proposals, group by thematic similarity, use Claude to identify synergies. Generate a "Proposal Synthesis Report" governors see before voting.

---

### 5.7 Hidden Leverage Detection — "You Have More Power Than You Know"

**Data available:** Vote history with outcomes. Proposal history with success rates. Governor IDs and team IDs.

**What to produce:**
- "Your votes have never changed an outcome — but 4/5 times you voted with the majority. Your opinion is well-calibrated."
- "You're the only governor who votes against your team 40% of the time. You're a swing vote."
- "Of 12 proposals you've voted on, 9 passed. You read the room well."
- "You've never submitted a proposal that failed. You understand the league's values deeply."

**Implementation:** New `analyze_governor_influence()`: compute voting accuracy (do their votes predict outcomes?), swing-vote frequency, proposal success rate, correlation with proposal outcomes. Generate as private report: "Here's how your votes shape outcomes. Here's where you have leverage."

**Why it's special:** Shows governors their *actual* power, not their *imagined* power. Some will discover they're more influential than they thought.

---

### 5.8 Governance Health / Tension Detection

**Data available:** All governance events. Voting patterns. Success rates. Cross-team voting alignment.

**What to produce:**
- "Proposal diversity is declining. Governors are proposing similar tweaks. The league is converging on a meta-game."
- "Cross-team voting is increasing. Governors care less about team loyalty and more about principles."
- "Consensus is breaking down. 90% of proposals fail now vs. 60% last season."
- "Proposal velocity is accelerating. Either they feel powerful, or they're desperate."

**Implementation:** New `compute_governance_health()` tracking: proposal diversity (Herfindahl index of parameter focus), consensus level (pass rate), cross-team voting rate, token velocity. Feed to Claude: "What's the health of the league's decision-making? Is it converging, fracturing, or stable?"

---

### 5.9 Governance Proposal Grading — Constructive Feedback Without Prescription

**Data available:** `Proposal` with `raw_text`, `interpretation`, `tier`. Voting results. Passage status.

**What to produce (only for failed proposals, private to proposer):**
- "This proposal failed 30-70. Here's what the outcome might reveal: governors value possession variety more than pace. A 10-second clock is outside the Overton window."
- "Your proposal is Tier 1 (mechanics). Tier 1 proposals pass 45% of the time. Proposals with impact analysis pass 70%."
- "Similar proposals have passed before — but they were phrased differently. The wording may have mattered."

**Implementation:** New `PROPOSAL_FEEDBACK_PROMPT` receiving proposal text, vote results, similar historical proposals and outcomes, tier success rates. Ask Claude: "What might this outcome reveal about the league's preferences?" Store as `report_type="proposal_feedback"` (visible only to proposer).

**Why it's special:** Governors learn to be better proposers without being told what to do. The AI is a coach, not a judge.

---

### 5.10 Cross-Season Evolution Tracking

**Data available:** Multiple `SeasonArchiveRow` entries with `rule_change_history`, `total_proposals`, final standings.

**What to produce:**
- "Across 5 seasons, 127 rule changes. Three-pointers grew from 3 to 5 points. Quarters shrunk from 10 to 7 minutes. You're playing a fundamentally different game than Season 1."
- "Season 1: mechanics changes dominated. Season 3: governance meta-changes. Season 5: AI-suggested syntheses. The culture evolved."
- "Your team has won 3 championships, each under completely different rule sets. Your adaptability is your strength."

**Implementation:** New `compute_historical_context()` retrieving all season archives, computing deltas (rule changes, proposal volume, governance style), identifying trends. New `HISTORICAL_NARRATIVE_PROMPT`: "How has the game changed? What's the trajectory?"

---

### 5.11 "Newspaper" Coverage — The Pinwheel Post

**Concept:** Generate a multi-section "newspaper" after each round combining all AI outputs into a cohesive publication:

- **Headlines:** "UPSET! Team X ends Team Y's 7-game streak" (from commentary)
- **Governance desk:** "Coalition fractures as pace-block loses first vote in 4 rounds" (from governance report)
- **Column:** "The rule meta is shifting — defense is back" (from rule evolution)
- **Letters to the editor:** Anonymized governance health observations
- **Stats page:** Hot players, cold players, team power rankings

**Implementation:** New template `templates/pages/newspaper.html` that aggregates existing report outputs into a newspaper layout. One AI call to generate headlines and editorial framing; the rest is composition of existing reports. Low marginal token cost, high UX impact.

---

### 5.12 "What If" Counterfactual Engine

**Concept:** Let governors (or AI autonomously) ask: "What if this rule hadn't passed?"

- Replay the round's games with the previous ruleset parameters
- Compare outcomes: "Under old rules, Team A wins by 6 instead of losing by 3. The rule change flipped the outcome."
- Use in simulation reports: "This round's rule change was the difference in 2 of 4 games."

**Implementation:** Already have `simulate_game()` as a pure function with deterministic seeds. Re-run with alternate `RuleSet`, compare results. New function `compute_counterfactual()` in `core/simulation.py`. Feed delta to AI for narrative.

**Why it's special:** Makes governance consequences *concrete*. "Your vote changed who won."

---

### Design Principles for All Ideas

1. **Uses data that already exists** in the database
2. **Never prescribes** — describes patterns, doesn't say "you should"
3. **Preserves privacy** — private reports visible only to the person, aggregate analysis without naming individuals
4. **Feels personal and contextualized** — not generic
5. **Can be implemented incrementally** — each is a self-contained prompt + query pattern
6. **Makes invisible dynamics visible** — the core principle of Resonant Computing

The thread connecting them all: **the AI becomes a mirror that helps players see themselves and their league more clearly. Not a player. Not a judge. A witness to their governance.**
