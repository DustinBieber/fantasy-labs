"""Attachment storage on the hub's own disk (optionally an iCloud Drive folder), served via signed links."""
import re
import secrets
from pathlib import Path
from urllib.parse import quote

from ..config import settings
from ..crypto import sign, verify_signature


def base_dir() -> Path:
    path = Path(settings.files_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w.\- ]+", "_", Path(name).name).strip(" .") or "file"
    return name[:120]


def save(kind: str, obj_id: int, name: str, content: bytes) -> dict:
    rel = f"{kind}/{obj_id}/{secrets.token_hex(4)}-{_safe_name(name)}"
    target = base_dir() / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return {"name": name, "url": f"/api/files/{quote(rel)}?sig={sign(rel)}", "local": rel}


def resolve(rel: str, sig: str) -> Path | None:
    if not verify_signature(rel, sig):
        return None
    root = base_dir()
    path = (root / rel).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path
