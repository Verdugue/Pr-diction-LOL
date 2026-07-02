"use client";
import { use, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ArrowLeft, Loader2, Trophy, Skull, TrendingUp, Swords, Users,
  Flame, Crown, Eye, Building, ShieldAlert, Star, Zap, Bug, TrendingDown, Info, X,
} from "lucide-react";
import { MatchupInfoButton, type MatchupBreakdown } from "@/components/MatchupPopup";
import { champIcon, initDDragon } from "@/lib/ddragon";
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis,
  CartesianGrid, Tooltip, ReferenceLine,
} from "recharts";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface CurvePoint { minute: number; player_win_prob: number; gold_diff: number; kills_diff: number; is_pregame?: boolean; }
interface BlamePlayer {
  pid: number; name: string; champion: string; team: string; won: boolean;
  kda_str: string; kill_participation: number; wards_placed: number;
  wards_killed: number; cs: number; gold: number; impact_score: number;
}
interface KeyEvent { min: number; label: string; team: string; type: string; }
interface PlayerSnap { kills: number; deaths: number; assists: number; gold: number; cs: number; }
interface MinuteSnapshot {
  minute: number; gold_diff: number; towers_blue: number; towers_red: number;
  players: Record<string, PlayerSnap>;
}
interface PlayerAnalysis { facts: string[]; advice: string[]; }
interface GameData {
  match_id: string; blue_won: boolean; duration_min: number;
  curve: CurvePoint[]; key_events: KeyEvent[]; blame: BlamePlayer[];
  pregame_matchup: MatchupBreakdown | null; snapshots: MinuteSnapshot[];
  player_analysis: Record<string, PlayerAnalysis>;
}

interface DecisiveZone {
  start: number; end: number; peak: number; duration: number; dir: "high" | "low";
}

function findDecisiveZones(
  curve: CurvePoint[], thrHi = 75, thrLo = 25, hysteresis = 10, minDur = 4
): DecisiveZone[] {
  const zones: DecisiveZone[] = [];
  let inZone = false, z0 = -1, zd: "high" | "low" = "high";
  const exHi = thrHi - hysteresis, exLo = thrLo + hysteresis;

  for (let i = 0; i < curve.length; i++) {
    const { player_win_prob: p } = curve[i];
    if (!inZone) {
      if      (p > thrHi) { inZone = true; z0 = i; zd = "high"; }
      else if (p < thrLo) { inZone = true; z0 = i; zd = "low";  }
    } else {
      const exiting = (zd === "high" && p < exHi) || (zd === "low" && p > exLo);
      if (exiting) {
        const dur = curve[i - 1].minute - curve[z0].minute;
        const zp  = curve.slice(z0, i).map(x => x.player_win_prob);
        if (dur >= minDur)
          zones.push({ start: curve[z0].minute, end: curve[i - 1].minute,
            peak: zd === "high" ? Math.max(...zp) : Math.min(...zp), duration: dur, dir: zd });
        inZone = false;
        if      (p > thrHi) { inZone = true; z0 = i; zd = "high"; }
        else if (p < thrLo) { inZone = true; z0 = i; zd = "low";  }
      }
    }
  }
  if (inZone && z0 >= 0) {
    const dur = curve[curve.length - 1].minute - curve[z0].minute;
    const zp  = curve.slice(z0).map(x => x.player_win_prob);
    if (dur >= minDur)
      zones.push({ start: curve[z0].minute, end: curve[curve.length - 1].minute,
        peak: zd === "high" ? Math.max(...zp) : Math.min(...zp), duration: dur, dir: zd });
  }
  return zones;
}

