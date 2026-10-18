"""Explicit authenticated Release download, integrity verification and safe extraction."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[2]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('asset', choices=tuple(json.loads((ROOT / 'docs/locks/ASSETS.lock.json').read_bytes())['assets']))
    p.add_argument('--download', action='store_true')
    p.add_argument('--archive', type=Path)
    p.add_argument('--extract', action='store_true')
    a = p.parse_args()
    lock = json.loads((ROOT / 'docs/locks/ASSETS.lock.json').read_text())
    spec = lock['assets'][a.asset]
    if not a.download and not a.archive:
        print(json.dumps(spec, indent=2))
        return
    if a.download and a.archive:
        p.error('Use --download or --archive, not both')
    if a.download:
        directory = ROOT / 'build/assets/downloads' / str(time.time_ns())
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
        if a.asset == 'ps-runtime':
            base = ROOT / 'build/assets/opt45-runtime/opt45-runtime/AB_SHARED_OPT45_X128_Y768_LAYOUT4_FIXED_20261010'
            base_spec = lock['assets']['opt45-runtime']
            base_manifest = base / 'manifest.json'
            if not base_manifest.is_file() or hashlib.sha256(base_manifest.read_bytes()).hexdigest() != base_spec['source_manifest_sha256']:
                raise FileNotFoundError('Extract and verify opt45-runtime before ps-runtime')
            for name, item in json.loads(base_manifest.read_bytes())['files'].items():
                file = (base / name).resolve()
                if not file.is_relative_to(base) or file.stat().st_size != item['bytes'] or hashlib.sha256(file.read_bytes()).hexdigest() != item['sha256']:
                    raise ValueError('Base runtime member mismatch: ' + name)
        destination = (ROOT / 'build/assets' / spec['destination']).resolve()
        if not destination.is_relative_to(ROOT / 'build/assets') or destination.exists():
            raise FileExistsError('Extraction requires a fresh destination under build/assets')
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
        if a.asset.startswith(('opt45-', 'ps-')):
            root = destination / spec['root_prefix']
            index = json.loads((root / 'asset_manifest.json').read_bytes())
            for name, item in index['files'].items():
                file = (root / name).resolve()
                if not file.is_relative_to(root) or file.stat().st_size != item['bytes'] or hashlib.sha256(file.read_bytes()).hexdigest() != item['sha256']:
                    raise ValueError('Asset member identity mismatch: ' + name)
            result['payload_files_verified'] = len(index['files'])
            if a.asset == 'ps-runtime':
                delta = json.loads((root / 'delta_manifest.json').read_bytes())
                if delta['base_manifest_sha256'] != base_spec['source_manifest_sha256'] or delta['target_manifest_sha256'] != spec['source_manifest_sha256']:
                    raise ValueError('PS delta/base identity mismatch')
                bundle = (root / delta['target_directory']).resolve()
                if not bundle.is_relative_to(root):
                    raise ValueError('PS target escapes asset root')
                for name, item in delta['reuse'].items():
                    source, target = (base / name).resolve(), (bundle / name).resolve()
                    if not source.is_relative_to(base) or not target.is_relative_to(bundle) or target.exists():
                        raise ValueError('Invalid PS reuse path: ' + name)
                    if source.stat().st_size != item['bytes'] or hashlib.sha256(source.read_bytes()).hexdigest() != item['sha256']:
                        raise ValueError('PS base identity mismatch: ' + name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                manifest = bundle / 'manifest.json'
                if hashlib.sha256(manifest.read_bytes()).hexdigest() != spec['source_manifest_sha256']:
                    raise ValueError('PS frozen manifest mismatch')
                entries = json.loads(manifest.read_bytes())['files']
                for name, item in entries.items():
                    file = (bundle / name).resolve()
                    if not file.is_relative_to(bundle) or file.stat().st_size != item['bytes'] or hashlib.sha256(file.read_bytes()).hexdigest() != item['sha256']:
                        raise ValueError('PS materialized member mismatch: ' + name)
                result.update(materialized_runtime=str(bundle), frozen_runtime_members_verified=len(entries), reused_base_members=len(delta['reuse']))
            if a.asset == 'opt45-runtime':
                bundle = root / 'AB_SHARED_OPT45_X128_Y768_LAYOUT4_FIXED_20261010'
                manifest = bundle / 'manifest.json'
                if hashlib.sha256(manifest.read_bytes()).hexdigest() != spec['source_manifest_sha256']:
                    raise ValueError('Research runtime manifest mismatch')
                entries = json.loads(manifest.read_bytes())['files']
                for name, item in entries.items():
                    file = (bundle / name).resolve()
                    if not file.is_relative_to(bundle) or file.stat().st_size != item['bytes'] or hashlib.sha256(file.read_bytes()).hexdigest() != item['sha256']:
                        raise ValueError('Research runtime payload mismatch: ' + name)
                result['frozen_runtime_members_verified'] = len(entries)

        if a.asset == 'h576-runtime':
            root = destination / 'v5_h576_ui_20261017_01'
            inputs = json.loads((root / 'inputs.json').read_bytes())
            for name, digest in inputs['files'].items():
                file = (root / name).resolve()
                if not file.is_relative_to(root) or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
                    raise ValueError('H576 public input identity mismatch: ' + name)
            frozen = ROOT / 'docs/locks/H576_PACKAGE.lock.json'
            if (root / 'manifest.json').read_bytes() != frozen.read_bytes():
                raise ValueError('H576 frozen manifest mismatch')
            for name, item in json.loads(frozen.read_bytes())['files'].items():
                file = (root / name).resolve()
                if not file.is_relative_to(root) or file.stat().st_size != item['bytes'] or hashlib.sha256(file.read_bytes()).hexdigest() != item['sha256']:
                    raise ValueError('H576 frozen member mismatch: ' + name)
            result.update(public_inputs_verified=len(inputs['files']), frozen_runtime_members_verified=84,
                          scope='Files only; historical boot binding cannot authorize or configure a new board window')
        if a.asset == 'model':
            import sys
            sys.path.insert(0, str(ROOT))
            from model.b.runtime import verify_bundle
            identity = verify_bundle(destination / 'B_MODEL_BUNDLE_V1')
            if identity['manifest_sha256'] != json.loads((ROOT / 'docs/locks/BASELINE_LOCK.json').read_text())['B_manifest_sha256']:
                raise ValueError('Wrong model manifest')
            result['model_manifest'] = identity
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
