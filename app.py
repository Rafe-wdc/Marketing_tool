"""
ScaleSerum - Brand Brain "ideal customer" persona rewriter (FastAPI).

One job: take the user's rough Q02 draft plus all the onboarding context, send it
to Google Gemini, and return a cleaned-up, optimized customer persona.

Run it:
    pip install -r requirements.txt
    copy .env.example .env   (then paste your GEMINI_API_KEY into .env)
    python app.py

Interactive docs (test the endpoint in your browser):
    http://localhost:3001/docs        <- Swagger UI

Your React frontend POSTs to:
    http://localhost:3001/api/brand-brain/rewrite-persona
"""

import os
import json
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
load_dotenv()  # reads GEMINI_API_KEY / GEMINI_MODEL / PORT from the .env file

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
PORT = int(os.environ.get("PORT", "3001"))
MAX_DRAFT = 4000  # guard against absurdly large input

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing. Copy .env.example to .env and set it.")

# One reusable Gemini client for the whole app. `.aio` gives us the async client.
client = genai.Client(api_key=GEMINI_API_KEY)

app = FastAPI(title="Brand Brain Persona Rewriter", version="1.0.0")

# Which frontend origins may call this API. Defaults to the local dev origins;
# override in production by setting ALLOWED_ORIGINS in .env to a comma-separated
# list (e.g. "https://app.scaleserum.com,https://staging.scaleserum.com").
DEFAULT_ORIGINS = [
    # local dev
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:5175",
    # production frontend (add staging / other domains here or via ALLOWED_ORIGINS)
    "https://app.scaleserum.com",
]
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()
] or DEFAULT_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / response shapes (these also power the Swagger docs)
# ---------------------------------------------------------------------------
class BrandContext(BaseModel):
    businessType: Optional[str] = None       # Q01 selection
    industry: Optional[str] = None
    brandName: Optional[str] = None
    website: Optional[str] = None
    audienceShort: Optional[str] = None
    channels: Optional[List[str]] = None     # e.g. ["Meta", "Google"]
    adBudget: Optional[str] = None


class RewriteRequest(BaseModel):
    draft: str = Field(default="", description="The user's rough Q02 answer")
    context: BrandContext = Field(default_factory=BrandContext)


class RewriteResponse(BaseModel):
    optimized_persona: str
    raw: str
    fallback: bool = False


# ---- Q10: AI-suggested funnel (lead-to-sale journey) ----------------------
class BrandBrainAnswers(BaseModel):
    """All the Brand Brain answers we use to build the funnel. All optional so a
    partially-filled questionnaire still produces a sensible funnel."""
    businessType: Optional[str] = None           # business type
    idealCustomer: Optional[str] = None          # the optimized persona
    brandVoice: Optional[str] = None             # voice & tone
    language: Optional[str] = None               # content language
    trafficChannels: Optional[List[str]] = None  # channels they run
    salesCycle: Optional[str] = None             # e.g. "1-4 weeks"
    competitors: Optional[List[str]] = None      # top competitors
    marketingGoal: Optional[str] = None          # primary marketing goal
    journey: str = ""                            # lead-to-sale journey (may be empty)


class FunnelRequest(BaseModel):
    answers: BrandBrainAnswers = Field(default_factory=BrandBrainAnswers)
    context: BrandContext = Field(default_factory=BrandContext)  # business info etc.


class FunnelStage(BaseModel):
    stage: str            # short label, e.g. "Trial Pass Lead"
    description: str      # one line explaining what happens at this stage


class FunnelResponse(BaseModel):
    funnel: List[FunnelStage]
    optimized_journey: str
    fallback: bool = False


# ---- Step 11: gap analysis (find missing context, ask follow-ups) ---------
class GapRequest(BaseModel):
    """Everything collected so far. `exclude` lets the Reanalyze button ask for
    fresh gaps instead of repeating the ones already on screen."""
    answers: BrandBrainAnswers = Field(default_factory=BrandBrainAnswers)
    context: BrandContext = Field(default_factory=BrandContext)
    exclude: Optional[List[str]] = None   # gap titles already shown to the user
    max_gaps: int = 3                     # how many follow-up questions to return