function generateZoneExplanation(curve: CurvePoint[], zone: DecisiveZone): string {
  const triggerIdx = curve.findIndex(p => p.minute === zone.start);
  if (triggerIdx < 1) return `La probabilité est passée sous le seuil dès la minute ${zone.start}.`;

  const prev = curve[triggerIdx - 1];
  const curr = curve[triggerIdx];

  // Ce qui a changé entre la minute précédente et le déclenchement
  const goldDelta  = curr.gold_diff - prev.gold_diff;
  const killsDelta = curr.kills_diff - prev.kills_diff;

  const triggers: string[] = [];
  if (Math.abs(goldDelta) >= 300)
    triggers.push(`${goldDelta > 0 ? "+" : ""}${goldDelta.toLocaleString()}g d'écart en 1 min`);
  if (killsDelta !== 0)
    triggers.push(`${Math.abs(killsDelta)} mort${Math.abs(killsDelta) > 1 ? "s" : ""} supplémentaire${Math.abs(killsDelta) > 1 ? "s" : ""}`);

  // Valeur max après le déclenchement (pour "jamais remonté")
  const maxAfter = Math.max(...curve.slice(triggerIdx + 1).map(p => p.player_win_prob));

  const probPrev = prev.player_win_prob.toFixed(0);
  const probCurr = curr.player_win_prob.toFixed(0);
  const threshold = zone.dir === "low" ? 25 : 75;

  let why = `À ${zone.start} min, la win probability chute de ${probPrev}% à ${probCurr}%`;
  if (triggers.length) why += ` — ${triggers.join(" et ")}`;
  why += `.`;

  const whyNotBefore = ` Avant, à ${prev.minute} min, on était encore à ${probPrev}% — au-dessus du seuil critique de ${threshold}%.`;

  const whyNotAfter = maxAfter < 35
    ? ` Ensuite : jamais remonté au-dessus de ${maxAfter.toFixed(0)}% — la game ne s'est jamais rééquilibrée.`
    : ` La game a brièvement failli se rééquilibrer (max ${maxAfter.toFixed(0)}% après) mais sans jamais dépasser le seuil de sortie.`;

  return why + whyNotBefore + whyNotAfter;
}

const EVENT_ICONS: Record<string, React.ReactNode> = {
  first_blood: <Swords size={12} />,
  dragon:      <Flame size={12} />,
  elder_dragon:<Zap size={12} />,
  dragon_soul: <Star size={12} />,
  baron:       <Crown size={12} />,
  rift_herald: <Eye size={12} />,
  tower:       <Building size={12} />,
  inhibitor:   <ShieldAlert size={12} />,
  teamfight:   <Users size={12} />,
  void_grub:   <Bug size={12} />,
  gold_swing:  <TrendingUp size={12} />,
};

function ChampionAvatar({ name, size = 36 }: { name: string; size?: number }) {
  const [err, setErr] = useState(false);
  return (
    <div className="rounded-lg overflow-hidden shrink-0"
      style={{ width: size, height: size, border: "1px solid var(--border)" }}>
      {!err ? (
        <img src={champIcon(name)} alt={name} className="w-full h-full object-cover"
          onError={() => setErr(true)} />
      ) : (
        <div className="w-full h-full flex items-center justify-center text-xs font-bold"
          style={{ background: "var(--bg-hover)", color: "var(--gold)" }}>
          {name.slice(0, 2)}
        </div>
      )}
    </div>
  );
}

