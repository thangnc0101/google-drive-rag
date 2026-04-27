from __future__ import annotations

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(prog="googledriverag")
    sub = parser.add_subparsers(dest="command")

    serve_cmd = sub.add_parser("serve", help="Start the API server")
    serve_cmd.add_argument("--config", default="config.yaml")
    serve_cmd.add_argument("--host", default=None)
    serve_cmd.add_argument("--port", type=int, default=None)

    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn

        import os

        from googledriverag.app import create_app
        from googledriverag.config import load_config
        from googledriverag.logging_config import setup_logging

        config = load_config(args.config)

        setup_logging(level=os.environ.get("LOG_LEVEL", config.server.log_level))
        if args.host:
            config.server.host = args.host
        if args.port:
            config.server.port = args.port

        app = create_app(config)
        uvicorn.run(app, host=config.server.host, port=config.server.port)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
