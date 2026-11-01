"""Stream closed installed-service195 evidence; no inference or hardware operations."""
import json
from pathlib import Path
import sys
import tarfile


def main():
    work=Path('/var/lib/opt50-final-20261101');state=Path('/var/lib/opt50-demo-v1')
    closed=json.loads((work/'service_quality/closed.json').read_bytes())
    if closed['status']!='PASS' or closed['images']!=195:raise ValueError('closed actual service195 required')
    with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
        archive.add(work/'service_quality',arcname='service_quality')
        for row in closed['rows']:archive.add(state/'requests'/row['request_id'],arcname='requests/'+row['request_id'])
        for session in sorted(closed['sessions']):archive.add(state/session,arcname=session)
        app=Path('/opt/opt50-demo-v1/private');receipt=json.loads((app/'install_receipt.json').read_bytes())
        archive.add(app/'install_receipt.json',arcname='installed/install_receipt.json')
        for name in sorted(receipt['files']):archive.add(app/name,arcname='installed/'+name)
        archive.add(work/'service-openssl-info/build_receipt.json',arcname='service_build_receipt.json')
        archive.add(work/'original_service',arcname='original_service')
        archive.add('/home/ubuntu/fpga_project/opt50_integration/b24_accuracy_ready_20261028_v1/dataset/manifest.json',arcname='dataset_manifest.json')
        archive.add(state/'model/manifest.json',arcname='model_manifest.json')
        archive.add(state/'model/model_catalog.hpp',arcname='model_catalog.hpp')


if __name__=='__main__':main()
