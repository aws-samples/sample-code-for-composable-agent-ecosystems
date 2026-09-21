"""Tessera ecosystem export — turn a single ecosystem into a standalone,
customer-deployable Terraform package (self-serve / Model 1).

Two pure, offline pieces:
  * bom.py      — Bill-of-Materials analyzer (read-only dependency closure)
  * generate.py — Terraform package generator (BoM -> HCL package)

The generator vendors the `eco-lite` Terraform module (infra/modules/eco-lite) and
the AgentCore runtime source (app/agent) into the package. When the eco-lite module
isn't present yet (pre-Slice-2), the generator emits a PENDING placeholder instead of
failing, so BoM + HCL generation are usable and testable offline.

No infrastructure is provisioned by this package; it only produces artifacts.
"""
