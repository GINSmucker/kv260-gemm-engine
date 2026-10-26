"""Read-only SHA256 validation for the three source snapshots; no board access."""
import hashlib
import json
from pathlib import Path, PurePosixPath


def main():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'docs/versions/source_manifest.json').read_text(encoding='utf-8'))
    checked = 0
    for name, expected in manifest['files'].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name:
            raise ValueError('unsafe manifest path: ' + name)
        path = root.joinpath(*relative.parts)
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise ValueError('not a regular source file: ' + name)
        path.resolve().relative_to(root.resolve())
        raw = path.read_bytes()
        if len(raw) != expected['bytes'] or hashlib.sha256(raw).hexdigest() != expected['sha256']:
            raise ValueError('source identity differs: ' + name)
        checked += 1
    print(json.dumps({'status': 'PASS', 'files_checked': checked,
                      'scope': 'Source snapshot identity only; not HLS, board, performance or accuracy acceptance',
                      'hardware_access': False, 'writes': False}, indent=2))


if __name__ == '__main__':
    main()
