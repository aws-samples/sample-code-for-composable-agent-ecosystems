"""Unit tests for the deterministic risk clustering (pure, no network)."""
import importlib.util, os

# The clustering logic that lived in scripts/risk_cluster.py was migrated into
# tessera_platform.catalog.synth_risk (same `cluster()` entrypoint).
_HERE = os.path.dirname(__file__)
_OLD_MOD = os.path.abspath(os.path.join(_HERE, "..", "..", "scripts", "risk_cluster.py"))


def _mod():
    if os.path.exists(_OLD_MOD):
        spec = importlib.util.spec_from_file_location("risk_cluster", _OLD_MOD)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    from tessera_platform.catalog import synth_risk
    return synth_risk


def _fixture():
    techniques = [
        {"technique_id": "T1", "name": "A", "tactics": ["credential-access"], "mitigations": ["M1", "M2"]},
        {"technique_id": "T2", "name": "B", "tactics": ["credential-access"], "mitigations": ["M2"]},
        {"technique_id": "T3", "name": "C", "tactics": ["credential-access"], "mitigations": []},
        {"technique_id": "T4", "name": "D", "tactics": ["execution"], "mitigations": ["M3"]},
    ]
    controls = [{"control_id": "M1", "name": "c1"}, {"control_id": "M2", "name": "c2"},
                {"control_id": "M3", "name": "c3"}]
    vulns = [{"cve_or_ref": "CVE-1", "known_ransomware": True, "name": "v1", "product": "p"},
             {"cve_or_ref": "CVE-2", "known_ransomware": False}]
    return techniques, controls, vulns


def test_cluster_groups_by_tactic_and_gathers_controls():
    m = _mod()
    cs = m.cluster(*_fixture())
    by_id = {c["cluster_id"]: c for c in cs}
    assert set(by_id) == {"cluster-credential-access", "cluster-execution"}
    ca = by_id["cluster-credential-access"]
    assert set(ca["technique_ids"]) == {"T1", "T2", "T3"}
    assert ca["control_ids"] == ["M1", "M2"]           # union of the techniques' mitigations
    assert cs[0]["cluster_id"] == "cluster-credential-access"  # largest first


def test_cluster_prefers_mitigated_techniques_and_caps():
    m = _mod()
    techniques = [{"technique_id": f"T{i}", "tactics": ["execution"],
                   "mitigations": (["M1"] if i < 3 else [])} for i in range(10)]
    cs = m.cluster(techniques, [{"control_id": "M1", "name": "c"}], [], per_cluster=4)
    ex = cs[0]
    assert ex["size"] == 4
    # the three mitigated techniques must be retained (preferred) within the cap
    assert {"T0", "T1", "T2"}.issubset(set(ex["technique_ids"]))


def test_cluster_attaches_exploited_ransomware_context_only():
    m = _mod()
    cs = m.cluster(*_fixture())
    ctx = cs[0]["exploited_context"]
    assert [c["cve"] for c in ctx] == ["CVE-1"]        # ransomware only, non-ransomware excluded
