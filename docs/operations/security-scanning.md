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

## A note on Slingshot

Slingshot (the internal scanner) post-processes **raw** checkov output and does **not**
honor inline `#checkov:skip` comments or any ASH config. A clean ASH report is therefore
**not** automatically a clean Slingshot report — Slingshot findings must be fixed in code
or accepted through Slingshot's own exception workflow. The inline skip reasons make good
source material for those exception justifications.
