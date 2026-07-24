# Script Lab — "Test Script" AI Review

Documentation for the Script Lab review feature: what it does, how it scores, the
API contract, and the design decisions behind the prompt.

---

## What it does

After a brand is created and trained (the 3 Brand Brain onboarding endpoints), the
sales team opens **Script Lab → Test Script**, pastes an ad script, and picks a
**marketing angle** and **funnel stage**. The AI reviews the script **against that
brand's context** (persona, voice, offer, funnel, goal — forwarded by the main app)
and returns a structured critique: an overall score, an emotional-angle verdict,
"did it follow the brief" ratings, dimension scores, a section-by-section
breakdown, and concrete improvements.

It evaluates **two things at once**:
1. **Execution quality** — is this a good script? (hook, tension, offer, CTA, pacing)
2. **Brief adherence** — did it follow the brand voice, funnel stage, and chosen
   marketing angle it was written for?

---

## Endpoint

`POST /api/script-lab/test-script` — a route in the same FastAPI service (`app.py`)
as the three Brand Brain endpoints. Stateless, non-blocking (returns HTTP 200 with
`fallback: true` and a neutral scorecard on any error), ~15–25s latency (uses a 45s
upstream timeout).

### Request
```json
{
  "script": "the ad script (max 8000 chars)",
  "marketingAngle": "Original | Urgency | Authority | Social Proof | Pain Point | Aspiration",
  "funnelStage": "Cold, Top of Funnel | Warm, Middle of Funnel | Hot, Bottom of Funnel | Retargeting",
  "adSource": "meta", "region": "", "adName": "", "adNumber": "",
  "answers": { "…Brand Brain context, loaded by brand_id and forwarded by the main app…" },
  "context": { "…business info…" }
}
```
All fields optional; empty values are skipped.

### Response
```json
{
  "overall_score": 88,
  "verdict": "one-line summary",
  "verdict_band": "No changes needed | Minor tweaks only | Needs work before going live | Rewrite required",
  "emotional_angle": { "label": "Authority", "status": "ANGLE WORKS | ANGLE WEAK | ANGLE OFF", "critique": "…" },
  "context_alignment": {
    "brand_voice_fit": "Strong | Moderate | Weak",
    "funnel_stage_fit": "Strong | Moderate | Weak",
    "marketing_angle_fit": "Strong | Moderate | Weak"
  },
  "dimension_scores": {
    "attention": 90, "resonance": 85, "conversion": 80, "creative": 80,
    "marketing_angle_execution": 85
  },
  "section_breakdown": [ { "section": "Hook", "score": 9, "comment": "…" } ],
  "improvements": [
    { "title": "…", "why_it_matters": "…", "suggested_rewrite": "…", "metrics_impacted": "CTR, CPL, Lead Quality" }
  ],
  "fallback": false
}
```

### Field guide
| Field | Meaning |
|-------|---------|
| `overall_score` | 0–100 headline score. |
| `verdict_band` | Plain-language band derived from the score. |
| `emotional_angle` | Headline verdict on whether the script **executes the chosen angle**. |
| `context_alignment` | "Did it follow the brief?" — voice / funnel-stage / angle fit, each Strong/Moderate/Weak. |
| `dimension_scores` | 5 scores /100. `marketing_angle_execution` = how consistently the chosen angle is expressed end-to-end. |
| `section_breakdown` | 6 sections each /10 with an angle- and stage-aware comment. |
| `improvements` | Highest-leverage fixes, each with a rewrite in the brand voice + chosen angle, and the metrics it moves. |

---

## How the scoring works (prompt design)

The marketing angle and funnel stage the user selected are treated as the
**creative brief** — not just labels. The reviewer judges writing quality AND
faithfulness to that brief throughout the critique.

**Definitions are baked into the prompt** so interpretation stays consistent:

- **Marketing angles:** Original, Authority, Urgency, Social Proof, Pain Point,
  Aspiration.
- **Funnel stages:** Cold (top), Warm (middle), Hot (bottom), Retargeting.

**Key scoring rules:**
- **Angles combine, they don't replace.** A section may layer another persuasion
  style (e.g. Authority + Social Proof) — that's good copywriting and is **not**
  penalized. A section is only capped (≤5/10) when it **abandons/replaces** the
  chosen angle so the selected angle is essentially absent.
- **Funnel-aware CTA/conversion.** The `conversion` dimension and the CTA section
  are judged relative to the stage: a cold audience should get a low-commitment ask
  (watch/register); a hot audience a direct ask (book/buy). The wrong ask for the
  stage lowers the score.
- **Awareness must match the stage.** "As you already know…" to a cold audience, or
  a weak "follow us" CTA to a hot audience, lowers the section score.
- **Score bands:** 90–100 "No changes needed", 70–89 "Minor tweaks only", 50–69
  "Needs work before going live", 0–49 "Rewrite required".
- **Scores stay consistent with comments** — no high score with a negative comment.
- **No invented brand facts** — everything is grounded in the provided context.

### Verified behavior (local tests)
| Scenario | Result |
|----------|--------|
| Real production script (Remuneration, Cold/Original, well-made) | 88/100, `ANGLE WORKS`, all alignment Strong |
| Aspirational copy submitted as "Authority" (angle replaced) | 25/100, `ANGLE OFF`, angle_exec 5 |
| Authority **+** Social Proof combined, angle=Authority | ~78/100, not capped — combining rewarded |
| Cold stage with a hard "Buy now" CTA | conversion 10, CTA 3/10 — wrong-stage ask penalized |
| Cold script assuming prior familiarity ("as you already know…") | funnel_stage_fit Weak, hook flagged |

---

## Prompt evolution (changelog)

1. **v1 — base critic.** Section scores + improvements + a single emotional-angle block.
2. **v2 — marketing angle as a constraint.** Angle evaluated throughout; added angle
   definitions, `marketing_angle_execution` dimension, and `context_alignment`
   (voice/funnel/angle fit). Fixed a scoring bug (high scores with negative comments).
3. **v3 — funnel stage as a constraint.** Added funnel-stage definitions; section
   rule also catches stage-awareness mismatches. (No `funnel_stage_execution` score
   added — `funnel_stage_fit` covers it, avoiding redundancy.)
4. **v4 — precision fixes (current).** (a) Penalize *replacing* the chosen angle, not
   *combining* it with another. (b) `conversion` is now judged relative to the funnel
   stage.

Each round was cross-reviewed (ChatGPT) and each change was verified against real
script inputs before shipping.

---

## Status & deployment

- ✅ Endpoint built, prompt at v4, compiles, verified locally.
- ⏳ **Not live until `app.py` is redeployed** to `tool.lawttorney.com`.
- ⚠️ **Proxy timeout:** this endpoint can take ~15–25s. Set the reverse-proxy read
  timeout to ≥60s (e.g. Nginx `proxy_read_timeout 60s;`) or the connection may be
  cut before the AI responds.
- Frontend: a `useTestScript()` hook ships in `frontend-integration.example.jsx`;
  the response now has 5 dimension scores + a `context_alignment` block to render.

## Known gap (not built yet)

The Script Lab UI advertises that it *"matches the ad to past tests so every change
is scored against its history and the coaching keeps getting sharper."* **That
learning loop does not exist yet** — this endpoint is stateless and has no memory of
past scripts or real ad performance (CTR/CPL). Each review is a one-shot judgment.
Implementing "scored against history" would require storing each test + its real
outcome and feeding past winners into the review. This is the natural next build,
alongside the "Creative Coach" follow-up chat panel.
