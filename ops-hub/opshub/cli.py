"""Command line: ``python -m opshub <command>``.

  serve [--host H] [--port P]   run the dashboard and API (plus the scheduler unless OPSHUB_SCHEDULER=off)
  scheduler                     run only the scheduler (syncs, daily jobs, Telegram replies)
  sync [connector ...]          pull from all (or the named) connectors, then run rules
  rules                         re-run watch rules and send notifications
  job <name>                    run one job now: digest, muse_export, calendar, sheets_report, telegram_poll, backup
  backup [DIR]                  copy the database now (default ./backups, keeps 14)
  connectors                    show each connector's mode and missing settings
  digest                        print the open-items digest
  google-login                  connect a Google account (Sheets, Drive, Gmail, Calendar)
  lightspeed-login              authorize Lightspeed R-Series; prints the account id
  telegram-chats                list chats that messaged the bot (to find TELEGRAM_CHAT_ID)
  sheet-template DIR            write the master-data tabs as CSVs to import into Google Sheets
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
    sub.add_parser("scheduler")
    p_job = sub.add_parser("job")
    p_job.add_argument("name")
    p_login = sub.add_parser("google-login")
    p_login.add_argument("--port", type=int, default=8765)
    p_ls = sub.add_parser("lightspeed-login")
    p_ls.add_argument("--port", type=int, default=8766)
    sub.add_parser("telegram-chats")
    p_bk = sub.add_parser("backup")
    p_bk.add_argument("dir", nargs="?")
    p_tpl = sub.add_parser("sheet-template")
    p_tpl.add_argument("dir")
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
    if args.cmd == "sheet-template":
        sheet_template(args.dir)
        return
    if args.cmd == "telegram-chats":
        telegram_chats()
        return
    db.create_all()
    if args.cmd == "lightspeed-login":
        lightspeed_login(args.port)
        return
    if args.cmd == "init-db":
        print(f"Tables ready in {settings.database_url}")
        return
    if args.cmd == "backup":
        from .services.backup import backup

        print(json.dumps(backup(args.dir), indent=2))
        return
    if args.cmd == "google-login":
        google_login(args.port)
        return
    if args.cmd == "scheduler":
        import threading

        from .services.jobs import run_forever

        print("Scheduler running (Ctrl+C to stop)")
        try:
            run_forever(threading.Event())
        except KeyboardInterrupt:
            pass
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
        elif args.cmd == "job":
            from .services import jobs

            print(json.dumps(jobs.run_job(session, args.name, settings), indent=2, default=str))
        elif args.cmd == "rules":
            print(json.dumps(sync.run_rules(session, settings), indent=2))
        elif args.cmd == "digest":
            print(notify.digest_text(session))


def google_login(port: int) -> None:
    """Installed-app OAuth: open the consent page, catch the redirect on localhost, store the refresh token."""
    import os
    import secrets

    from .connectors import google_auth
    from .services import kv

    cid, secret = os.environ.get("GOOGLE_OAUTH_CLIENT_ID"), os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    if not (cid and secret):
        raise SystemExit("Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET (a Desktop app OAuth client) first.")
    redirect = f"http://127.0.0.1:{port}/"
    state = secrets.token_urlsafe(16)
    got = _catch_redirect(port, google_auth.consent_url(cid, redirect, state))
    if got.get("state") != state or "code" not in got:
        raise SystemExit("Google login failed or was cancelled.")
    tokens = google_auth.exchange_code(cid, secret, got["code"], redirect)
    if not tokens.get("refresh_token"):
        raise SystemExit("Google didn't return a refresh token; remove Ops Hub's access at "
                         "myaccount.google.com/permissions and try again.")
    kv.set(google_auth.KV_REFRESH, tokens["refresh_token"])
    print("Connected. The refresh token is saved in the Ops Hub database.")


def _catch_redirect(port: int, url: str) -> dict[str, str]:
    """Open ``url`` in a browser and wait for the OAuth redirect to http://127.0.0.1:port/."""
    import http.server
    import urllib.parse
    import webbrowser

    got: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            got.update({k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab and go back to the terminal.")

        def log_message(self, *a):
            pass

    print("Open this link and approve access:\n\n" + url + "\n")
    webbrowser.open(url)
    try:
        server = http.server.HTTPServer(("127.0.0.1", port), Handler)
        server.timeout = 300
        server.handle_request()
    except OSError:
        pass
    if "code" not in got:
        pasted = input("If the browser couldn't reach this machine, paste the full address it ended on: ").strip()
        got.update({k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query).items()})
    return got


def lightspeed_login(port: int) -> None:
    import os
    import secrets
    from urllib.parse import urlencode

    import httpx

    from .connectors.lightspeed import KV_REFRESH, TOKEN_URL
    from .services import kv

    cid, secret = os.environ.get("LIGHTSPEED_CLIENT_ID"), os.environ.get("LIGHTSPEED_CLIENT_SECRET")
    if not (cid and secret):
        raise SystemExit("Set LIGHTSPEED_CLIENT_ID and LIGHTSPEED_CLIENT_SECRET (from your Lightspeed API client) first.")
    state = secrets.token_urlsafe(16)
    authorize = os.environ.get("LIGHTSPEED_AUTHORIZE_URL", "https://cloud.lightspeedapp.com/auth/oauth/authorize")
    url = authorize + "?" + urlencode({"response_type": "code", "client_id": cid, "state": state,
                                       "scope": os.environ.get("LIGHTSPEED_SCOPE", "employee:all")})
    got = _catch_redirect(port, url)
    if got.get("state") not in (state, None) or "code" not in got:
        raise SystemExit("Lightspeed authorization failed or was cancelled.")
    with httpx.Client(timeout=30) as client:
        resp = client.post(os.environ.get("LIGHTSPEED_TOKEN_URL", TOKEN_URL), data={
            "client_id": cid, "client_secret": secret, "code": got["code"], "grant_type": "authorization_code"})
        resp.raise_for_status()
        tokens = resp.json()
        kv.set(KV_REFRESH, tokens["refresh_token"])
        acct = client.get("https://api.lightspeedapp.com/API/V3/Account.json",
                          headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()
    account = (acct.get("Account") or {})
    account = account[0] if isinstance(account, list) else account
    print(f"Connected. Refresh token saved. Set LIGHTSPEED_ACCOUNT_ID={account.get('accountID', '?')} "
          f"({account.get('name', '')}).")


def telegram_chats() -> None:
    import os

    import httpx

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN first, then send the bot (or the group it's in) a message.")
    body = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30).json()
    chats = {}
    for up in body.get("result", []):
        chat = (up.get("message") or up.get("edited_message") or up.get("my_chat_member") or {}).get("chat") or {}
        if chat.get("id") is not None:
            chats[chat["id"]] = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
    if not chats:
        print("No messages yet. Send the bot a message (in a group: mention it or use /start), then run this again.")
    for cid, name in chats.items():
        print(f"{cid}\t{name}")


def sheet_template(directory: str) -> None:
    import csv
    from pathlib import Path

    from .demo import fixtures
    from .timeutil import utcnow

    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    for tab, rows in fixtures.sheets_master(utcnow()).items():
        with open(out / f"{tab}.csv", "w", newline="") as f:
            csv.writer(f).writerows(rows)
        print(f"wrote {out / (tab + '.csv')}  ({len(rows) - 1} sample rows)")
    print("Import each CSV as a tab of one Google Sheet (tab names must match), then replace the sample rows.")


if __name__ == "__main__":
    main()
