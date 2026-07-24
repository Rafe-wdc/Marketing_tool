# API Handoff — Brand Brain AI Service

API contract for the frontend developer. Four endpoints — three add AI to the brand
onboarding wizard, one powers Script Lab. Plain HTTP + JSON — no SDK, no auth
headers, no libraries needed.

> Companion doc: **`FRONTEND_HANDOFF.md`** covers the React wiring (hooks, state
> mapping, what to save). **This** doc is the API contract itself.

---

## 1. Basics

| | |
|---|---|
| **Base URL (local)** | `http://localhost:3001` |
| **Base path** | `/api/brand-brain` |
| **Method** | `POST` (all four endpoints) |
| **Request header** | `Content-Type: application/json` |
| **Auth** | None currently — do **not** send an Authorization header |
| **Interactive docs** | `{BASE_URL}/docs` (Swagger UI) |
| **Health check** | `GET {BASE_URL}/health` → `{"ok": true, "model": "gemini-2.5-flash"}` |

Put the base URL in an env var per environment — never hardcode it:

```env
VITE_API_BASE=http://localhost:3001        # dev
# VITE_API_BASE=https://api.scaleserum.com # prod
```

### CORS

These origins are allowed by default:

```
http://localhost:3000   http://localhost:5173
http://localhost:5174   http://localhost:5175
https://app.scaleserum.com
```

If you're developing on a **different port**, tell the backend dev — it must be
added to `ALLOWED_ORIGINS` server-side. Note `http://` and `https://` count as
**different** origins.

---

## 2. Rules that apply to every endpoint

**1. Every field is optional.** Send what you have. Empty/missing values are
skipped, not treated as blanks. A half-filled questionnaire still returns useful
output.

**2. It always returns HTTP `200` — never treat this as a blocking call.** If the
AI fails (bad key, timeout, outage), you still get `200` with `"fallback": true`
and a safe default value. There is no `4xx`/`5xx` error path to handle for AI
failures. Only network-level failures throw — wrap in `try/catch` and continue.

```js
// The pattern for all three:
try {
  const res  = await fetch(url, { method: "POST", headers, body });
  const data = await res.json();
  // use data — it's always usable, fallback or not
} catch {
  // network died — carry on with what the user typed
} finally {
  goToNextStep();   // NEVER block the wizard on this call
}
```

**3. `fallback: true` means "the AI didn't run".** The response is still valid,
just not AI-improved. Don't show an error to the user — just proceed. Optionally
log it.

**4. Expect 1–4 seconds.** The call waits on Gemini. Always show a loading state
(`"Optimizing…"`, `"Analyzing…"`). Server-side timeout is 12s.

---

## 3. Shared objects

Two objects are reused across the endpoints.

### `context` — the earlier onboarding steps

| Field | Type | Example |
|-------|------|---------|
| `businessType` | string | `"Education / Coaching / Consulting"` |
| `industry` | string | `"Education / Coaching"` |
| `brandName` | string | `"sdsargq"` |
| `website` | string | `"https://example.com"` |
| `audienceShort` | string | `"busy urban professionals"` |
| `channels` | string[] | `["Meta", "Google"]` |
| `adBudget` | string | `"$5000"` |

### `answers` — the Brand Brain questionnaire

| Field | Type | Example |
|-------|------|---------|
| `businessType` | string | `"Education / Coaching / Consulting"` |
| `idealCustomer` | string | the **optimized** persona from endpoint 1 |
| `brandVoice` | string | `"Bold / Direct"` |
| `language` | string | `"English only"` |
| `trafficChannels` | string[] | `["Meta (Facebook + Instagram)", "YouTube"]` |
| `salesCycle` | string | `"1-4 weeks"` |
| `competitors` | string[] | `["Cult.fit", "IronHaus"]` |
| `marketingGoal` | string | `"Increase revenue per lead (LTV)"` |
| `journey` | string | the lead-to-sale journey free text (may be `""`) |

