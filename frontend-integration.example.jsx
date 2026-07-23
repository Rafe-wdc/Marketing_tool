// -----------------------------------------------------------------------------
// FRONTEND SNIPPET — paste this logic into your Brand Brain wizard (React).
//
// This is the ONLY frontend change. When the user clicks "Next" on Q02
// ("Who is the ideal customer?"), we send their draft + all onboarding fields
// to the Python backend, swap in the optimized persona, and always advance.
//
// Rename the state variables (answers, businessInfo, audience, ...) to match
// whatever your wizard already uses. The important part is the fetch call.
// -----------------------------------------------------------------------------

import { useState } from "react";

// Point this at your Python backend. In dev that's the Flask server;
// in prod use your deployed URL or a proxy path.
const REWRITE_URL =
  import.meta?.env?.VITE_REWRITE_URL ||
  "http://localhost:3001/api/brand-brain/rewrite-persona";

export function useRewritePersona() {
  const [rewriting, setRewriting] = useState(false);

  // Call this instead of your normal "advance to Q03" logic.
  async function rewriteThenAdvance({ answers, businessInfo, audience, setAnswers, goToNextQuestion }) {
    // Gather ALL onboarding context that has been collected so far.
    const context = {
      businessType: answers.q01, // Q01 selection (e.g. "Education / Coaching / Consulting")
      industry: businessInfo.industry,
      brandName: businessInfo.brandName,
      website: businessInfo.website,
      audienceShort: audience.whoIsAudience,
      channels: audience.trafficChannels, // e.g. ["Meta", "Google"]
      adBudget: audience.monthlyAdBudget,
    };

    setRewriting(true);
    try {
      const res = await fetch(REWRITE_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ draft: answers.q02, context }),
      });
      const data = await res.json();

      setAnswers((a) => ({
        ...a,
        q02: data.optimized_persona, // optimized text becomes the stored persona
        q02_raw: data.raw, // keep the user's original draft
      }));
    } catch (e) {
      // Non-blocking: if the network/API fails, just keep the raw draft.
      console.warn("persona rewrite failed, keeping raw draft", e);
    } finally {
      setRewriting(false);
      goToNextQuestion(); // ALWAYS advance — never trap the user on Q02
    }
  }

  return { rewriting, rewriteThenAdvance };
}

// -----------------------------------------------------------------------------
// Example usage inside your Q02 component:
//
//   const { rewriting, rewriteThenAdvance } = useRewritePersona();
//
//   <button
//     disabled={rewriting}
//     onClick={() =>
//       rewriteThenAdvance({ answers, businessInfo, audience, setAnswers, goToNextQuestion })
//     }
//   >
//     {rewriting ? "Optimizing…" : "Next →"}
//   </button>
//
// When the brand is finally saved, persist BOTH q02 (optimized) and q02_raw.
// Downstream (Ad Review, Script Lab, Research Watch) should read the optimized one.
// -----------------------------------------------------------------------------


// =============================================================================
// FEATURE #2 — AI-suggested funnel on Q10 ("Walk through the lead-to-sale journey")
//
// Fills the "AI-SUGGESTED FUNNEL (CONFIRM / EDIT)" chips. Trigger it whenever it
// makes sense for your UX — e.g. when the user lands on Q10, or right after they
// type their journey (debounced), or on a "Suggest funnel" button. It uses ALL
// the Brand Brain answers, so it works even if the Q10 text box is empty.
// =============================================================================

const FUNNEL_URL =
  import.meta?.env?.VITE_FUNNEL_URL ||
  "http://localhost:3001/api/brand-brain/suggest-funnel";

