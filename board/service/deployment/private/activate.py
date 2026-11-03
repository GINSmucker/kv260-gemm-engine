"""Install/enable only the two new demo services, after software preparation."""
import argparse
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
from common import APP, STATE, create, encoded, need, pin


def activate(enable):
    need(sys.platform == 'linux' and os.geteuid() == 0, 'root Linux required')
    from backend import verify_install
    config = verify_install()
    need(config['status'] == 'SOFTWARE_READY_NOT_HARDWARE_RUN', 'software receipt required')
    need(not (STATE / 'activation.json').exists(), 'activation already consumed; inspect installed services')
    need(sorted(p.name for p in Path('/sys/kernel/config/device-tree/overlays').iterdir()) == ['k26-starter-kits_image_1']
         and not Path('/dev/dri/renderD128').exists(), 'unused starter baseline required')
    # Diagnostic-only device occupancy and pinned firmware audit before any
    # service installation. Hardware actions still happen only in the backend.
    from loader import prepare_files
    prepare_files()
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.bind(('0.0.0.0', 8080))
    for name in ('opt50-demo-hardware.service', 'opt50-demo-web.service'):
        need(not (Path('/etc/systemd/system') / name).exists(), 'existing unit would be overwritten')
    try:
        pwd.getpwnam('opt50demo')
    except KeyError:
        subprocess.run(['/usr/sbin/useradd', '--system', '--user-group', '--no-create-home',
                        '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin', 'opt50demo'], check=True)
    account = pwd.getpwnam('opt50demo')
    need(account.pw_uid != 0 and account.pw_shell in ('/usr/sbin/nologin', '/bin/false'), 'demo account must be unprivileged')
    os.umask(0o077)
    create(STATE / 'activation.json', encoded({'schema': 'opt50_demo_activation_intent_v1',
           'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
           'automatic_boot_enabled': enable, 'UART_logging_claimed': False,
           'action': 'install two units and start once; no flash or U-Boot changes'}))
    for name in ('opt50-demo-hardware.service', 'opt50-demo-web.service'):
        create(Path('/etc/systemd/system') / name, (APP / 'units' / name).read_bytes(), 0o644)
    subprocess.run(['/usr/bin/systemctl', 'daemon-reload'], check=True)
    if enable:
        subprocess.run(['/usr/bin/systemctl', 'enable', 'opt50-demo-hardware.service', 'opt50-demo-web.service'], check=True)
    subprocess.run(['/usr/bin/systemctl', 'start', '--no-block', 'opt50-demo-hardware.service', 'opt50-demo-web.service'], check=True)
    print(json.dumps({'status': 'DEMO_SERVICES_SUBMITTED_NOT_YET_VERIFIED', 'boot_enabled': enable,
                      'url': 'http://192.168.137.2:8080', 'automatic_retry': False}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--authorized-install-start-demo', action='store_true', required=True)
    parser.add_argument('--enable-at-boot', action='store_true')
    args = parser.parse_args()
    activate(args.enable_at_boot)
