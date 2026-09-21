"""Post-confirmation trigger — disables each new user (pending admin approval) and
notifies admins via SNS so they can review and enable the account."""
import os
import boto3

cognito = boto3.client("cognito-idp")
sns = boto3.client("sns")
SIGNUP_TOPIC_ARN = os.environ.get("TESSERA_SIGNUP_TOPIC_ARN", "")


def handler(event, context):
    user_pool_id = event["userPoolId"]
    username = event["userName"]
    user_attrs = event.get("request", {}).get("userAttributes", {})
    email = user_attrs.get("email", username)
    user_sub = user_attrs.get("sub", "")

    # Approval gate (config-gated). When SIGNUP_REQUIRE_APPROVAL=true, disable the new
    # user until an admin enables them + notify admins. Default OFF: new users (e.g. SSO)
    # are usable immediately, no approval needed. Re-enable by setting the env var to true.
    require_approval = os.environ.get("SIGNUP_REQUIRE_APPROVAL", "false").lower() == "true"
    if not require_approval:
        print(f"[signup] auto-approved — no admin gate (SIGNUP_REQUIRE_APPROVAL off): {email} (sub: {user_sub})")
        return event

    # Approval gate: disable the new user until an admin enables them.
    cognito.admin_disable_user(UserPoolId=user_pool_id, Username=username)
    print(f"[ADMIN-ACTION-REQUIRED] New user pending approval: {email} (sub: {user_sub})")

    # Notify admins. Best-effort: a notification failure must NEVER break sign-up
    # (the user is already confirmed+disabled by the time we get here).
    if SIGNUP_TOPIC_ARN:
        try:
            subject = f"[Tessera] New signup pending approval: {email}"[:100]
            message = (
                "A new user signed up to Tessera and is AWAITING ADMIN APPROVAL.\n\n"
                f"  Email:     {email}\n"
                f"  User ID:   {user_sub}\n"
                f"  Username:  {username}\n"
                f"  User pool: {user_pool_id}\n\n"
                "The account is currently DISABLED. To approve it, ENABLE the user:\n\n"
                "  - Cognito console -> this user pool -> Users -> select the user -> Enable user\n"
                "      OR\n"
                f"  - aws cognito-idp admin-enable-user --user-pool-id {user_pool_id} --username {username}\n\n"
                "If this signup looks illegitimate, leave the account disabled (or delete it)."
            )
            sns.publish(TopicArn=SIGNUP_TOPIC_ARN, Subject=subject, Message=message)
            print(f"[notify] published signup-approval notification for {email}")
        except Exception as e:
            print(f"[warn] failed to publish signup notification: {type(e).__name__}: {e}")

    return event
