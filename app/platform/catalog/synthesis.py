"""tessera_platform.catalog.synthesis — the OPTIONAL synthesis plugin contract.

Most domains need no synthesis (advice: `synthesis: none`). Domains that derive records
(risk: cluster ATT&CK tactics -> risk slots) declare `synthesis: {plugin: <module>}` and
the plugin implements this protocol. The generic orchestrator calls it; if a domain has
no synthesis, orchestration simply skips it — risk-shaped clustering is never forced on
a domain that doesn't need it.
"""
import importlib


class Synthesizer:
    """Protocol. A concrete synthesizer (e.g. the risk `synth_plan` module wrapped here)
    turns catalog inputs into clusters/slots for the LLM synthesis Map step."""

    def plan(self, store, domain, event):
        """Return {clusters, slots_total, slots_to_synthesize, to_run, skipped}."""
        raise NotImplementedError


class NoopSynthesizer(Synthesizer):
    def plan(self, store, domain, event):
        return {"clusters": 0, "slots_total": 0, "slots_to_synthesize": 0, "to_run": [], "skipped": []}


class ModuleSynthesizer(Synthesizer):
    """Adapts a module (like the risk synth_plan) that exposes a `plan_synthesis(store,
    domain, event)` function into the Synthesizer protocol."""

    def __init__(self, module):
        self.module = module

    def plan(self, store, domain, event):
        fn = getattr(self.module, "plan_synthesis", None)
        if fn is None:
            raise AttributeError(f"synthesis plugin {self.module!r} has no plan_synthesis()")
        return fn(store, domain, event)


def load_synthesizer(synthesis_spec):
    """synthesis_spec: 'none' | {plugin: 'dotted.module'} -> Synthesizer instance."""
    if not synthesis_spec or synthesis_spec == "none":
        return NoopSynthesizer()
    if isinstance(synthesis_spec, dict) and synthesis_spec.get("plugin"):
        mod = importlib.import_module(synthesis_spec["plugin"])
        return ModuleSynthesizer(mod)
    return NoopSynthesizer()
