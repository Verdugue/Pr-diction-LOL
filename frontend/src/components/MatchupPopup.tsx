"use client";
import { Info } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export interface MatchupLane {
  role: string; blue: string; red: string;
  wr_blue: number | null; n_games: number | null;
  role_weight: number; confidence: number; weighted_delta: number | null;
}
export interface MatchupBreakdown {
  lanes: MatchupLane[]; p_matchup: number | null; n_resolved: number;
}

const ROLE_LABELS: Record<string, string> = {
  top: "Top", jungle: "Jungle", mid: "Mid", adc: "ADC", support: "Support",
};

function Popup({ breakdown }: { breakdown: MatchupBreakdown }) {
  const { lanes, p_matchup, n_resolved } = breakdown;

  return (
    <div
      className="absolute right-0 top-8 z-50 w-[420px] rounded-xl p-4 text-left"
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border)",
        boxShadow: "0 12px 40px rgba(0,0,0,0.6)",
      }}
    >
      <p className="text-xs font-bold tracking-widest uppercase mb-3" style={{ color: "var(--gold)" }}>
        Pourquoi ce % ? — Matchups par lane
      </p>

      {n_resolved === 0 ? (
        <p className="text-xs" style={{ color: "var(--muted)" }}>
          Aucun matchup documenté pour ces champions sur OP.GG.
        </p>
      ) : (
        <>
          <div className="space-y-2 mb-3">
            {lanes.map(l => {
              const hasData = l.wr_blue !== null;
              const favor   = hasData ? (l.wr_blue! > 52 ? "blue" : l.wr_blue! < 48 ? "red" : "neutral") : "neutral";
              const wrColor = favor === "blue" ? "var(--blue)" : favor === "red" ? "var(--red)" : "var(--muted)";
              const barPct  = hasData ? Math.max(0, Math.min(100, (l.wr_blue! - 35) / 30 * 100)) : 50;

              return (
                <div key={l.role} className="rounded-lg px-3 py-2"
                  style={{ background: "rgba(255,255,255,0.03)", border: "1px solid var(--border)" }}>
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-bold w-14 shrink-0" style={{ color: "var(--gold-dim)" }}>
                        {ROLE_LABELS[l.role] ?? l.role}
                      </span>
                      <span className="text-xs" style={{ color: "var(--muted)" }}>
                        {l.blue} <span style={{ color: "var(--border)" }}>vs</span> {l.red}
                      </span>
                    </div>
                    {hasData ? (
                      <div className="text-right shrink-0">
                        <span className="text-sm font-black font-mono" style={{ color: wrColor }}>
                          {l.wr_blue!.toFixed(1)}%
                        </span>
                        <span className="text-xs ml-1.5" style={{ color: "var(--muted)" }}>
                          ({l.n_games?.toLocaleString()} games)
                        </span>
                      </div>
                    ) : (
                      <span className="text-xs" style={{ color: "var(--muted)" }}>N/A</span>
                    )}
                  </div>

                  {hasData && (
                    <div className="h-1 rounded-full mb-1.5 overflow-hidden" style={{ background: "var(--bg)" }}>
                      <div className="h-full rounded-full transition-all"
                        style={{ width: `${barPct}%`, background: wrColor }} />
                    </div>
                  )}

                  <div className="flex items-center gap-3 text-xs" style={{ color: "var(--muted)" }}>
                    <span>poids <b style={{ color: "var(--text)" }}>{(l.role_weight * 100).toFixed(0)}%</b></span>
                    {hasData && (
                      <>
                        <span>confiance <b style={{ color: "var(--text)" }}>{(l.confidence * 100).toFixed(0)}%</b></span>
                        <span className="ml-auto font-mono font-bold"
                          style={{ color: (l.weighted_delta ?? 0) > 0 ? "var(--blue)" : (l.weighted_delta ?? 0) < 0 ? "var(--red)" : "var(--muted)" }}>
                          {(l.weighted_delta ?? 0) >= 0 ? "+" : ""}{((l.weighted_delta ?? 0) * 100).toFixed(1)} pp
                        </span>
                      </>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {p_matchup !== null && (
            <div className="rounded-lg px-3 py-2 text-xs flex items-center justify-between"
              style={{ background: "rgba(232,196,112,0.06)", border: "1px solid rgba(232,196,112,0.15)" }}>
              <span style={{ color: "var(--muted)" }}>Avantage matchup (pondéré rôle × confiance)</span>
              <span className="font-black font-mono" style={{ color: "var(--gold)" }}>
                {(p_matchup * 100).toFixed(1)}% blue
              </span>
            </div>
          )}

          <p className="text-xs mt-2" style={{ color: "var(--muted)", opacity: 0.6 }}>
            WR OP.GG = victoires d'équipe complètes, pas 1v1. Jungle pèse 28% car impact global sur la carte.
          </p>
        </>
      )}
    </div>
  );
}

export function MatchupInfoButton({ breakdown }: { breakdown: MatchupBreakdown }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(v => !v)}
        className="rounded-full p-0.5 transition-opacity hover:opacity-100"
        style={{ opacity: open ? 1 : 0.5 }}
      >
        <Info size={14} style={{ color: "var(--gold)" }} />
      </button>
      {open && <Popup breakdown={breakdown} />}
    </div>
  );
}
