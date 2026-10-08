"""Explicit authenticated Release download, integrity verification and safe extraction."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('asset', choices=('board-runtime', 'ip-rebuild', 'model', 'f05-source', 'f05-runtime', 'v5-source', 'v5-runtime', 'opt38-platform'))
    p.add_argument('--download', action='store_true')
    p.add_argument('--archive', type=Path)
    p.add_argument('--extract', action='store_true')
    a = p.parse_args()
    lock = json.loads((ROOT / 'ASSETS.lock.json').read_text())
    spec = lock['assets'][a.asset]
    if not a.download and not a.archive:
        print(json.dumps(spec, indent=2))
        return
    if a.download and a.archive:
        p.error('Use --download or --archive, not both')
    if a.download:
        directory = ROOT / 'assets/downloads' / str(time.time_ns())
        directory.mkdir(parents=True)
        subprocess.run(['gh', 'release', 'download', spec['release_tag'], '--repo', lock['repository'], '--pattern', spec['filename'], '--dir', str(directory)], check=True)
        archive = directory / spec['filename']
    else:
        archive = a.archive.resolve()
    digest = hashlib.sha256()
    with archive.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    if archive.stat().st_size != spec['bytes'] or digest.hexdigest() != spec['sha256']:
        raise ValueError('Archive size/SHA mismatch')
    result = {'status': 'PASS_ARCHIVE_SHA256', 'asset': a.asset, 'sha256': digest.hexdigest()}
    if a.extract:
        destination = (ROOT / 'assets' / spec['destination']).resolve()
        if not destination.is_relative_to(ROOT / 'assets') or destination.exists():
            raise FileExistsError('Extraction requires a fresh destination under system/assets')
        with tarfile.open(archive, 'r:gz') as t:
            seen = set()
            for member in t.getmembers():
                path = PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or ':' in member.name or '\\' in member.name or not (member.isfile() or member.isdir()):
                    raise ValueError('Unsafe archive member')
                if member.name.casefold() in seen:
                    raise ValueError('Duplicate archive member')
                seen.add(member.name.casefold())
            destination.mkdir(parents=True)
            t.extractall(destination, filter='data')
        result.update(destination=str(destination), members=len(seen))
        if a.asset == 'model':
            import sys
            sys.path.insert(0, str(ROOT))
            from model.b.runtime import verify_bundle
            identity = verify_bundle(destination / 'B_MODEL_BUNDLE_V1')
            if identity['manifest_sha256'] != json.loads((ROOT / 'BASELINE_LOCK.json').read_text())['B_manifest_sha256']:
                raise ValueError('Wrong model manifest')
            result['model_manifest'] = identity
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
