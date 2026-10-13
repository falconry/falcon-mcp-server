"""Introspection of tool and prompt callables."""

import inspect
import sys
from collections.abc import Callable
from typing import Any

from falcon_mcp_server.context import Context

if sys.version_info >= (3, 14):
    import annotationlib


def get_signature(func: Callable[..., Any]) -> inspect.Signature:
    """Return the signature of func, tolerating unresolvable annotations."""
    try:
        return inspect.signature(func, eval_str=True)
    except Exception:
        # NOTE(vytas): An annotation may refer to a name that only exists for
        #   type checkers; settle for the annotations as they are written.
        if sys.version_info >= (3, 14):
            return inspect.signature(
                func, annotation_format=annotationlib.Format.FORWARDREF
            )
        return inspect.signature(func)


def find_context_param(func: Callable[..., Any]) -> str | None:
    """Return the name of the parameter of func annotated with Context."""
    try:
        params = get_signature(func).parameters.values()
    except (TypeError, ValueError):
        return None

    for param in params:
        if param.annotation is Context:
            return param.name

        # NOTE(vytas): An unresolved annotation is a string, or a forward
        #   reference on Python 3.14+.
        written = getattr(
            param.annotation, '__forward_arg__', param.annotation
        )
        if (
            isinstance(written, str)
            and written.rpartition('.')[2] == 'Context'
        ):
            return param.name

    return None