function SnapshotModal({ snap, blame, playerTeam, onClose }: {
  snap: MinuteSnapshot; blame: BlamePlayer[]; playerTeam: string; onClose: () => void;
}) {
  const blueTeam = blame.filter(p => p.team === "blue").sort((a, b) => a.pid - b.pid);
  const redTeam  = blame.filter(p => p.team === "red").sort((a, b) => a.pid - b.pid);
  const goldColor = snap.gold_diff > 300 ? "#0bc4e3" : snap.gold_diff < -300 ? "#e84057" : "var(--muted)";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
      onClick={onClose}>
      <div className="rounded-2xl p-6 w-[640px] max-h-[85vh] overflow-y-auto"
        style={{ background: "var(--bg-card)", border: "1px solid var(--border)", boxShadow: "0 24px 80px rgba(0,0,0,0.7)" }}
        onClick={e => e.stopPropagation()}>

        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <div>
            <p className="text-xs tracking-widest uppercase mb-0.5" style={{ color: "var(--gold-dim)" }}>Snapshot</p>
            <p className="text-2xl font-black" style={{ color: "var(--gold)" }}>Minute {snap.minute}</p>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-white/5" style={{ color: "var(--muted)" }}>
            <X size={18} />
          </button>
        </div>

        {/* Résumé global */}
        <div className="grid grid-cols-3 gap-3 mb-5">
          <div className="rounded-xl px-4 py-3 text-center" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid var(--border)" }}>
            <p className="text-xs mb-1" style={{ color: "var(--muted)" }}>Écart gold</p>
            <p className="text-xl font-black font-mono" style={{ color: goldColor }}>
              {snap.gold_diff >= 0 ? "+" : ""}{snap.gold_diff.toLocaleString()}g
            </p>
          </div>
          <div className="rounded-xl px-4 py-3 text-center" style={{ background: "rgba(11,196,227,0.06)", border: "1px solid rgba(11,196,227,0.2)" }}>
            <p className="text-xs mb-1" style={{ color: "var(--blue)" }}>Tours Blue</p>
            <p className="text-xl font-black" style={{ color: "var(--blue)" }}>{snap.towers_blue}</p>
          </div>
          <div className="rounded-xl px-4 py-3 text-center" style={{ background: "rgba(232,64,87,0.06)", border: "1px solid rgba(232,64,87,0.2)" }}>
            <p className="text-xs mb-1" style={{ color: "var(--red)" }}>Tours Red</p>
            <p className="text-xl font-black" style={{ color: "var(--red)" }}>{snap.towers_red}</p>
          </div>
        </div>

        {/* Joueurs — 2 colonnes */}
        <div className="grid grid-cols-2 gap-4">
          {([["blue", blueTeam], ["red", redTeam]] as const).map(([team, players]) => (
            <div key={team}>
              <p className="text-xs font-bold tracking-widest uppercase mb-2"
                style={{ color: team === "blue" ? "var(--blue)" : "var(--red)" }}>
                {team === playerTeam ? "Ton équipe" : "Adversaires"}
              </p>
              <div className="space-y-1.5">
                {players.map(p => {
                  const s = snap.players[String(p.pid)];
                  if (!s) return null;
                  const kdaStr = `${s.kills}/${s.deaths}/${s.assists}`;
                  const accentColor = team === "blue" ? "#0bc4e3" : "#e84057";
                  return (
                    <div key={p.pid} className="flex items-center gap-2.5 rounded-lg px-3 py-2"
                      style={{ background: "rgba(255,255,255,0.03)", border: "1px solid var(--border)" }}>
                      <ChampionAvatar name={p.champion} size={28} />
                      <div className="flex-1 min-w-0">
                        <p className="text-xs font-semibold truncate">{p.champion}</p>
                        <p className="text-xs" style={{ color: "var(--muted)" }}>{s.cs} CS</p>
                      </div>
                      <div className="text-right shrink-0">
                        <p className="text-xs font-mono font-bold" style={{ color: accentColor }}>{kdaStr}</p>
                        <p className="text-xs font-mono" style={{ color: "var(--muted)" }}>
                          {s.gold.toLocaleString()}g
                        </p>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>

        <p className="text-xs text-center mt-4" style={{ color: "var(--muted)", opacity: 0.5 }}>
          Cliquez en dehors pour fermer
        </p>
      </div>
    </div>
  );
}

const EVENT_SHORT: Record<string, string> = {
  first_blood: "FB", dragon: "D", elder_dragon: "E",
  dragon_soul: "DS", baron: "B", rift_herald: "H",
  tower: "T", inhibitor: "Inh", void_grub: "VG",
};

// Only these types get an overlay line on the chart (skip towers to reduce noise)
const OVERLAY_TYPES = new Set(["first_blood","dragon","elder_dragon","dragon_soul","baron","rift_herald","inhibitor"]);

function WinCurve({ curve, playerTeam, blueWon, pregameProb, events, onMinuteClick }: {
  curve: CurvePoint[]; playerTeam: string; blueWon: boolean;
  pregameProb?: number | null; events?: KeyEvent[];
  onMinuteClick?: (minute: number) => void;
}) {
  const playerWon = playerTeam === "blue" ? blueWon : !blueWon;
  const lineColor = playerWon ? "#0bc4e3" : "#e84057";
  const overlayEvents = (events ?? []).filter(e => OVERLAY_TYPES.has(e.type));

  const CustomTooltip = ({ active, payload, label }: any) => {
    if (!active || !payload?.length) return null;
    const p = payload[0].value as number;
    const gd = payload[1]?.value as number;
    // Events at this exact minute or within 0.5 min
    const near = overlayEvents.filter(e => Math.abs(e.min - (label as number)) <= 0.5);
    return (
      <div className="card-shine rounded-lg p-3 text-xs space-y-1.5" style={{ minWidth: 160 }}>
        <p style={{ color: "var(--muted)" }}>{label} min</p>
        <p className="font-bold text-sm" style={{ color: p > 50 ? "#0bc4e3" : "#e84057" }}>
          {p.toFixed(1)}% win
        </p>
        {gd !== undefined && (
          <p style={{ color: gd >= 0 ? "#0bc4e3" : "#e84057" }}>
            {gd >= 0 ? "+" : ""}{gd.toLocaleString()}g diff
          </p>
        )}
        {near.length > 0 && (
          <div className="pt-1.5 space-y-0.5" style={{ borderTop: "1px solid rgba(255,255,255,0.1)" }}>
            {near.map((ev, i) => {
              const mine = ev.team === playerTeam;
              return (
                <p key={i} style={{ color: mine ? "#0bc4e3" : "#e84057" }}>
                  {ev.label}
                </p>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  return (
    <ResponsiveContainer width="100%" height={280}>
      <AreaChart
        data={curve}
        margin={{ top: 10, right: 10, left: -20, bottom: 0 }}
        style={{ cursor: onMinuteClick ? "crosshair" : "default" }}
        onClick={(chartData) => {
          if (chartData?.activeLabel != null && onMinuteClick)
            onMinuteClick(chartData.activeLabel as number);
        }}
      >
        <defs>
          <linearGradient id="grad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={lineColor} stopOpacity={0.2} />
            <stop offset="95%" stopColor={lineColor} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
        <XAxis dataKey="minute" tick={{ fill: "rgba(232,224,208,0.4)", fontSize: 11 }}
          tickFormatter={v => `${v}m`} />
        <YAxis domain={[0, 100]} tick={{ fill: "rgba(232,224,208,0.4)", fontSize: 11 }}
          tickFormatter={v => `${v}%`} />
        <Tooltip content={<CustomTooltip />} />
        <ReferenceLine y={50} stroke="rgba(255,255,255,0.15)" strokeDasharray="4 4" />
        {pregameProb != null && (
          <ReferenceLine
            y={pregameProb}
            stroke="rgba(232,196,112,0.6)"
            strokeDasharray="5 3"
            label={{ value: `Pré-game ${pregameProb.toFixed(1)}%`, fill: "rgba(232,196,112,0.8)", fontSize: 10, position: "insideTopLeft" }}
          />
        )}
        {/* Event overlay lines — snapped to integer minute for categorical XAxis */}
        {overlayEvents.map((ev, i) => {
          const mine = ev.team === playerTeam;
          const color = mine ? "rgba(11,196,227,0.55)" : "rgba(232,64,87,0.55)";
          const short = EVENT_SHORT[ev.type] ?? "·";
          return (
            <ReferenceLine key={`ev-${i}`} x={Math.round(ev.min)} stroke={color} strokeWidth={1.5}
              label={{ value: short, position: "insideTopLeft", fontSize: 8, fill: color }} />
          );
        })}
        <Area type="monotone" dataKey="player_win_prob"
          stroke={lineColor} strokeWidth={2.5} fill="url(#grad)" dot={false} />
        <Area type="monotone" dataKey="gold_diff" hide />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function EventRow({ event, playerTeam }: { event: KeyEvent; playerTeam: string }) {
  const isMyTeam = event.team === playerTeam;
  const accent = isMyTeam ? "var(--blue)" : "var(--red)";
  const icon = EVENT_ICONS[event.type] ?? <TrendingDown size={12} />;

  return (
    <div className="flex items-center gap-3 py-2.5"
      style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
      <span className="text-xs font-mono w-10 shrink-0 text-right" style={{ color: "var(--gold)" }}>
        {event.min}m
      </span>
      <span className="shrink-0" style={{ color: accent }}>{icon}</span>
      <span className="text-sm flex-1">{event.label}</span>
      <span className="text-xs px-2 py-0.5 rounded shrink-0"
        style={{ background: `${accent}15`, color: accent }}>
        {isMyTeam ? "Ton éq." : "Adversaire"}
      </span>
    </div>
  );
}

function BlameRow({ player, rank, teamWon, analysis, defaultOpen = false }: {
  player: BlamePlayer; rank: number; teamWon: boolean;
  analysis?: PlayerAnalysis; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const isFautif = player.impact_score < -5;
  const isCarry  = player.impact_score > 15;
  const accent   = isCarry ? "var(--blue)" : isFautif ? "var(--red)" : "var(--muted)";
  const rowBg    = isCarry ? "rgba(11,196,227,0.04)" : isFautif ? "rgba(232,84,87,0.04)" : "transparent";
  const maxImpact = 80;
  const barW     = Math.min(Math.abs(player.impact_score) / maxImpact * 100, 100);

  const badge = rank === 1 ? (teamWon ? "MVP" : "Fautif #1") : null;
  const badgeColor = teamWon ? "var(--gold)" : "var(--red)";
  const hasAnalysis = analysis && (analysis.facts.length > 0 || analysis.advice.length > 0);

  return (
    <div style={{ borderBottom: "1px solid var(--border)" }}>
      <div
        className="flex items-center gap-3 py-3 px-2 rounded-lg transition-colors"
        style={{ background: rowBg, cursor: hasAnalysis ? "pointer" : "default" }}
        onClick={() => hasAnalysis && setOpen(v => !v)}
      >
        <ChampionAvatar name={player.champion} size={34} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="font-bold text-sm truncate">{player.champion}</span>
            {badge && (
              <span className="text-xs px-1.5 py-0.5 rounded font-bold shrink-0"
                style={{ background: `${badgeColor}20`, color: badgeColor, fontSize: 10 }}>
                {badge}
              </span>
            )}
            {hasAnalysis && (
              <span className="text-xs ml-auto shrink-0" style={{ color: "var(--muted)", opacity: 0.6 }}>
                {open ? "▲" : "▼"} analyse
              </span>
            )}
          </div>
          <span className="text-xs truncate block" style={{ color: "var(--muted)" }}>{player.name}</span>
          <div className="flex items-center gap-2 mt-1.5">
            <div className="flex-1 h-1 rounded-full" style={{ background: "var(--bg)" }}>
              <div className="h-full rounded-full" style={{ width: `${barW}%`, background: accent }} />
            </div>
            <span className="text-xs font-mono font-bold shrink-0 w-10 text-right" style={{ color: accent }}>
              {player.impact_score > 0 ? "+" : ""}{player.impact_score}
            </span>
          </div>
        </div>
        <div className="text-right shrink-0">
          <p className="font-mono text-xs font-semibold">{player.kda_str}</p>
          <p className="text-xs" style={{ color: "var(--muted)" }}>{player.kill_participation}% KP</p>
        </div>
      </div>

      {/* Analyse contextuelle */}
      {open && hasAnalysis && (
        <div className="mx-2 mb-3 rounded-xl overflow-hidden"
          style={{ border: "1px solid rgba(232,64,87,0.2)", background: "rgba(232,64,87,0.04)" }}>
          {analysis!.facts.length > 0 && (
            <div className="px-4 pt-3 pb-2">
              <p className="text-xs font-bold tracking-widest uppercase mb-2" style={{ color: "var(--red)" }}>
                Ce qui s'est passé
              </p>
              <ul className="space-y-1.5">
                {analysis!.facts.map((f, i) => (
                  <li key={i} className="text-xs flex gap-2" style={{ color: "var(--text)" }}>
                    <span style={{ color: "var(--red)", flexShrink: 0 }}>•</span>
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {analysis!.advice.length > 0 && (
            <div className="px-4 py-3" style={{ borderTop: "1px solid rgba(232,196,112,0.15)" }}>
              <p className="text-xs font-bold tracking-widest uppercase mb-2" style={{ color: "var(--gold)" }}>
                À retenir
              </p>
              <ul className="space-y-1.5">
                {analysis!.advice.map((a, i) => (
                  <li key={i} className="text-xs flex gap-2" style={{ color: "var(--text)" }}>
                    <span style={{ color: "var(--gold)", flexShrink: 0 }}>→</span>
                    <span>{a}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function GamePage({ params }: { params: Promise<{ matchId: string }> }) {
  const { matchId } = use(params);
  const searchParams = useSearchParams();
  const router = useRouter();
  const playerTeam = searchParams.get("team") ?? "blue";

  const [data, setData] = useState<GameData | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"curve" | "blame">("curve");
  const [selectedMinute, setSelectedMinute] = useState<number | null>(null);

  useEffect(() => {
    initDDragon();
    // Auto-detect region from match ID prefix (KR_ → asia, EUW1_ → europe)
    const region = matchId.startsWith("KR_") ? "kr"
      : matchId.startsWith("NA1_") ? "na"
      : "euw";
    const endpoint = region === "euw"
      ? `${API}/game/${matchId}?player_team=${playerTeam}`
      : `${API}/pro/game/${matchId}?player_team=${playerTeam}&region=${region}`;
    fetch(endpoint)
      .then(r => r.json())
      .then(setData)
      .finally(() => setLoading(false));
  }, [matchId, playerTeam]);

  if (loading) return (
    <main className="min-h-screen flex items-center justify-center gap-3" style={{ color: "var(--muted)" }}>
      <Loader2 size={20} className="animate-spin" style={{ color: "var(--gold)" }} />
      Analyse en cours...
    </main>
  );

  if (!data) return (
    <main className="min-h-screen flex items-center justify-center" style={{ color: "var(--red)" }}>
      Partie introuvable.
    </main>
  );

  const playerWon = playerTeam === "blue" ? data.blue_won : !data.blue_won;
  const resultColor = playerWon ? "var(--blue)" : "var(--red)";
  const decisiveZones = findDecisiveZones(data.curve);

  // Tri : équipe qui perd → pire d'abord (fautif en haut) ; équipe qui gagne → meilleur d'abord
  const sortTeam = (players: BlamePlayer[], teamWon: boolean) =>
    [...players].sort((a, b) => teamWon
      ? b.impact_score - a.impact_score   // gagnants : meilleur d'abord
      : a.impact_score - b.impact_score   // perdants : pire d'abord (fautif #1 en haut)
    );

  const myTeamWon  = playerWon;
  const oppTeamWon = !playerWon;
  const myTeam  = sortTeam(data.blame.filter(p => p.team === playerTeam), myTeamWon);
  const oppTeam = sortTeam(data.blame.filter(p => p.team !== playerTeam), oppTeamWon);

  // Détection swings de gold depuis la courbe (variation > 1500g entre 2 minutes)
  const goldSwingEvents: KeyEvent[] = data.curve
    .filter((p, i) => i > 0 && Math.abs(p.gold_diff - data.curve[i - 1].gold_diff) > 1500)
    .map(p => ({
      min: p.minute,
      label: `Écart gold ${p.gold_diff >= 0 ? "+" : ""}${p.gold_diff.toLocaleString()}g`,
      team: p.gold_diff > 0 ? playerTeam : playerTeam === "blue" ? "red" : "blue",
      type: "gold_swing",
    }));

  const allEvents = [...data.key_events, ...goldSwingEvents].sort((a, b) => a.min - b.min);

  const TABS = [
    { id: "curve" as const, label: "Courbe win %", icon: <TrendingUp size={14} /> },
    { id: "blame" as const, label: "Qui est fautif ?", icon: <Swords size={14} /> },
  ];

  return (
    <main className="min-h-screen px-4 py-10 max-w-5xl mx-auto">
      <button onClick={() => router.back()}
        className="flex items-center gap-2 text-sm mb-8 opacity-50 hover:opacity-100 transition-opacity"
        style={{ color: "var(--gold)" }}>
        <ArrowLeft size={14} /> Retour
      </button>

      {/* Header */}
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <p className="text-xs tracking-widest uppercase mb-1" style={{ color: "var(--gold-dim)" }}>
            Analyse post-game
          </p>
          <h1 className="text-3xl font-black" style={{ color: resultColor }}>
            {playerWon ? "Victoire" : "Défaite"}
          </h1>
          <p className="text-sm mt-1" style={{ color: "var(--muted)" }}>
            {data.duration_min} min · {allEvents.length} événements clés
          </p>
        </div>
        {/* Carte pré-game matchup */}
        {data.pregame_matchup && data.pregame_matchup.p_matchup !== null && (
          <div className="card-shine rounded-xl p-4 text-right shrink-0">
            <div className="flex items-center justify-end gap-1.5 mb-1">
              <p className="text-xs" style={{ color: "var(--muted)" }}>Pré-game blue</p>
              <MatchupInfoButton breakdown={data.pregame_matchup} />
            </div>
            <p className="text-2xl font-black" style={{ color: "var(--gold)" }}>
              {(data.pregame_matchup.p_matchup * 100).toFixed(1)}%
            </p>
            <p className="text-xs mt-0.5" style={{ color: "var(--muted)" }}>
              {data.pregame_matchup.n_resolved} lane{data.pregame_matchup.n_resolved > 1 ? "s" : ""} documentée{data.pregame_matchup.n_resolved > 1 ? "s" : ""}
            </p>
          </div>
        )}

        {decisiveZones.length > 0 && (
          <div className="card-shine rounded-xl p-4 text-right shrink-0" style={{ minWidth: 140 }}>
            {/* Label + icône info */}
            <div className="flex items-center justify-end gap-1.5 mb-1">
              <p className="text-xs" style={{ color: "var(--muted)" }}>
                {decisiveZones.length === 1 ? "Partie décidée à" : "Fenêtres décisives"}
              </p>
              {decisiveZones.map((z, i) => (
                <div key={i} className="relative group">
                  <Info size={12} className="cursor-help shrink-0" style={{ color: "var(--muted)" }} />
                  <div
                    className="absolute right-0 top-5 z-20 hidden group-hover:block w-80 p-3 rounded-xl text-xs text-left leading-relaxed pointer-events-none"
                    style={{
                      background: "var(--bg-card)",
                      border: "1px solid var(--border)",
                      boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
                    }}
                  >
                    <p className="font-semibold mb-1.5" style={{ color: "var(--gold)" }}>
                      Pourquoi {z.start} min ?
                    </p>
                    <p style={{ color: "var(--muted)" }}>
                      {generateZoneExplanation(data.curve, z)}
                    </p>
                  </div>
                </div>
              ))}
            </div>

            {/* Valeur(s) */}
            {decisiveZones.length === 1 ? (
              <p className="text-2xl font-black" style={{ color: "var(--gold)" }}>
                {decisiveZones[0].start} min
              </p>
            ) : (
              <div className="space-y-1">
                {decisiveZones.map((z, i) => (
                  <p key={i} className="font-black" style={{ color: "var(--gold)", fontSize: 15 }}>
                    {z.start}→{z.end} min
                    <span className="font-normal text-xs ml-1" style={{ color: "var(--muted)" }}>
                      ({z.duration}m)
                    </span>
                  </p>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <hr className="divider-gold mb-6" />

      {/* Tabs */}
      <div className="flex gap-1 mb-6 p-1 rounded-xl" style={{ background: "var(--bg-card)" }}>
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-semibold transition-all"
            style={{
              background: tab === t.id ? "var(--bg-hover)" : "transparent",
              color: tab === t.id ? "var(--text)" : "var(--muted)",
              border: tab === t.id ? "1px solid var(--border)" : "1px solid transparent",
            }}>
            {t.icon}{t.label}
          </button>
        ))}
      </div>

      {/* ── Curve tab ── */}
      {tab === "curve" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Courbe */}
          <div className="lg:col-span-2 card-shine rounded-xl p-5">
            <p className="text-sm font-semibold mb-4" style={{ color: "var(--muted)" }}>
              Probabilité de victoire — minute par minute
            </p>
            <p className="text-xs mt-1 mb-0" style={{ color: "var(--muted)", opacity: 0.5 }}>
              Cliquez sur un point du graph pour voir le tableau de bord de cette minute
            </p>
            <WinCurve
              curve={data.curve}
              playerTeam={playerTeam}
              blueWon={data.blue_won}
              events={data.key_events}
              onMinuteClick={setSelectedMinute}
              pregameProb={
                data.pregame_matchup?.p_matchup != null
                  ? (playerTeam === "blue"
                      ? data.pregame_matchup.p_matchup * 100
                      : (1 - data.pregame_matchup.p_matchup) * 100)
                  : null
              }
            />
          </div>

          {/* Timeline des events — colonne droite */}
          <div className="card-shine rounded-xl p-5 overflow-y-auto" style={{ maxHeight: 380 }}>
            <p className="text-sm font-semibold mb-2" style={{ color: "var(--muted)" }}>
              Moments clés
            </p>
            {allEvents.length === 0 ? (
              <p className="text-xs" style={{ color: "var(--muted)" }}>Aucun événement enregistré.</p>
            ) : (
              allEvents.map((ev, i) => (
                <EventRow key={i} event={ev} playerTeam={playerTeam} />
              ))
            )}
          </div>
        </div>
      )}

      {/* ── Blame tab ── */}
      {tab === "blame" && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-4">
            {/* Ton équipe */}
            <div className="card-shine rounded-xl p-4">
              <div className="flex items-center gap-2 mb-3 pb-3"
                style={{ borderBottom: "1px solid var(--border)" }}>
                <Users size={14} style={{ color: myTeamWon ? "var(--blue)" : "var(--red)" }} />
                <span className="font-bold text-sm">Ton équipe</span>
                {myTeamWon
                  ? <Trophy size={13} className="ml-auto" style={{ color: "var(--gold)" }} />
                  : <Skull size={13} className="ml-auto" style={{ color: "var(--red)" }} />}
              </div>
              {myTeam.map((p, i) => (
                <BlameRow key={p.pid} player={p} rank={i + 1} teamWon={myTeamWon}
                  analysis={data.player_analysis?.[String(p.pid)]}
                  defaultOpen={!myTeamWon && i === 0} />
              ))}
            </div>

            {/* Équipe adverse */}
            <div className="card-shine rounded-xl p-4">
              <div className="flex items-center gap-2 mb-3 pb-3"
                style={{ borderBottom: "1px solid var(--border)" }}>
                <Swords size={14} style={{ color: oppTeamWon ? "var(--blue)" : "var(--red)" }} />
                <span className="font-bold text-sm">Équipe adverse</span>
                {oppTeamWon
                  ? <Trophy size={13} className="ml-auto" style={{ color: "var(--gold)" }} />
                  : <Skull size={13} className="ml-auto" style={{ color: "var(--red)" }} />}
              </div>
              {oppTeam.map((p, i) => (
                <BlameRow key={p.pid} player={p} rank={i + 1} teamWon={oppTeamWon}
                  analysis={data.player_analysis?.[String(p.pid)]}
                  defaultOpen={!oppTeamWon && i === 0} />
              ))}
            </div>
          </div>

          <p className="text-xs text-center pb-2" style={{ color: "var(--muted)" }}>
            Score d&apos;impact = kill participation + vision − pénalité morts
            · Perdants triés du plus au moins fautif
          </p>
        </div>
      )}

      {/* Snapshot modal */}
      {selectedMinute != null && (() => {
        const snap = data.snapshots.find(s => s.minute === selectedMinute);
        return snap ? (
          <SnapshotModal
            snap={snap}
            blame={data.blame}
            playerTeam={playerTeam}
            onClose={() => setSelectedMinute(null)}
          />
        ) : null;
      })()}
    </main>
  );
}
