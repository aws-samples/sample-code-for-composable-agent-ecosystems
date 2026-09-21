"""Sharing Lambda — manage ecosystem sharing via Amazon Verified Permissions (AVP) Cedar policies.

Routes:
  GET    /api/ecosystems/{id}/sharing             → list sharing policies
  POST   /api/ecosystems/{id}/sharing             → grant access
  DELETE  /api/ecosystems/{id}/sharing/{policyId}  → revoke access
  GET    /api/admin/sharing?principal_type=X&principal_id=Y → list all shares for a principal
  DELETE  /api/admin/sharing/{policyId}            → revoke by policyId (admin)
"""
import os
import json
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from auth import require_auth, is_admin

import boto3

POLICY_STORE_ID = os.environ["POLICY_STORE_ID"]
USER_POOL_ID = os.environ["USER_POOL_ID"]

# Amazon Verified Permissions (AVP) client
# Amazon Verified Permissions (AVP) client
avp = boto3.client("verifiedpermissions")
cognito = boto3.client("cognito-idp")

CORS_HEADERS = {
    "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
}

# Cedar permission policies for Amazon Verified Permissions. "full" grants all actions
# on a specific ecosystem resource — scoped to a single principal and single resource.
PERMISSION_STATEMENTS = {
    "run": 'permit(principal == Tessera::User::"{principal_id}", action in [Tessera::Action::"run", Tessera::Action::"view"], resource == Tessera::Ecosystem::"{eco_id}");',
    "edit": 'permit(principal == Tessera::User::"{principal_id}", action in [Tessera::Action::"run", Tessera::Action::"edit", Tessera::Action::"manage_files", Tessera::Action::"view"], resource == Tessera::Ecosystem::"{eco_id}");',
    "full": 'permit(principal == Tessera::User::"{principal_id}", action in [Tessera::Action::"run", Tessera::Action::"edit", Tessera::Action::"manage_files", Tessera::Action::"view", Tessera::Action::"share", Tessera::Action::"delete"], resource == Tessera::Ecosystem::"{eco_id}");',
}

GROUP_PERMISSION_STATEMENTS = {
    "run": 'permit(principal in Tessera::Group::"{principal_id}", action in [Tessera::Action::"run", Tessera::Action::"view"], resource == Tessera::Ecosystem::"{eco_id}");',
    "edit": 'permit(principal in Tessera::Group::"{principal_id}", action in [Tessera::Action::"run", Tessera::Action::"edit", Tessera::Action::"manage_files", Tessera::Action::"view"], resource == Tessera::Ecosystem::"{eco_id}");',
    "full": 'permit(principal in Tessera::Group::"{principal_id}", action, resource == Tessera::Ecosystem::"{eco_id}");',
}


def handler(event, context):
    print(f"[sharing] method={event.get('httpMethod')} path={event.get('path')} pathParams={event.get('pathParameters')}")
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS}

    try:
        user = require_auth(event)
        print(f"[sharing] user={user}")
    except PermissionError:
        print("[sharing] auth failed - no user in claims")
        return _err(401, "Unauthorized")

    method = event.get("httpMethod", "GET")
    path = event.get("path", "")
    path_params = event.get("pathParameters") or {}

    # Admin sharing routes: /api/admin/sharing
    if "/api/admin/sharing" in path:
        if not is_admin(user):
            return _err(403, "Admin only")
        if method == "GET":
            qs = event.get("queryStringParameters") or {}
            return list_shares_for_principal(qs.get("principal_type", "user"), qs.get("principal_id", ""))
        if method == "DELETE":
            policy_id = path_params.get("policyId", "")
            if not policy_id:
                return _err(400, "Missing policyId")
            return delete_sharing(policy_id)
        return _err(404, "Not found")

    # Ecosystem sharing routes: /api/ecosystems/{id}/sharing
    eco_id = path_params.get("id", "")
    if not eco_id:
        return _err(400, "Missing ecosystem id")

    if method == "GET":
        return list_sharing(eco_id)
    elif method == "POST":
        if not is_admin(user):
            return _err(403, "Only owner or admin can share")
        body = json.loads(event.get("body") or "{}")
        return create_sharing(eco_id, body)
    elif method == "DELETE":
        if not is_admin(user):
            return _err(403, "Only owner or admin can revoke")
        policy_id = path_params.get("policyId", "")
        if not policy_id:
            return _err(400, "Missing policyId")
        return delete_sharing(policy_id)

    return _err(404, "Not found")


