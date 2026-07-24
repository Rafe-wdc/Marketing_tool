# Brand Brain AI Service — Setup / Handoff

A small **FastAPI (Python)** service that adds two AI features to the ScaleSerum
brand onboarding, both powered by **Google Gemini**:

1. `POST /api/brand-brain/rewrite-persona` — rewrites the Q02 "ideal customer" draft.
2. `POST /api/brand-brain/suggest-funnel` — generates the Q10 lead-to-sale funnel.
3. `POST /api/brand-brain/analyze-gaps` — finds missing-context follow-ups for Step 11.
4. `POST /api/script-lab/test-script` — reviews an ad script against the brand context (Script Lab).

This is a **standalone backend service**. The frontend (React) just calls these two
HTTP endpoints. Nothing else in the app changes.

---

## 1. Requirements

- **Python 3.10+** (developed on 3.12)
- A **Google Gemini API key** — https://aistudio.google.com/apikey
- Outbound HTTPS access to `generativelanguage.googleapis.com`

## 2. Install

```bash
python -m venv .venv
# Windows:  .\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Linux/Mac: ./.venv/bin/pip install -r requirements.txt
```

`requirements.txt`:
```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
google-genai>=1.0.0
python-dotenv>=1.0.0
```

## 3. Configuration (environment variables)

Create a `.env` file next to `app.py` (copy from `.env.example`):

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `GEMINI_API_KEY` | **Yes** | — | Server-side only. **Never** expose in frontend/client code. |
| `GEMINI_MODEL` | No | `gemini-2.5-flash` | `gemini-2.0-flash` is **deprecated** — do not use. |
| `PORT` | No | `3001` | Port uvicorn listens on. |

```env
GEMINI_API_KEY=your_real_key_here
GEMINI_MODEL=gemini-2.5-flash
PORT=3001
```

> Env is read **once at startup** — restart the service after any `.env` change.

## 4. Run

```bash
# Windows
.\.venv\Scripts\python.exe app.py
# Linux/Mac
./.venv/bin/python app.py
```

Startup binds `0.0.0.0:$PORT`. Interactive API docs (Swagger): `http://<host>:<port>/docs`

Health check: `GET /health` → `{ "ok": true, "model": "gemini-2.5-flash" }`

## 5. CORS

Allowed frontend origins are controlled by the `ALLOWED_ORIGINS` env var
(comma-separated). If unset, it defaults to the local dev origins
(`localhost:3000/5173/5174/5175`). No code change needed — just set the var:

```env
# dev default (already fine for local frontend work):
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173,http://localhost:5174,http://localhost:5175
# production — add the real frontend URL(s):
# ALLOWED_ORIGINS=https://app.scaleserum.com
```

> If the frontend and this API are served from the **same origin** (e.g. Nginx
> proxies `/api/brand-brain` on the same domain), CORS isn't triggered at all —
> but keeping the localhost origins set does no harm.

---

## 6. API contracts

### `POST /api/brand-brain/rewrite-persona`
Request:
```json
{
  "draft": "string (user's Q02 answer, may be empty)",
  "context": {
    "businessType": "string", "industry": "string", "brandName": "string",
    "website": "string", "audienceShort": "string",
    "channels": ["string"], "adBudget": "string"
  }
}
```
Response:
```json
{ "optimized_persona": "string", "raw": "string", "fallback": false }
```

### `POST /api/brand-brain/suggest-funnel`
Request:
```json
{
  "answers": {
    "businessType": "string", "idealCustomer": "string", "brandVoice": "string",
    "language": "string", "trafficChannels": ["string"],
    "salesCycle": "string", "competitors": ["string"],
    "marketingGoal": "string", "journey": "string (may be empty)"
  },
  "context": { "industry": "string", "brandName": "string", "adBudget": "string" }
}
```
Response:
```json
{
  "funnel": [ { "stage": "string", "description": "string" } ],
  "optimized_journey": "string",
  "fallback": false
}
```

### `POST /api/brand-brain/analyze-gaps`
Request:
```json
{
  "answers": { "...same shape as suggest-funnel answers..." },
  "context": { "...same shape as above..." },
  "max_gaps": 3,
  "exclude": ["gap titles already shown — used by the Reanalyze button"]
}
```
Response:
```json
{
  "gaps": [
    { "id": "gap_1", "title": "string", "question": "string",
      "why": "string", "options": ["string"], "multi_select": true }
  ],
  "fallback": false
}
```

### `POST /api/script-lab/test-script`
Reviews an ad script against the brand context. **Slower (~15–25s)** — it uses a
longer 45s upstream timeout, so make sure any proxy in front of it allows at least
that (see below).
Request:
```json
{
  "script": "the ad script (max 8000 chars)",
  "marketingAngle": "Authority",
  "funnelStage": "Cold, Top of Funnel",
  "adSource": "meta", "region": "string", "adName": "string", "adNumber": "string",
  "answers": { "...Brand Brain, loaded by brand_id and forwarded..." },
  "context": { "...business info..." }
}
```
Response:
```json
{
  "overall_score": 62,
  "verdict": "string", "verdict_band": "string",
  "emotional_angle": { "label": "string", "status": "ANGLE WEAK", "critique": "string" },
  "context_alignment": { "brand_voice_fit": "Moderate", "funnel_stage_fit": "Strong", "marketing_angle_fit": "Weak" },
  "dimension_scores": { "attention": 50, "resonance": 60, "conversion": 65, "creative": 55, "marketing_angle_execution": 40 },
  "section_breakdown": [ { "section": "Hook", "score": 5, "comment": "string" } ],
  "improvements": [ { "title": "string", "why_it_matters": "string",
                      "suggested_rewrite": "string", "metrics_impacted": "string" } ],
  "fallback": false
}
```

**All fields are optional except where noted.** All four endpoints are
**non-blocking**: on any error they still return HTTP 200 with `"fallback": true`
and a safe default (raw draft / generic funnel / generic gaps / neutral scorecard),
so the UI never gets stuck.

> **Proxy note:** `test-script` can take ~15–25s. If it sits behind Nginx/Caddy,
> set the proxy read timeout to ≥60s (e.g. Nginx `proxy_read_timeout 60s;`) or the
> proxy may cut the connection before the AI responds.

## 7. Files

| File | Purpose |
|------|---------|
| `app.py` | The whole service (all four endpoints). |
| `requirements.txt` | Python dependencies. |
| `.env.example` | Config template → copy to `.env`. |
| `frontend-integration.example.jsx` | React hooks the frontend uses to call the endpoints. |
| `README.md` | Longer walkthrough + test payloads. |
