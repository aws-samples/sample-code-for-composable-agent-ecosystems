"""API Gateway v2 WebSocket $connect REQUEST authorizer.

The browser cannot set headers on a WebSocket handshake, so the Cognito ID token is passed
as the `token` query-string parameter on the $connect URL (wss://.../?token=<idToken>).
This authorizer verifies the token's SIGNATURE (JWKS) + issuer/audience/expiry and allows
or denies the connection. Per-message handlers still re-verify the token (defense in depth);
this front gate additionally satisfies the requirement that $connect specify authorization.

Env: COGNITO_USER_POOL_ID (required), COGNITO_CLIENT_ID (optional; enables aud check),
AWS_REGION (provided by Lambda).
"""
import os

REGION = os.environ.get("AWS_REGION", "us-east-1")
_JWK_CLIENT = None


def _verify_jwt(token):
    """Verify a Cognito ID token. Returns claims dict on success, else None."""
    pool = os.environ.get("COGNITO_USER_POOL_ID", "")
    client = os.environ.get("COGNITO_CLIENT_ID", "")
    if not token or not pool:
        return None
    global _JWK_CLIENT
    try:
        import jwt
        from jwt import PyJWKClient
        if _JWK_CLIENT is None:
            _JWK_CLIENT = PyJWKClient(
                f"https://cognito-idp.{REGION}.amazonaws.com/{pool}/.well-known/jwks.json")
        key = _JWK_CLIENT.get_signing_key_from_jwt(token).key
        return jwt.decode(
            token, key, algorithms=["RS256"],
            audience=client if client else None,
            issuer=f"https://cognito-idp.{REGION}.amazonaws.com/{pool}",
            options={"verify_aud": bool(client)},
        )
    except Exception as e:  # noqa: BLE001 — any failure = deny
        print(f"[ws-authorizer] token verification failed: {type(e).__name__}: {str(e)[:200]}")
        return None


def handler(event, context):
    """REQUEST authorizer. Reads ?token= from the $connect query string.

    Returns a simple IAM policy allowing/denying execute-api:Invoke. On allow, the verified
    `sub` is returned as the principalId and surfaced in the request context for downstream
    handlers.
    """
    params = event.get("queryStringParameters") or {}
    token = params.get("token", "")
    claims = _verify_jwt(token)

    method_arn = event.get("methodArn", "*")
    effect = "Allow" if claims and claims.get("sub") else "Deny"
    principal_id = (claims or {}).get("sub", "unauthorized")

    policy = {
        "principalId": principal_id,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [{
                "Action": "execute-api:Invoke",
                "Effect": effect,
                "Resource": method_arn,
            }],
        },
    }
    if effect == "Allow":
        # Pass identity to $connect/downstream via the request context authorizer object.
        policy["context"] = {"sub": principal_id}
    return policy
