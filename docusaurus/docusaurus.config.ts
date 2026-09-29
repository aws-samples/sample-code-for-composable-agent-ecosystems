import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// GitHub Pages serves this site from two different hosts depending on visibility:
//   - PRIVATE (during review): a bare host like https://<random>.pages.github.io/ ,
//     which serves at the ROOT, so baseUrl must be '/'.
//   - PUBLIC (once shipped): https://aws-samples.github.io/<repo>/ , so baseUrl must be
//     the repo path.
// To work in both phases without editing code, DOCS_URL / DOCS_BASE_URL can be set at
// build time. The defaults below are the PRIVATE-review values (root); the workflow sets
// the public values when the site goes public.
const url = process.env.DOCS_URL || 'https://aws-samples.github.io';
const baseUrl = process.env.DOCS_BASE_URL || '/';

const config: Config = {
  title: 'Tessera',
  tagline: 'A composable platform for building multi-agent workflows through configuration, not code',
  favicon: 'img/favicon.svg',

  url,
  baseUrl,

  organizationName: 'aws-samples',
  projectName: 'sample-code-for-composable-agent-ecosystems',

  // Docs-heavy sample site: surface broken links loudly so the build fails on them.
  onBrokenLinks: 'throw',

  markdown: {
    mermaid: true,
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },

  themes: ['@docusaurus/theme-mermaid'],

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          routeBasePath: 'docs',
          editUrl:
            'https://github.com/aws-samples/sample-code-for-composable-agent-ecosystems/tree/main/docusaurus/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    image: 'img/tessera-social-card.png',
    colorMode: {
      defaultMode: 'dark',
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'Tessera',
      logo: {
        alt: 'Tessera',
        src: 'img/tessera-mark.svg',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'docsSidebar',
          position: 'left',
          label: 'Docs',
        },
        {
          href: 'https://github.com/aws-samples/sample-code-for-composable-agent-ecosystems',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {label: 'Introduction', to: '/docs/intro'},
            {label: 'Getting Started', to: '/docs/getting-started/installation'},
            {label: 'Core Concepts', to: '/docs/core-concepts/architecture'},
            {label: 'Guides', to: '/docs/guides/build-an-ecosystem'},
          ],
        },
        {
          title: 'More',
          items: [
            {label: 'Ecosystem Examples', to: '/docs/examples/example-1'},
            {label: 'Reference', to: '/docs/reference/ecosystem-yaml'},
            {
              label: 'GitHub',
              href: 'https://github.com/aws-samples/sample-code-for-composable-agent-ecosystems',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} Amazon.com, Inc. or its affiliates. Built with Docusaurus.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['bash', 'yaml', 'json', 'python'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
