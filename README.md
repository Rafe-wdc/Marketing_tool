# Brand Brain AI Service

A lightweight **FastAPI** service that adds three AI features to the ScaleSerum
brand-onboarding wizard, powered by **Google Gemini**.

It takes what the user typed during onboarding — typos, half-thoughts and all —
plus every other answer they gave, and turns it into clean, structured context
that the downstream AI surfaces (Ad Review, Script Lab, Research Watch, Funnel
Health) can actually use.

> **Stateless by design.** No database, no storage. It receives inputs, calls
> Gemini, returns the result. Saving the output onto the brand record is the main
> app's job.

---

## Features

| # | Endpoint | What it does |
|---|----------|--------------|
| 1 | `POST /api/brand-brain/rewrite-persona` | Rewrites the rough **"Who is the ideal customer?"** draft into a sharp 3–5 sentence persona — fixes spelling/grammar, sharpens it using the business context, never invents facts. |
| 2 | `POST /api/brand-brain/suggest-funnel` | Generates the **lead-to-sale funnel** as an ordered list of stages (plus a tidied journey narrative). Works even if the journey box is empty. |
| 3 | `POST /api/brand-brain/analyze-gaps` | Analyzes every answer and surfaces the **missing context** as multi-select follow-up questions. Supports a **Reanalyze** action that returns fresh gaps. |

Every endpoint is **non-blocking**: on any error (bad key, timeout, Gemini
outage) it still returns HTTP `200` with `"fallback": true` and a safe default,
so onboarding never gets stuck.

---

## Quick start

**Requirements:** Python 3.10+ and a [Google AI Studio API key](https://aistudio.google.com/apikey).

```bash
# 1. Install
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt   # Windows
# ./.venv/bin/pip install -r requirements.txt                   # Linux/Mac

# 2. Configure
copy .env.example .env        # Windows   (cp on Linux/Mac)
#    then paste your key into .env

# 3. Run
.\.venv\Scripts\python.exe app.py
```

Server starts on `http://localhost:3001`.

> **Windows note:** if `.\.venv\Scripts\activate` fails with *"running scripts is
> disabled"*, either call the venv's python directly as shown above, or run once:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

## Try it in the browser

Open **<http://localhost:3001/docs>** — FastAPI's interactive Swagger UI. Pick an
endpoint → **Try it out** → paste a sample body (below) → **Execute**. No curl needed.

Health check: `GET /health` → `{"ok": true, "model": "gemini-2.5-flash"}`

---

## Configuration

All config is environment variables, read from `.env` at startup.

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `GEMINI_API_KEY` | **Yes** | — | Server-side only. Never expose in frontend code. |
| `GEMINI_MODEL` | No | `gemini-2.5-flash` | `gemini-2.0-flash` is deprecated — don't use it. |
| `PORT` | No | `3001` | Port uvicorn listens on. |
| `ALLOWED_ORIGINS` | No | localhost dev origins + `https://app.scaleserum.com` | Comma-separated CORS allowlist. Setting it **replaces** the defaults. |

> ⚠️ Env vars are read **once at startup** — restart the server after any change.

---

## API reference

### 1. Rewrite persona

`POST /api/brand-brain/rewrite-persona`

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

```json
{
  "optimized_persona": "Urban professionals aged 25-40 who want fast, visible results but struggle to stay consistent…",
  "raw": "urbon profesionals 25-40 who wnt reslts…",
  "fallback": false
}
```

Send `"draft": ""` and it synthesizes a persona from the context alone.

### 2. Suggest funnel

`POST /api/brand-brain/suggest-funnel`

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

```json
{
  "funnel": [
    { "stage": "Ad Click", "description": "Prospect clicks a Meta or YouTube ad." },
    { "stage": "Trial Pass Lead", "description": "Prospect claims the free trial." }
  ],
  "optimized_journey": "…cleaned-up narrative…",
  "fallback": false
}
```

### 3. Analyze gaps

`POST /api/brand-brain/analyze-gaps`

Same `answers` + `context` as above, plus:

```json
{
  "max_gaps": 3,
  "exclude": ["titles already shown — used by the Reanalyze button"]
}
```

```json
{
  "gaps": [
    {
      "id": "gap_1",
      "title": "Customer Pain Points",
      "question": "What problems does your ideal customer face that you solve?",
      "why": "Lets the AI write ad copy that speaks to real challenges.",
      "options": ["Time constraints", "High costs", "Lack expertise"],
      "multi_select": true
    }
  ],
  "fallback": false
}
```

To **Reanalyze**, resend with the returned `title` values in `exclude` — the AI
then returns different gaps instead of repeating itself.

**All request fields are optional.** Empty values are skipped rather than sent as
blanks, so a partially-filled questionnaire still produces useful output.

---

## Frontend integration

| File | Purpose |
|------|---------|
| `FRONTEND_HANDOFF.md` | Full wiring guide — config, state mapping, save schema, checklist. |
| `frontend-integration.example.jsx` | React hooks for features 1 & 2. |
| `frontend/Step11GapAnalysis.jsx` | Drop-in React component for the gap-analysis step. |

When onboarding finishes, persist these on the brand record so the downstream AI
reads the improved context:

```js
{
  ideal_customer:       answers.q02,        // optimized persona
  ideal_customer_raw:   answers.q02_raw,    // original draft (audit / revert)
  funnel:               answers.funnel,     // [{ stage, description }]
  lead_to_sale_journey: answers.journey,    // tidied narrative
  gap_answers:          answers.gapAnswers, // { gapId: string[] }
}
```

---

## Deployment

See **`HANDOFF.md`** for the full server setup guide.

Recommended sizing: **1 vCPU · 2 GB RAM · 20 GB SSD**. The service is I/O-bound —
it spends its time waiting on Gemini, not computing — so it stays light even
under concurrent load. Being stateless, it's also a good fit for Cloud Run /
Render / Railway with autoscaling.

```
Internet → Nginx/Caddy (TLS) → Gunicorn + Uvicorn workers → Gemini API
```

---

## Project structure

```
├── app.py                            # the whole service — all 3 endpoints
├── requirements.txt
├── .env.example                      # copy to .env and add your key
├── HANDOFF.md                        # server/backend setup guide
├── FRONTEND_HANDOFF.md               # frontend wiring guide
├── frontend-integration.example.jsx  # React hooks (features 1 & 2)
└── frontend/
    └── Step11GapAnalysis.jsx         # drop-in gap-analysis component
```

## Security notes

- `.env` and `.venv/` are gitignored — **never commit your API key**.
- The Gemini key is used **server-side only**; it must never reach the browser.
- `ALLOWED_ORIGINS` restricts which frontends may call the API. Remember that
  `http://` and `https://` are *different* origins to the browser.
- Before exposing publicly, put it behind TLS and consider a rate limit — every
  call costs Gemini credits.
