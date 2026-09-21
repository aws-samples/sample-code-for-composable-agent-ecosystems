"""Save ecosystem YAML to Amazon S3 and deploy AWS Step Functions state machine.

Injects platform agents (challenger, report) and grounding checks into the ASL.
"""
import os, re, json, boto3, yaml

BUCKET = os.environ["ECOSYSTEMS_BUCKET"]
REGION = os.environ.get("AWS_REGION", "us-east-1")
AGENT_INVOKER_ARN = os.environ.get("AGENT_INVOKER_ARN", "")
GROUNDING_CHECK_ARN = os.environ.get("GROUNDING_CHECK_ARN", "")
EXTERNAL_GROUNDING_ARN = os.environ.get("EXTERNAL_GROUNDING_ARN", "")
SFN_ROLE_ARN = os.environ.get("SFN_ROLE_ARN", "")
POLICY_STORE_ID = os.environ.get("POLICY_STORE_ID", "")

# --- Ecosystem Evaluation Harness (docs/spec-ecosystem-evaluation.md) ---
# The Eval State Machine (compiled per-eco, spec §5.1) chains these Lambdas.
# Absent env vars ⇒ the eval SM is not compiled (graceful: an eco with no eval
# harness deployed still deploys its run SM). The deploy GATE consults the latest
# scorecard in the runs bucket under evaluations/<eco>/scorecards/.
EVAL_RUNNER_ARN = os.environ.get("EVAL_RUNNER_ARN", "")
EVAL_JUDGE_ARN = os.environ.get("EVAL_JUDGE_ARN", "")
EVAL_TRAJECTORY_ARN = os.environ.get("EVAL_TRAJECTORY_ARN", "")
EVAL_AGGREGATOR_ARN = os.environ.get("EVAL_AGGREGATOR_ARN", "")
EVAL_SFN_ROLE_ARN = os.environ.get("EVAL_SFN_ROLE_ARN", SFN_ROLE_ARN)
RUNS_BUCKET = os.environ.get("TESSERA_RUNS_BUCKET", "")

# Amazon S3 client
s3 = boto3.client("s3")
sfn = boto3.client("stepfunctions", region_name=REGION)
# Amazon Verified Permissions (AVP) client
avp = boto3.client("verifiedpermissions", region_name=REGION)


def _create_owner_policy(user_id: str, eco_id: str):
    """Create an AVP Cedar policy granting the owner full access to the ecosystem."""
    if not POLICY_STORE_ID:
        print(f"POLICY_STORE_ID not set, skipping owner policy for {eco_id}")
        return
    statement = f'permit(principal == Tessera::User::"{user_id}", action, resource == Tessera::Ecosystem::"{eco_id}");'
    try:
        avp.create_policy(
            policyStoreId=POLICY_STORE_ID,
            definition={
                "static": {
                    "statement": statement,
                    "description": f"Owner policy: user={user_id} full access on {eco_id}",
                }
            },
        )
    except Exception as e:
        print(f"Failed to create owner policy for {eco_id}: {e}")


# --- Platform Agent Configs ---

def challenger_agent_config(depends_on):
    # Phase 5e: output_kind: challenger triggers the runtime to append the
    # CHALLENGER_OUTPUT_FORMAT contract (mandatory ## VERDICT + ## WEAKNESSES
    # + ## UNRESOLVED CONTRADICTIONS sections, with verdict-consistency
    # enforcement: any BLOCKER → WEAK). The persona prompt below focuses on
    # the AGENT'S JOB, not output format — format is the runtime's contract.
    return {
        "id": "challenger_agent",
        "name": "Challenger",
        "swarm": "platform",
        "depends_on": depends_on,
        "tools": [],
        "output_kind": "challenger",
        "prompt": (
            "You are the Challenger Agent — the platform's final reviewer. You critically "
            "review the pipeline's outputs to surface weaknesses BEFORE the report is "
            "composed. For each agent's findings, examine: claims made without evidence "
            "or tool results to back them up; logical gaps or unsupported leaps in "
            "reasoning; contradictions between agents that weren't resolved; overconfident "
            "conclusions from limited data; and unsound premises carried over from the "
            "user's request (e.g. a misidentified tool, standard, or definition).\n\n"
            "Severity guidelines:\n"
            "  - BLOCKER: an issue that makes the deliverable unsafe to ship "
            "(missing essential evidence, internal contradiction left open, "
            "fabricated or misidentified premise).\n"
            "  - WARN: a concern the operator should know about but doesn't "
            "block ship (single-source claim, narrow sample, soft inference).\n"
            "  - NIT: minor wording or precision issue.\n\n"
            "Attribute each finding to the agent it concerns, or to (user request) when it "
            "is a flawed premise from the request. Be rigorous but fair. If the work is "
            "solid, say so — and reflect that in your VERDICT. The runtime enforces "
            "verdict-consistency: any BLOCKER you log forces VERDICT: WEAK."
        ),
    }


def checkpoint_challenger_config(layer_index, agent_ids, block_below):
    """Per-layer checkpoint challenger. Reviews ONLY its layer's agents and
    emits a verdict. agent-invoker treats agents carrying `_checkpoint_layer`
    specially: parse verdict; if below `block_below`, open a human gate
    (Accept/Loop/Abort) instead of auto-proceeding. On Loop the layer re-runs
    with this challenger's WEAKNESSES injected as corrective feedback.
    """
    return {
        "id": f"checkpoint_{layer_index}",
        "name": f"Checkpoint L{layer_index}",
        "swarm": "platform",
        "depends_on": list(agent_ids),
        "tools": [],
        "output_kind": "challenger",
        "_checkpoint_layer": layer_index,
        "_checkpoint_block_below": block_below,
        "_checkpoint_agent_ids": list(agent_ids),
        "prompt": (
            "You are a Checkpoint Reviewer — a fail-fast quality gate for ONE layer of the "
            "pipeline. Critically review the outputs of this layer's agents (shown to you in the "
            "conversation) before later layers build on them. Examine: wrong premises or "
            "misunderstandings — INCLUDING any the agents accepted from the user's request (a "
            "misidentified tool, standard, definition, or scope); claims made without evidence; "
            "unverified definitions; scope errors; unresolved contradictions. Catching a bad "
            "premise here prevents it propagating through the rest of the run.\n\n"
            "This may be the first layer of the run — that is normal. The layer's agent "
            "outputs are provided to you in the conversation; they are your primary "
            "material and are ALWAYS present when you run. NEVER claim there is no work to "
            "review, that only the user request is present, or that you are the 'first "
            "responder' — that is false. NEVER treat the absence of an earlier layer, prior "
            "reviewer, or additional 'upstream' as a problem or log it as a finding of any "
            "severity, and never let it affect your VERDICT.\n\n"
            "Severity guidelines:\n"
            "  - BLOCKER: a flaw that corrupts everything downstream (wrong premise, fabricated or "
            "misidentified definition, missing essential evidence).\n"
            "  - WARN: a concern worth surfacing that doesn't corrupt downstream work.\n"
            "  - NIT: minor precision issue.\n\n"
            "Be rigorous but fair. If the layer's work is sound, say so and reflect it in your "
            "VERDICT. Attribute each finding to the agent it concerns, or to (user request) when "
            "it is a flawed premise from the request. The runtime enforces verdict-consistency: "
            "any BLOCKER forces VERDICT: WEAK."
        ),
    }


def report_agent_config(depends_on, episodic=False, eco_id=""):
    # Phase 5e: output_kind: reporter tells the runtime to append the
    # REPORTER_OUTPUT_FORMAT contract — prose narrative with named sections,
    # NO STRUCTURED FINDINGS bullets. The reporter's old prompt forced it to
    # emit pseudo-findings; the new contract drops that and asks for real prose.
    cfg = {
        "id": "report_agent",
        "name": "Report",
        "swarm": "platform",
        "depends_on": depends_on,
        "tools": [],
        "output_kind": "reporter",
        "prompt": (
            "You are the Report Agent — the platform's final synthesizer. You "
            "produce a polished prose report that FAITHFULLY presents what the "
            "upstream agents found and what the Challenger flagged. You are NOT "
            "a judge — you are a faithful synthesizer.\n\n"
            "Rules (these override any other instinct):\n"
            "  1. Quote agents directly when their wording matters. Never "
            "paraphrase a finding into something softer or stronger than what "
            "they said.\n"
            "  2. When the Challenger flagged a claim as weak or logged a "
            "BLOCKER, surface BOTH the original claim AND the critique. Do not "
            "silently drop or rewrite the claim.\n"
            "  3. If agents disagreed, present the disagreement intact. Do not "
            "resolve it unless the evidence is overwhelming.\n"
            "  4. Use the Challenger's verdict as additional signal that "
            "informs Confidence and Risks — not as a replacement for what an "
            "agent said.\n"
            "  5. Cite agents inline with [per agent_id: ...] when the source "
            "matters for traceability.\n\n"
            "Use generateUI to create visual summaries where data supports it "
            "(Metric tiles, Chart, Table, Gauge for confidence, AlertBanner "
            "for critical caveats, CitationCard for key sources). The runtime "
            "will append the REPORTER format contract specifying the required "
            "section structure (Executive Summary, Key Findings, Disagreements "
            "& Tensions, Risks & Concerns, Next Steps)."
        ),
    }
    if episodic:
        cfg["prompt"] += (
            "\n\nEPISODE RECORD (cross-run precedent): after your report, use memory_write to "
            f"/tessera/catalog/episodes/{eco_id} to persist a COMPACT record of THIS run as "
            "precedent for future runs — the mission in one line, the key decisions/verdicts "
            "reached, what the Challenger flagged, the outcome, and any lesson learned. Embed "
            "today's date. Keep it to a paragraph or two: it is precedent about HOW the run went, "
            "NOT the full analysis (which already lives in your report and run history)."
        )
    return cfg


# --- Handler ---

def handler(event, context):
    eco_id = event["pathParameters"]["id"]
    path = event.get("path", "")

    if path.endswith("/deploy"):
        try:
            _b = json.loads(event.get("body") or "{}")
        except Exception:
            _b = {}
        if _b.get("action") == "promote_template":
            return promote_template(eco_id, event)
        return deploy_state_machine(eco_id)
    return save_yaml(eco_id, event)


def _slugify(s):
    return re.sub(r"[^a-z0-9_-]+", "-", str(s).lower()).strip("-") or "x"


def promote_template(eco_id, event):
    """Beautify Phase 3: promote a beautified look to the ecosystem's template.

    Stores the accumulated CSS overrides as templates/<id>/template.css (+ an
    example.html for provenance + a slots.json manifest), points the deliverable
    agent's output_format.template_id at it, and recompiles. LLM-free — the CSS
    overrides ARE the reusable stylesheet (see docs/spec-beautify-standardize.md).
    """
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from auth import require_auth, is_admin as _is_admin
    try:
        user = require_auth(event)
    except PermissionError:
        return response({"error": "Unauthorized"}, 401)

    body = json.loads(event.get("body") or "{}")
    agent_id = (body.get("agentId") or "").strip()
    template_css = body.get("templateCss") or ""
    example_html = body.get("exampleHtml") or ""
    transforms = body.get("transforms")
    transforms = transforms if isinstance(transforms, list) else []
    # Only DATA-INDEPENDENT structural transforms (e.g. tabs) are promotable to the template.
    # `diagram` transforms embed a run-specific SVG built from THIS run's data — they stay with
    # the run (saved deliverable), not the template (future runs have different data).
    transforms = [t for t in transforms if isinstance(t, dict) and t.get("type") in ("tabs",)]
    if not agent_id:
        return response({"error": "agentId is required"}, 400)
    if not template_css.strip() and not transforms:
        return response({"error": "Nothing to promote — make a design change first"}, 400)

    try:
        obj = s3.get_object(Bucket=BUCKET, Key=f"ecosystems/{eco_id}.yaml")
        cfg = yaml.safe_load(obj["Body"].read()) or {}
    except Exception as e:
        return response({"error": f"Ecosystem not found: {e}"}, 404)
    if bool(cfg.get("platform")):
        return response({"error": "Platform ecosystems are read-only via API."}, 403)
    owner = cfg.get("owner", "")
    if owner and owner != user["user_id"] and not _is_admin(user):
        return response({"error": "Only the owner or an admin can promote a template"}, 403)

    agents = cfg.get("agents")
    if not isinstance(agents, list):
        agents = (cfg.get("ecosystem") or {}).get("agents")
    target = next((a for a in (agents or []) if isinstance(a, dict) and a.get("id") == agent_id), None)
    if target is None:
        return response({"error": f"Agent {agent_id!r} not found in {eco_id}"}, 400)

    template_id = f"{_slugify(eco_id)}__{_slugify(agent_id)}"
    try:
        if template_css.strip():
            s3.put_object(Bucket=BUCKET, Key=f"templates/{template_id}/template.css",
                          Body=template_css.encode("utf-8"), ContentType="text/css; charset=utf-8")
        if transforms:
            s3.put_object(Bucket=BUCKET, Key=f"templates/{template_id}/transforms.json",
                          Body=json.dumps(transforms).encode("utf-8"), ContentType="application/json")
        if example_html.strip():
            s3.put_object(Bucket=BUCKET, Key=f"templates/{template_id}/example.html",
                          Body=example_html.encode("utf-8"), ContentType="text/html; charset=utf-8")
        slots = {"kind": "house_style", "source_agent": agent_id,
                 "transforms": transforms,
                 "note": "Promoted CSS override stylesheet (+ presentation transforms) for future deliverables."}
        s3.put_object(Bucket=BUCKET, Key=f"templates/{template_id}/slots.json",
                      Body=json.dumps(slots).encode("utf-8"), ContentType="application/json")
    except Exception as e:
        return response({"error": f"Failed to store template: {e}"}, 500)

    of = target.get("output_format")
    if not isinstance(of, dict):
        of = {}
    of["template_id"] = template_id
    target["output_format"] = of
    new_yaml = yaml.dump(cfg, default_flow_style=False, sort_keys=False)
    s3.put_object(Bucket=BUCKET, Key=f"ecosystems/{eco_id}.yaml", Body=new_yaml.encode(), ContentType="text/yaml")

    deploy_result = deploy_state_machine(eco_id)
    deploy_body = json.loads(deploy_result["body"])
    return response({
        "promoted": True,
        "template_id": template_id,
        "agent_id": agent_id,
        "transforms": len(transforms),
        "deployed": deploy_body.get("deployed", False),
        "error": deploy_body.get("error"),
    })


