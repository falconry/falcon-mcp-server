# SPDX-FileCopyrightText: 2026-present Vytautas Liuolia <vytautas.liuolia@gmail.com>
#
# SPDX-License-Identifier: Apache-2.0

"""MCP pagination."""

from collections.abc import Sequence
from typing import Any, TypeVar

from falcon_mcp_server import errors

T = TypeVar('T')


def paginate(
    items: Sequence[T], cursor: Any, prefix: str, page_size: int
) -> tuple[Sequence[T], str | None]:
    """Return the page of items starting at cursor, and the next cursor."""
    start = 0

    if cursor is not None:
        # NOTE(vytas): MCP Pagination, 7. Error Handling.
        #   Invalid cursors SHOULD result in an error with code -32602
        #   (Invalid params).
        head, _, offset = str(cursor).partition(prefix)
        if (
            head
            or not offset.isdecimal()
            or (start := int(offset)) >= len(items)
        ):
            raise errors.RPCInvalidParam('Invalid cursor')

    end = start + page_size
    next_cursor = f'{prefix}{end:04d}' if end < len(items) else None
    return items[start:end], next_cursor