def list_shares_for_principal(principal_type, principal_id):
    """List all sharing policies for a given principal across all ecosystems."""
    if not principal_id:
        return _err(400, "Missing principal_id")

    if principal_type == "group":
        pattern = f'Tessera::Group::"{principal_id}"'
    else:
        pattern = f'Tessera::User::"{principal_id}"'

    results = []
    params = {"policyStoreId": POLICY_STORE_ID, "filter": {"policyType": "STATIC"}}
    while True:
        resp = avp.list_policies(**params)
        for p in resp.get("policies", []):
            policy_id = p.get("policyId", "")
            try:
                detail = avp.get_policy(policyStoreId=POLICY_STORE_ID, policyId=policy_id)
                statement = detail.get("definition", {}).get("static", {}).get("statement", "")
                if pattern not in statement:
                    continue
                # Extract ecosystem ID
                eco_match = re.search(r'Tessera::Ecosystem::"([^"]+)"', statement)
                if not eco_match:
                    continue
                perm = _detect_permission(statement)
                results.append({"ecosystemId": eco_match.group(1), "permission": perm, "policyId": policy_id})
            except Exception:
                continue
        token = resp.get("nextToken")
        if not token:
            break
        params["nextToken"] = token

    return _ok(results)


def list_sharing(eco_id):
    """List all dynamic policies referencing this ecosystem."""
    policies = []
    params = {"policyStoreId": POLICY_STORE_ID, "filter": {"policyType": "STATIC"}}
    while True:
        resp = avp.list_policies(**params)
        for p in resp.get("policies", []):
            policy_id = p.get("policyId", "")
            try:
                detail = avp.get_policy(policyStoreId=POLICY_STORE_ID, policyId=policy_id)
                statement = detail.get("definition", {}).get("static", {}).get("statement", "")
                if f'Tessera::Ecosystem::"{eco_id}"' not in statement:
                    continue
                entry = _parse_policy_statement(statement, policy_id)
                if entry:
                    policies.append(entry)
            except Exception:
                continue
        token = resp.get("nextToken")
        if not token:
            break
        params["nextToken"] = token

    # Resolve emails for user principals
    for p in policies:
        if p["principal_type"] == "user":
            p["email"] = _resolve_email(p["principal_id"])

    return _ok(policies)


def create_sharing(eco_id, body):
    """Create a Cedar policy granting access."""
    principal_type = body.get("principal_type", "user")
    principal_id = body.get("principal_id", "")
    permission = body.get("permission", "run")

    if not principal_id:
        return _err(400, "Missing principal_id")
    if permission not in ("run", "edit", "full"):
        return _err(400, "Invalid permission")

    if principal_type == "group":
        templates = GROUP_PERMISSION_STATEMENTS
    else:
        templates = PERMISSION_STATEMENTS

    statement = templates[permission].format(principal_id=principal_id, eco_id=eco_id)

    resp = avp.create_policy(
        policyStoreId=POLICY_STORE_ID,
        definition={
            "static": {
                "statement": statement,
                "description": f"Sharing: {principal_type}={principal_id} {permission} on {eco_id}",
            }
        },
    )

    return _ok({"policyId": resp["policyId"], "statement": statement})


def delete_sharing(policy_id):
    """Delete a sharing policy."""
    avp.delete_policy(policyStoreId=POLICY_STORE_ID, policyId=policy_id)
    return _ok({"deleted": True, "policyId": policy_id})


def _parse_policy_statement(statement, policy_id):
    """Extract principal info and permission level from a Cedar statement."""
    # User pattern
    m = re.search(r'principal == Tessera::User::"([^"]+)"', statement)
    if m:
        principal_id = m.group(1)
        perm = _detect_permission(statement)
        return {"policyId": policy_id, "principal_type": "user", "principal_id": principal_id, "permission": perm}
    # Group pattern
    m = re.search(r'principal in Tessera::Group::"([^"]+)"', statement)
    if m:
        principal_id = m.group(1)
        perm = _detect_permission(statement)
        return {"policyId": policy_id, "principal_type": "group", "principal_id": principal_id, "permission": perm}
    return None


def _detect_permission(statement):
    if "manage_files" in statement:
        return "edit"
    if 'action in [' in statement:
        return "run"
    return "full"


def _resolve_email(sub):
    """Resolve a Cognito sub to email."""
    try:
        resp = cognito.list_users(UserPoolId=USER_POOL_ID, Filter=f'sub = "{sub}"', Limit=1)
        users = resp.get("Users", [])
        if users:
            attrs = {a["Name"]: a["Value"] for a in users[0].get("Attributes", [])}
            return attrs.get("email", sub)
    except Exception:
        pass
    return sub


def _ok(data):
    return {"statusCode": 200, "headers": CORS_HEADERS, "body": json.dumps(data)}


def _err(code, msg):
    return {"statusCode": code, "headers": CORS_HEADERS, "body": json.dumps({"error": msg})}
