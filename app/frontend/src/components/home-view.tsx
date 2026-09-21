"use client";

// HomeView — the product landing / launchpad shown first after sign-in (technical mode).
//
// It orients a new user (what Tessera is) and gives fast entry into every capability,
// while a live "at a glance" strip (counts passed in from SuperApp — no extra fetches)
// makes it a useful dashboard to return to rather than a static brochure. Purely
// presentational: navigation + counts come in as props so this component owns no state
// and never couples to the platform's data-fetching.

export type HomeNavTarget =
  | "ecosystems"
  | "run"
  | "attention"
  | "builder"
  | "domains"
  | "catalog"
  | "tools"
  | "help"
  | "admin";

interface Feature {
  icon: string;
  title: string;
  blurb: string;
  target: HomeNavTarget;
  adminOnly?: boolean;
}

const FEATURES: Feature[] = [
  { icon: "⬡", title: "Ecosystems", target: "ecosystems", blurb: "Discover and run multi-agent workflows — each a team of specialist agents that collaborate to produce an analysis, decision, or deliverable." },
  { icon: "⚙", title: "Builder", target: "builder", blurb: "Design a new ecosystem conversationally. Describe the goal and the AI builder shapes the agents, tools, and quality gates with you." },
  { icon: "🗂", title: "Domains", target: "domains", blurb: "Reusable, entity-keyed knowledge catalogs that ecosystems draw on — so agents share grounded context across runs." },
  { icon: "📚", title: "Catalog Browser", target: "catalog", blurb: "Explore the knowledge a domain has accumulated: the records, entities, and sources agents can consult." },
  { icon: "🔧", title: "Tool Catalog", target: "tools", blurb: "The tools agents can invoke — web search, code interpreter, memory, and custom gateway targets." },
  { icon: "▶", title: "Runs", target: "run", blurb: "Watch live runs unfold agent-by-agent, and revisit past runs with their full outputs and deliverables." },
  { icon: "🔔", title: "Attention", target: "attention", blurb: "Runs paused on a quality gate or awaiting your decision surface here, so nothing stalls unnoticed." },
  { icon: "👤", title: "Admin", target: "admin", blurb: "Manage users, groups, and ecosystem sharing across your organization.", adminOnly: true },
];

function Stat({ label, value, tone, onClick }: { label: string; value: number | string; tone: "accent" | "amber" | "green"; onClick?: () => void }) {
  const toneCls =
    tone === "amber" ? "text-amber-300"
    : tone === "green" ? "text-green-300"
    : "text-tessera-accent";
  const Wrapper: any = onClick ? "button" : "div";
  return (
    <Wrapper
      onClick={onClick}
      className={`flex-1 min-w-[120px] text-left bg-tessera-card border border-tessera-border rounded-xl px-4 py-3 ${onClick ? "hover:border-gray-500 transition cursor-pointer" : ""}`}
    >
      <div className={`text-2xl font-bold ${toneCls}`}>{value}</div>
      <div className="text-[11px] text-gray-500 uppercase tracking-wider mt-0.5">{label}</div>
    </Wrapper>
  );
}

export function HomeView({
  ecosystemCount,
  runningCount,
  attentionCount,
  isAdmin,
  onNavigate,
}: {
  ecosystemCount: number;
  runningCount: number;
  attentionCount: number;
  isAdmin: boolean;
  onNavigate: (target: HomeNavTarget) => void;
}) {
  const features = FEATURES.filter((f) => !f.adminOnly || isAdmin);

  return (
    <div className="max-w-5xl mx-auto">
      {/* Hero */}
      <section className="relative overflow-hidden rounded-2xl border border-tessera-border bg-tessera-card p-8 mb-6">
        <div className="absolute inset-x-0 top-0 h-[3px] bg-tessera-gradient opacity-90" />
        <div className="flex items-center gap-2 mb-3">
          <span className="text-tessera-accent text-2xl font-bold">⬡</span>
          <span className="text-2xl font-bold tessera-gradient">Tessera</span>
        </div>
        <h1 className="text-2xl md:text-3xl font-bold text-white leading-tight max-w-2xl">
          Compose, run, and govern multi-agent ecosystems.
        </h1>
        <p className="text-sm text-gray-400 mt-3 max-w-2xl leading-relaxed">
          Tessera turns a goal into a coordinated team of AI agents — each with a narrow, non-overlapping
          role — that build on each other&apos;s findings to produce rigorous analyses, decisions, and
          downloadable deliverables. Design them conversationally, run them with full visibility, and keep
          them grounded and reviewable.
        </p>
        <div className="flex flex-wrap gap-3 mt-5">
          <button
            onClick={() => onNavigate("ecosystems")}
            className="px-4 py-2.5 rounded-lg text-sm font-semibold text-white bg-tessera-gradient shadow-[0_4px_14px_rgba(139,92,246,0.3)] hover:opacity-95 transition"
          >
            Browse ecosystems →
          </button>
          <button
            onClick={() => onNavigate("builder")}
            className="px-4 py-2.5 rounded-lg text-sm font-semibold border border-tessera-border bg-tessera-card text-gray-200 hover:border-gray-400 hover:text-white transition"
          >
            ⚙ Build a new one
          </button>
          <button
            onClick={() => onNavigate("help")}
            className="px-4 py-2.5 rounded-lg text-sm font-medium text-gray-400 hover:text-tessera-accent transition"
          >
            New here? Read the guide
          </button>
        </div>
        <p className="text-[10px] text-gray-600 mt-4">Powered by Amazon Bedrock</p>
      </section>

      {/* At a glance — live counts (no extra fetches; passed from SuperApp state). */}
      <section className="flex flex-wrap gap-3 mb-8">
        <Stat label="Ecosystems" value={ecosystemCount} tone="accent" onClick={() => onNavigate("ecosystems")} />
        <Stat label="Runs in progress" value={runningCount} tone="green" onClick={runningCount > 0 ? () => onNavigate("run") : undefined} />
        <Stat label="Needs attention" value={attentionCount} tone="amber" onClick={attentionCount > 0 ? () => onNavigate("attention") : undefined} />
      </section>

      {/* Feature cards */}
      <section>
        <h2 className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-3">Explore</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {features.map((f) => (
            <button
              key={f.target}
              onClick={() => onNavigate(f.target)}
              className="text-left bg-tessera-card border border-tessera-border rounded-xl p-4 hover:border-tessera-accent/50 hover:shadow-lg hover:shadow-tessera-accent/5 transition group"
            >
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-lg">{f.icon}</span>
                <span className="text-sm font-semibold text-white group-hover:text-tessera-accent transition">{f.title}</span>
              </div>
              <p className="text-xs text-gray-400 leading-relaxed">{f.blurb}</p>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
