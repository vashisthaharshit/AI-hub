// The orchestration graphic: your repo at the center, the Brain planning,
// and Claude Code and Codex doing the work. Dots travel along the links to
// show work moving between them. Still when the user prefers less motion.

import { useEffect, useState } from "react";

const BRAIN = "#8e9bff";
const CLAUDE = "#f2a65a";
const CODEX = "#58e0c6";

const LINKS = [
  { id: "l1", d: "M260 180 C 250 120, 220 95, 175 82", color: BRAIN, dur: "2.8s" },
  { id: "l2", d: "M175 82 C 120 110, 95 160, 100 228", color: CLAUDE, dur: "3.4s" },
  { id: "l3", d: "M175 82 C 260 60, 360 95, 405 150", color: CODEX, dur: "3.8s" },
  { id: "l4", d: "M100 228 C 160 250, 210 220, 260 180", color: CLAUDE, dur: "2.6s" },
  { id: "l5", d: "M405 150 C 380 200, 320 210, 260 180", color: CODEX, dur: "3s" },
  { id: "l6", d: "M100 228 C 200 320, 330 300, 405 150", color: BRAIN, dur: "5s" },
];

function Node({ x, y, color, label, sub, r = 26, icon }: {
  x: number; y: number; color: string; label: string; sub: string; r?: number; icon: React.ReactNode;
}) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <circle r={r + 14} fill={color} opacity={0.12} className="halo" />
      <circle r={r} fill="#151b2e" stroke={color} strokeWidth={1.5} />
      <g color={color}>{icon}</g>
      <text y={r + 22} textAnchor="middle" fill="#e9edfa" fontSize="13" fontWeight="650" fontFamily="Figtree, sans-serif">{label}</text>
      <text y={r + 38} textAnchor="middle" fill="#7f89ab" fontSize="11" fontFamily="Figtree, sans-serif">{sub}</text>
    </g>
  );
}

const icon = {
  repo: <path d="M-9 -10h14l4 4v16h-18z M5 -10v4h4 M-5 -1h10 M-5 4h7" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />,
  brain: (
    <g fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
      <circle r="3" fill="currentColor" />
      <circle cx="-9" cy="-6" r="2.2" /><circle cx="9" cy="-6" r="2.2" /><circle cx="0" cy="10" r="2.2" />
      <path d="M-7.2 -4.8 L-2.4 -1.6 M7.2 -4.8 L2.4 -1.6 M0 3 L0 7.8" />
    </g>
  ),
  spark: <path d="M0 -11 L2.6 -2.6 L11 0 L2.6 2.6 L0 11 L-2.6 2.6 L-11 0 L-2.6 -2.6 Z" fill="currentColor" opacity=".9" />,
  hex: <path d="M0 -11 L9.5 -5.5 L9.5 5.5 L0 11 L-9.5 5.5 L-9.5 -5.5 Z M0 -4 L4 0 L0 4 L-4 0 Z" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />,
};

export function Constellation() {
  const [still, setStill] = useState(false);
  useEffect(() => {
    const q = window.matchMedia("(prefers-reduced-motion: reduce)");
    setStill(q.matches);
    const on = () => setStill(q.matches);
    q.addEventListener("change", on);
    return () => q.removeEventListener("change", on);
  }, []);

  return (
    <svg viewBox="40 20 430 330" className="constellation" role="img"
      aria-label="Your repo in the middle; the Brain plans the work and Claude Code and Codex do it">
      <defs>
        <radialGradient id="glow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#8e9bff" stopOpacity=".22" />
          <stop offset="100%" stopColor="#8e9bff" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="260" cy="180" r="150" fill="url(#glow)" />
      {LINKS.map((l) => (
        <path key={l.id} id={l.id} d={l.d} stroke={l.color} className={`flow ${l.id === "l6" ? "flow-slow" : ""}`} opacity={0.55} />
      ))}
      {!still && LINKS.map((l, i) => (
        <circle key={l.id + "dot"} r="3" fill={l.color} style={{ filter: `drop-shadow(0 0 4px ${l.color})` }}>
          <animateMotion dur={l.dur} repeatCount="indefinite" begin={`${i * 0.35}s`}>
            <mpath href={`#${l.id}`} />
          </animateMotion>
        </circle>
      ))}
      <Node x={260} y={180} r={30} color="#e9edfa" label="Your repo" sub="one workspace" icon={icon.repo} />
      <Node x={175} y={82} color={BRAIN} label="Brain" sub="plans the steps" icon={icon.brain} />
      <Node x={100} y={228} color={CLAUDE} label="Claude Code" sub="does a step" icon={icon.spark} />
      <Node x={405} y={150} color={CODEX} label="Codex" sub="does a step" icon={icon.hex} />
    </svg>
  );
}
