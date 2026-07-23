# Frontend Handoff — Brand Brain AI Features

Three AI features to wire into the existing Brand Brain wizard. The backend is a
separate service exposing 3 endpoints; the frontend just calls them at the right
moments and saves the results onto the brand. **No new libraries needed** — plain
`fetch` + React state.

| # | Feature | Where | Trigger | Endpoint |
|---|---------|-------|---------|----------|
| 1 | Rewrite ideal-customer persona | "Ideal customer" question | user clicks **Next** | `POST /rewrite-persona` |
| 2 | Suggest lead-to-sale funnel | "Lead-to-sale journey" question | on **landing** on that step | `POST /suggest-funnel` |
| 3 | Gap analysis (follow-up questions) | Final step | on **landing** + **Reanalyze** | `POST /analyze-gaps` |

> Note: Q05 (products & price points) has been removed, so the questionnaire is
> now 9 questions. The API uses **named** fields, not question numbers — the
> `answers.q0X` keys below are illustrative; map them to your real wizard state.

Base path: `/api/brand-brain`. All endpoints return HTTP 200 even on failure (a
`fallback: true` flag + safe default), so **never block the wizard on them**.

## Config

Set the backend base URL per environment (Vite shown; adjust for your build tool):

```env
VITE_REWRITE_URL=https://<backend-host>/api/brand-brain/rewrite-persona
VITE_FUNNEL_URL=https://<backend-host>/api/brand-brain/suggest-funnel
VITE_GAPS_URL=https://<backend-host>/api/brand-brain/analyze-gaps
```

Local dev default (if the env vars are unset) is `http://localhost:3001`.

## State mapping (do this once)

Map your wizard state to the payload the backend expects. Rename the left side to
whatever your wizard actually uses; the right side (the keys sent to the API) must
stay exactly as shown.

```js
// answers = the 10 Brand Brain answers; businessInfo + audience = earlier steps
export function buildBrandPayload({ answers, businessInfo, audience }) {
  return {
    answers: {
      businessType: answers.q01,           // business-type radio
      idealCustomer: answers.q02,          // store the OPTIMIZED persona here
      brandVoice: answers.q03,
      language: answers.q04,
      trafficChannels: answers.q05,        // string[]  e.g. ["Meta (Facebook + Instagram)"]
      salesCycle: answers.q06,             // "1-4 weeks"
      competitors: answers.q07,            // string[]
      marketingGoal: answers.q08,
      journey: answers.q09 || "",          // journey free text (may be empty)
    },
    context: {
      businessType: answers.q01,
      industry: businessInfo.industry,
      brandName: businessInfo.brandName,
      website: businessInfo.website,
      audienceShort: audience.whoIsAudience,
      channels: audience.trafficChannels,
      adBudget: audience.monthlyAdBudget,
    },
  };
}
```

---

## Feature 1 — Rewrite persona (Q02, on Next)

Replace the Q02 "Next" handler. Always advance even if the call fails.

```jsx
const REWRITE_URL = import.meta.env.VITE_REWRITE_URL || "http://localhost:3001/api/brand-brain/rewrite-persona";

async function handleNextFromQ02({ answers, businessInfo, audience, setAnswers, goNext, setBusy }) {
  const { context } = buildBrandPayload({ answers, businessInfo, audience });
  setBusy(true);
  try {
    const res = await fetch(REWRITE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ draft: answers.q02, context }),
    });
    const data = await res.json();
    setAnswers((a) => ({ ...a, q02: data.optimized_persona, q02_raw: data.raw }));
  } catch (e) {
    console.warn("persona rewrite failed, keeping raw draft", e);
  } finally {
    setBusy(false);
    goNext(); // ALWAYS advance
  }
}
```

Button: `{busy ? "Optimizing…" : "Next →"}` and `disabled={busy}`.

## Feature 2 — Suggest funnel (Q10, on mount)

```jsx
const FUNNEL_URL = import.meta.env.VITE_FUNNEL_URL || "http://localhost:3001/api/brand-brain/suggest-funnel";

// inside your Q10 component:
useEffect(() => {
  (async () => {
    setFunnelLoading(true);
    try {
      const res = await fetch(FUNNEL_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildBrandPayload({ answers, businessInfo, audience })),
      });
      const data = await res.json();
      setFunnel(data.funnel);                    // [{ stage, description }] -> render as chips
      setAnswers((a) => ({ ...a, funnel: data.funnel, q10: data.optimized_journey || a.q10 }));
    } catch (e) { console.warn("funnel failed", e); }
    finally { setFunnelLoading(false); }
  })();
}, []); // fire once on landing on Q10
```

Render `funnel` as the editable "AI-SUGGESTED FUNNEL (CONFIRM / EDIT)" chips
(each item has `stage` + a `description` you can show as a tooltip). Let the user
edit/reorder before "Finish & Train AI".

## Feature 3 — Gap analysis (Step 11)

Use the ready-made component **`frontend/Step11GapAnalysis.jsx`** (self-contained,
styled to match Brand Brain, includes Reanalyze). Just feed it the payload and
handle the finish:

```jsx
import Step11GapAnalysis from "./Step11GapAnalysis";

<Step11GapAnalysis
  brandPayload={buildBrandPayload({ answers, businessInfo, audience })}
  onFinish={(picks) => {
    // picks = { gap_1: ["Awareness","Decision"], gap_2: [...] }
    setAnswers((a) => ({ ...a, gapAnswers: picks }));
    saveBrandAndTrainAI();
  }}
/>
```

The component analyzes on mount, renders each gap as multi-select option boxes,
and **Reanalyze** re-calls the API excluding the titles already shown.

---

## What to save on the brand record

When the wizard finishes, persist these so the downstream AI (Ad Review, Script
Lab, Research Watch, Funnel Health, Morning Briefing) reads the improved context.
**Save the optimized values into whatever fields those sections already read.**

```js
{
  ideal_customer:      answers.q02,        // OPTIMIZED persona (feature 1)
  ideal_customer_raw:  answers.q02_raw,    // original draft (audit / revert)
  funnel:              answers.funnel,     // [{ stage, description }] (feature 2)
  lead_to_sale_journey: answers.q10,       // tidied journey text (feature 2)
  gap_answers:         answers.gapAnswers, // { gapId: string[] } (feature 3)
}
```

## Wiring checklist

- [ ] Add the 3 `VITE_*` env vars per environment.
- [ ] Add `buildBrandPayload()` once (shared).
- [ ] Q02: swap the Next handler for `handleNextFromQ02`; show "Optimizing…".
- [ ] Q10: add the `useEffect` fetch; render `funnel` chips.
- [ ] Add Step 11 to the wizard flow; render `<Step11GapAnalysis/>`.
- [ ] On finish, save the fields above onto the brand.
- [ ] Confirm the sub-sections read `ideal_customer` / `funnel` (likely already do).

## Reference files

- `frontend/Step11GapAnalysis.jsx` — drop-in Step 11 component.
- `frontend-integration.example.jsx` — hook versions of features 1 & 2.
- `README.md` — endpoint contracts + sample request bodies for manual testing.
- Live UI mockup of Step 11: shared separately as an artifact link.
