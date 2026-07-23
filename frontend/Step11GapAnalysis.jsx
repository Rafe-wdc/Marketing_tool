// =============================================================================
// Step11GapAnalysis.jsx
// Drop-in React component for the Brand Brain "Step 11 — Gap Analysis" screen.
//
// - Calls POST /api/brand-brain/analyze-gaps on mount (using all Q1–Q10 answers).
// - Renders each gap as a card with multi-select option boxes.
// - "Reanalyze" re-calls the API, excluding the gaps already shown.
// - On finish it hands the ticked answers back via onFinish(picks).
//
// This file is self-contained (fetch + styles inline) so it drops in with no
// extra deps. Wire it into your wizard where step === 11. Adjust `brandPayload`
// to match your real wizard state, and set VITE_GAPS_URL for prod.
// =============================================================================

import { useEffect, useState, useCallback } from "react";

const GAPS_URL =
  import.meta?.env?.VITE_GAPS_URL ||
  "http://localhost:3001/api/brand-brain/analyze-gaps";

/**
 * @param {object}   props
 * @param {object}   props.brandPayload  { answers: {...q1-q10}, context: {...} }
 * @param {function} props.onFinish      (picks) => void   picks = { [gapId]: string[] }
 * @param {number}   [props.maxGaps=3]
 */
