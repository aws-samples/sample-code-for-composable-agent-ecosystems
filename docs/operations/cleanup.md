# Cleanup / teardown

To stop ongoing charges, destroy the environment's infrastructure by running Terraform
directly:

```bash
source scripts/load-env.sh dev      # or staging / prod
cd infra
terraform destroy $TF_VARS
cd ..
```

## What survives a destroy (by design)

Some resources are protected so a destroy cannot silently delete data:

- **Data S3 buckets** — `access-logs`, `ecosystems`, and `runs` have
  `prevent_destroy = true`. Terraform will refuse to destroy them; they must be emptied
  and removed deliberately.
- **AgentCore Memory** — `prevent_destroy = true` so stored events/records are not lost.

The ephemeral **memory-payloads** bucket and the **frontend** site bucket use
`force_destroy` and will be emptied + removed by `destroy`.

## Fully removing the retained resources

Only do this when you are sure the data is no longer needed. Back up anything important
in S3 / DynamoDB / Memory first.

1. Temporarily remove the `prevent_destroy` guard. There are **four** guarded resources:
   the three data buckets (`access_logs`, `ecosystems`, `runs`) in
   `infra/modules/foundation/s3.tf` **and** AgentCore Memory in
   `infra/modules/agentcore/memory.tf`. Flip each `prevent_destroy = true` to `false`
   (or target-remove them from state and delete them manually). Remember to revert these
   edits afterward if you plan to redeploy.

2. **Empty the buckets — including all versions.** The `ecosystems`, `runs`, and
   `frontend` buckets have **versioning enabled**, so `aws s3 rm --recursive` (which only
   deletes current objects) leaves noncurrent versions + delete markers behind and the
   bucket delete still fails with `BucketNotEmpty`. Purge every version. For each bucket:

   ```bash
   aws s3 rm "s3://<bucket>" --recursive          # current objects
   # then purge noncurrent versions + delete markers:
   aws s3api list-object-versions --bucket <bucket> \
     --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json \
     | aws s3api delete-objects --bucket <bucket> --delete file:///dev/stdin
   aws s3api list-object-versions --bucket <bucket> \
     --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' --output json \
     | aws s3api delete-objects --bucket <bucket> --delete file:///dev/stdin
   ```

   > **Empty the `access-logs` bucket LAST (or twice).** The other buckets have S3
   > server-access logging pointed at `access-logs`, so deleting/emptying them **writes
   > new log objects into `access-logs`**. If you empty `access-logs` first, the final
   > `terraform destroy` fails on it with `BucketNotEmpty`. Empty it immediately before the
   > destroy, and if the destroy still trips on it, empty it once more and re-run.

3. Re-run `terraform destroy $TF_VARS` from `infra/`.

## Redeploying into the same account after a destroy

A `terraform destroy` deletes the **Secrets Manager** secret
(`${project}/gateway-m2m-client-secret`) with a recovery window rather than immediately.
If you then redeploy into the **same account**, the create fails with:

```
InvalidRequestException: You can't create this secret because a secret with this name is
already scheduled for deletion.
```

Force-delete the scheduled secret first so the name is free, then re-apply:

```bash
aws secretsmanager delete-secret --secret-id <project>/gateway-m2m-client-secret \
  --force-delete-without-recovery
```

(There is a short propagation delay; wait a few seconds before re-applying.)

## The AgentCore runtime

The runtime is created by the `agentcore` CLI, not Terraform, so `destroy` does not
remove it. Delete it with the AgentCore CLI / control-plane API if you no longer need it.

## The web-search credential provider

The web-search credential provider in the AgentCore Identity token vault is created out
of band and is not managed by Terraform; remove it separately if desired.