class GapItem(BaseModel):
    id: str                 # stable id for the frontend (assigned server-side)
    title: str              # short name of the gap, e.g. "Customer objections"
    question: str           # the follow-up question to show the user
    why: str                # one line: why this matters for the downstream AI
    options: List[str]      # suggested tick-able options (the rectangular boxes)
    multi_select: bool = True


class GapResponse(BaseModel):
    gaps: List[GapItem]
    fallback: bool = False


# ---------------------------------------------------------------------------
# The instructions we give Gemini (this is where the "quality" lives)
# ---------------------------------------------------------------------------
SYSTEM_INSTRUCTION = """
You are a senior direct-response marketing strategist. You rewrite a client's
rough "ideal customer" description into a sharp, structured customer persona used
to guide ad reviews, ad-script angle suggestions, and audience research.

Rules:
- Fix all spelling and grammar.
- Use the provided business context to sharpen the persona (who they are, their
  core pain, their desired outcome, and what makes them buy).
- NEVER invent specific unsupported facts (exact ages, incomes, locations, brand
  claims) that are not present in the draft or context. Generalize instead.
- Keep it concise: 3-5 sentences, third person, plain text.
- No markdown, no headings, no bullet points, no preamble.
- If the draft is empty, synthesize a plausible starter persona strictly from the
  context provided.
""".strip()

# The exact shape we force Gemini to return: {"optimized_persona": "..."}
RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={"optimized_persona": types.Schema(type=types.Type.STRING)},
    required=["optimized_persona"],
)


def build_context_block(ctx: BrandContext) -> str:
    """Turn the onboarding fields into a readable list, skipping empty ones."""
    channels = ctx.channels or []
    channels = ", ".join(str(c) for c in channels)

    rows = [
        ("Business type (Q01)", ctx.businessType),
        ("Industry", ctx.industry),
        ("Brand / sub-account", ctx.brandName),
        ("Website", ctx.website),
        ("Audience (short)", ctx.audienceShort),
        ("Traffic channels", channels),
        ("Monthly ad budget", ctx.adBudget),
    ]
    lines = [f"- {label}: {value}" for label, value in rows if value and str(value).strip()]
    return "\n".join(lines) if lines else "(no extra context)"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"ok": True, "model": GEMINI_MODEL}


@app.post("/api/brand-brain/rewrite-persona", response_model=RewriteResponse)
async def rewrite_persona(body: RewriteRequest):
    draft = (body.draft or "")[:MAX_DRAFT].strip()

    prompt = "\n".join(
        [
            "BUSINESS CONTEXT:",
            build_context_block(body.context),
            "",
            "CLIENT'S ROUGH DRAFT OF THE IDEAL CUSTOMER:",
            draft or "(empty)",
            "",
            "Rewrite the ideal customer persona following your rules.",
        ]
    )

    try:
        # Async call -> the server stays free to handle other requests while we
        # wait on Gemini.
        response = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.4,
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
                # Don't let a slow model call hang the user's "Next" click (ms).
                http_options=types.HttpOptions(timeout=12_000),
            ),
        )

        parsed = json.loads(response.text)
        optimized = str(parsed.get("optimized_persona") or "").strip()

        return RewriteResponse(optimized_persona=optimized or draft, raw=draft)

    except Exception as err:  # noqa: BLE001 - we deliberately never block onboarding
        # Non-blocking contract: hand the raw draft back so the UI can proceed.
        print(f"rewrite-persona failed: {err}")
        return RewriteResponse(optimized_persona=draft, raw=draft, fallback=True)


