"""Adapt only the two frozen public Sparse report reads to the existing 64MiB bound."""
from pathlib import Path
import types
REFERENCE=Path('/home/ubuntu/fpga_project/opt50_integration/b24_acceptance500_reboot_exports_20261030_v1/action_a77e3fa15328')
ARCHIVE_LIMIT=64*1024*1024
def need(ok,why):
    if not ok:raise ValueError(why)
def install(entry):
    original=entry.paired_reference_proof
    def bounded_proof(a,path):
        need(Path(path).absolute()==REFERENCE.absolute(),'fixed completed Sparse500 reference required')
        def reference_read(source,**kwargs):
            source=Path(source).absolute()
            if source==(REFERENCE/'evidence.tar.gz').absolute():
                need(kwargs=={'limit':512*1024*1024},'frozen Sparse archive read contract changed')
                return a.c.read_file(source,limit=ARCHIVE_LIMIT)
            need(source==(REFERENCE/'summary.json').absolute() and kwargs=={'limit':2*1024*1024},
                 'unexpected Sparse reference read')
            return a.c.read_file(source,**kwargs)
        # The original function still checks the exact archive SHA, embedded
        # summary bytes, model/dataset identities,500 images and zero mismatches.
        restricted=types.SimpleNamespace(c=types.SimpleNamespace(read_file=reference_read,decode_json=a.c.decode_json))
        return original(restricted,path)
    entry.paired_reference_proof=bounded_proof