def _validate_decision_block(yaml_content):
    """spec-decision-engine.md: validate an optional `decision:` block at save time.
    Returns a list of human-readable errors ([] = valid / absent). Never raises."""
    try:
        cfg = yaml.safe_load(yaml_content) or {}
    except Exception:
        return []
    dcfg = cfg.get("decision") or (cfg.get("ecosystem") or {}).get("decision")
    if not dcfg:
        return []
    errs = []
    try:
        from tessera_platform.decision import validate_config
        errs = list(validate_config(dcfg))
    except Exception as e:
        print(f"WARN: decision engine unavailable, skipping structural validation: {e}")
    agent_ids = {a.get("id") for a in (cfg.get("agents") or []) if isinstance(a, dict)}
    sb = dcfg.get("scored_by")
    if not sb:
        errs.append("decision.scored_by is required (the agent id that emits DECISION ITEMS)")
    elif sb not in agent_ids:
        errs.append(f"decision.scored_by={sb!r} is not an agent in this ecosystem ({sorted(agent_ids)})")
    return errs


def save_yaml(eco_id, event):
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from auth import require_auth, is_admin as _is_admin

    try:
        user = require_auth(event)
    except PermissionError:
        return response({"error": "Unauthorized"}, 401)

    body = json.loads(event["body"])
    yaml_content = body.get("yaml", "")

    # Eval authoring (spec §8, Phase 5): the deploy wizard can re-save an eco with
    # an SME-APPROVED `evaluation:` block merged in (from suggest-ecosystem
    # mode=eval_approve). Merge it into the YAML here so the compiler builds the
    # per-eco Eval State Machine. Eco-AGNOSTIC — the block is generic config.
    eval_block = body.get("evaluation")
    if isinstance(eval_block, dict) and yaml_content:
        try:
            _c = yaml.safe_load(yaml_content) or {}
            _c["evaluation"] = eval_block
            yaml_content = yaml.dump(_c, default_flow_style=False, sort_keys=False)
        except yaml.YAMLError:
            pass

    # --- Platform-ecosystem read-only enforcement (Phase 5d) ---------------
    # Platform ecosystems (e.g. platform-optimizer) are repo-managed. They
    # carry `platform: true` at the top level. Block all API-driven writes
    # to those — they should only be updated via deploy-platform-ecosystems.sh
    # which goes around this Lambda. Two checks:
    #   1. The existing object in S3 (if any) has platform:true — reject.
    #   2. The incoming body sets platform:true — reject (only the deploy
    #      script is allowed to introduce that flag).
    # The deploy script bypasses this Lambda entirely (writes directly to
    # S3 with admin creds), so this Lambda is the only API ingress.
    incoming_is_platform = False
    try:
        incoming_cfg = yaml.safe_load(yaml_content) or {}
        incoming_is_platform = bool(incoming_cfg.get("platform"))
    except yaml.YAMLError:
        # Let the existing pipeline surface YAML errors below; do not
        # claim platform-ness on unparseable input.
        incoming_cfg = {}

    existing_is_platform = False
    try:
        existing = s3.get_object(Bucket=BUCKET, Key=f"ecosystems/{eco_id}.yaml")
        existing_cfg = yaml.safe_load(existing["Body"].read()) or {}
        existing_is_platform = bool(existing_cfg.get("platform"))
    except s3.exceptions.NoSuchKey:
        pass

    if existing_is_platform or incoming_is_platform:
        return {
            "statusCode": 403,
            "headers": {"Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*")},
            "body": json.dumps({
                "error": "Platform ecosystems are read-only via API.",
                "detail": (
                    f"Ecosystem '{eco_id}' is a platform ecosystem (managed by "
                    "the platform, deployed from repo). Edit the source under "
                    "ecosystems/_platform/ and run scripts/deploy-platform-"
                    "ecosystems.sh."
                ),
                "platform": True,
            }),
        }

    # Ownership enforcement
    is_new = not existing_is_platform and 'existing_cfg' not in dir()
    try:
        existing_obj = s3.get_object(Bucket=BUCKET, Key=f"ecosystems/{eco_id}.yaml")
        existing_owner_cfg = yaml.safe_load(existing_obj["Body"].read()) or {}
        existing_owner = existing_owner_cfg.get("owner", "")
        is_new = False
    except Exception:
        existing_owner = ""
        is_new = True

    if is_new:
        # Inject owner into YAML on create
        try:
            parsed = yaml.safe_load(yaml_content) or {}
            parsed["owner"] = user["user_id"]
            yaml_content = yaml.dump(parsed, default_flow_style=False, sort_keys=False)
        except yaml.YAMLError:
            pass
    else:
        # On update, verify user is owner or admin
        if existing_owner and existing_owner != user["user_id"] and not _is_admin(user):
            return response({"error": "Only the owner or an admin can edit this ecosystem"}, 403)

    # Strip any injected platform agents (challenger/report/checkpoints) from the
    # YAML before storing — they are compiler-injected at deploy time, must never
    # live in the stored eco, and accumulating them corrupts the eco (duplicate
    # agent ids). Keep only user-authored agents. The eco's category/tags are
    # author-owned config (emitted by the builder, editable in the wizard) and
    # are persisted as-is — no classification happens here.
    try:
        _parsed = yaml.safe_load(yaml_content) or {}
        if isinstance(_parsed.get("agents"), list):
            _clean = [a for a in _parsed["agents"]
                      if not re.match(r"^(challenger_agent|report_agent|checkpoint_\d+)$", str((a or {}).get("id") or ""))]
            if len(_clean) != len(_parsed["agents"]):
                _parsed["agents"] = _clean
                yaml_content = yaml.dump(_parsed, default_flow_style=False, sort_keys=False)
    except yaml.YAMLError:
        pass

    # spec-decision-engine.md: reject a structurally invalid `decision:` block at save time.
    _dec_errs = _validate_decision_block(yaml_content)
    if _dec_errs:
        return response({"error": "Invalid `decision` block", "detail": _dec_errs}, 400)

    s3.put_object(Bucket=BUCKET, Key=f"ecosystems/{eco_id}.yaml", Body=yaml_content.encode(), ContentType="text/yaml")

    # Persist the builder conversation transcript (if the eco was built via the AI
    # builder) as a sidecar, for run replay. Stored under ecosystems/_transcripts/ —
    # the leading "_" keeps it out of list-ecosystems (which skips "/_" keys) and it is
    # never read by get-ecosystem. Best-effort — never block the save on it.
    build_transcript = body.get("build_transcript") or []
    if build_transcript:
        try:
            s3.put_object(
                Bucket=BUCKET,
                Key=f"ecosystems/_transcripts/{eco_id}.builder.json",
                Body=json.dumps(build_transcript, default=str).encode(),
                ContentType="application/json",
            )
        except Exception as e:
            print(f"[save-ecosystem] failed to persist build transcript for {eco_id}: {e}")

    # Create AVP owner policy for new ecosystems
    if is_new:
        _create_owner_policy(user["user_id"], eco_id)

    deploy_result = deploy_state_machine(eco_id)
    deploy_body = json.loads(deploy_result["body"])
    if deploy_body.get("deployed"):
        _emit_change_event(eco_id, "ecosystem.deployed", user.get("user_id", ""))

    return response({
        "saved": True,
        "id": eco_id,
        "deployed": deploy_body.get("deployed", False),
        "interactive": deploy_body.get("interactive", False),
        "state_machine_arn": deploy_body.get("state_machine_arn", ""),
        # Forward compile-time warnings so callers (frontend deploy wizard,
        # recompile scripts, CI checks) can surface them. Previously dropped,
        # which hid soft-warning kinds like synthesizer_no_writable_namespace
        # and shared_namespace_one_writer_no_auto_persist from view.
        "warnings": deploy_body.get("warnings", []),
        "error": deploy_body.get("error"),
        "eval_state_machine_arn": deploy_body.get("eval_state_machine_arn", ""),
        "eval_gate": deploy_body.get("eval_gate"),
    })


