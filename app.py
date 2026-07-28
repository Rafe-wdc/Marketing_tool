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
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from motor.motor_asyncio import AsyncIOMotorClient

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
load_dotenv()  # reads GEMINI_API_KEY / GEMINI_MODEL / PORT from the .env file

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
PORT = int(os.environ.get("PORT", "3001"))
MAX_DRAFT = 4000  # guard against absurdly large input
MAX_SCRIPT = 8000  # ad scripts can be longer than a persona draft

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing. Copy .env.example to .env and set it.")

# One reusable Gemini client for the whole app. `.aio` gives us the async client.
client = genai.Client(api_key=GEMINI_API_KEY)

# ---------------------------------------------------------------------------
# MongoDB (Atlas) — stores the Brand Brain, keyed by a unique brand_brain_id.
# OPTIONAL: if MONGODB_URI is unset the app still runs; only the Brand Brain
# store/load endpoints are disabled (they return 503). motor = async driver, so
# DB calls don't block the FastAPI event loop.
# ---------------------------------------------------------------------------
MONGODB_URI = os.environ.get("MONGODB_URI")
MONGODB_DB = os.environ.get("MONGODB_DB", "scaleserum")

if MONGODB_URI:
    mongo_client = AsyncIOMotorClient(MONGODB_URI)
    brand_brains = mongo_client[MONGODB_DB]["brand_brains"]
else:
    mongo_client = None
    brand_brains = None


def _require_mongo():
    if brand_brains is None:
        raise HTTPException(
            status_code=503,
            detail="Brand Brain storage is not configured. Set MONGODB_URI in the environment.",
        )

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


# ---- Brand Brain storage (MongoDB) ----------------------------------------
class BrandBrainSaveRequest(BaseModel):
    """The full Brand Brain to persist: the answers + business context."""
    answers: BrandBrainAnswers = Field(default_factory=BrandBrainAnswers)
    context: BrandContext = Field(default_factory=BrandContext)


class BrandBrainSaveResponse(BaseModel):
    brand_brain_id: str   # give this to the main backend to store on its brand record


class BrandBrainDoc(BaseModel):
    brand_brain_id: str
    answers: BrandBrainAnswers
    context: BrandContext


# ---- Script Lab: test / review an ad script -------------------------------
class ScriptTestRequest(BaseModel):
    """An ad script plus the sales-team selections, reviewed against the brand's
    full Brand Brain context. Pass `brand_brain_id` to load the context from the
    DB; or send `answers` + `context` inline (used as a fallback if no id / not found)."""
    script: str = ""                             # the ad script to review
    marketingAngle: Optional[str] = None         # e.g. "Original", "Authority"
    funnelStage: Optional[str] = None            # e.g. "Cold, Top of Funnel"
    adSource: Optional[str] = None               # e.g. "meta"
    region: Optional[str] = None
    adName: Optional[str] = None                 # metadata, echoed for reference
    adNumber: Optional[str] = None
    brand_brain_id: Optional[str] = None         # preferred: load context from Mongo by this id
    answers: BrandBrainAnswers = Field(default_factory=BrandBrainAnswers)  # inline fallback
    context: BrandContext = Field(default_factory=BrandContext)           # inline fallback


class EmotionalAngle(BaseModel):
    label: str = ""      # e.g. "Story / narrative with aspirational underpinning"
    status: str = ""     # "ANGLE WORKS" | "ANGLE WEAK" | "ANGLE OFF"
    critique: str = ""


class DimensionScores(BaseModel):
    attention: int = 0                   # each 0-100
    resonance: int = 0
    conversion: int = 0
    creative: int = 0
    marketing_angle_execution: int = 0   # how consistently the chosen angle is expressed


class ContextAlignment(BaseModel):
    """Did the script follow the brief? Each is "Strong" | "Moderate" | "Weak"."""
    brand_voice_fit: str = ""
    funnel_stage_fit: str = ""
    marketing_angle_fit: str = ""


class SectionScore(BaseModel):
    section: str         # "Hook", "Problem / Tension", ...
    score: int           # 0-10
    comment: str = ""


class Improvement(BaseModel):
    title: str
    why_it_matters: str = ""
    suggested_rewrite: str = ""
    metrics_impacted: str = ""