> Field names are **fixed** — they don't follow your question numbering. Map your
> wizard state onto these names once and reuse it. (Q05 products/pricing was
> removed, so there are no product fields.)

---

## 4. Endpoints

### 4.1 Rewrite persona

**`POST /api/brand-brain/rewrite-persona`**

Cleans up the user's "Who is the ideal customer?" answer.

**When to call:** when the user clicks **Next** on that question.

**Request**

| Field | Type | Notes |
|-------|------|-------|
| `draft` | string | What the user typed. May be `""` → a persona is synthesized from `context`. Max 4000 chars. |
| `context` | object | See [`context`](#context--the-earlier-onboarding-steps) |

```json
{
  "draft": "urbon profesionals 25-40 who wnt reslts fast but cant stay consistant",
  "context": {
    "businessType": "Education / Coaching / Consulting",
    "industry": "Education / Coaching",
    "audienceShort": "busy urban professionals",
    "channels": ["Meta", "Google"],
    "adBudget": "$5000"
  }
}
```

**Response**

| Field | Type | Notes |
|-------|------|-------|
| `optimized_persona` | string | The cleaned 3–5 sentence persona. **Show/store this.** |
| `raw` | string | The original draft, echoed back. Keep for audit/revert. |
| `fallback` | boolean | `true` = AI didn't run; `optimized_persona` equals `raw`. |

```json
{
  "optimized_persona": "Urban professionals aged 25-40 who want fast, visible results but struggle to stay consistent…",
  "raw": "urbon profesionals 25-40 who wnt reslts fast but cant stay consistant",
  "fallback": false
}
```

**Frontend action:** replace the answer with `optimized_persona`, keep `raw`
alongside it, then advance.

---

### 4.2 Suggest funnel

**`POST /api/brand-brain/suggest-funnel`**

Generates the lead-to-sale funnel chips.

**When to call:** when the user lands on the lead-to-sale journey question.

**Request**

| Field | Type | Notes |
|-------|------|-------|
| `answers` | object | See [`answers`](#answers--the-brand-brain-questionnaire) |
| `context` | object | See [`context`](#context--the-earlier-onboarding-steps) |

```json
{
  "answers": {
    "businessType": "Education / Coaching / Consulting",
    "idealCustomer": "Busy urban professionals who want fast results but struggle with consistency.",
    "trafficChannels": ["Meta (Facebook + Instagram)", "YouTube"],
    "salesCycle": "1-4 weeks",
    "competitors": ["Cult.fit", "IronHaus"],
    "marketingGoal": "Increase revenue per lead (LTV)",
    "journey": "ad to free trial page, they book an intro call, then we pitch the high ticket"
  },
  "context": { "industry": "Education / Coaching", "adBudget": "$5000" }
}
```

**Response**

| Field | Type | Notes |
|-------|------|-------|
| `funnel` | array | Ordered, 4–7 items, first touch → retention |
| `funnel[].stage` | string | Short label for the chip, e.g. `"Trial Pass Lead"` |
| `funnel[].description` | string | One line — good for a tooltip |
| `optimized_journey` | string | Tidied version of the journey text |
| `fallback` | boolean | `true` = a generic 5-stage starter funnel was returned |

```json
{
  "funnel": [
    { "stage": "Ad Click",        "description": "Prospect clicks a Meta or YouTube ad." },
    { "stage": "Trial Pass Lead", "description": "Prospect claims the free trial." },
    { "stage": "Intro Booked",    "description": "Lead books an intro call." }
  ],
  "optimized_journey": "Prospects arrive from Meta and YouTube ads…",
  "fallback": false
}
```

**Frontend action:** render `funnel` as the editable "AI-suggested funnel" chips.
The user may edit/reorder before finishing. `funnel` is **never empty** — even on
fallback you get chips to render.

---

### 4.3 Analyze gaps

**`POST /api/brand-brain/analyze-gaps`**

Finds context the questionnaire missed, as multi-select follow-up questions.

**When to call:** when the user lands on the final step, and again on **Reanalyze**.

**Request**

| Field | Type | Notes |
|-------|------|-------|
| `answers` | object | See [`answers`](#answers--the-brand-brain-questionnaire) |
| `context` | object | See [`context`](#context--the-earlier-onboarding-steps) |
| `max_gaps` | number | How many questions to return. Default `3`, clamped to 1–6. |
| `exclude` | string[] | Gap **titles** already shown. Send `[]` first time; on Reanalyze send the titles you already have so the AI returns *different* gaps. |

```json
{
  "answers": { "...": "..." },
  "context": { "...": "..." },
  "max_gaps": 3,
  "exclude": []
}
```

**Response**

| Field | Type | Notes |
|-------|------|-------|
| `gaps` | array | May contain **fewer** than `max_gaps` — the AI won't pad with filler |
| `gaps[].id` | string | `"gap_1"`, `"gap_2"`… Use as the React key and to index your picks |
| `gaps[].title` | string | Short label. **Send these back in `exclude` on Reanalyze** |
| `gaps[].question` | string | The question to display |
| `gaps[].why` | string | One line on why it matters — good as helper text |
| `gaps[].options` | string[] | 4–6 tick-able options |
| `gaps[].multi_select` | boolean | Always `true` — render as checkboxes |
| `fallback` | boolean | `true` = generic starter gaps were returned |

```json
{
  "gaps": [
    {
      "id": "gap_1",
      "title": "Customer Pain Points",
      "question": "What problems does your ideal customer face that you solve?",
      "why": "Lets the AI write ad copy that speaks to real challenges.",
      "options": ["Time constraints", "High costs", "Lack expertise", "Complex process"],
      "multi_select": true
    }
  ],
  "fallback": false
}
```

**Frontend action:** render each gap as a card with checkbox options. Collect the
ticked values as `{ [gapId]: string[] }`. For **Reanalyze**, resend the same body
with `exclude: gaps.map(g => g.title)` and clear the previous ticks.

> A ready-made React component for this screen ships in
> `frontend/Step11GapAnalysis.jsx`.

---

### 4.4 Test script (Script Lab)

**`POST /api/script-lab/test-script`**

Reviews an ad script against the brand's Brand Brain context and returns a
structured critique. **Not part of onboarding** — used later in Script Lab.

**When to call:** when the user clicks **Test Script** (and again on **Regenerate**).

> ⚠️ **Slower call — expect ~15–25s.** Always show a loading state. This one is a
> deliberate user action, so a longer wait is fine (unlike the onboarding calls).

**Request**

| Field | Type | Notes |
|-------|------|-------|
| `script` | string | The ad script (hook, body, CTA). Max 8000 chars. |
| `marketingAngle` | string | `Original` \| `Urgency` \| `Authority` \| `Social Proof` \| `Pain Point` \| `Aspiration` |
| `funnelStage` | string | `Cold, Top of Funnel` \| `Warm, Middle of Funnel` \| `Hot, Bottom of Funnel` \| `Retargeting` |
| `adSource` | string | e.g. `"meta"` |
| `region` | string | optional |
| `adName` / `adNumber` | string | optional metadata |
| `answers` | object | The brand's Brand Brain — **your app loads this by `brand_id` and forwards it.** See [`answers`](#answers--the-brand-brain-questionnaire) |
| `context` | object | Business info. See [`context`](#context--the-earlier-onboarding-steps) |

```json
{
  "script": "Many professionals aspire for board roles. Few intentionally prepare...",
  "marketingAngle": "Authority",
  "funnelStage": "Cold, Top of Funnel",
  "adSource": "meta",
  "answers": {
    "businessType": "Education / Coaching / Consulting",
    "idealCustomer": "Senior executives aged 40-55 preparing for board roles.",
    "brandVoice": "Professional / Authoritative",
    "marketingGoal": "Lead quality (higher intent)"
  },
  "context": { "industry": "Education / Coaching", "brandName": "Director's Institute" }
}
```

**Response**

| Field | Type | Notes |
|-------|------|-------|
| `overall_score` | number | 0–100 |
| `verdict` | string | one-line summary |
| `verdict_band` | string | `No changes needed` \| `Minor tweaks only` \| `Needs work before going live` \| `Rewrite required` |
| `emotional_angle` | object | `{ label, status, critique }` — status is `ANGLE WORKS` \| `ANGLE WEAK` \| `ANGLE OFF`. Headline verdict on the chosen angle. |
| `context_alignment` | object | `{ brand_voice_fit, funnel_stage_fit, marketing_angle_fit }` — each `Strong` \| `Moderate` \| `Weak`. "Did the script follow the brief?" |
| `dimension_scores` | object | `{ attention, resonance, conversion, creative, marketing_angle_execution }` each 0–100. `marketing_angle_execution` = how consistently the script expresses the **chosen** angle end-to-end. |
| `section_breakdown` | array | 6 items: `{ section, score (0–10), comment }` — Hook, Problem/Tension, Solution/Offer, Social Proof, CTA, Pacing |
| `improvements` | array | `{ title, why_it_matters, suggested_rewrite, metrics_impacted }` |
| `fallback` | boolean | `true` = review didn't complete; a neutral scorecard is returned |

```json
{
  "overall_score": 62,
  "verdict": "Needs work before going live. The script is too generic for a cold audience.",
  "verdict_band": "Needs work before going live",
  "emotional_angle": {
    "label": "Authority",
    "status": "ANGLE WEAK",
    "critique": "The brand name implies authority, but the script doesn't demonstrate it…"
  },
  "context_alignment": {
    "brand_voice_fit": "Moderate",
    "funnel_stage_fit": "Strong",
    "marketing_angle_fit": "Weak"
  },
  "dimension_scores": {
    "attention": 50, "resonance": 60, "conversion": 65, "creative": 55,
    "marketing_angle_execution": 40
  },
  "section_breakdown": [
    { "section": "Hook", "score": 5, "comment": "Opens with a generic observation…" }
  ],
  "improvements": [
    {
      "title": "Strengthen the Hook with Authoritative Insight",
      "why_it_matters": "Cold viewers need a reason to keep watching in the first seconds.",
      "suggested_rewrite": "Open with a specific board-readiness statistic…",
      "metrics_impacted": "3-sec view rate, retention, attention"
    }
  ],
  "fallback": false
}
```

**Frontend action:** render the scorecard — `overall_score` + `verdict_band` as the
headline, the emotional angle block (with a **Regenerate** button that re-calls this
endpoint), the 6 section scores, the 4 dimension scores, and the improvements list.
A ready-made hook (`useTestScript`) ships in `frontend-integration.example.jsx`.

---

## 5. Integration checklist

- [ ] Add `VITE_API_BASE` (or equivalent) per environment.
- [ ] Build one shared `buildBrandPayload()` that maps wizard state → `answers` + `context`.
- [ ] **Ideal customer question:** call on Next → swap in `optimized_persona`, keep `raw`, always advance.
- [ ] **Journey question:** call on mount → render `funnel` chips, allow edit.
- [ ] **Final step:** call on mount → render gap cards; wire **Reanalyze** with `exclude`.
- [ ] **Script Lab:** on **Test Script**, load the brand's context by `brand_id` and post it with the script + angle + funnel stage; render the scorecard; wire **Regenerate**.
- [ ] Show a loading state on all calls (onboarding ~1–4s, Script Lab ~15–25s).
- [ ] Never block navigation on the onboarding calls.
- [ ] Confirm your dev port is in the CORS allowlist.
- [ ] On finish, persist `ideal_customer`, `ideal_customer_raw`, `funnel`, `lead_to_sale_journey`, `gap_answers`.

## 6. Quick manual test

Open `{BASE_URL}/docs`, expand any endpoint → **Try it out** → paste a sample body
above → **Execute**.

Postman works too — `POST`, header `Content-Type: application/json`, body → raw →
JSON. Note that **Postman ignores CORS** (it's browser-only), so a Postman success
doesn't prove the browser call will work.
