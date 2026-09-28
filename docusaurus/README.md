# Tessera documentation site

This is the [Docusaurus](https://docusaurus.io/) site published to GitHub Pages at
`https://aws-samples.github.io/sample-code-for-composable-agent-ecosystems/`.

The site is **usage-focused**: how to install, run, and build with Tessera. The deeper
infrastructure/Terraform documentation lives in the repository's top-level `docs/`
directory.

## Local development

```bash
cd docusaurus
npm ci
npm start          # dev server with hot reload at http://localhost:3000
npm run build      # production build into ./build (what CI publishes)
npm run serve      # serve the production build locally
```

Requires Node.js 20+ (Node 22 recommended).

## Structure

- `docs/` — the documentation content (Markdown/MDX), organised by the sidebar in `sidebars.ts`.
- `src/pages/index.tsx` — the custom landing page.
- `src/css/custom.css` — the Tessera theme.
- `static/img/` — logos, favicon, and example GIFs.
- `docusaurus.config.ts` — site config (title, URL, navbar, footer).

## Publishing

`.github/workflows/gh-pages.yml` builds this site and deploys `docusaurus/build` to GitHub
Pages on every push to `main` that touches `docusaurus/**`. Pull requests get a build check
but never publish.

> **Note:** GitHub Pages must be enabled for the repository (Settings → Pages → Source:
> GitHub Actions). On a private repository this requires GitHub Enterprise Cloud, or the
> repository being public.
