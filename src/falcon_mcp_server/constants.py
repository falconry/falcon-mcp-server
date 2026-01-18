# SPDX-FileCopyrightText: 2026-present Vytautas Liuolia <vytautas.liuolia@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Common constants for the MCP protocol."""

CONTENT_EVENT_STREAM = 'text/event-stream'
"""Content type for SSE streams (``'text/event-stream'``)."""

MEDIA_TOON = 'text/toon'
"""
Media type for the TOON serialization format.
See also: https://github.com/toon-format/toon#media-type--file-extension.
"""

SUPPORTED_MCP_PROTOCOL_VERSIONS = frozenset({'2025-06-18', '2025-11-25'})
"""MCP protocol versions that ``falcon-mcp-server`` recognizes."""

PROJECT_URL = 'https://github.com/falconry/falcon-mcp-server'
"""GitHub URL of this project."""