def _emit_change_event(eco_id, change_type, actor=""):
    """Best-effort: ANNOUNCE an ecosystem change to the re-grounding bus. The source
    does NOT decide which catalogs update — the router does (spec-regrounding-bus.md).
    A failed emit never breaks the save/deploy."""
    try:
        import datetime
        bus = os.environ.get("REGROUNDING_BUS", "tessera-regrounding")
        boto3.client("events").put_events(Entries=[{
            "Source": "tessera.save-ecosystem",
            "DetailType": "ChangeSignal",
            "EventBusName": bus,
            "Detail": json.dumps({
                "change_type": change_type,          # e.g. ecosystem.deployed
                "subject": eco_id,
                "domain_hint": "tessera-self",
                "context": {"action": "deploy", "eco_id": eco_id},
                "actor": actor,
                "occurred_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }),
        }])
    except Exception as e:
        print(f"[regrounding] emit failed (non-fatal): {type(e).__name__}: {e}")


def deploy_state_machine(eco_id):
    """Compile YAML → ASL and create/update AWS Step Functions state machine."""
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=f"ecosystems/{eco_id}.yaml")
        config = yaml.safe_load(obj["Body"].read())
    except Exception as e:
        return response({"error": f"Failed to read YAML: {str(e)}"}, 400)

    # New eco execution model: INTERACTIVE ecos (strategy: interactive) answer synchronously
    # via the live serve path (spec-interactive-eco.md) — they run their multi-agent graph in
    # one turn, no Step Functions. So skip ASL compile/deploy and register as interactive.
    strat = config.get("strategy") or (config.get("ecosystem") or {}).get("strategy") or "orchestrated"
    if strat == "interactive":
        return response({"deployed": True, "interactive": True, "state_machine_arn": "",
                         "note": "interactive eco — served live (no Step Functions compiled)",
                         "warnings": []})

    # Quorum archetype (spec-quorum-archetype.md §2): the deliberation panel is assembled
    # and run at RUNTIME, never compiled to a Step Functions DAG. Gated behind QUORUM_ENABLED
    # and FAIL-CLOSED: disabled ⇒ refuse (no state machine created); enabled ⇒ register
    # WITHOUT compiling legacy rails, so it can never execute on the standard engine. Purely
    # additive — the orchestrated / interactive / choreography paths are untouched.
    if strat == "quorum":
        if os.environ.get("QUORUM_ENABLED", "").strip().lower() not in ("1", "true", "yes", "on"):
            return response({"error": "Quorum archetype is not enabled in this environment "
                                      "(QUORUM_ENABLED unset). No state machine compiled."}, 400)
        # Validate the optional whiteboard policy block (spec-quorum-whiteboard.md); fail-closed.
        _wb = ((config.get("quorum") or {}).get("whiteboard")) if isinstance(config.get("quorum"), dict) else None
        if _wb is not None:
            try:
                from tessera_platform.quorum.whiteboard import validate_policy as _wb_validate
                _wb_errs = _wb_validate(_wb)
            except Exception as _e:  # defensive: never let validation import break save
                _wb_errs = []
            if _wb_errs:
                return response({"error": "Invalid quorum.whiteboard policy: " + "; ".join(_wb_errs)}, 400)
        return response({"deployed": True, "quorum": True, "state_machine_arn": "",
                         "note": "quorum eco — assembled & deliberated at runtime (no Step Functions compiled)",
                         "warnings": []})

    asl = compile_to_asl(config, eco_id)
    asl_json = json.dumps(asl)

    # Phase 4.5: surface compile-time warnings (tool composition, drift) to the UI.
    warnings = compile_to_asl.__dict__.get("last_warnings", [])

    sm_name = f"tessera-dev-{eco_id}"
    try:
        existing = sfn.list_state_machines()
        sm_arn = None
        for sm in existing.get("stateMachines", []):
            if sm["name"] == sm_name:
                sm_arn = sm["stateMachineArn"]
                break

        if sm_arn:
            sfn.update_state_machine(stateMachineArn=sm_arn, definition=asl_json, roleArn=SFN_ROLE_ARN)
        else:
            result = sfn.create_state_machine(name=sm_name, definition=asl_json, roleArn=SFN_ROLE_ARN, type="STANDARD")
            sm_arn = result["stateMachineArn"]

        # --- Ecosystem Evaluation Harness (spec §5.1, §6.3, §10) ---
        # Compile/deploy the fresh per-eco Eval State Machine (if an `evaluation:`
        # block is declared) and consult the deploy gate against the latest
        # scorecard. The gate can REFUSE the deploy (gate: block + un-passed
        # scorecard / band-clearing regression). Fail loudly on a malformed block.
        eval_info = _deploy_eval_state_machine(eco_id, config, sm_arn)
        for w in eval_info.get("warnings", []):
            warnings.append(w)
        gate = eval_info.get("gate")
        if gate and gate.get("gate") == "block" and not gate.get("allow_deploy", True):
            return response({
                "error": "Deploy refused by evaluation gate (gate: block).",
                "eval_gate": gate,
                "state_machine_arn": sm_arn,
                "eval_state_machine_arn": eval_info.get("eval_state_machine_arn", ""),
                "warnings": warnings,
            }, 409)

        return response({"deployed": True, "state_machine_arn": sm_arn, "asl": asl,
                         "warnings": warnings,
                         "eval_state_machine_arn": eval_info.get("eval_state_machine_arn", ""),
                         "eval_gate": gate})
    except Exception as e:
        return response({"error": f"Deploy failed: {str(e)}"}, 500)


def _deploy_eval_state_machine(eco_id, config, run_sm_arn):
    """Compile the `evaluation:` block into a per-eco Eval State Machine and
    consult the deploy gate. Returns {warnings, eval_state_machine_arn, gate}.

    Eco-AGNOSTIC: everything comes from the generic `evaluation:` config; this
    function contains no domain vocabulary. Fails loudly on a malformed block
    (the parser raises EvaluationConfigError, surfaced by the caller)."""
    from tessera_platform.evaluation import (
        parse_evaluation_block, validate_evaluation_block,
    )
    from tessera_platform.evaluation.eval_compiler import compile_eval_state_machine

    out = {"warnings": [], "eval_state_machine_arn": "", "gate": None}
    ev = parse_evaluation_block(config)   # raises on malformed → loud fail
    if ev is None:
        return out
    out["warnings"] = validate_evaluation_block(config)

    # Compile the Eval SM and substitute the ${...} placeholders with real ARNs.
    if not (EVAL_RUNNER_ARN and EVAL_AGGREGATOR_ARN):
        out["warnings"].append({
            "kind": "eval_harness_not_deployed",
            "detail": "evaluation: block present but eval Lambdas are not wired "
                      "(EVAL_RUNNER_ARN/EVAL_AGGREGATOR_ARN unset). The Eval State "
                      "Machine was not compiled. Run cdk deploy to provision it.",
        })
    else:
        eval_asl = compile_eval_state_machine(config, ev, eco_id)
        eval_json = json.dumps(eval_asl)
        subs = {
            "${EvalRunnerLambdaArn}": EVAL_RUNNER_ARN,
            "${EvalJudgeLambdaArn}": EVAL_JUDGE_ARN or EVAL_RUNNER_ARN,
            "${EvalTrajectoryLambdaArn}": EVAL_TRAJECTORY_ARN or EVAL_RUNNER_ARN,
            "${EvalAggregatorLambdaArn}": EVAL_AGGREGATOR_ARN,
            "${TargetRunStateMachineArn}": run_sm_arn,
        }
        for k, v in subs.items():
            eval_json = eval_json.replace(k, v)
        eval_name = f"tessera-dev-{eco_id}-eval"
        try:
            eval_arn = None
            for sm in sfn.list_state_machines().get("stateMachines", []):
                if sm["name"] == eval_name:
                    eval_arn = sm["stateMachineArn"]
                    break
            role = EVAL_SFN_ROLE_ARN or SFN_ROLE_ARN
            if eval_arn:
                sfn.update_state_machine(stateMachineArn=eval_arn, definition=eval_json, roleArn=role)
            else:
                eval_arn = sfn.create_state_machine(
                    name=eval_name, definition=eval_json, roleArn=role, type="STANDARD")["stateMachineArn"]
            out["eval_state_machine_arn"] = eval_arn
        except Exception as e:
            out["warnings"].append({"kind": "eval_sm_deploy_failed",
                                    "detail": f"Eval State Machine deploy failed: {e}"})

    # Consult the deploy gate against the latest scorecard (spec §6.3, §10).
    out["gate"] = _consult_deploy_gate(eco_id, ev)
    return out


def _consult_deploy_gate(eco_id, ev):
    """Read the latest scorecard for eco_id from S3 and apply the deploy gate.

    A `block` gate with NO scorecard fails CLOSED (spec §10) — you cannot claim
    correctness you have not measured. `warn`/`off` with no scorecard are
    informational."""
    from tessera_platform.evaluation.scorecard import (
        Scorecard, apply_deploy_gate,
    )
    from tessera_platform.evaluation.aggregate import AggregatedMetric

    latest = _latest_scorecard(eco_id)
    if latest is None:
        if ev.gate == "block":
            return {"gate": "block", "allow_deploy": False, "failures": [],
                    "regressions": [],
                    "summary": "gate: block — no scorecard on record for this "
                               "ecosystem/version; run the Eval State Machine before "
                               "deploying (fail-closed, spec §10)."}
        return {"gate": ev.gate, "allow_deploy": True, "failures": [], "regressions": [],
                "summary": f"gate: {ev.gate} — no scorecard yet (informational)."}

    # Re-hydrate a minimal Scorecard from the stored dict for gate evaluation.
    metrics = []
    for m in latest.get("metrics", []):
        metrics.append(AggregatedMetric(
            metric_id=m["metric_id"], scorer=m.get("scorer", ""),
            median=m["median"], band=m["band"], empirical_band=m.get("empirical_band", 0.0),
            n=m.get("n", 1), miss_rate=m.get("miss_rate", 0.0),
            threshold=m.get("threshold")))
    lin = latest.get("lineage", {})
    sc = Scorecard(
        ecosystem_id=eco_id, ecosystem_version=lin.get("ecosystem_version", ""),
        config_hash=lin.get("config_hash", ""), golden_set_version=lin.get("golden_set_version", ""),
        judge_model=lin.get("judge_model"), timestamp=lin.get("timestamp", ""),
        gate=ev.gate, metrics=metrics)
    outcome = apply_deploy_gate(ev, sc)
    return outcome.as_dict()


def _latest_scorecard(eco_id):
    """Return the most-recent scorecard dict for eco_id from the runs bucket, or
    None. Scorecards live at evaluations/<eco>/scorecards/<timestamp>.json."""
    if not RUNS_BUCKET:
        return None
    prefix = f"evaluations/{eco_id}/scorecards/"
    try:
        keys = []
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=RUNS_BUCKET, Prefix=prefix):
            for o in page.get("Contents", []):
                keys.append((o["LastModified"], o["Key"]))
        if not keys:
            return None
        keys.sort()
        _, latest_key = keys[-1]
        return json.loads(s3.get_object(Bucket=RUNS_BUCKET, Key=latest_key)["Body"].read())
    except Exception as e:
        print(f"[eval-gate] failed to read latest scorecard for {eco_id}: {e}")
        return None


# --- ASL Compiler ---

# Phase 4.5: tool families that synthesizers shouldn't use — they should rely on
# upstream + memory_read instead of redoing research. This is a soft warning, not
# a hard rule (some synthesizers may legitimately need to verify a specific claim).
DISCOURAGED_TOOLS_FOR_SYNTHESIZERS = {"web_search", "web_search", "exa_search"}

# Phase 5.1: per-tool default budgets (max calls per agent per invocation).
# Tuned for typical workloads; operators override via YAML `tool_budget:` per
# agent when they need more (research-heavy ecosystems) or less (constrained
# environments). Synthesizers get 0 web_search by default — the role contract
# says they synthesize upstream, not research independently.
DEFAULT_TOOL_BUDGETS = {
    # role "drafter" = artifact builders (output_kind: drafter). They assemble a
    # typed file (PDF/HTML/docx/…) via code_interpreter, which legitimately needs
    # FAR more code calls than a prose synthesizer. Under-budgeting them forces a
    # chunk-and-resave loop that produced a stacked, mostly-blank PDF (buyerside
    # 2026-06-23: composer had only 8 code calls, rationed, and re-output() per
    # section → 7 concatenated PDF documents). Drafters get a generous cap.
    "web_search":       {"leaf": 6,  "synthesizer": 0,  "drafter": 0,  "researcher": 6},
    "exa_search":       {"leaf": 6,  "synthesizer": 0,  "drafter": 0,  "researcher": 6},
    "code_interpreter": {"leaf": 5,  "synthesizer": 8,  "drafter": 20, "researcher": 5},
    "calculator":       {"leaf": 10, "synthesizer": 10, "drafter": 10, "researcher": 10},
    "current_time":     {"leaf": 3,  "synthesizer": 3,  "drafter": 3,  "researcher": 3},
    "generateUI":       {"leaf": 5,  "synthesizer": 8,  "drafter": 10, "researcher": 8},
    "http_request":     {"leaf": 6,  "synthesizer": 2,  "drafter": 2,  "researcher": 6},
    # memory_read / memory_write intentionally UNCAPPED — they're the primary
    # inter-agent handoff mechanism, cheap, fast, and small payloads. Capping
    # them breaks the architecture (agents can't persist per-record data).
}

# Platform tools the runtime always provides (used by compiler when computing
# default budgets). Custom Gateway tools fall through to operator-explicit
# budgets only.
PLATFORM_TOOLS = set(DEFAULT_TOOL_BUDGETS.keys())

# Anything above this threshold is almost certainly a typo / misconfiguration.
TOOL_BUDGET_HIGH_WATERMARK = 50

# Tools whose whole purpose is fetching FRESH EXTERNAL FACTS. These must never be
# SILENTLY resolved to 0 just because an agent carries `depends_on` (which would
# otherwise classify it as a synthesizer, and synthesizers default to 0
# web_search). If the operator/builder explicitly listed such a tool, honor it
# with a research-grade budget — "listed ⇒ usable". An explicit YAML
# `tool_budget: {web_search: 0}` still wins (a deliberate opt-out).
RESEARCH_TOOLS = {"web_search", "exa_search"}


def _budget_role(agent: dict) -> str:
    """Derive an agent's tool-budget tier. `output_kind` wins (drafter/researcher);
    otherwise an agent with upstream deps is a synthesizer, else a leaf."""
    _ok = str(agent.get("output_kind") or "").lower()
    if _ok == "drafter":
        return "drafter"
    if _ok == "researcher":
        return "researcher"
    return "synthesizer" if agent.get("depends_on") else "leaf"


def _resolve_agent_tool_budget(agent: dict) -> dict:
    """Resolve per-tool invocation budgets for a single agent (pure; unit-tested).

    Defaults come from DEFAULT_TOOL_BUDGETS keyed by the agent's role tier; an
    explicit per-agent YAML `tool_budget:` always overrides.

    BACKSTOP: a research tool the operator explicitly listed in `tools` on a
    dependent (synthesizer-classified) agent is never silently defaulted to 0 —
    it gets the researcher budget instead. This prevents the trap where a
    research agent that carries `depends_on` (e.g. "analyse each ticker on the
    scanner's shortlist") is compiled as a synthesizer and can't search the web
    despite listing web_search. Only an explicit YAML override may zero it.
    """
    role = _budget_role(agent)
    tools_listed = agent.get("tools", []) or []
    yaml_override = agent.get("tool_budget") or {}
    resolved = {}
    for tool_name in PLATFORM_TOOLS:
        if tool_name not in tools_listed:
            continue
        _caps = DEFAULT_TOOL_BUDGETS[tool_name]
        default = _caps.get(role, _caps.get("synthesizer", _caps.get("leaf", 0)))
        if default == 0 and role == "synthesizer" and tool_name in RESEARCH_TOOLS:
            default = _caps.get("researcher", _caps.get("leaf", 0))
        resolved[tool_name] = yaml_override.get(tool_name, default)
    # Honor explicit operator caps for tools we don't have platform defaults for
    # (e.g. a custom Gateway tool).
    for tool_name, cap in yaml_override.items():
        if tool_name not in resolved:
            resolved[tool_name] = cap
    return resolved