export function useSuggestFunnel() {
  const [loading, setLoading] = useState(false);
  const [funnel, setFunnel] = useState([]); // [{ stage, description }]

  async function suggestFunnel({ answers, businessInfo, audience, setAnswers }) {
    // Map your wizard state -> the answers the backend expects.
    const payload = {
      answers: {
        businessType: answers.q01,
        idealCustomer: answers.q02,        // the optimized persona from feature #1
        brandVoice: answers.q03,
        language: answers.q04,
        trafficChannels: answers.q05,      // e.g. ["Meta (Facebook + Instagram)", "YouTube"]
        salesCycle: answers.q06,           // e.g. "1-4 weeks"
        competitors: answers.q07,          // e.g. ["Cult.fit", "IronHaus"]
        marketingGoal: answers.q08,
        journey: answers.q09 || "",        // journey free text (may be empty)
      },
      context: {
        industry: businessInfo.industry,
        brandName: businessInfo.brandName,
        website: businessInfo.website,
        audienceShort: audience.whoIsAudience,
        channels: audience.trafficChannels,
        adBudget: audience.monthlyAdBudget,
      },
    };

    setLoading(true);
    try {
      const res = await fetch(FUNNEL_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();

      setFunnel(data.funnel); // render these as the editable chips
      setAnswers?.((a) => ({
        ...a,
        funnel: data.funnel,               // the confirmed/edited funnel to save
        q10: data.optimized_journey || a.q10, // tidy the journey text too
      }));
    } catch (e) {
      console.warn("funnel suggestion failed", e);
    } finally {
      setLoading(false);
    }
  }

  return { loading, funnel, setFunnel, suggestFunnel };
}

// -----------------------------------------------------------------------------
// Example usage inside your Q10 component:
//
//   const { loading, funnel, suggestFunnel } = useSuggestFunnel();
//
//   useEffect(() => { suggestFunnel({ answers, businessInfo, audience, setAnswers }); }, []);
//
//   {loading ? "Generating funnel…" : funnel.map((s) => (
//     <Chip key={s.stage} title={s.description}>{s.stage}</Chip>
//   ))}
//
// The user can edit/reorder the chips before "Finish & Train AI". Persist the
// final `funnel` array + the tidied `q10` journey with the brand record — it
// powers the Funnel Health report, attribution windows & lead-to-purchase prob.
// -----------------------------------------------------------------------------


// =============================================================================
// FEATURE #3 — Gap analysis (Step 11/11)
//
// After all 10 questions, analyze everything and surface the missing-context
// follow-up questions as multi-select cards. Each gap = { title, question, why,
// options[] }. The "Reanalyze" button re-runs and asks for DIFFERENT gaps by
// passing the titles already shown in `exclude`.
// =============================================================================

const GAPS_URL =
  import.meta?.env?.VITE_GAPS_URL ||
  "http://localhost:3001/api/brand-brain/analyze-gaps";

// Reuse the same answers/context mapping you built for the funnel.
function buildBrandPayload({ answers, businessInfo, audience }) {
  return {
    answers: {
      businessType: answers.q01,
      idealCustomer: answers.q02,
      brandVoice: answers.q03,
      language: answers.q04,
      trafficChannels: answers.q05,
      salesCycle: answers.q06,
      competitors: answers.q07,
      marketingGoal: answers.q08,
      journey: answers.q09 || "",
    },
    context: {
      industry: businessInfo.industry,
      brandName: businessInfo.brandName,
      website: businessInfo.website,
      audienceShort: audience.whoIsAudience,
      channels: audience.trafficChannels,
      adBudget: audience.monthlyAdBudget,
    },
  };
}

export function useGapAnalysis() {
  const [loading, setLoading] = useState(false);
  const [gaps, setGaps] = useState([]);       // [{ id, title, question, why, options }]
  const [picks, setPicks] = useState({});     // { [gapId]: string[] } — ticked options

  // `reanalyze` = true tells the API to skip the gaps already on screen.
  async function analyzeGaps({ answers, businessInfo, audience, reanalyze = false }) {
    const payload = buildBrandPayload({ answers, businessInfo, audience });
    payload.max_gaps = 3;
    if (reanalyze) payload.exclude = gaps.map((g) => g.title); // ask for fresh ones

    setLoading(true);
    try {
      const res = await fetch(GAPS_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      setGaps(data.gaps);
      setPicks({}); // reset ticks for the new set
    } catch (e) {
      console.warn("gap analysis failed", e);
    } finally {
      setLoading(false);
    }
  }

  // Toggle a ticked option for a gap (multi-select checkboxes).
  function toggleOption(gapId, option) {
    setPicks((p) => {
      const cur = p[gapId] || [];
      const next = cur.includes(option) ? cur.filter((o) => o !== option) : [...cur, option];
      return { ...p, [gapId]: next };
    });
  }

  return { loading, gaps, picks, analyzeGaps, toggleOption, setPicks };
}

// -----------------------------------------------------------------------------
// Example usage in your Step 11 component:
//
//   const { loading, gaps, picks, analyzeGaps, toggleOption } = useGapAnalysis();
//
//   useEffect(() => { analyzeGaps({ answers, businessInfo, audience }); }, []);
//
//   {loading && <Spinner label="Analyzing for gaps…" />}
//   {gaps.map((g) => (
//     <div key={g.id} className="gap-card">
//       <h4>{g.title}</h4>
//       <p>{g.question}</p>
//       <small>{g.why}</small>
//       <div className="options">
//         {g.options.map((opt) => (
//           <label key={opt}>
//             <input type="checkbox"
//               checked={(picks[g.id] || []).includes(opt)}
//               onChange={() => toggleOption(g.id, opt)} />
//             {opt}
//           </label>
//         ))}
//       </div>
//     </div>
//   ))}
//
//   <button disabled={loading}
//     onClick={() => analyzeGaps({ answers, businessInfo, audience, reanalyze: true })}>
//     {loading ? "Reanalyzing…" : "Reanalyze"}
//   </button>
//
// On finish, save `picks` (the extra answers) onto the brand record so the
// downstream AI gets the fuller context.
// -----------------------------------------------------------------------------
