"""Shared auth module — extract Amazon Cognito claims from Amazon API Gateway authorizer context.

The Amazon Cognito Authorizer on Amazon API Gateway validates the JWT signature and expiry.
By the time the Lambda runs, the token is guaranteed valid. We just read the claims.
"""
import json
import os


def get_user_from_event(event):
    """Extract userId, email, and groups from Cognito authorizer claims."""
    claims = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    if not claims or not claims.get("sub"):
        return {"user_id": "", "email": "", "groups": []}

    groups = claims.get("cognito:groups", "")
    if isinstance(groups, str):
        groups = [g.strip() for g in groups.split(",") if g.strip()]
    elif not isinstance(groups, list):
        groups = []

    return {
        "user_id": claims["sub"],
        "email": claims.get("email", ""),
        "groups": groups,
    }


def is_admin(user):
    return "admin" in user.get("groups", [])


def require_auth(event):
    """Returns user dict or raises PermissionError."""
    user = get_user_from_event(event)
    if not user["user_id"]:
        raise PermissionError("Unauthorized")
    return user


def auth_error_response(status=401, message="Unauthorized"):
    """Return an API Gateway-compatible error response."""
    return {
        "statusCode": status,
        "headers": {"Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*")},
        "body": json.dumps({"message": message}),
    }