export default function Step11GapAnalysis({ brandPayload, onFinish, maxGaps = 3 }) {
  const [loading, setLoading] = useState(true);
  const [gaps, setGaps] = useState([]);
  const [picks, setPicks] = useState({}); // { [gapId]: string[] }
  const [note, setNote] = useState("Multi-select · your picks feed every AI surface.");

  const analyze = useCallback(
    async (reanalyze = false, shownTitles = []) => {
      setLoading(true);
      try {
        const res = await fetch(GAPS_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...brandPayload,
            max_gaps: maxGaps,
            exclude: reanalyze ? shownTitles : [],
          }),
        });
        const data = await res.json();
        setGaps(data.gaps || []);
        setPicks({});
        if (reanalyze) setNote("Fresh gaps — the AI skipped everything it already asked.");
      } catch (e) {
        console.warn("gap analysis failed", e);
        setNote("Couldn't reach the analyzer — you can continue without this step.");
      } finally {
        setLoading(false);
      }
    },
    [brandPayload, maxGaps]
  );

  // Analyze once when the user lands on Step 11.
  useEffect(() => {
    analyze(false);
  }, [analyze]);

  function toggle(gapId, option) {
    setPicks((p) => {
      const cur = p[gapId] || [];
      const next = cur.includes(option)
        ? cur.filter((o) => o !== option)
        : [...cur, option];
      return { ...p, [gapId]: next };
    });
  }

  const totalPicked = Object.values(picks).reduce((n, arr) => n + arr.length, 0);

  return (
    <div className="bb-gap">
      <style>{CSS}</style>

      <div className="bb-head">
        <span className="bb-kicker">✦ GAP ANALYSIS</span>
        <span className="bb-count">11 / 11</span>
      </div>
      <div className="bb-progress"><i /></div>

      <div className="bb-qid">Q11 · FINAL STEP · AI-DETECTED GAPS</div>
      <h2 className="bb-q">A few things are still missing</h2>
      <p className="bb-lede">
        We analyzed all 10 answers and found context the AI still needs. Tick what
        applies — or hit Reanalyze for a fresh look.
      </p>

      {loading ? (
        <div className="bb-analyzing">
          <span className="bb-spinner" /> Analyzing your profile for genuine gaps…
        </div>
      ) : (
        <div className="bb-gaps">
          {gaps.map((g) => (
            <div className="bb-card" key={g.id}>
              <div className="bb-card-top">
                <div className="bb-badge">◆</div>
                <div>
                  <div className="bb-title">{g.title}</div>
                  <div className="bb-cardq">{g.question}</div>
                  <div className="bb-why">{g.why}</div>
                </div>
                <div className="bb-picked">
                  {(picks[g.id] || []).length > 0 && (
                    <><b>{(picks[g.id] || []).length}</b> selected</>
                  )}
                </div>
              </div>
              <div className="bb-options">
                {g.options.map((opt) => {
                  const on = (picks[g.id] || []).includes(opt);
                  return (
                    <button
                      type="button"
                      key={opt}
                      className={"bb-opt" + (on ? " on" : "")}
                      role="checkbox"
                      aria-checked={on}
                      onClick={() => toggle(g.id, opt)}
                    >
                      <span className="bb-box">{on ? "✓" : ""}</span>
                      <span>{opt}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="bb-train">
        <div className="bb-train-lbl">+ HOW THIS TRAINS THE AI</div>
        <p>
          Closes the last context gaps — sharpens ad-review objections, Script Lab
          angles &amp; Research Watch filters before the Brand Brain locks in.
        </p>
      </div>

      <div className="bb-footer">
        <button
          type="button"
          className="bb-btn ghost"
          disabled={loading}
          onClick={() => analyze(true, gaps.map((g) => g.title))}
        >
          ↻ {loading ? "Reanalyzing…" : "Reanalyze"}
        </button>
        <button
          type="button"
          className="bb-btn primary"
          disabled={loading}
          onClick={() => onFinish?.(picks)}
        >
          Finish &amp; Train AI →
        </button>
      </div>
      <div className="bb-hint">{totalPicked ? `${totalPicked} selected · ` : ""}{note}</div>
    </div>
  );
}

// --- Styles (scoped by the .bb-gap prefix; safe to drop into any app) --------
const CSS = `
.bb-gap { --accent:#7c3aed; --accent2:#3b82f6; --ink:#1e2230; --muted:#868da4;
  --faint:#aab0c4; --line:#e6e9f4; --soft:#f3effe; --softer:#f8f6ff; --border:#d9cbfb;
  --card:#fff; color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
@media (prefers-color-scheme: dark){ .bb-gap{ --ink:#e9ebf4; --muted:#9aa1bb; --faint:#6b7290;
  --line:#262b3a; --soft:#211a3a; --softer:#1b1730; --border:#4a3d78; --card:#1b2030; } }
.bb-gap .bb-head{ display:flex; justify-content:space-between; align-items:center; }
.bb-kicker,.bb-count,.bb-qid,.bb-title,.bb-train-lbl{
  font-family:ui-monospace,"SF Mono",Consolas,monospace; }
.bb-kicker{ font-size:12px; letter-spacing:.16em; color:var(--accent); font-weight:600; }
.bb-count{ font-size:13px; color:var(--muted); }
.bb-progress{ height:6px; border-radius:999px; background:var(--line); margin:14px 0 20px; overflow:hidden; }
.bb-progress>i{ display:block; height:100%; width:100%; background:linear-gradient(90deg,var(--accent),var(--accent2)); }
.bb-qid{ font-size:12px; letter-spacing:.14em; color:var(--faint); }
.bb-q{ margin:8px 0 4px; font-size:26px; font-weight:800; letter-spacing:-.015em; }
.bb-lede{ color:var(--muted); font-size:15px; margin:0 0 22px; max-width:62ch; }
.bb-analyzing{ display:flex; align-items:center; gap:12px; color:var(--muted); font-size:15px; padding:30px 4px; }
.bb-spinner{ width:18px; height:18px; border-radius:50%; border:2.5px solid var(--line);
  border-top-color:var(--accent); animation:bb-spin .8s linear infinite; }
@keyframes bb-spin{ to{ transform:rotate(360deg); } }
@media (prefers-reduced-motion: reduce){ .bb-spinner{ animation:none; } }
.bb-gaps{ display:flex; flex-direction:column; gap:16px; }
.bb-card{ border:1px solid var(--line); border-radius:15px; padding:18px 20px; background:var(--softer); }
.bb-card:hover{ border-color:var(--border); }
.bb-card-top{ display:grid; grid-template-columns:34px 1fr auto; gap:12px; align-items:start; }
.bb-badge{ width:34px; height:34px; border-radius:10px; background:var(--soft); color:var(--accent);
  display:grid; place-items:center; font-size:14px; }
.bb-title{ font-size:12px; letter-spacing:.12em; color:var(--accent); font-weight:700; text-transform:uppercase; }
.bb-cardq{ font-size:17px; font-weight:700; margin:3px 0 0; letter-spacing:-.01em; }
.bb-why{ font-size:13.5px; color:var(--muted); margin:5px 0 0; }
.bb-picked{ font-size:12px; color:var(--faint); white-space:nowrap; padding-top:6px; }
.bb-picked b{ color:var(--accent); }
.bb-options{ display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:10px; margin-top:16px; }
.bb-opt{ display:flex; align-items:center; gap:11px; padding:12px 13px; border-radius:11px;
  border:1.5px solid var(--line); background:var(--card); cursor:pointer; font-size:14.5px;
  font-weight:500; color:var(--ink); text-align:left; transition:all .13s; }
.bb-opt:hover{ border-color:var(--border); }
.bb-box{ width:20px; height:20px; border-radius:6px; border:1.5px solid var(--faint);
  display:grid; place-items:center; flex:0 0 auto; font-size:12px; color:#fff; }
.bb-opt.on{ border-color:var(--accent); background:var(--soft); }
.bb-opt.on .bb-box{ background:var(--accent); border-color:var(--accent); }
.bb-opt:focus-visible{ outline:2px solid var(--accent); outline-offset:2px; }
.bb-train{ margin-top:22px; border-radius:13px; background:var(--soft); border:1px solid var(--border); padding:15px 18px; }
.bb-train-lbl{ font-size:12px; letter-spacing:.14em; color:var(--accent); font-weight:700; }
.bb-train p{ margin:6px 0 0; font-size:14px; }
.bb-footer{ display:flex; justify-content:space-between; gap:12px; margin-top:24px; }
.bb-btn{ border-radius:12px; height:48px; padding:0 22px; font-size:15px; font-weight:700; cursor:pointer;
  display:inline-flex; align-items:center; gap:9px; border:1px solid transparent; }
.bb-btn.ghost{ background:var(--card); border-color:var(--border); color:var(--accent); }
.bb-btn.primary{ background:linear-gradient(135deg,var(--accent),#8b5cf6); color:#fff;
  box-shadow:0 12px 26px -12px var(--accent); }
.bb-btn[disabled]{ opacity:.55; cursor:default; }
.bb-btn:focus-visible{ outline:2px solid var(--accent); outline-offset:2px; }
.bb-hint{ font-size:12.5px; color:var(--faint); margin-top:10px; text-align:center; }
@media (max-width:620px){ .bb-card-top{ grid-template-columns:30px 1fr; } .bb-picked{ grid-column:2; } .bb-q{ font-size:22px; } }
`;
