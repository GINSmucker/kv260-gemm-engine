"""UTF-8 evidence and content identities shared by offline and ARM tools."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")

def utc():
    return datetime.now(timezone.utc).isoformat()

def safe_path(root, name):
    root = Path(root).resolve()
    p = Path(name)
    if p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name:
        raise ValueError("Unsafe relative payload path")
    resolved = (root / p).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Payload escapes bundle")
    return resolved
