"""Things only the Mac running the hub can do: send iMessages/SMS through Messages.app and run Shortcuts.

Both shell out to built-in macOS tools (`osascript`, `shortcuts`). The hub must run as a LaunchAgent in a
logged-in user session (see mac/setup.sh) so macOS can show the one-time "allow Family Hub to control
Messages / run Shortcuts" permission prompts.
"""
import platform
import subprocess
import tempfile
from pathlib import Path

SEND_SCRIPT = """
on run argv
    set recipient to item 1 of argv
    set message to item 2 of argv
    set wanted to item 3 of argv
    tell application "Messages"
        if wanted is "sms" then
            set svc to 1st account whose service type = SMS
        else
            set svc to 1st account whose service type = iMessage
        end if
        send message to participant recipient of svc
    end tell
end run
"""


class MacOnlyError(RuntimeError):
    pass


def is_mac() -> bool:
    return platform.system() == "Darwin"


def _require_mac(feature: str) -> None:
    if not is_mac():
        raise MacOnlyError(f"{feature} only works when Family Hub runs on a Mac")


def run(cmd: list[str], timeout: float = 30) -> str:
    """Run a command and return stdout; raises with stderr on failure. Tests replace this function."""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"{cmd[0]} failed").strip()[:300])
    return proc.stdout


def send_message(recipient: str, text: str, service: str = "auto") -> str:
    """Send via iMessage; with service "auto", fall back to SMS (needs an iPhone with Text Message Forwarding).

    Returns the service that worked.
    """
    _require_mac("Sending iMessages")
    order = {"imessage": ["imessage"], "sms": ["sms"]}.get(service, ["imessage", "sms"])
    last_error = None
    for svc in order:
        try:
            run(["osascript", "-e", SEND_SCRIPT, recipient, text, svc])
            return svc
        except RuntimeError as exc:
            last_error = exc
    raise RuntimeError(f"Messages couldn't send to {recipient}: {last_error}")


def run_shortcut(name: str, timeout: float = 60) -> str:
    """Run a Shortcuts shortcut by name and return its text output (if any)."""
    _require_mac("Running Shortcuts")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.txt"
        run(["shortcuts", "run", name, "--output-path", str(out), "--output-type", "public.plain-text"], timeout)
        return out.read_text(errors="ignore").strip() if out.exists() else ""


def list_shortcuts() -> list[str]:
    _require_mac("Shortcuts")
    return [line.strip() for line in run(["shortcuts", "list"]).splitlines() if line.strip()]
