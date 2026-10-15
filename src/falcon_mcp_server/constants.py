# SPDX-FileCopyrightText: 2026-present Vytautas Liuolia <vytautas.liuolia@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Common constants for the MCP protocol."""

CONTENT_EVENT_STREAM = 'text/event-stream'
"""Content type for SSE streams (``'text/event-stream'``)."""

SUPPORTED_MCP_PROTOCOL_VERSIONS = frozenset({'2026-07-28'})
"""MCP protocol versions that ``falcon-mcp-server`` recognizes."""

META_PROTOCOL_VERSION = 'io.modelcontextprotocol/protocolVersion'
"""Request ``_meta`` key carrying the MCP protocol version."""

META_CLIENT_CAPABILITIES = 'io.modelcontextprotocol/clientCapabilities'
"""Request ``_meta`` key carrying the client capabilities."""

META_SERVER_INFO = 'io.modelcontextprotocol/serverInfo'
"""Result ``_meta`` key identifying the server software."""

WELL_KNOWN_RESOURCE_METADATA = '/.well-known/oauth-protected-resource'
"""
Path that the path of an end point is appended to, to get the location of its
OAuth 2.0 protected resource metadata.
See also: https://datatracker.ietf.org/doc/html/rfc9728#section-3.1.
"""

PROJECT_URL = 'https://github.com/falconry/falcon-mcp-server'
"""GitHub URL of this project."""