# ---------------------------------------------------------------------------
# Q10: AI-suggested funnel (lead-to-sale journey)
# ---------------------------------------------------------------------------
FUNNEL_SYSTEM_INSTRUCTION = """
You are a performance-marketing funnel strategist. Given a brand's full profile and
their described lead-to-sale journey, output the canonical funnel as an ORDERED list
of stages from first touch to retention.

Rules:
- 4 to 7 stages. Order them from top of funnel (first touch) to bottom (retention).
- Each stage has a short label (1-3 words, e.g. "Trial Pass Lead", "Intro Booked")
  and a one-line description of what happens there.
- Base the stages on the brand's ACTUAL business model, offers, sales cycle, and
  traffic channels. Do NOT invent channels or offers they did not mention.
- If the journey draft is provided, follow its real steps; only tidy and structure
  it. If it is empty, infer a sensible funnel from the rest of the profile.
- Also return a cleaned-up one-paragraph rewrite of the journey (fix spelling and
  grammar, plain text, no markdown). If the draft is empty, write a short journey
  narrative that matches the funnel you produced.
""".strip()

FUNNEL_RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "funnel": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "stage": types.Schema(type=types.Type.STRING),
                    "description": types.Schema(type=types.Type.STRING),
                },
                required=["stage", "description"],
            ),
        ),
        "optimized_journey": types.Schema(type=types.Type.STRING),
    },
    required=["funnel", "optimized_journey"],
)


def build_answers_block(a: BrandBrainAnswers) -> str:
    """Flatten the Brand Brain answers into a readable list, skipping empties."""
    channels = ", ".join(str(c) for c in (a.trafficChannels or []))
    competitors = ", ".join(str(c) for c in (a.competitors or []))
    rows = [
        ("Business type", a.businessType),
        ("Ideal customer", a.idealCustomer),
        ("Brand voice", a.brandVoice),
        ("Content language", a.language),
        ("Traffic channels", channels),
        ("Sales cycle", a.salesCycle),
        ("Competitors", competitors),
        ("Primary marketing goal", a.marketingGoal),
    ]
    lines = [f"- {label}: {value}" for label, value in rows if value and str(value).strip()]
    return "\n".join(lines) if lines else "(no answers provided)"


@app.post("/api/brand-brain/suggest-funnel", response_model=FunnelResponse)
async def suggest_funnel(body: FunnelRequest):
    journey = (body.answers.journey or "")[:MAX_DRAFT].strip()

    prompt = "\n".join(
        [
            "BUSINESS CONTEXT:",
            build_context_block(body.context),
            "",
            "BRAND BRAIN ANSWERS:",
            build_answers_block(body.answers),
            "",
            "CLIENT'S DESCRIBED LEAD-TO-SALE JOURNEY (Q10):",
            journey or "(empty)",
            "",
            "Produce the ordered funnel and the cleaned-up journey following your rules.",
        ]
    )

    try:
        response = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=FUNNEL_SYSTEM_INSTRUCTION,
                temperature=0.4,
                response_mime_type="application/json",
                response_schema=FUNNEL_RESPONSE_SCHEMA,
                http_options=types.HttpOptions(timeout=12_000),
            ),
        )

        parsed = json.loads(response.text)
        stages = [
            FunnelStage(stage=str(s.get("stage", "")).strip(),
                        description=str(s.get("description", "")).strip())
            for s in (parsed.get("funnel") or [])
            if str(s.get("stage", "")).strip()
        ]
        optimized_journey = str(parsed.get("optimized_journey") or "").strip()

        # If the model returned nothing usable, fall back rather than error.
        if not stages:
            raise ValueError("model returned no funnel stages")

        return FunnelResponse(
            funnel=stages,
            optimized_journey=optimized_journey or journey,
        )

    except Exception as err:  # noqa: BLE001 - never block onboarding
        # Non-blocking fallback: a generic starter funnel so the UI still has chips.
        print(f"suggest-funnel failed: {err}")
        fallback_funnel = [
            FunnelStage(stage="Ad Click", description="Prospect clicks an ad or link."),
            FunnelStage(stage="Lead", description="Prospect submits their details."),
            FunnelStage(stage="Qualified", description="Lead is contacted and qualified."),
            FunnelStage(stage="Purchase", description="Lead converts into a paying customer."),
            FunnelStage(stage="Retention", description="Customer is retained and re-engaged."),
        ]
        return FunnelResponse(funnel=fallback_funnel, optimized_journey=journey, fallback=True)


