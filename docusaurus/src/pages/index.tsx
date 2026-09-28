import type {ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';

import styles from './index.module.css';

type Feature = {
  icon: string;
  title: string;
  body: string;
};

const FEATURES: Feature[] = [
  {
    icon: '🧩',
    title: 'Config-driven ecosystems',
    body: 'Define multi-agent workflows as YAML — agents, inputs, dependencies, and outputs. No code to write; the compiler turns your ecosystem into a runnable pipeline.',
  },
  {
    icon: '🤝',
    title: 'Multi-agent orchestration',
    body: 'Agents run as a dependency graph (DAG) across parallel swarms. Each agent has a focused role, its own tools, and shared memory to build on upstream work.',
  },
  {
    icon: '📚',
    title: 'Domains & catalog',
    body: 'Ground agents in curated, versioned knowledge. Domains ingest regulatory and reference sources into a catalog that agents cite instead of hallucinating.',
  },
  {
    icon: '⚖️',
    title: 'Quorum deliberation',
    body: 'Empanel a set of scrutineer personas, let them deliberate, and produce a defensible verdict — for reviews that need more than a single opinion.',
  },
  {
    icon: '✅',
    title: 'Built-in quality gates',
    body: 'Every run is checked for output format, citations, and grounding — internal (Bedrock Guardrails) and external (web search) — with an optional challenger agent.',
  },
  {
    icon: '📊',
    title: 'Live web UI',
    body: 'Launch runs, watch the agent map stream in real time, review the generated report, and give inline feedback — all from the browser.',
  },
];

function Hero(): ReactNode {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className={styles.hero}>
      <div className="container">
        <h1 className={styles.heroTitle}>
          <span className="tessera-gradient-text">Tessera</span>
        </h1>
        <p className={styles.heroTagline}>{siteConfig.tagline}</p>
        <div className={styles.buttons}>
          <Link
            className="button button--primary button--lg"
            to="/docs/getting-started/quick-start">
            Get Started
          </Link>
          <Link
            className="button button--secondary button--lg"
            to="/docs/intro">
            What is Tessera?
          </Link>
        </div>

        <div className={styles.terminal}>
          <div className={styles.terminalBar}>
            <span className={clsx(styles.dot, styles.dotRed)} />
            <span className={clsx(styles.dot, styles.dotYellow)} />
            <span className={clsx(styles.dot, styles.dotGreen)} />
          </div>
          <pre className={styles.terminalBody}>
            <span className={styles.comment}># Seed content and compile every ecosystem</span>
            {'\n'}
            <span className={styles.prompt}>$</span> make seed
            {'\n\n'}
            <span className={styles.comment}># Open the UI and launch a run</span>
            {'\n'}
            <span className={styles.prompt}>$</span> terraform -chdir=infra output -raw frontend_url
            {'\n'}
            <span style={{color: '#27c93f'}}>✓</span> https://d3xamplecloudfront.net
          </pre>
        </div>
      </div>
    </header>
  );
}

function Features(): ReactNode {
  return (
    <section className={styles.features}>
      <div className="container">
        <h2 className={styles.sectionHeading}>Orchestrate agents through configuration</h2>
        <p className={styles.sectionSub}>
          Everything you need to build, ground, and run multi-agent workflows on AWS.
        </p>
        <div className="row">
          {FEATURES.map((f) => (
            <div className="col col--4" key={f.title} style={{marginBottom: '1.5rem'}}>
              <div className={styles.featureCard}>
                <div className={styles.featureIcon}>{f.icon}</div>
                <div className={styles.featureTitle}>{f.title}</div>
                <p className={styles.featureBody}>{f.body}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

export default function Home(): ReactNode {
  return (
    <Layout
      title="Tessera — Composable agentic security platform"
      description="Orchestrate multi-agent workflows on AWS through configuration, not code.">
      <Hero />
      <main>
        <Features />
      </main>
    </Layout>
  );
}