# ─── Phase 5e: Templates fetch ───────────────────────────────────────────────
#
# Templates live at s3://<ecosystems-bucket>/templates/<id>/template.<ext>.
# At compile time, the compiler fetches the body for any agent whose
# output_format.template_id matches a deployed template, and inlines it
# into the agent's prompt. _TEMPLATE_BODY_CACHE is per-Lambda-invocation
# (resets between invocations) — fine because compile is rare (on save).

_TEMPLATE_BODY_CACHE: dict = {}
# Common extensions to try, in priority order. Most templates are .yaml
# (the most common artifact-shape) but we also support html/json/md/txt.
_TEMPLATE_EXTENSIONS = (".yaml", ".yml", ".json", ".html", ".md", ".txt")


def _fetch_template_body(template_id: str) -> str:
    """Fetch the template body for the given template_id from S3.
    Returns the body as a string, or empty string if not found.
    Tries common extensions in order.
    """
    if not template_id:
        return ""
    if template_id in _TEMPLATE_BODY_CACHE:
        return _TEMPLATE_BODY_CACHE[template_id]
    for ext in _TEMPLATE_EXTENSIONS:
        key = f"templates/{template_id}/template{ext}"
        try:
            obj = s3.get_object(Bucket=BUCKET, Key=key)
            body = obj["Body"].read().decode("utf-8", errors="replace")
            _TEMPLATE_BODY_CACHE[template_id] = body
            return body
        except s3.exceptions.NoSuchKey:
            continue
        except Exception as e:
            print(f"template fetch error for {template_id}{ext}: {e}")
            continue
    _TEMPLATE_BODY_CACHE[template_id] = ""
    return ""


def validate_ecosystem(config, agents):
    """Validate the YAML before compiling. Raise on hard errors, return soft warnings.

    Hard errors (raise ValueError):
      - agent.depends_on references a non-existent agent id
      - duplicate agent ids in the same ecosystem

    Soft warnings (returned, surfaced in API response):
      - Synthesizer agent (depends_on != []) has discouraged tools (web_search etc.)
      - Synthesizer agent's prompt doesn't mention any of its declared upstream by id
        (drift signal: depends_on graph and prompt are out of sync)
      - Synthesizer agent's prompt doesn't mention citation format
    """
    warnings = []
    ids = [a.get("id") for a in agents]
    seen = set()
    for aid in ids:
        if not aid:
            raise ValueError(f"agent missing 'id' field in ecosystem YAML")
        if aid in seen:
            raise ValueError(f"duplicate agent id {aid!r} in ecosystem YAML")
        seen.add(aid)
    id_set = set(ids)
    for agent in agents:
        deps = agent.get("depends_on", []) or []
        for dep in deps:
            if dep not in id_set:
                raise ValueError(
                    f"agent {agent['id']!r} depends_on {dep!r} but no agent with that id "
                    f"exists in this ecosystem. Known agents: {sorted(id_set)}"
                )
        if not deps:
            continue  # leaves are exempt from synthesizer-specific checks
        prompt = (agent.get("prompt") or "")
        prompt_lower = prompt.lower()
        # Drift check: synthesizer should reference its declared upstream by id
        missing_in_prompt = [d for d in deps if d.lower() not in prompt_lower]
        if missing_in_prompt:
            warnings.append({
                "level": "warning",
                "agent": agent["id"],
                "kind": "drift",
                "message": (
                    f"synthesizer {agent['id']!r} depends on {missing_in_prompt} but its "
                    f"prompt doesn't mention them by id. The compiler will inject the "
                    f"sibling registry, but a prompt that references upstream agents by "
                    f"name produces sharper agent behavior."
                ),
            })
        # Tool composition: synthesizers should avoid web research tools.
        # A `researcher` (output_kind) is EXEMPT — research is its purpose (it
        # gets a real web_search budget and is grounding-checked).
        _ok2 = str(agent.get("output_kind") or "").lower()
        bad = [] if _ok2 == "researcher" else [
            t for t in (agent.get("tools") or []) if t in DISCOURAGED_TOOLS_FOR_SYNTHESIZERS
        ]
        if bad:
            warnings.append({
                "level": "warning",
                "agent": agent["id"],
                "kind": "tool_composition",
                "message": (
                    f"synthesizer {agent['id']!r} has {bad} in tools. Synthesizers should "
                    f"rely on upstream findings + memory_read rather than redoing research. "
                    f"Consider removing these tools or marking the agent as a leaf."
                ),
            })
        # Citation hint: nudge operator toward including the format in their prompt
        if "[per " not in prompt:
            warnings.append({
                "level": "info",
                "agent": agent["id"],
                "kind": "citation_hint",
                "message": (
                    f"synthesizer {agent['id']!r} prompt doesn't show the citation format. "
                    f"Compiler will inject MANDATORY UPSTREAM CITATIONS section, but a "
                    f"prompt that explicitly demonstrates `[per <agent_id>: ...]` produces "
                    f"better-formatted output."
                ),
            })

    # Phase 5.1: tool_budget warnings — applied to ALL agents (leaf or synth)
    for agent in agents:
        deps = agent.get("depends_on", []) or []
        _ok = str(agent.get("output_kind") or "").lower()
        # researcher/drafter are their own roles even though they carry deps —
        # a researcher is ALLOWED research tools (that's its purpose).
        if _ok == "researcher":
            role = "researcher"
        elif _ok == "drafter":
            role = "drafter"
        else:
            role = "synthesizer" if deps else "leaf"
        tools_listed = agent.get("tools", []) or []
        yaml_override = agent.get("tool_budget") or {}
        for tool_name, cap in yaml_override.items():
            # Reject obviously misconfigured budgets
            if not isinstance(cap, int) or cap < 0:
                raise ValueError(
                    f"agent {agent['id']!r} tool_budget[{tool_name!r}] must be a "
                    f"non-negative int, got {cap!r}"
                )
            # Budget=0 with the tool listed in tools is almost certainly a mistake
            if cap == 0 and tool_name in tools_listed:
                warnings.append({
                    "level": "warning",
                    "agent": agent["id"],
                    "kind": "budget_zero_with_tool_listed",
                    "message": (
                        f"agent {agent['id']!r} has {tool_name!r} in tools but "
                        f"tool_budget caps it at 0. Either remove {tool_name!r} from "
                        f"tools or grant it a positive budget."
                    ),
                })
            if cap > TOOL_BUDGET_HIGH_WATERMARK:
                warnings.append({
                    "level": "warning",
                    "agent": agent["id"],
                    "kind": "budget_unreasonably_high",
                    "message": (
                        f"agent {agent['id']!r} tool_budget[{tool_name!r}]={cap} is "
                        f"above the {TOOL_BUDGET_HIGH_WATERMARK}-call watermark. "
                        f"Likely a typo. Confirm the agent really needs this many calls."
                    ),
                })
        # Synthesizer overriding research tools to non-zero — counter to role contract
        if role == "synthesizer":
            for research_tool in DISCOURAGED_TOOLS_FOR_SYNTHESIZERS:
                if yaml_override.get(research_tool, 0) > 0:
                    warnings.append({
                        "level": "warning",
                        "agent": agent["id"],
                        "kind": "synthesizer_research_override",
                        "message": (
                            f"synthesizer {agent['id']!r} has tool_budget[{research_tool!r}]="
                            f"{yaml_override[research_tool]} > 0. Synthesizers are expected "
                            f"to rely on upstream + memory_read, not redo research. If this "
                            f"agent legitimately needs live research, set output_kind: researcher "
                            f"(it keeps its depends_on and gets a web_search budget)."
                        ),
                    })

    # Phase 5.1 follow-on: every synthesizer should have at least one writable
    # namespace, otherwise memory_write returns "not authorized" and the
    # synthesizer's analysis is invisible to downstream agents that try to read
    # via memory_read. Soft warning — operators may legitimately design read-only
    # synthesizers, but it's almost always an oversight.
    memory_cfg = config.get("memory", {}) or {}
    shared_namespaces = memory_cfg.get("shared_namespaces", []) or []
    for agent in agents:
        deps = agent.get("depends_on", []) or []
        if not deps:
            continue  # leaves are exempt
        has_writable = False
        for ns in shared_namespaces:
            write_list = ns.get("write")
            if write_list is None or write_list == "":
                continue
            if isinstance(write_list, str):
                write_list = [write_list]
            if isinstance(write_list, (list, tuple)):
                if agent["id"] in write_list or "all" in write_list:
                    has_writable = True
                    break
        if not has_writable:
            warnings.append({
                "level": "warning",
                "agent": agent["id"],
                "kind": "synthesizer_no_writable_namespace",
                "message": (
                    f"synthesizer {agent['id']!r} has no writable namespace. memory_write "
                    f"will be rejected at runtime, and downstream agents cannot consult "
                    f"this synthesizer's analysis via memory_read. Add a namespace where "
                    f"this agent can publish (e.g. write: [{agent['id']}]) or extend an "
                    f"existing namespace with `write: [all]` for synthesizers."
                ),
            })

    # Phase 5.4: shared-namespace anti-pattern warning. Detects the design
    # where a namespace is declared "shared" (multiple readers or read:[all])
    # but only one agent is authorized to write to it AND auto_persist is off.
    # Operator very likely meant either auto_persist:true (so each agent
    # auto-publishes its STRUCTURED FINDINGS to the shared dossier) or
    # write:[all] (so multiple peer agents can publish manually). Without
    # one of those, downstream readers find at most one writer's findings
    # and complain "upstream hasn't published" — verified failure mode from
    # da-navigator and model-risk-continuous-review on 2026-05-19.
    #
    # If the namespace IS intentionally a single-writer canonical catalog
    # (e.g. /catalog/agents/ written by an agent-pattern researcher), this
    # warning is informational and can be ignored. The warning surfaces the
    # design choice; it doesn't block deploy.
    for ns in shared_namespaces:
        ns_name = ns.get("name") or ns.get("path") or "<unnamed>"
        write_list = ns.get("write")
        read_list = ns.get("read")
        # Normalise.
        if write_list is None or write_list == "":
            write_list = []
        elif isinstance(write_list, str):
            write_list = [write_list]
        else:
            write_list = list(write_list)
        if read_list is None or read_list == "":
            read_list = []
        elif isinstance(read_list, str):
            read_list = [read_list]
        else:
            read_list = list(read_list)
        auto = bool(ns.get("auto_persist", False))

        has_multi_read = "all" in read_list or len(read_list) >= 2
        has_single_write = (
            len(write_list) == 1
            and "all" not in write_list
        )
        if has_multi_read and has_single_write and not auto:
            warnings.append({
                "level": "warning",
                "agent": write_list[0],  # the lone writer
                "kind": "shared_namespace_one_writer_no_auto_persist",
                "message": (
                    f"namespace {ns_name!r} (path {ns.get('path')!r}) is read by "
                    f"multiple agents but only {write_list[0]!r} can write to it, "
                    f"and auto_persist is off. Downstream agents will find at most "
                    f"one writer's findings here. If this is meant to be a per-run "
                    f"dossier where many agents contribute (typical for per-property, "
                    f"per-case, per-customer namespaces), set `write: [all]` and "
                    f"`auto_persist: true`. If it's intentionally a single-writer "
                    f"canonical catalog (like /catalog/agents/ owned by a pattern "
                    f"researcher), this warning is informational — ignore it."
                ),
            })

    # Hygiene guard (catalog cross-contamination) — added 2026-06-30 after the
    # buyerside audit. When MULTIPLE catalog namespaces are each write:[all] +
    # auto_persist:true, EVERY agent auto-mirrors its FULL findings into ALL of
    # them, so unrelated content cross-contaminates (a suburb writeup landed in
    # /catalog/title, /catalog/owner, /catalog/zoning, ...). A single shared
    # per-run dossier with write:[all]+auto_persist is fine; the smell is N>1
    # such catalogs. Fix: scope each catalog's `write` to the one agent that
    # owns that entity type. Non-blocking.
    def _norm_writelist(v):
        if v is None or v == "":
            return []
        return [v] if isinstance(v, str) else list(v)

    def _is_catalog_path(ns) -> bool:
        raw = (ns.get("path") or ns.get("name") or "").strip().strip("/")
        segs = [s for s in raw.split("/") if s]
        if segs and segs[0] == "tessera":
            segs = segs[1:]
        return bool(segs) and segs[0] == "catalog"

    catalog_fanout = [
        (ns.get("path") or ns.get("name"))
        for ns in shared_namespaces
        if _is_catalog_path(ns)
        and "all" in _norm_writelist(ns.get("write"))
        and bool(ns.get("auto_persist", False))
    ]
    if len(catalog_fanout) > 1:
        warnings.append({
            "level": "warning",
            "kind": "catalog_write_all_fanout",
            "message": (
                f"{len(catalog_fanout)} catalog namespaces are write:[all] + auto_persist:true "
                f"({catalog_fanout}). Every agent auto-mirrors its FULL findings into ALL of "
                f"them, so unrelated content cross-contaminates (e.g. a suburb writeup lands in "
                f"/catalog/title). A single shared per-run dossier with write:[all]+auto_persist "
                f"is fine; multiple are not. Scope each catalog's `write` to the single agent "
                f"that owns that entity type, and keep auto_persist on that owner only."
            ),
        })

    # Conclusion-leak guard (spec-domains-knowledge-taxonomy.md §9): an agent that
    # emits run-specific CONCLUSIONS (synthesizer / reporter / drafter / challenger)
    # should not write durable ENTITY catalogs — verdicts/valuations/recommendations
    # belong in the agent's output + run history, not the shared catalog. Only fires
    # when output_kind/agent_role is set (no false positives on unlabeled agents).
    _CONCLUSION_KINDS = {"synthesizer", "reporter", "drafter", "challenger"}
    for agent in agents:
        kind = (agent.get("output_kind") or agent.get("agent_role") or "").strip().lower()
        if kind not in _CONCLUSION_KINDS:
            continue
        leaked = [
            (ns.get("path") or ns.get("name"))
            for ns in shared_namespaces
            if _is_catalog_path(ns)
            and (agent["id"] in _norm_writelist(ns.get("write")) or "all" in _norm_writelist(ns.get("write")))
        ]
        if leaked:
            warnings.append({
                "level": "warning",
                "agent": agent["id"],
                "kind": "conclusion_leak_to_catalog",
                "message": (
                    f"{kind} {agent['id']!r} is authorized to write durable catalog(s) {leaked}. "
                    f"Conclusion producers should NOT populate entity catalogs — verdicts / "
                    f"valuations / recommendations belong in the agent's output and run history, "
                    f"not the shared catalog. Remove this agent from the catalog `write` list "
                    f"(or set the namespace's write to the owning analyst only)."
                ),
            })

    # Memory-design guard (memory-future-design.html #2/#7): flag run-scoped
    # conclusion-dump namespaces. A SINGLE-SEGMENT path (e.g. /tessera/trading_thesis)
    # is run-isolated — memory_read only sees the current run, so it never compounds
    # across runs (the write + semantic extraction are pure waste). Durable cross-run
    # knowledge belongs in an entity-keyed catalog (/tessera/catalog/<domain>/<entity>);
    # a run's conclusions/verdicts belong in the agent's OUTPUT (which already flows
    # downstream and is saved to run history), NOT in memory. Informational; non-blocking.
    for ns in shared_namespaces:
        ns_name = ns.get("name") or ns.get("path") or "<unnamed>"
        raw = (ns.get("path") or ns.get("name") or "").strip().strip("/")
        if not raw:
            continue
        segs = [s for s in raw.split("/") if s]
        if segs and segs[0] == "tessera":
            segs = segs[1:]
        is_shared_root = bool(segs) and segs[0] in ("catalog", "org")
        if len(segs) <= 1 and not is_shared_root:
            warnings.append({
                "level": "warning",
                "kind": "run_scoped_memory_namespace",
                "message": (
                    f"memory namespace {ns_name!r} is single-segment -> RUN-SCOPED: reads only "
                    f"see the current run, so it never compounds across runs (the write + "
                    f"semantic extraction are wasted). For durable cross-run knowledge use an "
                    f"entity-keyed catalog path like /tessera/catalog/<domain>/<entity>. For this "
                    f"run's conclusions/verdicts, rely on the agent's output (it flows downstream "
                    f"and is saved to run history) rather than writing them to memory."
                ),
            })

    # Phase 5f: validate ecosystem.outcome declarations.
    #
    # outcome:
    #   kind: report | file_artifact | action | decision | dataset | hybrid
    #   artifacts:
    #     - id: <stable id>             (required)
    #       agent: <agent_id>           (required, must exist in agents[])
    #       format: markdown|json|yaml|html|csv|pdf|docx|png  (required)
    #       save_as: <filename>         (optional; if missing, uses output<ext>)
    #       download_label: <string>    (optional; UI label)
    #
    # Hard errors:
    #   - artifact.agent references a non-existent agent
    #   - artifact.format conflicts with the producer agent's output_format.type
    # Soft warnings:
    #   - producer agent has no output_format declared (artifact may not be typed)
    #   - kind=file_artifact but no artifacts[] declared
    outcome = (config.get("ecosystem") or {}).get("outcome") or config.get("outcome") or {}
    if not outcome:
        warnings.append({
            "kind": "no_outcome_declared",
            "detail": (
                "No ecosystem.outcome declared. The run will produce no downloadable "
                "artifacts and the Outcome panel will be empty. Add a drafter agent with "
                "output_format and declare outcome.artifacts for a downloadable deliverable."
            ),
        })
    if outcome and not isinstance(outcome, dict):
        raise ValueError(
            f"ecosystem.outcome must be a mapping, got {type(outcome).__name__}"
        )
    if outcome:
        kind = outcome.get("kind", "report")
        valid_kinds = {"report", "file_artifact", "action", "decision", "dataset", "hybrid"}
        if kind not in valid_kinds:
            raise ValueError(
                f"ecosystem.outcome.kind {kind!r} not in {sorted(valid_kinds)}"
            )
        artifacts = outcome.get("artifacts") or []
        if not isinstance(artifacts, list):
            raise ValueError("ecosystem.outcome.artifacts must be a list")
        if kind == "file_artifact" and not artifacts:
            warnings.append({
                "kind": "outcome_no_artifacts",
                "detail": (
                    "ecosystem.outcome.kind is 'file_artifact' but no "
                    "artifacts[] declared — operators won't see any download "
                    "buttons in the run window."
                ),
            })
        agent_by_id = {a["id"]: a for a in agents}
        seen_artifact_ids: set = set()
        for idx, art in enumerate(artifacts):
            if not isinstance(art, dict):
                raise ValueError(
                    f"ecosystem.outcome.artifacts[{idx}] must be a mapping"
                )
            aid = art.get("id")
            if not aid:
                raise ValueError(f"ecosystem.outcome.artifacts[{idx}] missing 'id'")
            if aid in seen_artifact_ids:
                raise ValueError(
                    f"ecosystem.outcome.artifacts has duplicate id {aid!r}"
                )
            seen_artifact_ids.add(aid)
            producer = art.get("agent")
            if not producer:
                raise ValueError(
                    f"ecosystem.outcome.artifacts[{aid}] missing 'agent'"
                )
            if producer not in agent_by_id:
                raise ValueError(
                    f"ecosystem.outcome.artifacts[{aid}].agent={producer!r} "
                    f"references no agent in this ecosystem."
                )
            fmt = art.get("format")
            if not fmt:
                raise ValueError(
                    f"ecosystem.outcome.artifacts[{aid}] missing 'format'"
                )
            valid_formats = {"markdown", "json", "yaml", "html", "csv", "pdf", "docx", "png", "bundle"}
            if fmt not in valid_formats:
                raise ValueError(
                    f"ecosystem.outcome.artifacts[{aid}].format={fmt!r} not in "
                    f"{sorted(valid_formats)}"
                )
            # Cross-check: producer's output_format.type should match if declared
            producer_fmt = (agent_by_id[producer].get("output_format") or {}).get("type")
            if producer_fmt and producer_fmt != fmt:
                raise ValueError(
                    f"ecosystem.outcome.artifacts[{aid}].format={fmt!r} "
                    f"but its producer agent {producer!r} declares "
                    f"output_format.type={producer_fmt!r}. They must match."
                )
            if not producer_fmt and fmt != "markdown":
                warnings.append({
                    "kind": "outcome_artifact_producer_untyped",
                    "agent": producer,
                    "artifact_id": aid,
                    "detail": (
                        f"Artifact {aid!r} declares format={fmt!r} but its "
                        f"producer {producer!r} has no output_format declared. "
                        f"The artifact will fall back to output.txt unless the "
                        f"agent emits a typed payload."
                    ),
                })

    return warnings


