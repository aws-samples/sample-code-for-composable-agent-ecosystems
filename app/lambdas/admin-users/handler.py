"""Admin users Lambda — list pending users, approve/reject.

Routes:
  GET  /api/admin/users              → list disabled (pending) users
  POST /api/admin/users/{sub}/approve → enable user
  POST /api/admin/users/{sub}/reject  → delete user
"""
import os
import json
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from auth import require_auth, is_admin

import boto3

USER_POOL_ID = os.environ["USER_POOL_ID"]
cognito = boto3.client("cognito-idp")

CORS_HEADERS = {
    "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}


def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS}

    try:
        user = require_auth(event)
    except PermissionError:
        return {"statusCode": 401, "headers": CORS_HEADERS, "body": json.dumps({"error": "Unauthorized"})}

    if not is_admin(user):
        return {"statusCode": 403, "headers": CORS_HEADERS, "body": json.dumps({"error": "Admin only"})}

    method = event.get("httpMethod", "GET")
    path = event.get("path", "")
    path_params = event.get("pathParameters") or {}

    sub = path_params.get("sub")
    action = path_params.get("action")

    if method == "GET" and not sub:
        # ?all=true returns all users; default returns only pending (disabled)
        qs = event.get("queryStringParameters") or {}
        if qs.get("all") == "true":
            return list_all_users()
        return list_pending_users()
    elif method == "POST" and sub and action == "approve":
        return approve_user(sub)
    elif method == "POST" and sub and action == "reject":
        return reject_user(sub)

    return {"statusCode": 404, "headers": CORS_HEADERS, "body": json.dumps({"error": "Not found"})}


def list_all_users():
    users = []
    params = {"UserPoolId": USER_POOL_ID}
    while True:
        resp = cognito.list_users(**params)
        for u in resp.get("Users", []):
            attrs = {a["Name"]: a["Value"] for a in u.get("Attributes", [])}
            users.append({
                "sub": attrs.get("sub", ""),
                "email": attrs.get("email", ""),
                "status": u.get("UserStatus", ""),
                "enabled": u.get("Enabled", True),
            })
        token = resp.get("PaginationToken")
        if not token:
            break
        params["PaginationToken"] = token
    return {"statusCode": 200, "headers": CORS_HEADERS, "body": json.dumps(users)}


def list_pending_users():
    users = []
    params = {"UserPoolId": USER_POOL_ID, "Filter": 'status = "Disabled"'}
    while True:
        resp = cognito.list_users(**params)
        for u in resp.get("Users", []):
            attrs = {a["Name"]: a["Value"] for a in u.get("Attributes", [])}
            users.append({
                "sub": attrs.get("sub", ""),
                "email": attrs.get("email", ""),
                "status": u.get("UserStatus", ""),
                "enabled": u.get("Enabled", False),
                "created": str(u.get("UserCreateDate", "")),
            })
        token = resp.get("PaginationToken")
        if not token:
            break
        params["PaginationToken"] = token
    # Only return disabled users (pending approval)
    pending = [u for u in users if not u["enabled"]]
    return {"statusCode": 200, "headers": CORS_HEADERS, "body": json.dumps(pending)}


def approve_user(sub):
    username = _get_username_by_sub(sub)
    if not username:
        return {"statusCode": 404, "headers": CORS_HEADERS, "body": json.dumps({"error": "User not found"})}
    cognito.admin_enable_user(UserPoolId=USER_POOL_ID, Username=username)
    return {"statusCode": 200, "headers": CORS_HEADERS, "body": json.dumps({"approved": True, "sub": sub})}


def reject_user(sub):
    username = _get_username_by_sub(sub)
    if not username:
        return {"statusCode": 404, "headers": CORS_HEADERS, "body": json.dumps({"error": "User not found"})}
    cognito.admin_delete_user(UserPoolId=USER_POOL_ID, Username=username)
    return {"statusCode": 200, "headers": CORS_HEADERS, "body": json.dumps({"rejected": True, "sub": sub})}


def _get_username_by_sub(sub):
    try:
        resp = cognito.list_users(UserPoolId=USER_POOL_ID, Filter=f'sub = "{sub}"', Limit=1)
        users = resp.get("Users", [])
        return users[0]["Username"] if users else None
    except Exception:
        return None
