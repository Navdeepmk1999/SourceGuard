"""LangSmith tracing for the RAG pipeline.

IMPORTANT - why this module exists rather than just setting
`LANGCHAIN_TRACING_V2=true`: that environment variable enables LangSmith's
*auto-instrumentation of LangChain/LangGraph runnables*, and this codebase
has none. Generation streams straight from `httpx` to Groq's
OpenAI-compatible endpoint (`app/services/generation.py`), retrieval is
hand-written SQL (`app/services/retriever.py`), and verification is a
dependency-free heuristic (`app/services/nli_verifier.py`). The single
`langchain` import in the whole backend is
`langchain_text_splitters.RecursiveCharacterTextSplitter`, a pure string
splitter that never calls a model. Setting the env var alone would emit
zero traces.

So the pipeline is instrumented explicitly with the `langsmith` SDK's
`@traceable` decorator, which is the documented path for tracing
non-LangChain code. `LANGCHAIN_TRACING_V2` / `LANGSMITH_TRACING` are still
honored as the on/off switch (see `Settings.tracing_enabled`) so the
standard environment variables behave as an operator expects.
"""

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from app.core.config import get_settings

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def _noop_decorator(func: F) -> F:
    return func


def traced(name: str, run_type: str = "chain") -> Callable[[F], F]:
    """Wraps a pipeline stage in a LangSmith span when tracing is configured,
    and is a zero-overhead passthrough otherwise.

    Deliberately resolves `tracing_enabled` at decoration time (import time),
    not per call: `@traceable` wraps the function once, and re-deciding per
    invocation would mean paying the import and wrapper cost on the request
    path for every query even when tracing is off.
    """
    settings = get_settings()
    if not settings.tracing_enabled:
        return _noop_decorator

    try:
        from langsmith import traceable
    except ImportError:  # pragma: no cover - langsmith is in requirements.txt
        logger.warning("LangSmith tracing enabled but `langsmith` is not installed.")
        return _noop_decorator

    return traceable(name=name, run_type=run_type, project_name=settings.langsmith_project)
