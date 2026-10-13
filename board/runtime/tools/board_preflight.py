"""Read-only SSH snapshot. Does not inspect configfs, load modules or alter hardware."""
import argparse
import json
import shlex
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', default='kv260')
    a = p.parse_args()
    if a.host.startswith('-') or any(c.isspace() for c in a.host):
        raise ValueError('Use a configured SSH host alias')
    code = '''import json,pathlib,platform
p=pathlib.Path
owners=[]
for d in p('/proc').iterdir():
 if d.name.isdigit():
  try:
   c=(d/'comm').read_text().strip()
   if c.startswith('model_xrt') or c.startswith('f8_'): owners.append({'pid':int(d.name),'comm':c})
  except OSError: pass
print(json.dumps({'boot_id':p('/proc/sys/kernel/random/boot_id').read_text().strip(),'kernel':platform.release(),'architecture':platform.machine(),'ZOCL_loaded':any(s.startswith('zocl ') for s in p('/proc/modules').read_text().splitlines()),'render_nodes':[d.name for d in p('/dev/dri').glob('renderD*')],'owners':owners,'scope':'USER_READONLY_NOT_ROOT_C0_NOT_BOARD_AUTHORIZATION'}))'''
    r = subprocess.run(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', a.host, 'python3 -c ' + shlex.quote(code)], capture_output=True, timeout=20)
    if r.returncode:
        raise RuntimeError('Read-only SSH failed: ' + r.stderr.decode(errors='replace'))
    print(json.dumps(json.loads(r.stdout), indent=2))


if __name__ == '__main__':
    main()