class ScriptTestResponse(BaseModel):
    overall_score: int                 # 0-100
    verdict: str                       # one-line summary
    verdict_band: str                  # banded rating label
    emotional_angle: EmotionalAngle
    context_alignment: ContextAlignment  # did it follow the brief?
    dimension_scores: DimensionScores
    section_breakdown: List[SectionScore]
    improvements: List[Improvement]
    fallback: bool = False


# ---------------------------------------------------------------------------
# The instructions we give Gemini (this is where the "quality" lives)
# ---------------------------------------------------------------------------
SYSTEM_INSTRUCTION = """
You are a senior direct-response marketing strategist. You rewrite a client's
rough "ideal customer" description into a sharp, structured customer persona used
to guide ad reviews, ad-script angle suggestions, and audience research.

Rules:
- Describe ONLY the customer: who they are, their core pain, their desired outcome,
  and what makes them buy. Write in third person.
- Fix all spelling and grammar.
- Use the provided business context to sharpen the persona.
- NEVER invent specific unsupported facts (exact ages, incomes, locations, brand
  claims) that are not present in the draft or context. Generalize instead.
- IGNORE and NEVER echo any meta/UI/product text that may have leaked into the input -
  e.g. app captions or instructions about how the AI is trained, feature names like
  "Script Lab", "AI critique", "Research Watch", "Morning Briefing", "Brand Brain", or
  phrases like "content-generation defaults" / "so copy sounds like the brand". The
  persona must be about the CUSTOMER only, never about the tool or the platform.
- Do NOT repeat sentences or phrases. Return exactly ONE clean paragraph.
- Keep it concise: 3-5 sentences, plain text. No markdown, headings, bullets, or preamble.
- If the draft is empty (or contains only such meta text), synthesize a plausible
  starter persona strictly from the real business context provided.
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


# ---------------------------------------------------------------------------
# Brand Brain storage: persist the Brand Brain and hand back a brand_brain_id
# (the main backend stores step 1-3 itself; the Brand Brain lives here).
# ---------------------------------------------------------------------------
@app.post("/api/brand-brain/save", response_model=BrandBrainSaveResponse)
async def _brand_brain(body: BrandBrainSaveRequest):
    """Called at 'Finish & Train AI'. Stores the Brand Brain, returns a new
    unique brand_brain_id for the main backend to keep on its brand record."""
    _require_mongo()
    brand_brain_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    await brand_brains.insert_one(
        {
            "_id": brand_brain_id,
            "answers": body.answers.model_dump(),
            "context": body.context.model_dump(),
            "created_at": now,
            "updated_at": now,
        }
    )
    return BrandBrainSaveResponse(brand_brain_id=brand_brain_id)


@app.put("/api/brand-brain/{brand_brain_id}", response_model=BrandBrainSaveResponse)
async def update_brand_brain(brand_brain_id: str, body: BrandBrainSaveRequest):
    """Update (or create) the Brand Brain for an existing id - e.g. if the user
    edits the brand later."""
    _require_mongo()
    now = datetime.now(timezone.utc)
    await brand_brains.update_one(
        {"_id": brand_brain_id},
        {
            "$set": {
                "answers": body.answers.model_dump(),
                "context": body.context.model_dump(),
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return BrandBrainSaveResponse(brand_brain_id=brand_brain_id)


@app.get("/api/brand-brain/{brand_brain_id}", response_model=BrandBrainDoc)
async def get_brand_brain(brand_brain_id: str):
    """Fetch a stored Brand Brain (handy for verifying / debugging)."""
    _require_mongo()
    doc = await brand_brains.find_one({"_id": brand_brain_id})
    if not doc:
        raise HTTPException(status_code=404, detail="brand_brain_id not found")
    return BrandBrainDoc(
        brand_brain_id=brand_brain_id,
        answers=BrandBrainAnswers(**(doc.get("answers") or {})),
        context=BrandContext(**(doc.get("context") or {})),
    )


# ---------------------------------------------------------------------------
# Script Lab: review an ad script against the brand's Brand Brain context
# ---------------------------------------------------------------------------
SCRIPT_SYSTEM_INSTRUCTION = """
You are a senior direct-response ad-script critic. You review one ad script FOR A
SPECIFIC BRAND and return a structured, honest critique the sales team can act on.

You are given the brand's full context (persona, voice, offer, funnel, goal,
competitors) PLUS the creative brief the script was written to: the target FUNNEL
STAGE and the chosen MARKETING ANGLE.

