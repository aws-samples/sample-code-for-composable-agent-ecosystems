# ─────────────────────────────────────────────────────────────────────────────
# Amazon Verified Permissions (Cedar) — per-ecosystem authorization.
# ─────────────────────────────────────────────────────────────────────────────

locals {
  cedar_schema = jsonencode({
    Tessera = {
      entityTypes = {
        User = {
          shape         = { type = "Record", attributes = {} }
          memberOfTypes = ["Group"]
        }
        Group = {
          shape         = { type = "Record", attributes = {} }
          memberOfTypes = []
        }
        Ecosystem = {
          shape = {
            type = "Record"
            attributes = {
              platform = { type = "Boolean" }
            }
          }
          memberOfTypes = []
        }
        Run = {
          shape         = { type = "Record", attributes = {} }
          memberOfTypes = ["Ecosystem"]
        }
      }
      actions = {
        run          = { appliesTo = { principalTypes = ["User", "Group"], resourceTypes = ["Ecosystem"] } }
        edit         = { appliesTo = { principalTypes = ["User", "Group"], resourceTypes = ["Ecosystem"] } }
        delete       = { appliesTo = { principalTypes = ["User", "Group"], resourceTypes = ["Ecosystem"] } }
        view         = { appliesTo = { principalTypes = ["User", "Group"], resourceTypes = ["Ecosystem", "Run"] } }
        manage_files = { appliesTo = { principalTypes = ["User", "Group"], resourceTypes = ["Ecosystem"] } }
      }
    }
  })
}

resource "aws_verifiedpermissions_policy_store" "main" {
  validation_settings {
    mode = "STRICT"
  }
}

# The schema is a separate resource in the AWS provider.
resource "aws_verifiedpermissions_schema" "main" {
  policy_store_id = aws_verifiedpermissions_policy_store.main.policy_store_id

  definition {
    value = local.cedar_schema
  }
}

# Cognito as the identity source.
resource "aws_verifiedpermissions_identity_source" "cognito" {
  policy_store_id = aws_verifiedpermissions_policy_store.main.policy_store_id

  configuration {
    cognito_user_pool_configuration {
      user_pool_arn = aws_cognito_user_pool.main.arn
      client_ids    = [aws_cognito_user_pool_client.frontend.id]
    }
  }

  principal_entity_type = "Tessera::User"
}

# ── Static Cedar policies (RBAC) ──────────────────────────────────────────────

# 1. Any authenticated user can RUN platform ecosystems.
resource "aws_verifiedpermissions_policy" "platform_ecosystem_run" {
  policy_store_id = aws_verifiedpermissions_policy_store.main.policy_store_id
  definition {
    static {
      description = "All authenticated users can run platform ecosystems"
      statement   = "permit(principal, action == Tessera::Action::\"run\", resource) when { resource.platform };"
    }
  }
  # STRICT validation requires the schema to exist before policies referencing it.
  depends_on = [aws_verifiedpermissions_schema.main]
}

# 2. Any authenticated user can VIEW platform ecosystems.
resource "aws_verifiedpermissions_policy" "platform_ecosystem_view" {
  policy_store_id = aws_verifiedpermissions_policy_store.main.policy_store_id
  definition {
    static {
      description = "All authenticated users can view platform ecosystems"
      statement   = "permit(principal, action == Tessera::Action::\"view\", resource is Tessera::Ecosystem) when { resource.platform };"
    }
  }
  depends_on = [aws_verifiedpermissions_schema.main]
}

# 2b/2c. DEMO OPEN ACCESS (config-gated). When demo_open_access = true, every authenticated
# user can view + run EVERY ecosystem — so self-registered colleagues see and can run the
# owner-scoped demo solutions. These are NOT gated on resource.platform, so they do not
# require (mis)labeling demo ecosystems platform:true (which hides them from the picker).
# Disabled by default; intended for dev/demo only.
resource "aws_verifiedpermissions_policy" "demo_view_all" {
  count           = var.demo_open_access ? 1 : 0
  policy_store_id = aws_verifiedpermissions_policy_store.main.policy_store_id
  definition {
    static {
      description = "DEMO: all authenticated users can view all ecosystems"
      statement   = "permit(principal, action == Tessera::Action::\"view\", resource is Tessera::Ecosystem);"
    }
  }
  depends_on = [aws_verifiedpermissions_schema.main]
}

resource "aws_verifiedpermissions_policy" "demo_run_all" {
  count           = var.demo_open_access ? 1 : 0
  policy_store_id = aws_verifiedpermissions_policy_store.main.policy_store_id
  definition {
    static {
      description = "DEMO: all authenticated users can run all ecosystems"
      statement   = "permit(principal, action == Tessera::Action::\"run\", resource is Tessera::Ecosystem);"
    }
  }
  depends_on = [aws_verifiedpermissions_schema.main]
}

# 3. Admin group: full access to everything.
resource "aws_verifiedpermissions_policy" "admin_full_access" {
  policy_store_id = aws_verifiedpermissions_policy_store.main.policy_store_id
  definition {
    static {
      description = "Admin group has full access to everything"
      statement   = "permit(principal in Tessera::Group::\"admin\", action, resource);"
    }
  }
  depends_on = [aws_verifiedpermissions_schema.main]
}
