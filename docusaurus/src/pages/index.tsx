import type {ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import useBaseUrl from '@docusaurus/useBaseUrl';
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

type ComparisonRow = {
  aspect: string;
  tessera: string;
  codeFirst: string;
};

const COMPARISON: ComparisonRow[] = [
  {aspect: 'Define a workflow', tessera: 'YAML config', codeFirst: 'Write & maintain Python'},
  {aspect: 'Grounding', tessera: 'Versioned catalog + grounding checks', codeFirst: 'Build it yourself'},
  {aspect: 'Quality', tessera: 'Challenger, quorum, format/citation/grounding gates', codeFirst: 'Build it yourself'},
  {aspect: 'UI', tessera: 'Live generative UI streamed to a web app', codeFirst: 'None / build your own'},
  {aspect: 'Deployment', tessera: 'Terraform to your AWS account', codeFirst: 'Bring your own infra'},
  {aspect: 'Secrets', tessera: 'Zero static secrets (vault / Secrets Manager)', codeFirst: 'Varies'},
  {aspect: 'Evaluation', tessera: 'Opt-in eval harness (regression + sign-off)', codeFirst: 'Build it yourself'},
];

function Hero(): ReactNode {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className={styles.hero}>
      <div className="container">
        <img
          className={styles.heroLogo}
          src={useBaseUrl('/img/tessera-logo.svg')}
          alt="Tessera"
          width={96}
          height={96}
        />
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

function SeeItRun(): ReactNode {
  return (
    <section className={styles.showcase}>
      <div className="container">
        <h2 className={styles.sectionHeading}>Watch an ecosystem run</h2>
        <p className={styles.sectionSub}>
          Launch, and the specialist agents stream across a live execution graph into a
          synthesizer, an adversarial challenger, and a final report.
        </p>
        <img
          className={styles.showcaseImg}
          src={useBaseUrl('/img/hero-run.gif')}
          alt="A Tessera ecosystem running: specialist agents stream across a live execution graph into a synthesizer, drafter, challenger, and final report."
          loading="lazy"
        />
        <p className={styles.showcaseCaption}>
          <Link to="/docs/demos/buyer-side-property-report">See the full walkthrough →</Link>
        </p>
      </div>
    </section>
  );
}

function WhyTessera(): ReactNode {
  return (
    <section className={styles.why}>
      <div className="container">
        <h2 className={styles.sectionHeading}>Why Tessera</h2>
        <p className={styles.sectionSub}>
          Code-first frameworks give you primitives and leave grounding, quality, UI, and
          deployment to you. Tessera is opinionated about exactly those parts.
        </p>
        <div className={styles.tableWrap}>
          <table className={styles.compareTable}>
            <thead>
              <tr>
                <th></th>
                <th>Tessera</th>
                <th>Code-first frameworks<br/>(LangGraph · CrewAI · AutoGen)</th>
              </tr>
            </thead>
            <tbody>
              {COMPARISON.map((row) => (
                <tr key={row.aspect}>
                  <td><strong>{row.aspect}</strong></td>
                  <td className={styles.tesseraCell}>{row.tessera}</td>
                  <td className={styles.codeFirstCell}>{row.codeFirst}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
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
          {FEATURES.map((f, i) => (
            <div className="col col--4" key={f.title} style={{marginBottom: '1.5rem'}}>
              <div className={clsx(styles.featureCard, styles[`tint${i % 6}`])}>
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
      title="Tessera — Multi-agent AI workflows on AWS"
      description="Multi-agent AI workflows on AWS, declared in YAML. Compiled to AWS Step Functions, run on Amazon Bedrock AgentCore, streamed as live generative UI.">
      <Hero />
      <main>
        <Features />
        <SeeItRun />
        <WhyTessera />
      </main>
    </Layout>
  );
}