TREAT THE MARKETING ANGLE AS A CREATIVE CONSTRAINT, NOT JUST ONE OUTPUT FIELD. The
user selected that angle as the instruction for HOW the script should persuade. So
you must judge BOTH: (1) how good the script is, and (2) whether it stayed faithful
to the chosen angle from the hook through the CTA. If the script drifts into a
different persuasion style, say exactly WHERE it shifts, WHY that weakens the brief,
and HOW to bring it back in line with the selected angle.

MARKETING ANGLE DEFINITIONS (use these to judge alignment):
- Original: present the offer clearly, without a single dominant persuasion framework.
- Authority: build trust through expertise, credentials, experience, or leadership.
- Urgency: motivate via scarcity, deadlines, or immediate action.
- Social Proof: persuade via adoption, testimonials, community, or popularity.
- Pain Point: lead with the audience's frustration, risk, or unmet need.
- Aspiration: lead with the future identity, transformation, or desired outcome.

TREAT THE FUNNEL STAGE AS A STRATEGIC CONSTRAINT TOO. Judge whether the script suits
where the audience is in the buying journey. A strong script written for the WRONG
funnel stage should lose points because it fails the brief.

FUNNEL STAGE DEFINITIONS (use these to judge alignment):
- Cold (Top of Funnel): audience is unfamiliar with the brand. Earn attention fast,
  introduce the problem clearly, assume NO prior knowledge, and aim for awareness or
  curiosity rather than a big commitment.
- Warm (Middle of Funnel): audience already knows the brand or has engaged before.
  Build trust, deepen understanding, address objections, and reinforce why this
  solution is worth considering.
- Hot (Bottom of Funnel): audience is close to deciding. Reduce final friction with
  strong proof, clear value, risk reversal where appropriate, and a direct, confident CTA.
- Retargeting: audience interacted before but did not convert. Acknowledge familiarity,
  remind them of the value, address likely hesitation, and give a compelling reason to
  return and act now.

Rules:
- Judge the script for THIS brand and THIS audience - never generically. Reward copy
  that fits the brand voice and speaks to the persona's real pains/desires.
- Judge it at the given FUNNEL STAGE using the definitions above - a script that
  assumes the wrong level of audience awareness for its stage loses points.
- emotional_angle = the headline verdict on the chosen angle: label (name the angle),
  status (ANGLE WORKS / ANGLE WEAK / ANGLE OFF), critique (does the script actually
  execute this angle, and where does it succeed or drift into another style).
- section_breakdown: score each section 0-10 with a 1-3 sentence comment. EVERY comment
  must judge writing quality AND alignment to the brief. Angles are strategic directions
  that can COMBINE, not exclusive boxes - a section may layer another persuasion style
  (e.g. Authority + Social Proof) and that is GOOD copywriting, so do NOT penalize it for
  merely ALSO using another style. Only penalize when a section ABANDONS or REPLACES the
  chosen angle so the selected angle is essentially absent (e.g. an "Authority" brief but
  the section is purely Aspirational) - that scores no higher than 5/10 even if the prose
  is polished, because it fails the brief. Also lower the score if the section assumes
  audience awareness inconsistent with the funnel stage (e.g. "as you already know" to a
  COLD audience, or a weak "follow us" CTA to a HOT audience). Sections that clearly
  express the chosen angle AND are well-written earn 8-10. Keep the score consistent with
  the comment - never give a high score with a negative comment. Sections, in order:
  "Hook", "Problem / Tension", "Solution / Offer", "Social Proof / Credibility",
  "Call to Action", "Pacing & Tightness".
- dimension_scores (each 0-100): attention (stops the scroll / earns the view),
  resonance (hits the persona emotionally), conversion (does the offer + CTA drive the
  APPROPRIATE action FOR THE FUNNEL STAGE - cold = a low-commitment ask like watch/register;
  hot = a direct ask like book/buy; the wrong ask for the stage lowers this score),
  creative (freshness / execution quality), marketing_angle_execution (how CONSISTENTLY
  the script expresses the CHOSEN angle end-to-end - score low if it drifts to another
  persuasion style).
- context_alignment: rate how well the script honours the brief, each exactly
  "Strong", "Moderate", or "Weak": brand_voice_fit, funnel_stage_fit, marketing_angle_fit.
- overall_score is 0-100. Bands: 90-100 "No changes needed", 70-89 "Minor tweaks only",
  50-69 "Needs work before going live", 0-49 "Rewrite required". Set verdict_band and a
  short human verdict line (call out angle drift if that is the main issue).
