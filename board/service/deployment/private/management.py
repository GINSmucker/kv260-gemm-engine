"""Original wire50 HELLO/INFO/STOP only; no compute or register operations."""
import socket
import struct
from common import exact, need


class Admin:
    def __init__(self, path, pid, sha):
        self.stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.stream.settimeout(5)
            self.stream.connect(str(path))
            peer, uid, _ = struct.unpack('3i', self.stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            need(peer == pid and uid == 0, 'owner management peer differs')
            self.call(1, bytes.fromhex(sha))
        except BaseException:
            self.stream.close()
            raise

    def call(self, operation, body=b''):
        need(operation in (1, 7, 8), 'management operation forbidden')
        self.stream.settimeout(150 if operation == 8 else 10)
        self.stream.sendall(struct.pack('<IHHQIIII', 0x3035504f, 50, operation, 0, 0, 0, len(body), 0) + body)
        magic, version, op, run, sequence, layer, size, status = struct.unpack('<IHHQIIII', exact(self.stream, 32))
        need((magic, version, op, run, sequence, layer, status) == (0x3035504f, 50, operation, 0, 0, 0, 0)
             and size == (3200 if operation == 8 else 0), 'invalid management reply; no retry')
        raw = exact(self.stream, size)
        if operation == 8:
            values = struct.unpack('<400Q', raw)
            pl = [i for i in range(48) if i % 4 != 1]
            need(values[7] == 36 and [values[8+8*i+7] for i in pl] == list(range(1, 37))
                 and all(values[8+8*i+7] == 0 for i in range(49) if i not in pl), '36-call diagnostics differ')
        return raw

    def close(self):
        self.stream.close()
