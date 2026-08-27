"""host.python.gates package — re-export compat for _harness migration (T5).

Old gates used plain harness import with ``gates/`` on sys.path.
New canonical is ``from host.python.gates._harness import gate_harness``.
This re-export keeps ``from host.python.gates import gate_harness`` working
and documents the 1-version compat window for the legacy plain import.
"""

from ._harness import gate_harness, parametrized_gate  # re-export compat  # noqa: F401

__all__ = ["gate_harness", "parametrized_gate"]