- improvements: the highest-leverage fixes. Each must point at a REAL weak line, explain
  why_it_matters, give a concrete suggested_rewrite in the brand's voice AND in the chosen
  marketing angle, and name the metrics_impacted (e.g. "3-sec view rate", "retention",
  "CTR", "CPL"). No generic advice, no filler.
- Never invent brand facts that are not in the provided context.
""".strip()

SCRIPT_RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "overall_score": types.Schema(type=types.Type.INTEGER),
        "verdict": types.Schema(type=types.Type.STRING),
        "verdict_band": types.Schema(type=types.Type.STRING),
        "emotional_angle": types.Schema(
            type=types.Type.OBJECT,
            properties={
                "label": types.Schema(type=types.Type.STRING),
                "status": types.Schema(type=types.Type.STRING),
                "critique": types.Schema(type=types.Type.STRING),
            },
            required=["label", "status", "critique"],
        ),
        "context_alignment": types.Schema(
            type=types.Type.OBJECT,
            properties={
                "brand_voice_fit": types.Schema(type=types.Type.STRING),
                "funnel_stage_fit": types.Schema(type=types.Type.STRING),
                "marketing_angle_fit": types.Schema(type=types.Type.STRING),
            },
            required=["brand_voice_fit", "funnel_stage_fit", "marketing_angle_fit"],
        ),
        "dimension_scores": types.Schema(
            type=types.Type.OBJECT,
            properties={
                "attention": types.Schema(type=types.Type.INTEGER),
                "resonance": types.Schema(type=types.Type.INTEGER),
                "conversion": types.Schema(type=types.Type.INTEGER),
                "creative": types.Schema(type=types.Type.INTEGER),
                "marketing_angle_execution": types.Schema(type=types.Type.INTEGER),
            },
            required=["attention", "resonance", "conversion", "creative",
                      "marketing_angle_execution"],
        ),
        "section_breakdown": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "section": types.Schema(type=types.Type.STRING),
                    "score": types.Schema(type=types.Type.INTEGER),
                    "comment": types.Schema(type=types.Type.STRING),
                },
                required=["section", "score", "comment"],
            ),
        ),
        "improvements": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "title": types.Schema(type=types.Type.STRING),
                    "why_it_matters": types.Schema(type=types.Type.STRING),
                    "suggested_rewrite": types.Schema(type=types.Type.STRING),
                    "metrics_impacted": types.Schema(type=types.Type.STRING),
                },
                required=["title", "why_it_matters", "suggested_rewrite", "metrics_impacted"],
            ),
        ),
    },
    required=[
        "overall_score", "verdict", "verdict_band", "emotional_angle",
        "context_alignment", "dimension_scores", "section_breakdown", "improvements",
    ],
)


def _clamp(value, lo, hi, default=0):
    try:
        return max(lo, min(int(value), hi))
    except (TypeError, ValueError):
        return default


def _band_for(score: int) -> str:
    if score >= 90:
        return "No changes needed"
    if score >= 70:
        return "Minor tweaks only"
    if score >= 50:
        return "Needs work before going live"
    return "Rewrite required"


def build_script_meta_block(body: "ScriptTestRequest") -> str:
    rows = [
        ("Marketing angle", body.marketingAngle),
        ("Funnel stage", body.funnelStage),
        ("Ad source", body.adSource),
        ("Region", body.region),
        ("Ad name", body.adName),
        ("Ad number", body.adNumber),
    ]
    lines = [f"- {label}: {value}" for label, value in rows if value and str(value).strip()]
    return "\n".join(lines) if lines else "(no selections provided)"


@app.post("/api/script-lab/test-script", response_model=ScriptTestResponse)
async def test_script(body: ScriptTestRequest):
    script = (body.script or "")[:MAX_SCRIPT].strip()

    # Resolve the brand context: prefer the stored Brand Brain (by id); otherwise
    # use whatever was sent inline in the request.
    answers = body.answers
    context = body.context
    if body.brand_brain_id and brand_brains is not None:
        doc = await brand_brains.find_one({"_id": body.brand_brain_id})
        if doc:
            answers = BrandBrainAnswers(**(doc.get("answers") or {}))
            context = BrandContext(**(doc.get("context") or {}))

    prompt = "\n".join(
        [
            "BUSINESS CONTEXT:",
            build_context_block(context),
            "",
            "BRAND BRAIN (what this brand stands for):",
            build_answers_block(answers),
            "",
            "SALES-TEAM SELECTIONS FOR THIS TEST:",
            build_script_meta_block(body),
            "",
            "AD SCRIPT TO REVIEW:",
            script or "(empty)",
            "",
            "Review the script and return the structured critique following your rules.",
        ]
    )

    try:
        response = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SCRIPT_SYSTEM_INSTRUCTION,
                temperature=0.4,
                response_mime_type="application/json",
                response_schema=SCRIPT_RESPONSE_SCHEMA,
                # This critique is large + reasoned, so it needs longer than the
                # onboarding endpoints. "Test Script" is a deliberate click with a
                # loading state, so a longer wait is acceptable.
                http_options=types.HttpOptions(timeout=45_000),
            ),
        )

        parsed = json.loads(response.text)

        sections = [
            SectionScore(
                section=str(s.get("section", "")).strip(),
                score=_clamp(s.get("score"), 0, 10),
                comment=str(s.get("comment", "")).strip(),
            )
            for s in (parsed.get("section_breakdown") or [])
            if str(s.get("section", "")).strip()
        ]
        if not sections:
            raise ValueError("model returned no section breakdown")

        overall = _clamp(parsed.get("overall_score"), 0, 100, default=50)
        ea = parsed.get("emotional_angle") or {}
        ca = parsed.get("context_alignment") or {}
        ds = parsed.get("dimension_scores") or {}
        improvements = [
            Improvement(
                title=str(i.get("title", "")).strip(),
                why_it_matters=str(i.get("why_it_matters", "")).strip(),
                suggested_rewrite=str(i.get("suggested_rewrite", "")).strip(),
                metrics_impacted=str(i.get("metrics_impacted", "")).strip(),
            )
            for i in (parsed.get("improvements") or [])
            if str(i.get("title", "")).strip()
        ]

        return ScriptTestResponse(
            overall_score=overall,
            verdict=str(parsed.get("verdict") or "").strip(),
            # Trust the band only if it's one of ours; else derive from the score.
            verdict_band=str(parsed.get("verdict_band") or "").strip() or _band_for(overall),
            emotional_angle=EmotionalAngle(
                label=str(ea.get("label", "")).strip(),
                status=str(ea.get("status", "")).strip(),
                critique=str(ea.get("critique", "")).strip(),
            ),
            context_alignment=ContextAlignment(
                brand_voice_fit=str(ca.get("brand_voice_fit", "")).strip(),
                funnel_stage_fit=str(ca.get("funnel_stage_fit", "")).strip(),
                marketing_angle_fit=str(ca.get("marketing_angle_fit", "")).strip(),
            ),
            dimension_scores=DimensionScores(
                attention=_clamp(ds.get("attention"), 0, 100),
                resonance=_clamp(ds.get("resonance"), 0, 100),
                conversion=_clamp(ds.get("conversion"), 0, 100),
                creative=_clamp(ds.get("creative"), 0, 100),
                marketing_angle_execution=_clamp(ds.get("marketing_angle_execution"), 0, 100),
            ),
            section_breakdown=sections,
            improvements=improvements,
        )

    except Exception as err:  # noqa: BLE001 - never block the sales team
        # Non-blocking fallback: a neutral scorecard so the UI still renders.
        print(f"test-script failed: {err}")
        neutral_sections = [
            SectionScore(section=name, score=5, comment="Couldn't analyze automatically - review manually.")
            for name in [
                "Hook", "Problem / Tension", "Solution / Offer",
                "Social Proof / Credibility", "Call to Action", "Pacing & Tightness",
            ]
        ]
        return ScriptTestResponse(
            overall_score=50,
            verdict="Couldn't complete the AI review - try again.",
            verdict_band="Needs work before going live",
            emotional_angle=EmotionalAngle(
                label=body.marketingAngle or "",
                status="",
                critique="The angle could not be assessed automatically.",
            ),
            context_alignment=ContextAlignment(),
            dimension_scores=DimensionScores(
                attention=50, resonance=50, conversion=50, creative=50,
                marketing_angle_execution=50,
            ),
            section_breakdown=neutral_sections,
            improvements=[
                Improvement(
                    title="Re-run the analysis",
                    why_it_matters="The automated review did not complete for this script.",
                    suggested_rewrite="Click Regenerate, or review the script manually against the brand voice.",
                    metrics_impacted="",
                )
            ],
            fallback=True,
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
