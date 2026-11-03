"""Validate captured deployment completeness and device-free request framing."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]/'board/service/deployment'
SPEC = importlib.util.spec_from_file_location('deployed_demo_common', ROOT/'private/common.py')
COMMON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMMON)


class DeployedServiceTests(unittest.TestCase):
    def test_manifest_and_install_receipt_cover_source_bytes(self):
        manifest = json.loads((ROOT/'source_manifest.json').read_bytes())
        self.assertEqual(manifest['schema'], 'opt50_deployed_source_v1')
        records = manifest['files']
        self.assertEqual(len(records), len({r['path'] for r in records}))
        for r in records:
            p = ROOT/r['path']
            self.assertEqual(p.stat().st_size, r['bytes'], r['path'])
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(), r['sha256'], r['path'])
        receipt = json.loads((ROOT/'private/install_receipt.json').read_bytes())
        for name, expected in receipt['files'].items():
            if name.startswith('build/'):
                continue
            p = ROOT/'private'/name
            self.assertEqual({'bytes': p.stat().st_size,
                'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}, expected, name)

    def test_python_sources_compile_and_local_imports_are_present(self):
        files = list(ROOT.rglob('*.py'))
        names = {p.stem for p in files}
        external = set(sys.stdlib_module_names)|{'numpy', 'PIL', 'onnxruntime', '__future__'}
        for p in files:
            source = p.read_bytes()
            compile(source, str(p), 'exec')
            for node in ast.walk(ast.parse(source)):
                imports = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                    [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                for name in imports:
                    self.assertIn(name.split('.')[0], names|external, str(p)+': '+name)
        self.assertEqual((ROOT/'web/common.py').read_bytes(), (ROOT/'private/common.py').read_bytes())

    def test_systemd_entrypoints_and_web_resources_exist(self):
        for unit, entry in [('opt50-demo-hardware.service', 'private/backend.py'),
                            ('opt50-demo-web.service', 'web/web.py')]:
            text = (ROOT/'private/units'/unit).read_text(encoding='utf-8')
            self.assertIn('/opt/opt50-demo-v1/'+entry, text)
            self.assertTrue((ROOT/entry).is_file())
        import re
        html = (ROOT/'web/static/index.html').read_text(encoding='utf-8')
        for name in re.findall(r'(?:src|href)=["\'](/[^"\']+)', html):
            self.assertTrue((ROOT/'web/static'/name.lstrip('/')).is_file(), name)

    def test_frame_roundtrip_preserves_body_and_header(self):
        left, right = socket.socketpair()
        with left, right:
            COMMON.send(left, {'action': 'infer', 'request_id': 'a'*32}, b'jpeg bytes')
            header, body = COMMON.receive(right)
        self.assertEqual(header, {'action': 'infer', 'request_id': 'a'*32})
        self.assertEqual(body, b'jpeg bytes')

    def test_invalid_request_ids_and_oversized_frames_are_rejected(self):
        for token in ('A'*32, '../request', 'a'*31, None):
            with self.assertRaises(ValueError):
                COMMON.request_id(token)
        left, right = socket.socketpair()
        with left, right:
            left.sendall(struct.pack('!II', 65537, 0))
            with self.assertRaisesRegex(ValueError, 'frame too large'):
                COMMON.receive(right)

    def test_truncated_frames_are_rejected(self):
        left, right = socket.socketpair()
        with left, right:
            left.sendall(struct.pack('!II', 10, 0)+b'{}')
            left.shutdown(socket.SHUT_WR)
            with self.assertRaisesRegex(ValueError, 'truncated message'):
                COMMON.receive(right)


if __name__ == '__main__':
    unittest.main()
