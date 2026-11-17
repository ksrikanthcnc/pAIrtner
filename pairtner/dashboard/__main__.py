"""``python -m pairtner.dashboard`` — start the admin dashboard."""
from __future__ import annotations

import argparse
import dataclasses
import logging
import sys

from pairtner.dashboard.app import create_server
from pairtner.dashboard.config import DashboardConfigError, load_dashboard_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pairtner.dashboard")
    parser.add_argument("--host", help="bind address (overrides settings/env)")
    parser.add_argument("--port", type=int, help="port (overrides settings/env)")
    parser.add_argument("--db", help="SQLite path (overrides DB_PATH)")
    parser.add_argument("--settings", default="settings.yaml")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    try:
        config = load_dashboard_config(args.settings)
    except DashboardConfigError as exc:
        print(f"dashboard: {exc}", file=sys.stderr)
        return 2
    overrides = {
        k: v for k, v in (("host", args.host), ("port", args.port), ("db_path", args.db))
        if v is not None
    }
    config = dataclasses.replace(config, **overrides)

    server = create_server(config)
    host, port = server.server_address[:2]
    logging.getLogger(__name__).info(
        "dashboard listening on http://%s:%s (db=%s, raw_text=%s, admin_actions=%s)",
        host, port, config.db_path, config.show_raw_text, config.admin_actions_enabled,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
