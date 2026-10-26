"""Terminal helper for the running app, through its local API (so it sees exactly what the dashboard sees).

    python -m scripts.trader_cli status | mode | paper | stop

`paper` only ever switches Off -> Paper. Live stays a button you press on the dashboard yourself.
"""
from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from app import config


def call(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    req = urllib.request.Request(f"http://127.0.0.1:{config.APP_PORT}/api{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    if config.APP_PASSWORD:
        token = base64.b64encode(f"kalshi:{config.APP_PASSWORD}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never route localhost via a proxy
    with opener.open(req, timeout=15) as r:
        return json.load(r)


def status_text(d: Dict[str, Any]) -> str:
    e = d["equity"]
    total, today = e["equity"] - e["bankroll"], e["equity"] - d["day_start"]
    lines = [f"AI trader: {d['mode'].upper()}" + (f"  STOPPED ({d['kill_reason']})" if d["killed"] else ""),
             f"  equity ${e['equity']:,.2f}  total {total:+,.2f}  today {today:+,.2f}  "
             f"in positions ${e['open_cost']:,.2f}  resting ${e['resting_cost']:,.2f}",
             f"  last cycle {d.get('last_cycle_hst') or 'not yet (it runs every 2 minutes)'}",
             "Go-live test: " + ("PASSED" if d["gate"]["passed"] else "not yet")]
    lines += [f"  [{'x' if c['ok'] else ' '}] {c['label']}: {c['detail']}" for c in d["gate"]["checks"]]
    if d["live_blockers"]:
        lines.append("Live is locked until: " + "; ".join(d["live_blockers"]))
    return "\n".join(lines)


def main(argv) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    try:
        if cmd == "status":
            print(status_text(call("GET", "/auto/status")))
        elif cmd == "mode":
            print(call("GET", "/auto/status")["mode"])
        elif cmd == "paper":
            if call("GET", "/auto/status")["mode"] == "off":
                call("POST", "/auto/mode", {"mode": "paper"})
            print("AI trader: " + call("GET", "/auto/status")["mode"].upper())
        elif cmd == "stop":
            r = call("POST", "/auto/kill", {"reason": "stopped from Terminal"})
            print(f"AI trader stopped; {r['cancelled']} resting orders cancelled. Resume on the dashboard.")
        else:
            print(__doc__)
            return 2
    except urllib.error.HTTPError as e:
        print(f"Kalshi Edge refused the request ({e.code}): {e.read().decode(errors='replace')[:300]}")
        return 1
    except (urllib.error.URLError, OSError) as e:
        print(f"Kalshi Edge isn't answering on port {config.APP_PORT}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
