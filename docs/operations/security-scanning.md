# Security scanning (ASH) and suppressions

Tessera is scanned with the [Automated Security Helper (ASH)](https://awslabs.github.io/automated-security-helper/),
which runs several scanners over the repo. This page explains how to run the scan and how
findings are suppressed **in-source** so the suppressions travel with the code (no
scan-config file has to ship with the repo).

## Running ASH locally

```bash
uvx git+https://github.com/awslabs/automated-security-helper.git@v3 --mode local \
  --config-overrides 'global_settings.ignore_paths=[{"path":"**/.terraform","reason":"gitignored provider cache"},{"path":"**/.terraform/**","reason":"gitignored provider cache"},{"path":"**/node_modules","reason":"gitignored deps"},{"path":"**/node_modules/**","reason":"gitignored deps"}]'
```

- The `ignore_paths` override excludes two **gitignored** trees that are never part of the
  shipped code but that scanners would otherwise walk: `infra/.terraform/` (downloaded
  provider binaries — grype flags CVEs in their compiled Go stdlib) and `**/node_modules/`
  (frontend dependency tree — npm-audit/grype noise). Excluding them makes the scan reflect
  only the code and dependency manifests we actually ship.
- To see every finding regardless of suppressions, add `--ignore-suppressions`.

### Which scanners run

`bandit` and `semgrep` (Python/JS SAST), `checkov` (Terraform IaC), `detect-secrets`,
`grype` (dependency CVEs), and `npm-audit` run when their tool is available. `cdk-nag` and
`cfn-nag` show **MISSING** and are **not applicable** — this repo is Terraform, not CDK or
CloudFormation (checkov is the IaC scanner in use). `syft`/`opengrep` are optional and, if
their binaries aren't installed, also show MISSING; their domains are already covered by
grype and semgrep respectively. `grype` requires the `grype` binary on `PATH`
(`brew install grype`), otherwise it shows MISSING.

## How suppressions work (in-source, per scanner)

Suppressions live **in the source files**, not in a committed ASH config. This is
deliberate: the `.ash/` directory (ASH's config + output) is **gitignored**, so anything
placed there would not travel with the repo. Each scanner has its own native in-source
mechanism:

| Scanner | Mechanism | Where |
|---|---|---|
| **checkov** | inline `#checkov:skip=<RULE>:<reason>` comment inside the resource block | the `.tf` files |
| **detect-secrets** | inline `# pragma: allowlist secret` on the flagged line | the source line itself |
| **grype** | `ignore:` list keyed by vulnerability id, with a `reason` | `.grype.yaml` (tracked at repo root) |

Every suppression carries a written justification. Real findings are **not** suppressed.

### checkov (`#checkov:skip`)

Findings that are accepted-risk, not-applicable, or false positives carry an inline skip
with a reason, e.g.:

```hcl
resource "aws_s3_bucket" "runs" {
  #checkov:skip=CKV_AWS_144:Cross-region replication is out of scope for this single-region deployment. Accepted risk.
  ...
}
```

The skip must be **inside** the resource block (checkov associates the comment with the
enclosing resource). Categories covered include S3 cross-region replication / SSE-KMS on
log buckets, Lambda concurrency/code-signing/VPC placement, CloudFront custom-cert/geo
(no custom domain), the WebSocket `$connect`-only authorizer (an AWS platform constraint),
and eco-lite `count`-gated false positives.

### detect-secrets (`# pragma: allowlist secret`)

Reviewed false positives (public AU legislation IDs in API URLs, a mock test credential,
Secrets Manager **ARN** references, and `REPLACE_*` placeholder tokens) carry an inline
pragma:

```python
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"  # nosec B105  # pragma: allowlist secret
```

Note: the pragma must be its **own** `#` comment token — merging it into another comment
(e.g. `# nosec B105 pragma: ...`) is not recognized by detect-secrets.

### grype (`.grype.yaml`)

Dependency CVEs that can't currently be remediated by a version bump are ignored by
vulnerability id in `.grype.yaml`, with a reason and a re-check note. ASH honors the
repo-root `.grype.yaml` automatically (no extra flag). The file also `exclude:`s the
gitignored dependency trees. Example:

```yaml
ignore:
  - vulnerability: GHSA-45hq-cxwh-f6vc
    reason: "Pillow: fixed only in 12.3.0 (no manylinux wheel yet). Whiteboard render uses internal draw ops only, no untrusted images. Re-check for 12.3.0 wheel."
```

Prefer **fixing** a dependency CVE with a version bump; only ignore when the fix is
genuinely unavailable (see the `make package` manylinux-wheel note in
[troubleshooting](troubleshooting.md)).

## npm dependency advisories (frontend + docs site)

Two npm projects are scanned: the product frontend (`app/frontend`, a Next.js **static
export** — no Node server at runtime) and the documentation site (`docusaurus`, built and
published to GitHub Pages). Both are build-time toolchains; neither ships a running Node
process. Transitive advisories are pinned to patched versions with npm
[`overrides`](https://docs.npmjs.com/cli/v10/configuring-npm/package-json#overrides) in each
project's `package.json`, which the committed `package-lock.json` then resolves. CI installs
with `npm ci`, so the lockfile is authoritative.

### Resolved via overrides

| Package | Pinned to | Why | Project |
|---|---|---|---|
| `fastify` | `^5.12.5` | request/auth/validation bypass + DoS advisories | frontend |
| `dompurify` | `^3.4.16` | in-place DOM XSS | frontend |
| `sharp` | `^0.35.5` | librsvg CVE | frontend |
| `source-map-js` | `^1.2.2` | event-loop DoS | both |
| `katex` | `^0.18.2` | prototype-pollution trust bypass | both |
| `postcss-selector-parser` | `^7.1.6` | quadratic-complexity CPU exhaustion | both |
| `next` | `^15.5.27` (direct dep) | SSG/ISR cache poisoning | frontend |
| `shell-quote` | `^1.12.0` | `quote()` command injection | docusaurus |
| `http-cache-semantics` | `^4.3.0` | cross-user cached-response disclosure | docusaurus |
| `tinypool` | `^2.2.0` | prototype-pollution → RCE in `run()` options | docusaurus |

`tinypool` is a major bump (1.x → 2.x) of a dep that `@docusaurus/core` pins at `^1.0.2`;
the Docusaurus static build was verified to succeed with 2.2.0 (the SSG `run()` usage is
compatible), so it is treated as safe here. Re-verify the docs build if `@docusaurus/core`
is upgraded.

### Known-unfixable: `braces` (GHSA-vfj7-8cjw-p6xm / CVE-2026-93687)

`braces` has a stack-exhaustion DoS advisory covering **`<=3.0.3`**, and **3.0.3 is the
latest published release** — there is no version to upgrade to. It is pulled transitively
via `chokidar` + `micromatch` (from `tailwindcss` in the frontend, and `@docusaurus/core`
in the docs site), both **build-time only**. After the overrides above, this is the sole
remaining root-cause advisory in either project; every other npm-audit entry is a
"depends-on-`braces`" wrapper that clears automatically once `braces` publishes a fix.

- Suppressed in `.grype.yaml` by id with a re-check note.
- Ignored for update PRs in `.github/dependabot.yml`.
- **Re-check** when `braces` publishes `>3.0.3`, or when the tailwindcss / Docusaurus major
  upgrades below drop the chain.

### Deferred: major-version upgrades (separate, tested migrations)

These advisories are only resolved by major upgrades that carry real breakage risk and
should be done as deliberate, tested migrations — **not** folded into a security patch pass.
They are build-time toolchains and the shipped static output is unaffected by the advisories.

| Upgrade | Clears | Breakage risk |
|---|---|---|
| **tailwindcss 3.4 → 4.x** (frontend) | `chokidar`, `micromatch`, `postcss-selector-parser` (6.x paths), and the `braces` chain | **High.** Tailwind 4 changes the config format and PostCSS plugin wiring; needs CSS regression testing across the UI. |
| **@excalidraw/excalidraw** (frontend) | `sass`, `katex`/`mermaid` (editor copies) | **Medium.** npm proposes a *downgrade* to 0.17.6; the diagram editor needs manual verification. |
| **Docusaurus 3.10 → 4.x** (docs site) | the `@docusaurus/*` wrapper advisories + `braces` chain | **High.** Major framework upgrade; theme/config/plugin changes, needs a full docs build + visual review. Docs-site only — cannot affect the product deployment. |

Until these are scheduled, the residual advisories are build-time, non-runtime, and tracked
above. GitHub Dependabot **alerts** (the Security tab) are not silenced by `.github/dependabot.yml`
(that file governs update PRs); genuinely-unfixable alerts like `braces` are dismissed
per-advisory in the Security tab with the rationale from this page.