# ---------------------------------------------------------------------------
# Step 11: gap analysis (analyze all answers -> find missing context)
# ---------------------------------------------------------------------------
GAP_SYSTEM_INSTRUCTION = """
You are a marketing strategist auditing a brand's onboarding profile for
completeness. You are given every answer the client provided. Your job is to find
the most important CONTEXT GAPS - information that was NOT captured but that the
downstream AI (ad review, script lab, research watch, funnel health) genuinely
needs to produce strong output for THIS specific brand.

Rules:
- Return the most valuable gaps only, ordered by importance. Never pad the list.
- Do NOT re-ask anything already answered, and do NOT repeat any gap in the
  "already shown" list.
- Each gap must be a GENUINE gap for this brand - not a generic question. Tie it to
  what they already told you.
- For each gap: a short title (2-4 words), a clear follow-up question, a one-line
  "why" (what downstream feature it unblocks), and 4-6 concise, tick-able answer
  options (2-5 words each) that the user can multi-select.
- Options must be realistic, mutually distinct choices for this brand. The frontend
  will add its own "Other" box, so do not include one.
""".strip()

GAP_RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "gaps": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "title": types.Schema(type=types.Type.STRING),
                    "question": types.Schema(type=types.Type.STRING),
                    "why": types.Schema(type=types.Type.STRING),
                    "options": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING),
                    ),
                },
                required=["title", "question", "why", "options"],
            ),
        ),
    },
    required=["gaps"],
)


@app.post("/api/brand-brain/analyze-gaps", response_model=GapResponse)
async def analyze_gaps(body: GapRequest):
    max_gaps = max(1, min(int(body.max_gaps or 3), 6))
    already_shown = ", ".join(body.exclude or []) or "(none)"

    prompt = "\n".join(
        [
            "BUSINESS CONTEXT:",
            build_context_block(body.context),
            "",
            "ALL BRAND BRAIN ANSWERS:",
            build_answers_block(body.answers),
            "",
            f"Journey (Q10): {body.answers.journey or '(empty)'}",
            "",
            f"Return AT MOST {max_gaps} gaps, ordered by importance.",
            f"Already shown to the user (do NOT repeat these): {already_shown}",
        ]
    )

    try:
        response = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=GAP_SYSTEM_INSTRUCTION,
                # Slightly higher so "Reanalyze" surfaces different angles.
                temperature=0.7,
                response_mime_type="application/json",
                response_schema=GAP_RESPONSE_SCHEMA,
                http_options=types.HttpOptions(timeout=12_000),
            ),
        )

        parsed = json.loads(response.text)
        gaps: List[GapItem] = []
        for i, g in enumerate(parsed.get("gaps") or [], start=1):
            title = str(g.get("title", "")).strip()
            options = [str(o).strip() for o in (g.get("options") or []) if str(o).strip()]
            if not title or not options:
                continue
            gaps.append(GapItem(
                id=f"gap_{i}",
                title=title,
                question=str(g.get("question", "")).strip(),
                why=str(g.get("why", "")).strip(),
                options=options,
            ))
            if len(gaps) >= max_gaps:
                break

        if not gaps:
            raise ValueError("model returned no usable gaps")

        return GapResponse(gaps=gaps)

    except Exception as err:  # noqa: BLE001 - never block onboarding
        # Non-blocking fallback: a couple of broadly-useful gaps so the step still
        # renders. These are generic on purpose (used only when the AI call fails).
        print(f"analyze-gaps failed: {err}")
        fallback_gaps = [
            GapItem(
                id="gap_1",
                title="Customer objections",
                question="What are the main objections that stop people from buying?",
                why="Ad Review & Script Lab need known objections to write rebuttals.",
                options=["Price too high", "No time", "Tried before - didn't work",
                         "Skeptical of results", "Needs partner approval"],
            ),
            GapItem(
                id="gap_2",
                title="Proof & credibility",
                question="What proof do you have that you can show in ads?",
                why="Creative angles rely on proof (testimonials, data, guarantees).",
                options=["Client testimonials", "Before/after results", "Case studies",
                         "Money-back guarantee", "Awards / certifications"],
            ),
        ]
        return GapResponse(gaps=fallback_gaps[:max_gaps], fallback=True)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
