"""Groups — AWS Lambda function to manage Amazon Cognito groups (admin only).

Routes:
  GET    /api/admin/groups                      → list groups
  POST   /api/admin/groups                      → create group
  DELETE  /api/admin/groups/{name}               → delete group
  GET    /api/admin/groups/{name}/members        → list members
  POST   /api/admin/groups/{name}/members        → add member
  DELETE  /api/admin/groups/{name}/members/{userId} → remove member
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
    "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
}


def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS}

    try:
        user = require_auth(event)
    except PermissionError:
        return _err(401, "Unauthorized")

    if not is_admin(user):
        return _err(403, "Admin only")

    method = event.get("httpMethod", "GET")
    path_params = event.get("pathParameters") or {}
    group_name = path_params.get("name")
    user_id = path_params.get("userId")
    path = event.get("path", "")

    # Route dispatch
    if "/members" in path:
        if method == "GET" and group_name:
            return list_members(group_name)
        elif method == "POST" and group_name:
            body = json.loads(event.get("body") or "{}")
            return add_member(group_name, body.get("userId", ""))
        elif method == "DELETE" and group_name and user_id:
            return remove_member(group_name, user_id)
    else:
        if method == "GET" and not group_name:
            return list_groups()
        elif method == "POST" and not group_name:
            body = json.loads(event.get("body") or "{}")
            return create_group(body)
        elif method == "DELETE" and group_name:
            return delete_group(group_name)

    return _err(404, "Not found")


def list_groups():
    groups = []
    params = {"UserPoolId": USER_POOL_ID}
    while True:
        resp = cognito.list_groups(**params)
        for g in resp.get("Groups", []):
            # Get member count
            members_resp = cognito.list_users_in_group(
                UserPoolId=USER_POOL_ID, GroupName=g["GroupName"], Limit=1
            )
            # Approximate count — Cognito doesn't return total directly
            count = len(members_resp.get("Users", []))
            if members_resp.get("NextToken"):
                count = "1+"  # indicates more than 1
            groups.append({
                "name": g["GroupName"],
                "description": g.get("Description", ""),
                "memberCount": count,
                "created": str(g.get("CreationDate", "")),
            })
        token = resp.get("NextToken")
        if not token:
            break
        params["NextToken"] = token
    return _ok(groups)


def create_group(body):
    name = body.get("name", "").strip()
    description = body.get("description", "")
    if not name:
        return _err(400, "Missing group name")
    cognito.create_group(
        UserPoolId=USER_POOL_ID,
        GroupName=name,
        Description=description,
    )
    return _ok({"created": True, "name": name})


def delete_group(name):
    cognito.delete_group(UserPoolId=USER_POOL_ID, GroupName=name)
    return _ok({"deleted": True, "name": name})


def list_members(group_name):
    members = []
    params = {"UserPoolId": USER_POOL_ID, "GroupName": group_name}
    while True:
        resp = cognito.list_users_in_group(**params)
        for u in resp.get("Users", []):
            attrs = {a["Name"]: a["Value"] for a in u.get("Attributes", [])}
            members.append({
                "sub": attrs.get("sub", ""),
                "email": attrs.get("email", ""),
                "username": u.get("Username", ""),
            })
        token = resp.get("NextToken")
        if not token:
            break
        params["NextToken"] = token
    return _ok(members)


def add_member(group_name, user_id):
    if not user_id:
        return _err(400, "Missing userId")
    # user_id is the username (Cognito requires Username, not sub)
    # Try to find username by sub first
    username = _get_username_by_sub(user_id) or user_id
    cognito.admin_add_user_to_group(
        UserPoolId=USER_POOL_ID,
        Username=username,
        GroupName=group_name,
    )
    return _ok({"added": True, "userId": user_id, "group": group_name})


def remove_member(group_name, user_id):
    username = _get_username_by_sub(user_id) or user_id
    cognito.admin_remove_user_from_group(
        UserPoolId=USER_POOL_ID,
        Username=username,
        GroupName=group_name,
    )
    return _ok({"removed": True, "userId": user_id, "group": group_name})


def _get_username_by_sub(sub):
    try:
        resp = cognito.list_users(UserPoolId=USER_POOL_ID, Filter=f'sub = "{sub}"', Limit=1)
        users = resp.get("Users", [])
        return users[0]["Username"] if users else None
    except Exception:
        return None


def _ok(data):
    return {"statusCode": 200, "headers": CORS_HEADERS, "body": json.dumps(data)}


def _err(code, msg):
    return {"statusCode": code, "headers": CORS_HEADERS, "body": json.dumps({"error": msg})}
