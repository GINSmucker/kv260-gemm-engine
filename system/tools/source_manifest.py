"""Write an explicit development source overlay without changing the handoff lock."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / 'system/PACKAGE_MANIFEST.json'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    output = a.output.resolve()
    original = json.loads(LOCK.read_text(encoding='utf-8'))['files']
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT).decode().split('\0')
    entries = {}
    for name in sorted(set(filter(None, names))):
        path = (ROOT / name).resolve()
        if path in (output, ROOT / 'system/DEVELOPMENT_SOURCE.lock.json'):
            continue
        if not path.is_relative_to(ROOT) or path.is_symlink() or not path.is_file():
            raise ValueError('Non-file or escaping source: ' + name)
        data = path.read_bytes()
        item = {'bytes': len(data), 'sha256': sha(data)}
        if item != original.get(name):
            entries[name] = item
    value = {'schema': 'development_source_overlay_v1',
             'base_revision': 'eba1a78ece0fe4eea7931c5a3458845cb6842bfd',
             'base_manifest_sha256': sha(LOCK.read_bytes()), 'files': entries}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'overlay_files': len(entries), 'sha256': sha(output.read_bytes()), 'output': str(output)}))


if __name__ == '__main__':
    main()
