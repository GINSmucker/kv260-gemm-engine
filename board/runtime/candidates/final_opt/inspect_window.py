"""Read a single dispatched window; never retries, signals, reconnects or mutates hardware."""
import argparse
import json
from pathlib import Path
import statistics
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('label');parser.add_argument('--wait',type=int,default=0);args=parser.parse_args()
    record=Path('/var/lib/opt50-final-20261101')/('window-'+args.label+'.json')
    root=Path(json.loads(record.read_bytes())['root']);began=time.monotonic()
    while not (root/'closed.json').exists() and time.monotonic()-began<args.wait:
        errors=[p for p in (root/'execution').glob('*.stderr') if p.stat().st_size]
        if errors:break
        time.sleep(.5)
    rows=[json.loads(p.read_bytes()) for p in sorted((root/'execution').glob('*_result.json'))]
    result={'label':args.label,'root':str(root),'closed':(root/'closed.json').exists(),'completed_graphs':len(rows),
            'errors':{p.name:p.read_text() for p in (root/'execution').glob('*.stderr') if p.stat().st_size}}
    if rows:
        result['timings']={key:{'median':statistics.median(r[key] for r in rows),'mean':statistics.mean(r[key] for r in rows),'values':[r[key] for r in rows]}
                           for key in ('Runner_run_ms','CPU_reference_ms','INFO_ms')}
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
