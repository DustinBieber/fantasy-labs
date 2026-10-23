"""Command line: ``python -m opshub <command>``.

  serve [--host H] [--port P]   run the dashboard and API
  sync [connector ...]          pull from all (or the named) connectors, then run rules
  rules                         re-run watch rules and send notifications
  connectors                    show each connector's mode and missing settings
  digest                        print the open-items digest
  init-db                       create database tables
"""
from __future__ import annotations

import argparse
import json
import logging

from . import db
from .config import get_settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="opshub")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_sync = sub.add_parser("sync")
    p_sync.add_argument("connectors", nargs="*")
    sub.add_parser("rules")
    sub.add_parser("connectors")
    sub.add_parser("digest")
    sub.add_parser("init-db")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    settings = get_settings()
    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("opshub.api:app", host=args.host, port=args.port)
        return
    db.create_all()
    if args.cmd == "init-db":
        print(f"Tables ready in {settings.database_url}")
        return
    if args.cmd == "connectors":
        from .connectors import all_connectors

        for c in all_connectors():
            d = c.describe(settings.demo_mode)
            missing = f"  missing: {', '.join(d['missing_settings'])}" if d["missing_settings"] and d["mode"] != "live" else ""
            print(f"{d['key']:<16} {d['mode']:<8} {d['name']}{missing}")
        return

    from .services import notify, sync

    with db.session_scope() as session:
        if args.cmd == "sync":
            if args.connectors:
                runs = [sync.run_connector(session, k, settings) for k in args.connectors]
                result = {"runs": [{"connector": r.connector, "status": r.status, "stats": r.stats, "error": r.error}
                                   for r in runs], **sync.run_rules(session, settings)}
            else:
                result = sync.sync_all(session, settings)
            print(json.dumps(result, indent=2, default=str))
        elif args.cmd == "rules":
            print(json.dumps(sync.run_rules(session, settings), indent=2))
        elif args.cmd == "digest":
            print(notify.digest_text(session))


if __name__ == "__main__":
    main()
