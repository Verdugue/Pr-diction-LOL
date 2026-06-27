"use client";
import { useState, useEffect, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Loader2, X, Search, Shield, Swords, AlertTriangle } from "lucide-react";
import { MatchupInfoButton, type MatchupBreakdown } from "@/components/MatchupPopup";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const ROLES: { key: string; label: string; short: string }[] = [
  { key: "TOP",     label: "Top",     short: "T" },
  { key: "JUNGLE",  label: "Jungle",  short: "J" },
  { key: "MIDDLE",  label: "Mid",     short: "M" },
  { key: "BOTTOM",  label: "ADC",     short: "A" },
  { key: "UTILITY", label: "Support", short: "S" },
];

interface ChampInfo { primary: string; viable: string[]; }
interface PairDetail { pair: string; winrate: number; games: number; }
interface OffMetaDetail { champion: string; role: string; meta_ratio: number; penalty: number; }
interface DraftResult {
  blue_win_probability: number;
  blue_synergy: number;
  red_synergy: number;
  blue_pairs: PairDetail[];
  red_pairs: PairDetail[];
  blue_off_meta: OffMetaDetail[];
  red_off_meta: OffMetaDetail[];
  matchup_breakdown: MatchupBreakdown | null;
}

function ChampionPicker({
  role, value, onChange, allChamps, usedChamps,
}: {
  role: string; value: string; onChange: (v: string) => void;
  allChamps: Record<string, ChampInfo>; usedChamps: Set<string>;
}) {
  const [query, setQuery] = useState(value);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => { setQuery(value); }, [value]);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  // Trier : méta dans ce rôle d'abord, puis alphabétique
  const all = Object.keys(allChamps);
  const filtered = all
    .filter(c => c.toLowerCase().includes(query.toLowerCase()) && !usedChamps.has(c))
    .sort((a, b) => {
      const aViable = allChamps[a]?.viable.includes(role) ? 0 : 1;
      const bViable = allChamps[b]?.viable.includes(role) ? 0 : 1;
      if (aViable !== bViable) return aViable - bViable;
      return a.localeCompare(b);
    })
    .slice(0, 10);

  const isOffMeta = value && !allChamps[value]?.viable.includes(role);

  function select(c: string) { onChange(c); setQuery(c); setOpen(false); }
  function clear() { onChange(""); setQuery(""); setOpen(false); }

  return (
    <div ref={ref} className="relative">
      <div className="flex items-center gap-1.5 rounded-lg px-2.5 py-2"
        style={{
          background: "var(--bg-card)",
          border: `1px solid ${isOffMeta ? "rgba(232,64,87,0.5)" : "var(--border)"}`,
        }}>
        <Search size={11} style={{ color: "var(--muted)", flexShrink: 0 }} />
        <input
          className="flex-1 bg-transparent text-xs outline-none placeholder:opacity-30 min-w-0"
          placeholder={ROLES.find(r => r.key === role)?.label ?? role}
          value={query}
          onChange={e => { setQuery(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
        />
        {isOffMeta && <AlertTriangle size={10} style={{ color: "var(--red)", flexShrink: 0 }} />}
        {value && (
          <button onClick={clear}>
            <X size={10} style={{ color: "var(--muted)" }} />
          </button>
        )}
      </div>
      {open && filtered.length > 0 && (
        <div className="absolute z-50 top-full mt-1 w-full rounded-lg overflow-hidden shadow-xl"
          style={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}>
          {filtered.map(c => {
            const meta = allChamps[c]?.viable.includes(role);
            return (
              <button key={c}
                className="w-full text-left px-3 py-1.5 text-xs hover:bg-white/5 transition-colors flex items-center justify-between gap-2"
                onMouseDown={() => select(c)}>
                <span style={{ color: "var(--text)" }}>{c}</span>
                {!meta && <span style={{ color: "var(--red)", fontSize: 9 }}>off-meta</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function SynergyBars({ pairs }: { pairs: PairDetail[] }) {
  if (!pairs.length) return null;
  return (
    <div className="space-y-1.5 mt-4 pt-4" style={{ borderTop: "1px solid var(--border)" }}>
      <p className="text-xs font-semibold tracking-widest uppercase mb-2" style={{ color: "var(--muted)" }}>
        Synergies
      </p>
      {pairs.slice(0, 5).map(p => {
        const color = p.winrate >= 55 ? "var(--blue)" : p.winrate <= 45 ? "var(--red)" : "var(--muted)";
        return (
          <div key={p.pair}>
            <div className="flex justify-between text-xs mb-0.5">
              <span style={{ color: "var(--muted)" }} className="truncate pr-2">{p.pair}</span>
              <span className="font-mono font-bold shrink-0" style={{ color }}>
                {p.winrate.toFixed(1)}%
                {p.games > 0 && <span className="opacity-40 ml-1 font-normal">({p.games})</span>}
              </span>
            </div>
            <div className="h-1 rounded-full" style={{ background: "var(--bg)" }}>
              <div className="h-full rounded-full"
                style={{ width: `${Math.max(p.winrate - 30, 0) / 70 * 100}%`, background: color }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function OffMetaBadges({ items, color }: { items: OffMetaDetail[]; color: string }) {
  if (!items.length) return null;
  return (
    <div className="mt-3 space-y-1">
      {items.map(o => (
        <div key={o.champion} className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs"
          style={{ background: "rgba(232,64,87,0.08)", border: "1px solid rgba(232,64,87,0.2)" }}>
          <AlertTriangle size={10} style={{ color: "var(--red)" }} />
          <span style={{ color: "var(--text)" }}>{o.champion}</span>
          <span style={{ color: "var(--muted)" }}>({o.role})</span>
          <span className="ml-auto font-mono" style={{ color: "var(--red)" }}>
            -{o.penalty.toFixed(0)}pp
          </span>
          <span style={{ color: "var(--muted)" }}>{o.meta_ratio.toFixed(0)}% méta</span>
        </div>
      ))}
    </div>
  );
}

export default function DraftPage() {
  const router = useRouter();
  const [allChamps, setAllChamps] = useState<Record<string, ChampInfo>>({});
  const [blue, setBlue] = useState<string[]>(["", "", "", "", ""]);
  const [red, setRed]   = useState<string[]>(["", "", "", "", ""]);
  const [result, setResult] = useState<DraftResult | null>(null);
  const [loading, setLoading] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    fetch(`${API}/champions`)
      .then(r => r.json())
      .then(d => setAllChamps(d.champions));
  }, []);

  const predict = useCallback(async (b: string[], r: string[]) => {
    const blueSlots = ROLES.map((role, i) => ({ champion: b[i] ?? "", role: role.key })).filter(s => s.champion);
    const redSlots  = ROLES.map((role, i) => ({ champion: r[i] ?? "", role: role.key })).filter(s => s.champion);
    if (!blueSlots.length && !redSlots.length) { setResult(null); return; }
    setLoading(true);
    try {
      const res = await fetch(`${API}/draft/predict`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ blue: blueSlots, red: redSlots }),
      });
      setResult(await res.json());
    } finally { setLoading(false); }
  }, []);

  function handleChange(team: "blue" | "red", i: number, v: string) {
    const next = (team === "blue" ? blue : red).map((c, j) => j === i ? v : c);
    if (team === "blue") setBlue(next);
    else setRed(next);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      predict(team === "blue" ? next : blue, team === "red" ? next : red);
    }, 300);
  }

  const allUsed = new Set([...blue, ...red].filter(Boolean));
  const prob = result?.blue_win_probability ?? 50;
  const probColor = prob > 60 ? "var(--blue)" : prob < 40 ? "var(--red)" : "var(--gold)";

  return (
    <main className="min-h-screen px-4 py-10 max-w-3xl mx-auto">
      <button onClick={() => router.push("/")}
        className="flex items-center gap-2 text-sm mb-8 opacity-50 hover:opacity-100 transition-opacity"
        style={{ color: "var(--gold)" }}>
        <ArrowLeft size={14} /> Retour
      </button>

      <div className="mb-8">
        <p className="text-xs tracking-widest uppercase mb-1" style={{ color: "var(--gold-dim)" }}>Outil draft</p>
        <h1 className="text-4xl font-black text-gold-gradient">Draft Tester</h1>
        <p className="text-sm mt-1" style={{ color: "var(--muted)" }}>
          Composez deux équipes par rôle. Les picks hors-méta sont pénalisés.
        </p>
      </div>

      {/* Win probability bar */}
      <div className="card-shine rounded-xl p-5 mb-6 glow-gold">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-1.5">
            <Shield size={14} style={{ color: "var(--blue)" }} />
            <span className="text-xs font-bold" style={{ color: "var(--blue)" }}>BLUE</span>
            {result && (
              <span className="text-xs ml-1" style={{ color: "var(--muted)" }}>
                syn. {result.blue_synergy.toFixed(1)}%
              </span>
            )}
          </div>
          {loading
            ? <Loader2 size={18} className="animate-spin" style={{ color: "var(--gold)" }} />
            : (
              <div className="relative flex items-center gap-1.5">
                <span className="text-2xl font-black tabular-nums" style={{ color: probColor }}>
                  {result ? `${prob.toFixed(1)}%` : "—"}
                </span>
                {result?.matchup_breakdown && (
                  <MatchupInfoButton breakdown={result.matchup_breakdown} />
                )}
              </div>
            )
          }
          <div className="flex items-center gap-1.5">
            {result && (
              <span className="text-xs mr-1" style={{ color: "var(--muted)" }}>
                syn. {result.red_synergy.toFixed(1)}%
              </span>
            )}
            <span className="text-xs font-bold" style={{ color: "var(--red)" }}>RED</span>
            <Swords size={14} style={{ color: "var(--red)" }} />
          </div>
        </div>
        <div className="h-3 rounded-full overflow-hidden flex" style={{ background: "rgba(232,64,87,0.3)" }}>
          <div className="h-full rounded-full transition-all duration-500"
            style={{ width: result ? `${prob}%` : "50%", background: "var(--blue)" }} />
        </div>
        {!result && (
          <p className="text-xs text-center mt-2" style={{ color: "var(--muted)" }}>
            Ajoutez des champions pour voir la prédiction
          </p>
        )}
      </div>

      {/* Teams */}
      <div className="grid grid-cols-2 gap-4">
        {(["blue", "red"] as const).map(team => {
          const picks = team === "blue" ? blue : red;
          const pairs = team === "blue" ? result?.blue_pairs : result?.red_pairs;
          const offMeta = team === "blue" ? result?.blue_off_meta : result?.red_off_meta;
          const accent = team === "blue" ? "var(--blue)" : "var(--red)";
          const borderColor = team === "blue" ? "rgba(11,196,227,0.3)" : "rgba(232,64,87,0.3)";

          return (
            <div key={team} className="card-shine rounded-xl p-4" style={{ borderColor }}>
              <div className="flex items-center gap-2 mb-3">
                {team === "blue"
                  ? <Shield size={13} style={{ color: accent }} />
                  : <Swords size={13} style={{ color: accent }} />
                }
                <p className="text-xs font-bold tracking-widest uppercase" style={{ color: accent }}>
                  Équipe {team === "blue" ? "Bleue" : "Rouge"}
                </p>
              </div>

              <div className="space-y-2">
                {ROLES.map((role, i) => (
                  <div key={role.key} className="flex items-center gap-2">
                    <span className="text-xs font-bold w-14 shrink-0" style={{ color: "var(--muted)" }}>
                      {role.label}
                    </span>
                    <div className="flex-1">
                      <ChampionPicker
                        role={role.key}
                        value={picks[i]}
                        onChange={v => handleChange(team, i, v)}
                        allChamps={allChamps}
                        usedChamps={new Set([...allUsed].filter(x => x !== picks[i]))}
                      />
                    </div>
                  </div>
                ))}
              </div>

              {offMeta && offMeta.length > 0 && <OffMetaBadges items={offMeta} color={accent} />}
              {pairs && <SynergyBars pairs={pairs} />}
            </div>
          );
        })}
      </div>

      <button
        onClick={() => { setBlue(["","","","",""]); setRed(["","","","",""]); setResult(null); }}
        className="w-full mt-4 py-3 rounded-xl text-sm font-semibold transition-all"
        style={{ border: "1px solid var(--border)", color: "var(--muted)" }}>
        Réinitialiser
      </button>

      <div className="mt-6 text-center">
        <button onClick={() => router.push("/synergy")}
          className="text-sm opacity-50 hover:opacity-100 transition-opacity font-semibold"
          style={{ color: "var(--gold)" }}>
          Mini-jeu Synergie →
        </button>
      </div>
    </main>
  );
}
