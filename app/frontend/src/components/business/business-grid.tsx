"use client";

/**
 * Business solution metadata helpers (docs/spec-business-view.md §4.1).
 * Fallback-driven from eco metadata (name/description/category) so EVERY eco renders with
 * no config; a `presentation:` block just enriches these fields. No eco-specific code.
 *
 * NOTE: the card-wall "Solutions" page this file used to export was removed — with dozens
 * of ecosystems (most without a category or distinct description) it was noise. Choosing a
 * solution now happens in the searchable SolutionPicker modal.
 */

export interface BizEco {
  id: string;
  name?: string;
  description?: string;
  category?: string;
  tags?: string[];
  presentation?: {
    business_title?: string;
    tagline?: string;
    hero_outcome?: string;
    category?: string;
    what_you_get?: string;
    time_estimate?: string;
  };
}

const prettify = (id: string, name?: string) =>
  (name && name.trim()) ||
  id.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

const firstSentence = (s?: string) => {
  if (!s) return "";
  const t = s.replace(/\s+/g, " ").trim();
  const i = t.search(/\.\s/);
  return i > 12 && i < 160 ? t.slice(0, i + 1) : t.slice(0, 160);
};

const catIcon = (cat: string) => {
  const c = (cat || "").toLowerCase();
  if (c.includes("risk") || c.includes("compliance")) return "🛡️";
  if (c.includes("vendor")) return "🔎";
  if (c.includes("finance") || c.includes("invest")) return "📈";
  if (c.includes("ops") || c.includes("incident")) return "🚨";
  if (c.includes("legal")) return "⚖️";
  if (c.includes("research") || c.includes("analysis")) return "🔬";
  if (c.includes("health") || c.includes("clinic")) return "🩺";
  if (c.includes("creative")) return "🎨";
  return "◆";
};

export function describeEco(e: BizEco) {
  const p = e.presentation || {};
  const category = p.category || e.category || (e.tags && e.tags[0]) || "Solutions";
  return {
    id: e.id,
    title: p.business_title || prettify(e.id, e.name),
    tagline: p.tagline || firstSentence(e.description) || "Run this solution on your inputs.",
    hero: p.hero_outcome || p.what_you_get || "",
    category,
    icon: catIcon(category),
    time: p.time_estimate || "",
  };
}
