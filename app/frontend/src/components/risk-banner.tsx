"use client";

import { useEffect, useRef } from "react";

// Per-category accent. Category -> archetype color for the animated hero banner (WS-7).
const CAT_COLOR: Record<string, string> = {
  "data-breach": "#3b82f6", "ip-theft": "#3b82f6",
  "ai-agent-compromise": "#a855f7",
  "identity-access": "#22d3ee",
  "service-disruption": "#14b8a6", "supply-chain": "#14b8a6",
  "ransomware": "#ef4444",
  "insider-threat": "#f59e0b",
  "regulatory-compliance": "#8b5cf6",
  "reputational": "#ec4899",
};

// A lightweight constellation particle field tinted per risk category. Non-interactive,
// pauses off-screen (IntersectionObserver), and honors prefers-reduced-motion (static).
export function RiskBanner({ category }: { category?: string }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const color = CAT_COLOR[category || ""] || "#3b82f6";
  useEffect(() => {
    const canvas = ref.current; if (!canvas) return;
    const ctx = canvas.getContext("2d"); if (!ctx) return;
    const reduce = typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const DPR = Math.min((typeof window !== "undefined" && window.devicePixelRatio) || 1, 2);
    let w = 0, h = 0, raf = 0, running = true;
    const resize = () => { const r = canvas.getBoundingClientRect(); w = r.width; h = r.height; canvas.width = w * DPR; canvas.height = h * DPR; ctx.setTransform(DPR, 0, 0, DPR, 0, 0); };
    resize();
    const rnd = (a: number, b: number) => a + Math.random() * (b - a);
    const N = Math.max(16, Math.min(44, Math.floor(w / 22)));
    const P = Array.from({ length: N }, () => ({ x: rnd(0, w), y: rnd(0, h), vx: rnd(-0.25, 0.25), vy: rnd(-0.25, 0.25), r: rnd(1, 2.4) }));
    const draw = () => {
      ctx.clearRect(0, 0, w, h);
      for (let i = 0; i < P.length; i++) {
        for (let j = i + 1; j < P.length; j++) {
          const a = P[i], b = P[j]; const d = Math.hypot(a.x - b.x, a.y - b.y);
          if (d < 78) { ctx.globalAlpha = (1 - d / 78) * 0.3; ctx.strokeStyle = color; ctx.lineWidth = 0.6; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); }
        }
      }
      ctx.globalAlpha = 0.85;
      for (const p of P) {
        ctx.fillStyle = color; ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill();
        if (!reduce) { p.x += p.vx; p.y += p.vy; if (p.x < 0 || p.x > w) p.vx *= -1; if (p.y < 0 || p.y > h) p.vy *= -1; }
      }
      ctx.globalAlpha = 1;
      if (!reduce && running) raf = requestAnimationFrame(draw);
    };
    draw();
    const io = new IntersectionObserver(([e]) => { running = e.isIntersecting; if (running && !reduce) { cancelAnimationFrame(raf); raf = requestAnimationFrame(draw); } else cancelAnimationFrame(raf); });
    io.observe(canvas);
    const onResize = () => { resize(); if (reduce) draw(); };
    window.addEventListener("resize", onResize);
    return () => { cancelAnimationFrame(raf); io.disconnect(); window.removeEventListener("resize", onResize); };
  }, [category, color]);
  return <canvas ref={ref} aria-hidden style={{ position: "absolute", inset: 0, width: "100%", height: "100%", opacity: 0.5, pointerEvents: "none" }} />;
}

export const categoryColor = (category?: string) => CAT_COLOR[category || ""] || "#3b82f6";
