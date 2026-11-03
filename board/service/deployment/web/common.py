"""Device-free durable records and bounded request framing for the demo."""
import hashlib
import json
import os
from pathlib import Path
import re
import struct

APP = Path('/opt/opt50-demo-v1/private')
STATE = Path('/var/lib/opt50-demo-v1')
RUN = Path('/run/opt50-demo')
VENV = '/home/ubuntu/fpga_project/model_b/.venv/bin/python'
MAX_IMAGE = 8 * 1024 * 1024
MAX_REQUESTS = 500
MODEL_SHA = 'ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43'


def need(ok, message):
    if not ok:
        raise ValueError(message)


def pin(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n').encode()


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def create(path, raw, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        if hasattr(os, 'fchmod'):
            os.fchmod(stream.fileno(), mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_dir(Path(path).parent)


def replace(path, value, mode=0o600):
    path = Path(path)
    temporary = path.with_name(path.name + '.new')
    create(temporary, encoded(value), mode)
    os.replace(temporary, path)
    fsync_dir(path.parent)


def request_id(value):
    need(isinstance(value, str) and re.fullmatch('[0-9a-f]{32}', value), '32 lowercase hex request ID required')
    return value


def exact(stream, count):
    out = bytearray()
    while len(out) < count:
        block = stream.recv(count - len(out))
        need(block, 'truncated message')
        out.extend(block)
    return bytes(out)


def send(stream, value, body=b''):
    raw = encoded(value)
    need(len(raw) <= 65536 and len(body) <= MAX_IMAGE, 'frame too large')
    stream.sendall(struct.pack('!II', len(raw), len(body)) + raw + body)


def receive(stream):
    size, body_size = struct.unpack('!II', exact(stream, 8))
    need(0 < size <= 65536 and body_size <= MAX_IMAGE, 'frame too large')
    header = json.loads(exact(stream, size))
    need(isinstance(header, dict), 'object frame required')
    return header, exact(stream, body_size)


class Ledger:
    """A consumed ID is never resubmitted, even after a lost HTTP reply/reboot."""
    def __init__(self, root, boot):
        self.root, self.boot = Path(root), boot
        self.root.mkdir(mode=0o700, exist_ok=True)

    def get(self, token):
        folder = self.root / request_id(token)
        if not folder.exists():
            return None
        path = folder / 'result.json'
        if path.exists():
            return json.loads(path.read_bytes())
        if not (folder / 'intent.json').exists():
            # A process/power failure during durable admission is never treated
            # as an unused ID. No graph can precede this intent's publication.
            return {'request_id': token, 'stage': 'INCOMPLETE_ADMISSION_NO_REPLAY',
                    'image_sha256': pin((folder / 'image.jpg').read_bytes())['sha256']
                    if (folder / 'image.jpg').exists() else None, 'automatic_replay': False}
        intent = json.loads((folder / 'intent.json').read_bytes())
        return dict(intent, stage='SUBMITTED' if intent['boot_id'] == self.boot else 'INTERRUPTED_OLD_BOOT_NO_REPLAY')

    def admit(self, token, raw):
        token = request_id(token)
        previous = self.get(token)
        if previous is not None:
            need(previous['image_sha256'] == pin(raw)['sha256'], 'request ID already belongs to another image')
            return previous, False
        need(0 < len(raw) <= MAX_IMAGE, 'JPEG must be 1..8 MiB')
        need(len(list(self.root.iterdir())) < MAX_REQUESTS, 'demo evidence limit reached; export before maintenance')
        folder = self.root / token
        folder.mkdir(mode=0o700)
        create(folder / 'image.jpg', raw)
        value = {'request_id': token, 'boot_id': self.boot, 'stage': 'SUBMITTED',
                 'image_sha256': pin(raw)['sha256'], 'source_model_sha256': MODEL_SHA,
                 'automatic_replay': False}
        create(folder / 'intent.json', encoded(value))
        return value, True

    def history(self, limit=100):
        need(type(limit) is int and 1 <= limit <= 100, 'history bound')
        folders = sorted((p for p in self.root.iterdir() if p.is_dir()),
                         key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)[:limit]
        keys = ('request_id', 'stage', 'boot_id', 'image_sha256', 'Runner_run_ms',
                'CPU_reference_ms', 'bitwise_mismatches')
        return [{k: value.get(k) for k in keys} for folder in folders
                for value in [self.get(folder.name)]]
