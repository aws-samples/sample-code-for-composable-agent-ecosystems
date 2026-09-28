import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// The GitHub Pages URL for this repo. baseUrl matches the repo name so assets
// resolve under https://aws-samples.github.io/sample-code-for-composable-agent-ecosystems/.
const config: Config = {
  title: 'Tessera',
  tagline: 'Composable agentic security platform — orchestrate multi-agent workflows through configuration, not code',
  favicon: 'img/favicon.svg',

  url: 'https://aws-samples.github.io',
  baseUrl: '/sample-code-for-composable-agent-ecosystems/',

  organizationName: 'aws-samples',
  projectName: 'sample-code-for-composable-agent-ecosystems',

  // Docs-heavy sample site: surface broken links loudly so the build fails on them.
  onBrokenLinks: 'throw',

  markdown: {
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },

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
