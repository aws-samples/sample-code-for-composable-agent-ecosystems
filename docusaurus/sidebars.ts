import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docsSidebar: [
    'intro',
    {
      type: 'category',
      label: 'Getting Started',
      items: [
        'getting-started/installation',
        'getting-started/quick-start',
      ],
    },
    {
      type: 'category',
      label: 'Core Concepts',
      items: [
        'core-concepts/architecture',
        'core-concepts/ecosystems',
        'core-concepts/agents-and-orchestration',
        'core-concepts/quorum',
        'core-concepts/domains',
        'core-concepts/catalog',
      ],
    },
    {
      type: 'category',
      label: 'Guides',
      items: [
        'guides/build-an-ecosystem',
        'guides/create-a-domain',
        'guides/create-a-catalog',
        'guides/run-and-review',
        'guides/share-an-ecosystem',
      ],
    },
    {
      type: 'category',
      label: 'Ecosystem Examples',
      items: [
        'examples/example-1',
        'examples/example-2',
        'examples/example-3',
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      items: [
        'reference/ecosystem-yaml',
        'reference/cli-and-make',
        'reference/glossary',
      ],
    },
  ],
};

export default sidebars;
