# ─────────────────────────────────────────────────────────────────────────────
# REST API route definitions.
#
# The full route tree is declared as a flat list of {method, path, fn} and rendered
# into an OpenAPI document (rest_api.tf). API Gateway builds the entire resource tree
# from the spec in one operation — far more maintainable than hundreds of individual
# resource/method/integration blocks, and it keeps the routing table readable.
#
# `fn` is the short function key (matches compute module output keys). Paths use API
# Gateway path syntax: {id} = path param, {proxy+} = greedy capture.
# ─────────────────────────────────────────────────────────────────────────────

locals {
  rest_routes = [
    # ── Ecosystems ──
    { method = "GET", path = "/api/ecosystems", fn = "list-ecosystems" },
    { method = "POST", path = "/api/ecosystems/suggest", fn = "suggest-ecosystem" },
    { method = "GET", path = "/api/ecosystems/{id}", fn = "get-ecosystem" },
    { method = "PUT", path = "/api/ecosystems/{id}", fn = "save-ecosystem" },
    { method = "POST", path = "/api/ecosystems/{id}/deploy", fn = "save-ecosystem" },

    # Productized export: generate a self-serve Terraform package for this ecosystem.
    { method = "POST", path = "/api/ecosystems/{id}/export", fn = "export-ecosystem" },

    # Past-runs viewer. {execName} + greedy proxy for its sub-routes.
    { method = "GET", path = "/api/ecosystems/{id}/runs", fn = "runs-api" },
    { method = "GET", path = "/api/ecosystems/{id}/runs/{execName}", fn = "runs-api" },
    { method = "ANY", path = "/api/ecosystems/{id}/runs/{execName}/{proxy+}", fn = "runs-api" },

    # Per-ecosystem persistent guidance store.
    { method = "GET", path = "/api/ecosystems/{id}/guidance", fn = "runs-api" },
    { method = "POST", path = "/api/ecosystems/{id}/guidance", fn = "runs-api" },
    { method = "PATCH", path = "/api/ecosystems/{id}/guidance/{gid}", fn = "runs-api" },
    { method = "DELETE", path = "/api/ecosystems/{id}/guidance/{gid}", fn = "runs-api" },

    # Ecosystem files.
    { method = "GET", path = "/api/ecosystems/{id}/files", fn = "ecosystem-files" },
    { method = "PUT", path = "/api/ecosystems/{id}/files/{filename}", fn = "ecosystem-files" },
    { method = "DELETE", path = "/api/ecosystems/{id}/files/{filename}", fn = "ecosystem-files" },

    # Ecosystem sharing (owner + admin).
    { method = "GET", path = "/api/ecosystems/{id}/sharing", fn = "sharing" },
    { method = "POST", path = "/api/ecosystems/{id}/sharing", fn = "sharing" },
    { method = "DELETE", path = "/api/ecosystems/{id}/sharing/{policyId}", fn = "sharing" },

    # ── Uploads ──
    { method = "POST", path = "/api/uploads/presign", fn = "presign-upload" },

    # ── Run session (top-level /api/runs/{sessionId}/…) ──
    { method = "POST", path = "/api/runs/{sessionId}/chat", fn = "chat" },
    { method = "GET", path = "/api/runs/{sessionId}/artifacts/{artifactKey+}", fn = "run-artifact" },
    { method = "POST", path = "/api/runs/{sessionId}/artifacts/{artifactKey+}", fn = "run-artifact" },
    { method = "GET", path = "/api/runs/{sessionId}/bundle", fn = "run-artifact" },
    { method = "POST", path = "/api/runs/{sessionId}/gate-decision", fn = "gate-decision" },

    # ── Attention inbox ──
    { method = "GET", path = "/api/attention", fn = "runs-api" },

    # ── Optimizer / patch ──
    { method = "POST", path = "/api/optimize-ecosystem", fn = "optimize-ecosystem" },
    { method = "POST", path = "/api/apply-eco-patch", fn = "apply-eco-patch" },

    # ── Domains ──
    { method = "GET", path = "/api/domains", fn = "domains" },
    { method = "POST", path = "/api/domains", fn = "domains" },
    { method = "POST", path = "/api/domains/seed", fn = "domain-seed" },
    { method = "POST", path = "/api/domains/match", fn = "domain-seed" },
    { method = "GET", path = "/api/domains/{id}", fn = "domains" },
    { method = "POST", path = "/api/domains/{id}/assign", fn = "domains" },
    { method = "POST", path = "/api/domains/{id}/apply", fn = "domains" },
    { method = "POST", path = "/api/domains/{id}/propose", fn = "domains" },
    { method = "POST", path = "/api/domains/{id}/ratify", fn = "domains" },

    # ── Agents ──
    { method = "GET", path = "/api/agents", fn = "list-agents" },
    { method = "POST", path = "/api/agents/suggest", fn = "suggest-agent" },

    # ── Tools ──
    { method = "GET", path = "/api/tools", fn = "list-tools" },
    { method = "POST", path = "/api/tools", fn = "tool-catalog" },
    { method = "GET", path = "/api/tools/{name}", fn = "tool-catalog" },
    { method = "PUT", path = "/api/tools/{name}", fn = "tool-catalog" },
    { method = "DELETE", path = "/api/tools/{name}", fn = "tool-catalog" },

    # ── Catalog (Tool Catalog UI) ──
    { method = "GET", path = "/api/catalog", fn = "tool-catalog" },
    { method = "POST", path = "/api/catalog", fn = "tool-catalog" },
    { method = "GET", path = "/api/catalog/{name}", fn = "tool-catalog" },
    { method = "POST", path = "/api/catalog/{name}", fn = "tool-catalog" },
    { method = "PUT", path = "/api/catalog/{name}", fn = "tool-catalog" },
    { method = "DELETE", path = "/api/catalog/{name}", fn = "tool-catalog" },

    # ── Admin ──
    { method = "GET", path = "/api/admin/users", fn = "admin-users" },
    { method = "POST", path = "/api/admin/users/{sub}/{action}", fn = "admin-users" },
    { method = "GET", path = "/api/admin/sharing", fn = "sharing" },
    { method = "DELETE", path = "/api/admin/sharing/{policyId}", fn = "sharing" },
    { method = "GET", path = "/api/admin/groups", fn = "groups" },
    { method = "POST", path = "/api/admin/groups", fn = "groups" },
    { method = "DELETE", path = "/api/admin/groups/{name}", fn = "groups" },
    { method = "GET", path = "/api/admin/groups/{name}/members", fn = "groups" },
    { method = "POST", path = "/api/admin/groups/{name}/members", fn = "groups" },
    { method = "DELETE", path = "/api/admin/groups/{name}/members/{userId}", fn = "groups" },
  ]

  # Distinct functions referenced by REST routes — used to grant API Gateway invoke.
  rest_functions = toset([for r in local.rest_routes : r.fn])

  # All functions referenced by either API (REST + WebSocket handler) — used to build
  # the deterministic name/ARN maps in websocket.tf.
  all_referenced_fns = toset(concat([for r in local.rest_routes : r.fn], [var.websocket_function_name]))
}
