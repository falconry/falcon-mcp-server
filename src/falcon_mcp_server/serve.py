"""Serve an instance of Server directly without adding it to a Falcon app."""

import argparse
import sys

import falcon.asgi

try:
    import uvicorn
except ImportError:
    uvicorn = None


def main() -> None:
    if uvicorn is None:
        sys.stderr.write(
            'Serving requires Uvicorn to be installed.\n'
            'Install falcon-mcp-server with the "serve" feature, e.g.:\n\n'
            '\tpip install falcon-mcp-server[serve]\n\n'
        )
        sys.exit(1)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('server', help='server object')
    parser.add_argument(
        '-p',
        '--port',
        type=int,
        default=8000,
        help='serve HTTP on this port (default: %(default)s',
    )
    # NOTE(vytas): --reload does not work with an app instance.
    # parser.add_argument(
    #     '--reload', action='store_true',
    #     help='reload upon source code changes (for development)')

    args = parser.parse_args()

    app = falcon.asgi.App()
    # TODO(vytas): Actually parse the resource attr, and add it to the app.

    uvicorn.run(app=app, port=args.port)


if __name__ == '__main__':
    main()
