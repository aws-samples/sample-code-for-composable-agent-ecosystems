# ecosystem_yaml_v1

Canonical template for producing a Tessera ecosystem YAML.

## When to use

- An agent (or operator) is generating a NEW ecosystem definition (e.g. AI builder, suggest-ecosystem Lambda, optimizer's `patch_synthesizer` proposing a v{N+1})
- The output must conform to the Tessera compiler's expected structure so it deploys cleanly via save-ecosystem

## What this template provides

- `template.yaml` — annotated YAML skeleton with placeholders in `{curly braces}`
- `schema.json` — JSON Schema (draft-07) validating the YAML structure
- This file (`description.md`) — human-facing context

## Required structure

- `ecosystem` block: `name`, `description`, `version`
- At least one entry in `agents`
- Each agent: `id` (snake_case, starts with letter), `name`, `swarm`, `prompt` (≥50 chars)

## Phase 5e fields supported

- `output_kind`: which contract the runtime applies (`analyst`, `synthesizer`, `challenger`, `reporter`, `drafter`, `creative`, `chat`, `operator`)
- `output_format`: typed file output (`markdown` default; `json` / `yaml` / `html` validated; `pdf` / `docx` / `csv` / `png` deferred)
- `output_format.template_id`: cross-reference to another template (e.g. an agent declaring `output_format.template_id: credit_memo_v1` will have that template's body inlined into its prompt)

## Versioning

This is `v1`. Future revisions ship as `ecosystem_yaml_v2` etc. so existing agents pointing at v1 don't silently change behaviour.