def compile_to_asl(config, eco_id=""):
    """Compile ecosystem config to Step Functions ASL with platform agents.

    Generates: Layer → MergeLayer → [Grounding] → next Layer
    - MergeLayer: Pass state that consolidates parallel results into $.results
    - Grounding: Task state (optional) that validates agent output against dependencies
    """
    # Idempotency: strip any previously-injected platform agents (challenger,
    # report, per-level checkpoints) before validate + re-injection. Without this,
    # a stored YAML that accumulated 'challenger_agent'/'report_agent' (e.g. from a
    # round-trip that included them) fails validation with "duplicate agent id" and
    # every re-save adds another copy. They are re-injected fresh further below.
    agents = [a for a in config.get("agents", [])
              if not re.match(r"^(challenger_agent|report_agent|checkpoint_\d+)$", str(a.get("id") or ""))]
    config = {**config, "agents": agents}
    ecosystem_name = config.get("ecosystem", {}).get("name", "unnamed")

    # Phase 4.5: validate the YAML graph before compiling. Raises on hard errors
    # (depends_on to nonexistent agent). Returns soft warnings (tool composition,
    # drift between depends_on and prompt text) attached to the function for the
    # caller (deploy_state_machine) to surface in the API response.
    compile_to_asl.last_warnings = validate_ecosystem(config, agents)

    quality = config.get("quality", {})
    enable_challenger = quality.get("challenger", True)
    # Per-level checkpoints: challenger: per_level enables a fail-fast challenger
    # after each premise-bearing (user) layer, with a human gate (Accept/Loop/
    # Abort) when the verdict is below block_below. "true"/"final" keep the
    # single end-of-run challenger. "off"/false disable.
    challenger_per_level = (str(enable_challenger).lower() == "per_level")
    _VERDICT_RANK = {"WEAK": 0, "ADEQUATE": 1, "STRONG": 2}
    _cg = quality.get("challenger_gate") or {}
    checkpoint_block_below = str(_cg.get("block_below", "ADEQUATE")).upper()
    if checkpoint_block_below not in _VERDICT_RANK:
        checkpoint_block_below = "ADEQUATE"
    # Long-lived pause: a checkpoint's waitForTaskToken waits for a human
    # (Accept/Loop/Abort), so its timeout must cover the operator being away —
    # NOT the ~30min agent backstop. Default 24h; configurable via
    # quality.challenger_gate.pause_seconds; bounded to [60s, SFN max 1y].
    try:
        checkpoint_pause_seconds = int(_cg.get("pause_seconds", 86400))
    except (TypeError, ValueError):
        checkpoint_pause_seconds = 86400
    checkpoint_pause_seconds = max(60, min(checkpoint_pause_seconds, 31536000))
    # Gate fatigue rebalance: advisory (default) = the checkpoint runs and annotates
    # the DAG with its verdict but only PAUSES on a high-confidence failure (a WEAK
    # verdict carrying a BLOCKER); borderline-weak layers proceed automatically.
    # blocking = pause on any sub-threshold verdict (the original behaviour).
    checkpoint_mode = str(_cg.get("mode", "advisory")).lower()
    if checkpoint_mode not in ("advisory", "blocking"):
        checkpoint_mode = "advisory"
    enable_report = config.get("report", True)
    enable_grounding = quality.get("grounding", True) and bool(GROUNDING_CHECK_ARN)
    enable_external_grounding = quality.get("external_grounding", True) and bool(EXTERNAL_GROUNDING_ARN)

    # Phase 5g: quality gate modes — ask (default) / warn / halt per validator.
    VALID_GATE_MODES = {"ask", "warn", "halt"}
    DEFAULT_GATES = {"citation": "ask", "format": "ask", "grounding": "ask", "output_kind": "ask"}
    raw_gates = quality.get("gates") or {}
    gate_config = dict(DEFAULT_GATES)
    for gate_name, mode in raw_gates.items():
        if gate_name not in DEFAULT_GATES:
            compile_to_asl.last_warnings.append({"kind": "unknown_gate", "detail": f"quality.gates.{gate_name} is not a recognized gate. Valid: {sorted(DEFAULT_GATES.keys())}"})
            continue
        if mode not in VALID_GATE_MODES:
            raise ValueError(f"quality.gates.{gate_name} mode {mode!r} invalid. Must be one of: {sorted(VALID_GATE_MODES)}")
        gate_config[gate_name] = mode

    # Resolve memory namespace ACLs from the ecosystem YAML's `memory.shared_namespaces`.
    # Each entry has {name, path, write: [agent_ids], read: [agent_ids | "all"]}.
    # We compute per-agent {writable: [paths], readable: [paths]} and inject into
    # each agent_config as `memory`. Agent reads this in build_tools to enforce ACL
    # at runtime. AgentCore Memory namespaces are rooted at /tessera, so paths from
    # the YAML are prefixed if not already absolute under /tessera.
    #
    # NOTE on `memory.strategies` field: operators may declare e.g. `strategies: [semantic, episodic]`
    # in YAML. The platform manages strategy provisioning at the CDK level (currently a single
    # platform-wide semantic strategy). Per-ecosystem strategy customisation is not yet
    # supported — the YAML field is informational only and is intentionally ignored here.
    # Future work: per-ecosystem CfnMemoryStrategy if customers need isolation.
    memory_cfg = config.get("memory", {}) or {}
    shared_namespaces = memory_cfg.get("shared_namespaces", []) or []
    # Entity-keying (M.1/M.6): load the canonical key pattern for each catalog type
    # from the ecosystem's mounted domain(s), so the runtime can tell each catalog-
    # writing agent the EXACT key shape to emit (e.g. selling-agent -> {name-agency})
    # instead of a generic "primary entity" hint. type -> key pattern.
    domain_key_patterns = {}
    for _dom_id in (config.get("domains") or []):
        try:
            _dd = yaml.safe_load(s3.get_object(Bucket=BUCKET, Key=f"domains/{_dom_id}.yaml")["Body"].read())
            for _t in (_dd.get("taxonomy") or []):
                if _t.get("type") and _t.get("key"):
                    domain_key_patterns.setdefault(_t["type"], _t["key"])
        except Exception as _e:
            print(f"[compile] could not load domain {_dom_id} key patterns: {type(_e).__name__}: {str(_e)[:120]}")
    # Episodic precedent (memory-future-design.html #3): opt-in via memory.episodic: true.
    # Rides on the existing catalog + semantic path — NO separate AgentCore episodic
    # strategy yet (deferred until semantic is validated). The report agent persists a
    # compact EPISODE RECORD to an org-scoped /catalog/episodes/<eco> namespace; future
    # runs read it as precedent (e.g. via memory_search). Cross-run, cross-mission.
    episodic_enabled = bool(memory_cfg.get("episodic") or config.get("episodic"))
    if episodic_enabled:
        shared_namespaces = list(shared_namespaces) + [{
            "name": "episodes",
            "path": f"/catalog/episodes/{eco_id}",
            "write": ["report_agent"],
            "read": ["all"],
        }]
        # Close the read loop so this isn't another write-but-never-read namespace:
        # nudge leaf agents (the ones that set direction) to pull precedent first.
        _ep_ns = f"/tessera/catalog/episodes/{eco_id}"
        _ep_hint = (
            f"\n\nPRECEDENT CHECK: before you begin, call memory_read('{_ep_ns}') to see how prior "
            "analogous runs of this ecosystem went. Treat any results as PRECEDENT (useful "
            "approaches and pitfalls to consider), NOT as answers — re-verify anything you rely "
            "on, since the prior mission likely differed."
        )
        for _a in agents:
            if not (_a.get("depends_on") or []):
                _a["prompt"] = (_a.get("prompt") or "") + _ep_hint
    all_agent_ids = {a["id"] for a in agents}

    def _normalize_namespace(p: str, src_label: str = "") -> str:
        p = (p or "").strip().rstrip("/")
        if not p:
            raise ValueError(
                f"shared_namespaces entry has empty path/name "
                f"(label: {src_label or '<unnamed>'}). Each namespace must declare a non-empty `path`."
            )
        if not p.startswith("/"):
            p = "/" + p
        if not p.startswith("/tessera"):
            p = "/tessera" + p
        return p

    def _norm_acl_list(val):
        """Normalize a YAML write/read field into a list. Accepts scalar 'all',
        list, None — anything else becomes empty list. The Opus builder sometimes
        emits `read: \"all\"` as a scalar; treat it the same as `read: [\"all\"]`.
        """
        if val is None or val == "":
            return []
        if isinstance(val, str):
            return [val]
        if isinstance(val, (list, tuple)):
            return list(val)
        return []

    # Track the original (user-authored) agent ids — checkpoints are inserted
    # only after these layers, never after the injected report/challenger.
    user_agent_ids = {a["id"] for a in agents}

    if enable_report:
        all_ids = {a["id"] for a in agents}
        depended_on = set()
        for a in agents:
            for dep in a.get("depends_on", []):
                depended_on.add(dep)
        leaf_ids = sorted(all_ids - depended_on) or [agents[-1]["id"]]

        if challenger_per_level:
            # Per-level checkpoints replace the single end challenger.
            agents.append(report_agent_config(leaf_ids, episodic=episodic_enabled, eco_id=eco_id))
        elif enable_challenger:
            agents.append(challenger_agent_config(leaf_ids))
            agents.append(report_agent_config(leaf_ids + ["challenger_agent"], episodic=episodic_enabled, eco_id=eco_id))
        else:
            agents.append(report_agent_config(leaf_ids, episodic=episodic_enabled, eco_id=eco_id))

    # Per-agent memory ACL — runs AFTER challenger/report append so they get
    # proper writable/readable lists too (Phase 4.5 fix; was a known bug from
    # Phase 4 where challenger/report shipped with empty memory dicts and every
    # memory_read failed with "agent X is not authorized").
    for agent in agents:
        writable = []
        readable = []
        auto_persist_namespaces = []
        entity_key_patterns = {}
        for ns in shared_namespaces:
            label = ns.get("name") or ns.get("path") or "<unnamed>"
            try:
                path = _normalize_namespace(ns.get("path") or ns.get("name", ""), label)
            except ValueError as e:
                # Fail compilation loudly — silent skip would mask operator misconfiguration
                raise ValueError(
                    f"ecosystem {ecosystem_name!r} memory namespace {label!r}: {e}"
                )
            write_list = _norm_acl_list(ns.get("write"))
            read_list = _norm_acl_list(ns.get("read"))
            auto_persist = bool(ns.get("auto_persist", False))
            if agent["id"] in write_list or "all" in write_list:
                writable.append(path)
                # M.7: only auto-persist to a namespace that is actually READ by
                # someone. Auto-persisting (write + extraction cost) to a namespace
                # with no readers is the run-scoped conclusion-dump anti-pattern —
                # pure waste. read_list empty ⇒ nobody reads it ⇒ skip auto-persist.
                if auto_persist and read_list:
                    # Path may contain {sessionId}, {agentId}, {ecosystemId}, {date}
                    # template placeholders that the agent-invoker substitutes at
                    # runtime via _substitute_template.
                    auto_persist_namespaces.append(path)
                    # Entity-keying: record the domain key pattern for this catalog
                    # type so the runtime instructs THIS agent on the exact key shape.
                    _segs = path.strip("/").split("/")  # /tessera/catalog/<type>
                    if len(_segs) >= 3 and _segs[1] == "catalog" and _segs[2] in domain_key_patterns:
                        entity_key_patterns[_segs[2]] = domain_key_patterns[_segs[2]]
            if "all" in read_list or agent["id"] in read_list:
                readable.append(path)
        agent["memory"] = {
            "writable": writable,
            "readable": readable,
            "auto_persist_namespaces": auto_persist_namespaces,
            "entity_key_patterns": entity_key_patterns,
        }
        # Phase 5b: inject ecosystem ID so agent-invoker can resolve files
        agent["_ecosystem_id"] = eco_id
        # Phase 5g: inject quality gate modes so agent-invoker respects them
        agent["_gate_config"] = gate_config
        # spec-decision-engine.md: thread the eco-level `decision` block onto the SCORER agent
        # (decision.scored_by) so the runtime gives it the scoring contract and agent-invoker
        # evaluates its items. The deliverable agent renders the computed result from context.
        _dcfg = config.get("decision") or (config.get("ecosystem") or {}).get("decision")
        if _dcfg and agent.get("id") == _dcfg.get("scored_by"):
            agent["decision"] = _dcfg

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 4.5: per-agent role_contract metadata.
    #
    # The compiler computes structural facts derived from the depends_on graph and
    # the sibling agent list. The runtime renders these into prompt sections so
    # every agent knows: (a) who else exists in this ecosystem, (b) which upstream
    # it MUST cite, (c) the citation format. Operator-authored persona + domain
    # contribution stay in agent_config.prompt — compiler does NOT mutate that.
    #
    # Citation enforcement is later: agent-invoker rejects synthesizer outputs
    # missing required [per <upstream>: ...] cites.
    # ──────────────────────────────────────────────────────────────────────────
    agents_summary = [
        {"id": a["id"], "name": a.get("name", a["id"])} for a in agents
    ]
    LANE_DISCIPLINE = (
        "Each other agent has its own role defined in its own prompt. Don't "
        "reproduce their analysis — assume they did their job and synthesize on "
        "top of it. Stay in your lane (your YAML prompt)."
    )
    CITATION_FORMAT = "[per <agent_id>: <upstream finding>]"
    NOVEL_MARKER = "[novel — <reason>]"
    # Phase 5h: audience-appropriate provenance. If the ecosystem declares
    # outcome.audience, thread it onto the deliverable agent(s) so the runtime injects
    # the DELIVERABLE_PROVENANCE_CONTRACT (audience-language provenance in the body +
    # a platform-stamped Assurance & Provenance block). Opt-in: unset = no change.
    _outcome = (config.get("ecosystem") or {}).get("outcome") or config.get("outcome") or {}
    _audience = (_outcome.get("audience") or "").strip() if isinstance(_outcome, dict) else ""
    _artifact_agents = {art.get("agent") for art in (_outcome.get("artifacts") or [])
                        if isinstance(art, dict) and art.get("agent")}
    # Structural role-scoping (enforced role isolation). The DAG + outcome already
    # encode who authors the deliverable; derive it here so the runtime can (a) frame
    # every NON-author agent as "produce only your slice — you are NOT the deliverable
    # author" and (b) demote the full mission to context for them. This makes role
    # isolation a platform invariant instead of relying on hand-written prompt guards.
    _deliverable_agents = set(_artifact_agents) | {
        a["id"] for a in agents if a.get("output_kind") in ("reporter", "drafter")
    } | {a["id"] for a in agents if a["id"] == "report_agent"}
    # A single representative author id to name in the scope guard.
    _primary_author = (
        (sorted(_artifact_agents)[0] if _artifact_agents else None)
        or next((a["id"] for a in agents if a.get("output_kind") == "drafter"), None)
        or next((a["id"] for a in agents if a.get("output_kind") == "reporter"), None)
        or ("report_agent" if any(a["id"] == "report_agent" for a in agents) else None)
    )
    for agent in agents:
        deps = agent.get("depends_on", []) or []
        siblings = [s for s in agents_summary if s["id"] != agent["id"]]
        is_author = agent["id"] in _deliverable_agents
        # Concrete, per-agent "do not redo" derived from the sibling roster (structural,
        # not hand-authored). Names each other agent's ownership by id.
        does_not = "; ".join(f"{s['id']} owns {s['name']}" for s in siblings)
        agent["role_contract"] = {
            "role": "synthesizer" if deps else "leaf",
            "is_deliverable_author": is_author,
            "deliverable_author": _primary_author,
            "does_not": does_not,
            "must_cite_upstream": list(deps),
            "cite_threshold": 1,
            "other_agents_in_ecosystem": siblings,
            "lane_discipline": LANE_DISCIPLINE,
            "citation_format": CITATION_FORMAT,
            "novel_marker": NOVEL_MARKER,
        }
        # Soft signal (not a hard reject — the runtime SCOPE guard neutralises it): a
        # NON-author agent whose prompt instructs it to build the final deliverable is
        # structurally mis-scoped. Warn so the author/builder can tighten it; the runtime
        # still injects "you are not the deliverable author" so behaviour is safe either way.
        if not is_author:
            _p = (agent.get("prompt") or "").lower()
            if re.search(r"\b(produce|assemble|generate|create|build|draft|zip|render)\b[^.]{0,80}"
                         r"\b(the\s+)?(full\s+)?(bundle|kit|deck|report|deliverable|artifact|\.zip|zip file)\b", _p):
                compile_to_asl.last_warnings = list(compile_to_asl.last_warnings or []) + [{
                    "level": "warning",
                    "agent": agent["id"],
                    "kind": "non_author_authors_deliverable",
                    "message": (
                        f"agent {agent['id']!r} is not the deliverable author "
                        f"({_primary_author!r} is) but its prompt instructs it to build the "
                        f"deliverable. The runtime injects a SCOPE guard to prevent this, but "
                        f"the prompt should be narrowed to this agent's own slice."
                    ),
                }]
        # Attach audience to the deliverable agent(s) so the runtime renders
        # audience-appropriate provenance. A deliverable agent is one that produces an
        # outcome artifact, or is declared reporter/drafter, or is the auto report_agent.
        if _audience and (
            agent["id"] in _artifact_agents
            or agent.get("output_kind") in ("reporter", "drafter")
            or agent["id"] == "report_agent"
        ):
            agent["audience"] = _audience
        # Human-pause budget for ask-mode gates (citation/format/grounding/output_kind).
        # An ask-gate withholds the task token until an operator decides; the task's
        # waitForTaskToken waits this long before the run ends with GateExpired. Reusing
        # the checkpoint pause (default 24h) makes per-output gates long-lived too, so
        # their async-inbox items no longer auto-expire after the old ~30min backstop.
        # Safe: a crashing Lambda fails via the Lambda error path (retried); this long
        # timeout only governs the *deliberate* human pause, not agent execution.
        agent["_gate_pause_seconds"] = checkpoint_pause_seconds

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 5e: template_id resolution.
    #
    # When an agent declares output_format.template_id, fetch the template body
    # from s3://<eco-bucket>/templates/<id>/template.<ext> at compile time and
    # inline it into the agent's prompt as a "USE THIS TEMPLATE" section.
    # Compile-time injection (not runtime fetch) means:
    #   - The runtime needs no S3 access for templates
    #   - Templates baked into the SFN ASL are immutable for that version of
    #     the ecosystem (predictable behaviour, version-pinned)
    #   - Updating a template requires re-running save-ecosystem to pick up
    #     the new body (forces explicit versioning)
    # ──────────────────────────────────────────────────────────────────────────
    for agent in agents:
        ofmt = agent.get("output_format") or {}
        template_id = ofmt.get("template_id") if isinstance(ofmt, dict) else None
        if not template_id:
            continue
        template_body = _fetch_template_body(template_id)
        if not template_body:
            # Soft-warn instead of failing the compile — operator may be
            # iterating on a template that hasn't been deployed yet.
            compile_to_asl.last_warnings = list(compile_to_asl.last_warnings or []) + [{
                "kind": "template_not_found",
                "agent": agent.get("id"),
                "template_id": template_id,
                "detail": (
                    f"Agent {agent.get('id')!r} references template_id={template_id!r} "
                    f"but no template was found at templates/{template_id}/. "
                    "Run scripts/deploy-platform-templates.sh."
                ),
            }]
            continue
        existing_prompt = agent.get("prompt", "")
        agent["prompt"] = (
            existing_prompt.rstrip()
            + "\n\n"
            + f"--- USE THIS TEMPLATE ({template_id}) ---\n"
            + "Your typed output (per output_format) MUST conform to the structure\n"
            + "below. Replace placeholder values; preserve the structural keys.\n\n"
            + template_body
            + "\n--- END TEMPLATE ---\n"
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 5.1: per-agent tool_budget — hard-cap on each tool's invocation count
    # to prevent runaway tool spirals (e.g. 17 web_searches → MaxTokensReached
    # mid-stream → STRUCTURED FINDINGS lost). Defaults vary by role; operators
    # override in YAML when they need more (research-heavy ecosystems) or less
    # (read-only synthesizers). Runtime enforces; agent prompt sees the budget.
    # ──────────────────────────────────────────────────────────────────────────
    for agent in agents:
        # Role drives default budgets. A DRAFTER (output_kind: drafter) is an
        # artifact builder that needs many code_interpreter calls; it's checked
        # BEFORE synthesizer (drafters also carry depends_on). Otherwise:
        # synthesizer if it has upstream deps, else leaf.
        # Role-tiered defaults + research-tool backstop live in the pure helper
        # _resolve_agent_tool_budget (unit-tested). See RESEARCH_TOOLS note above.
        agent["tool_budget"] = _resolve_agent_tool_budget(agent)

    layers = compute_layers(agents)

    # Concurrency control (proper fan-out throttling): a parallel swarm compiles
    # to a Map with MaxConcurrency, NOT an unbounded Parallel. This caps how many
    # agents hit Bedrock simultaneously WITHOUT encoding concurrency as fake
    # depends_on edges (depends_on stays purely data/citation, so the citation
    # gate is honest). 0 = unlimited (behaviourally equivalent to the old
    # Parallel) for ecosystems that don't set the knob.
    max_concurrency = int(
        (config.get("ecosystem") or {}).get("max_concurrency")
        or config.get("max_concurrency")
        or 0
    )

    # ── Checkpoint placement helpers (computed once, used in the layer loop) ──
    # #1: do NOT checkpoint terminal/meta layers — a reporter is the terminal
    # synthesis (nothing downstream consumes it) and a challenger IS the
    # reviewer (checkpointing it is reviewing a reviewer). These add no value
    # and just create gate fatigue. Detect by output_kind OR id, since an eco
    # may declare a report/challenger as a user agent. (Drafter is intentionally
    # NOT excluded — its deliverable can still warrant a review.)
    def _is_meta_layer_agent(a):
        ok = str(a.get("output_kind") or "").lower()
        if ok in ("reporter", "challenger"):
            return True
        return bool(re.match(r"^(report|challenger)(_agent\b|-agent\b|_|-|$)", str(a.get("id") or "")))

    # #2: only checkpoint layers that have DOWNSTREAM DEPENDENTS. A checkpoint's
    # value is catching a bad premise BEFORE later layers build on it; if a
    # layer's outputs are consumed only by the injected report/challenger (i.e.
    # it's the last pre-report synthesis layer), gating it can't prevent any
    # downstream propagation — it's just gate fatigue. So we compute the set of
    # agents that some NON-meta agent depends on (directly or transitively) and
    # require a layer to contain at least one such agent to earn a checkpoint.
    _deps_map = {a["id"]: list(a.get("depends_on") or []) for a in agents}
    _has_downstream_dependent: set = set()
    for _a in agents:
        if _is_meta_layer_agent(_a):
            continue  # the report/challenger consuming a layer does NOT count
        # Walk this non-meta agent's transitive upstream; every agent reached is
        # depended-on by a non-meta agent, so it has a real downstream dependent.
        _stack = list(_deps_map.get(_a["id"], []))
        _seen: set = set()
        while _stack:
            _d = _stack.pop()
            if _d in _seen:
                continue
            _seen.add(_d)
            _stack.extend(_deps_map.get(_d, []))
        _has_downstream_dependent |= _seen

    # Build a linear chain of state names, then wire them together
    # For each layer i: Layer{i} → MergeLayer{i} → [Grounding_{agent}...] → Layer{i+1}
    chain = []  # list of (state_name, state_def) in execution order
    extra_states = {}  # terminal states (e.g. checkpoint-expiry Fail) added post-wiring

    # spec-run-tuning §3: stamp a model TIER on every agent (leaf / synthesizer /
    # terminal / reviewer / report / drafter), derived from output_kind + the DAG
    # (downstream-dependents set computed above). agentcore resolves the per-run
    # model from this tier + run_config.model_profile / overrides. Computed once
    # here so build_task (incl. checkpoints) carries it into agent_config.
    for _a in agents:
        _oid = str(_a.get("output_kind") or "").lower()
        if _oid == "challenger":
            _a["tier"] = "reviewer"
        elif _oid == "reporter":
            _a["tier"] = "report"
        elif _oid == "drafter":
            _a["tier"] = "drafter"
        elif _oid == "researcher":
            _a["tier"] = "researcher"  # context-aware researcher — leaf-class model
        elif not (_a.get("depends_on") or []):
            _a["tier"] = "leaf"
        elif _a["id"] in _has_downstream_dependent:
            _a["tier"] = "synthesizer"
        else:
            _a["tier"] = "terminal"  # final synthesis whose only consumer is report

    for i, layer in enumerate(layers):
        # 1. The layer state (Task or Parallel)
        layer_name = f"Layer{i}"
        # A checkpoint is inserted after a layer only in per_level mode and only
        # when the layer is entirely user-authored agents (never after the
        # injected report layer). Tag each such agent with its layer index so
        # the agent-invoker can pull prior checkpoint feedback on a loop re-run.
        layer_is_meta = bool(layer) and all(_is_meta_layer_agent(a) for a in layer)
        # A layer earns a checkpoint only if at least one of its agents is
        # depended on (directly/transitively) by a non-meta agent — i.e. it has
        # downstream work to protect. The last pre-report synthesis layer (whose
        # only consumer is the injected report/challenger) is skipped.
        layer_has_downstream = any(a["id"] in _has_downstream_dependent for a in layer)
        is_checkpoint_layer = (
            challenger_per_level and bool(layer)
            and all(a["id"] in user_agent_ids for a in layer)
            and not layer_is_meta
            and layer_has_downstream
        )
        if is_checkpoint_layer:
            for a in layer:
                a["_layer"] = i
        if len(layer) == 1:
            chain.append((layer_name, build_task(layer[0])))
            # Single-task layer: its ResultPath wrote directly into $.results.<id>.
            # The merge step is a pass-through that preserves all top-level state fields.
            chain.append((f"MergeLayer{i}", {
                "Type": "Pass",
                "Parameters": {
                    "mission.$": "$.mission",
                    "session_id.$": "$.session_id",
                    "user_id.$": "$.user_id",
                    "dry_run.$": "$.dry_run",
                    "dependency_context.$": "$.dependency_context",
                    "input_context.$": "$.input_context",
                    "run_config.$": "$.run_config",
                    "results.$": "$.results",
                },
            }))
        else:
            # Throttled fan-out: a Map with MaxConcurrency caps how many agents
            # run against Bedrock at once, expressing "run these in parallel but
            # cap concurrency" with the primitive built for it — NOT by inventing
            # depends_on edges (which would poison the citation contract). One
            # iterator serves every agent in the layer; each agent's static config
            # is carried as item data and read at $.agent_config.
            #
            # Per-agent retry_attempts/pause differences collapse to the layer max
            # (a shared iterator can't hold per-item retry policy); GateRetry (25)
            # and the generic retry mirror build_task exactly.
            iterator_retry_attempts = max(
                int(a.get("retry_attempts") if a.get("retry_attempts") is not None else 1)
                for a in layer
            )
            iterator_pause = max(
                int(a.get("_checkpoint_pause_seconds") or a.get("_gate_pause_seconds") or 1800)
                for a in layer
            )
            iterator_retry = [
                {"ErrorEquals": ["GateRetry"], "IntervalSeconds": 1, "MaxAttempts": 25, "BackoffRate": 1.0},
            ]
            if iterator_retry_attempts > 0:
                iterator_retry.append({
                    "ErrorEquals": [
                        "ValueError", "States.TaskFailed", "Lambda.ServiceException",
                        "Lambda.AWSLambdaException", "Lambda.SdkClientException",
                    ],
                    "IntervalSeconds": 5, "MaxAttempts": iterator_retry_attempts, "BackoffRate": 2.0,
                })
            iterator_task = {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke.waitForTaskToken",
                "Parameters": {
                    "FunctionName": AGENT_INVOKER_ARN,
                    "Payload": {
                        "TaskToken.$": "$$.Task.Token",
                        # agent_config comes from the Map item, not baked statically.
                        "agent_config.$": "$.agent_config",
                        "mission.$": "$.mission",
                        "dependency_context.$": "$.dependency_context",
                        "session_id.$": "$.session_id",
                        "user_id.$": "$.user_id",
                        "dry_run.$": "$.dry_run",
                        "run_config.$": "$.run_config",
                        "results.$": "$.results",
                    },
                },
                # Fixed path (per-item ResultPath isn't possible in a shared
                # iterator); ResultSelector re-keys each item's output by agent id.
                "ResultPath": "$.__agent_out",
                "TimeoutSeconds": iterator_pause,
                "Retry": iterator_retry,
                "End": True,
            }
            # Seed the per-layer item list (each item carries one agent's config).
            # NAMED Layer{i} so /resume and checkpoint-loop targets still land here;
            # it flows into the fan-out Map below.
            chain.append((layer_name, {
                "Type": "Pass",
                "Result": [{"agent_config": agent} for agent in layer],
                "ResultPath": f"$.layer_items{i}",
            }))
            chain.append((f"{layer_name}Fanout", {
                "Type": "Map",
                "ItemsPath": f"$.layer_items{i}",
                "MaxConcurrency": max_concurrency,
                "ItemSelector": {
                    "agent_config.$": "$$.Map.Item.Value.agent_config",
                    "mission.$": "$.mission",
                    "dependency_context.$": "$.dependency_context",
                    "session_id.$": "$.session_id",
                    "user_id.$": "$.user_id",
                    "dry_run.$": "$.dry_run",
                    "run_config.$": "$.run_config",
                    "results.$": "$.results",
                },
                "ItemProcessor": {
                    "ProcessorConfig": {"Mode": "INLINE"},
                    # Inner state name must be unique across the WHOLE machine
                    # (SFN validates ItemProcessor states globally), so key it per layer.
                    "StartAt": f"{layer_name}Agent",
                    "States": {f"{layer_name}Agent": iterator_task},
                },
                # Map returns an array in item order; re-key each output by agent id
                # so $.parallel_out{i} has the same {id: result} shape as before.
                "ResultPath": f"$.parallel_out{i}",
                "ResultSelector": {
                    f"{agent['id']}.$": f"$[{idx}].__agent_out"
                    for idx, agent in enumerate(layer)
                },
            }))
            # Merge the per-layer outputs into the running $.results dict so downstream layers see them.
            chain.append((f"MergeLayer{i}", {
                "Type": "Pass",
                "Parameters": {
                    "mission.$": "$.mission",
                    "session_id.$": "$.session_id",
                    "user_id.$": "$.user_id",
                    "dry_run.$": "$.dry_run",
                    "dependency_context.$": "$.dependency_context",
                    "input_context.$": "$.input_context",
                    "run_config.$": "$.run_config",
                    "results.$": f"States.JsonMerge($.results, $.parallel_out{i}, false)",
                },
            }))

        # 3. Grounding checks for agents in this layer that have dependencies
        if enable_grounding:
            for agent in layer:
                if agent.get("depends_on"):
                    grounding_name = f"Grounding_{agent['id']}"
                    chain.append((grounding_name, {
                        "Type": "Task",
                        "Resource": "arn:aws:states:::lambda:invoke",
                        "Parameters": {
                            "FunctionName": GROUNDING_CHECK_ARN,
                            "Payload": {
                                "agent_id": agent["id"],
                                "results.$": "$.results",
                                "session_id.$": "$.session_id",
                                "mission.$": "$.mission",
                                "dry_run.$": "$.dry_run",
                            },
                        },
                        "ResultPath": f"$.grounding.{agent['id']}",
                        "TimeoutSeconds": 30,
                    }))

        # 4. External (web-search) grounding for every agent in this layer that
        #    produces structured findings — that's all of them, since the agent-invoker
        #    enforces STRUCTURED FINDINGS post-stream. Runs after the internal grounding
        #    so the grounding badge appears first in the UI.
        if enable_external_grounding:
            for agent in layer:
                external_name = f"External_{agent['id']}"
                chain.append((external_name, {
                    "Type": "Task",
                    "Resource": "arn:aws:states:::lambda:invoke",
                    "Parameters": {
                        "FunctionName": EXTERNAL_GROUNDING_ARN,
                        "Payload": {
                            "agent_id": agent["id"],
                            "results.$": "$.results",
                            "session_id.$": "$.session_id",
                            "dry_run.$": "$.dry_run",
                        },
                    },
                    "ResultPath": f"$.external_grounding.{agent['id']}",
                    "TimeoutSeconds": 150,
                }))

        # 5. Per-level checkpoint (challenger:per_level). Reviews this layer's
        #    agents; agent-invoker auto-proceeds if verdict >= block_below, else
        #    opens a human gate (Accept/Loop/Abort). The Choice routes a "loop"
        #    decision back to Layer{i} (re-run with feedback) or proceeds.
        if is_checkpoint_layer:
            cp_cfg = checkpoint_challenger_config(i, [a["id"] for a in layer], checkpoint_block_below)
            cp_cfg["_checkpoint_pause_seconds"] = checkpoint_pause_seconds
            cp_cfg["_checkpoint_mode"] = checkpoint_mode
            # Reviewer tier: by default (no run_config) the model resolves to Opus
            # in agentcore (a checkpoint has depends_on → synthesizer-default), so
            # the strict VERDICT contract stays on the top tier. A run's
            # model_profile / by_type[reviewer] override CAN downshift it (e.g.
            # `balanced` → Sonnet) — that's intentional and per-run only.
            cp_cfg["tier"] = "reviewer"
            cp_task = build_task(cp_cfg)
            cp_task["ResultPath"] = f"$.checkpoint{i}"
            # The checkpoint waits for a human decision — long-lived pause so an
            # away operator doesn't get the run killed by a States.Timeout.
            cp_task["TimeoutSeconds"] = checkpoint_pause_seconds
            # Graceful expiry: if the pause elapses with no decision, end the run
            # with a clean, named GateExpired failure instead of a raw
            # States.Timeout. (The Attention inbox marks the item expired.)
            cp_task.setdefault("Catch", []).append({
                "ErrorEquals": ["States.Timeout"],
                "Next": f"CheckpointExpired{i}",
            })
            extra_states[f"CheckpointExpired{i}"] = {
                "Type": "Fail",
                "Error": "GateExpired",
                "Cause": f"Checkpoint at layer {i} expired: no operator decision (Accept/Loop/Abort) within the pause window.",
            }
            chain.append((f"Checkpoint{i}", cp_task))
            chain.append((f"CheckpointChoice{i}", {
                "Type": "Choice",
                "Choices": [{
                    "Variable": f"$.checkpoint{i}.decision",
                    "StringEquals": "loop",
                    "Next": f"Layer{i}",
                }],
                # Default (proceed) wired to the next chain state below.
            }))

    # Wire Next/End on each state in the chain. Choice states route via
    # Choices/Default (no Next/End); their Default is the next chain state.
    states = {"Start": {
        "Type": "Pass",
        # Default dry_run=false when the execution input omits it, so every
        # downstream `$.dry_run` reference is safe regardless of launch path
        # (normal runs are unaffected; the input's value wins if present).
        "Parameters": {"merged.$": "States.JsonMerge(States.StringToJson('{\"dry_run\":false,\"run_config\":{}}'), $, false)"},
        "OutputPath": "$.merged",
        "Next": "ResumeRouter",
    }}
    # Resume-from-selected-state: a Choice after Start that jumps straight to
    # Layer{N} when the execution input carries `resume_from_index` (set by the
    # /resume endpoint, which pre-seeds $.results with the reused upstream layers'
    # outputs from the prior run). Absent → normal start at Layer0. This lets a
    # user re-run a DAG (failed OR succeeded) from any layer without re-running
    # — or re-paying for — upstream work.
    _resume_choices = [
        {
            "And": [
                {"Variable": "$.resume_from_index", "IsPresent": True},
                {"Variable": "$.resume_from_index", "NumericEquals": _i},
            ],
            "Next": f"Layer{_i}",
        }
        for _i in range(1, len(layers))
    ]
    states["ResumeRouter"] = {
        "Type": "Choice",
        "Choices": _resume_choices,
        "Default": chain[0][0],
    }
    for idx, (name, state) in enumerate(chain):
        nxt = chain[idx + 1][0] if idx + 1 < len(chain) else None
        if state.get("Type") == "Choice":
            if nxt:
                state["Default"] = nxt
        elif nxt:
            state["Next"] = nxt
        else:
            state["End"] = True
        states[name] = state

    # Terminal states (checkpoint-expiry Fail) are added after wiring so the
    # linear Next/End pass doesn't try to give them a successor.
    states.update(extra_states)

    return {"Comment": f"Tessera: {ecosystem_name}", "StartAt": "Start", "States": states}


def build_task(agent):
    # Phase 5.4: SFN per-task timeout is the OUTER backstop. The agent-invoker
    # Lambda enforces a 90s stream-silence heartbeat (see docs/features/
    # stream-silence-heartbeat.md) — that is the primary mechanism for catching
    # stuck agents. This SFN-level timeout only fires if the Lambda itself
    # misbehaves. Default 900s matches the Lambda's max timeout. Operators may
    # override per-agent via YAML `task_timeout: <int seconds>` for unusual
    # cases (e.g. a Code-Interpreter-heavy simulation that legitimately needs
    # more than 15 minutes — would also require increasing the Lambda function timeout
    # timeout, which currently caps at 900s, so values above 900 require
    # platform changes too).
    task_timeout_seconds = int(agent.get("task_timeout") or 900)

    # Phase 5.4: SFN-level Retry policy on agent tasks. Agent-invoker raises
    # ValueError on STRUCTURED FINDINGS / citation contract failures
    # (deliberate loud-fail). Without a retry, a single stochastic Sonnet
    # citation drop stops a 25-minute parallel run because the parallel
    # state stops all sibling branches. With one retry, ~80% of stochastic
    # drops self-correct on the second attempt without masking real bugs
    # (those still fail after the retry).
    #
    # MaxAttempts default is 1 retry (so 2 total invocations max). Operators
    # may override per-agent via YAML `retry_attempts: <int>` — e.g. agents
    # with very stochastic tool patterns may justify 2-3 retries; agents
    # where retries are expensive (heavy code_interpreter sessions) might
    # set 0. Set to 0 to disable retry entirely.
    #
    # ErrorEquals covers ValueError (citation/STRUCTURED FINDINGS contract),
    # States.TaskFailed (catch-all for Lambda task errors), and
    # Lambda.ServiceException / Lambda.AWSLambdaException for transient
    # AWS-side issues. Stuck-agent failures (AgentStreamSilenceError) are
    # NOT retried — if the agent is genuinely stuck, retrying just wastes
    # another 240s of silence-detection budget.
    retry_attempts = int(agent.get("retry_attempts") if agent.get("retry_attempts") is not None else 1)
    retry_block = [
        # Operator LOOP on a per-output gate (citation/format/output_kind on a
        # regular agent) resolves as SendTaskFailure(error="GateRetry"). Catch it
        # FIRST (before the generic States.TaskFailed entry, which would otherwise
        # grab it with only ~1 attempt and kill the run after a couple of loops)
        # with a high attempt budget so the agent RE-RUNS — reading the operator's
        # accumulated guidance — on every Loop. No backoff so loops are immediate.
        # (Checkpoint gates loop via CheckpointChoice; this gives per-output gates
        # the equivalent loop capability.)
        {
            "ErrorEquals": ["GateRetry"],
            "IntervalSeconds": 1,
            "MaxAttempts": 25,
            "BackoffRate": 1.0,
        },
    ]
    if retry_attempts > 0:
        retry_block.append(
            {
                "ErrorEquals": [
                    "ValueError",
                    "States.TaskFailed",
                    "Lambda.ServiceException",
                    "Lambda.AWSLambdaException",
                    "Lambda.SdkClientException",
                ],
                "IntervalSeconds": 5,
                "MaxAttempts": retry_attempts,
                "BackoffRate": 2.0,
            },
        )

    # Phase 5g: waitForTaskToken — agent-invoker calls SendTaskSuccess/Failure
    # instead of returning. Enables ask/warn/halt gate modes (SFN pauses on ask).
    task_def = {
        "Type": "Task",
        "Resource": "arn:aws:states:::lambda:invoke.waitForTaskToken",
        "Parameters": {
            "FunctionName": AGENT_INVOKER_ARN,
            "Payload": {
                "TaskToken.$": "$$.Task.Token",
                "agent_config": agent,
                "mission.$": "$.mission",
                "dependency_context.$": "$.dependency_context",
                "session_id.$": "$.session_id",
                "user_id.$": "$.user_id",
                "dry_run.$": "$.dry_run",
                "run_config.$": "$.run_config",
                "results.$": "$.results",
            },
        },
        "ResultPath": f"$.results.{agent['id']}",
        "TimeoutSeconds": int(agent.get("_checkpoint_pause_seconds") or agent.get("_gate_pause_seconds") or 1800),
    }
    if retry_block:
        # Add GateRetry to ErrorEquals so operator "retry" decision re-enters the state
        retry_block[0]["ErrorEquals"].append("GateRetry")
        task_def["Retry"] = retry_block
    return task_def


def compute_layers(agents):
    layers = []
    placed = set()
    remaining = list(agents)
    while remaining:
        layer = [a for a in remaining if all(dep in placed for dep in a.get("depends_on", []))]
        if not layer:
            layer = remaining
        layers.append(layer)
        for a in layer:
            placed.add(a["id"])
        remaining = [a for a in remaining if a["id"] not in placed]
    return layers


def response(body, status=200):
    return {"statusCode": status, "headers": {"Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*")}, "body": json.dumps(body)}
