"""The PredictRLM agent runtime and its own skills."""

from __future__ import annotations

from ..._optional import import_optional

try:
    from . import skills
    from .runtime import PredictRLM
except ModuleNotFoundError as exc:
    if exc.name == "predict_rlm":
        import_optional("predict_rlm", extra="agents", feature="PredictRLM agent")
    raise

__all__ = ["PredictRLM", "skills"]
