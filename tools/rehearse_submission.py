"""Check a source checkout and restore pinned assets into one exclusive new directory."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from analyze_timing import analyze
from check_release import ROOT, need, pin, verify
from restore_bundle import restore
from verify_pair_archive import verify as verify_saved_outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--compiler', type=Path, help='Optional existing C++17 compiler for device-free tests')
    args = parser.parse_args()
    out = args.out.resolve()
    need(not out.exists() and out.parent.is_dir(), 'exclusive new output under an existing parent required')
    out.mkdir()
    records = []
    revision = None
    if (ROOT/'.git').exists() and shutil.which('git'):
        git = subprocess.run(['git','rev-parse','HEAD'], cwd=ROOT, capture_output=True, text=True)
        if git.returncode == 0:
            revision = git.stdout.strip()
    result = {'schema': 'opt50_submission_rehearsal_v1', 'status': 'FAIL', 'steps': records,
              'hardware_accessed': False, 'HLS_RTL_Vivado_PL': 'NOT RUN',
              'source_commit': revision,
              'source_identity': 'Git revision when available; frozen file identities are independently checked.'}

    def command(name, argv):
        run = subprocess.run(argv, cwd=ROOT, capture_output=True, timeout=240)
        (out/(name+'.stdout.txt')).write_bytes(run.stdout)
        (out/(name+'.stderr.txt')).write_bytes(run.stderr)
        records.append({'name': name, 'status': 'PASS' if run.returncode == 0 else 'FAIL',
                        'exit_code': run.returncode})
        need(run.returncode == 0, name+' failed; inspect rehearsal logs')

    try:
        result['source_checks'] = verify()
        manifest = json.loads((ROOT/'assets_manifest.json').read_bytes())['assets']
        assets = {}
        for name in ('sparse24-payload.tar.gz','dense36-payload.tar.gz','layer02-payload.tar.gz','paired500-evidence.tar.gz'):
            path = args.assets/name
            identity = pin(path.read_bytes())
            need(identity == manifest[name]['identity'], 'asset identity differs: '+name)
            assets[name] = identity
        result['assets'] = assets
        for name in ('sparse24','dense36','layer02'):
            restored = restore(name, out/name, args.assets/(name+'-payload.tar.gz'))
            records.append({'name': 'restore_'+name, 'status': 'PASS', 'files': restored['restored_files']})
        proof = out/'saved-evidence'
        proof.mkdir()
        shutil.copyfile(args.assets/'paired500-evidence.tar.gz', proof/'evidence.tar.gz')
        with tarfile.open(proof/'evidence.tar.gz', 'r:gz') as archive:
            for name in ('report/summary.json','report/per_image_comparison.csv'):
                (proof/Path(name).name).write_bytes(archive.extractfile(name).read())
        result['saved_logits_review'] = verify_saved_outputs(proof)
        timing = analyze(args.assets/'paired500-evidence.tar.gz')
        need(timing == json.loads((ROOT/'results/board500/timing_breakdown.json').read_bytes()),
             'recalculated timing differs from delivered data')
        records.append({'name': '390_native_timing_receipts', 'status': 'PASS'})
        command('docs', [sys.executable, '-B', 'tools/check_docs.py'])
        command('python_tests', [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
        command('web_tests', ['node', '--test', '--test-isolation=none',
                             'board/ui/tests/workbench.test.mjs', 'board/ui/tests/charts.test.mjs',
                             'board/ui/tests/final.test.mjs', 'board/ui/tests/sparse24.test.mjs'])
        if args.compiler:
            for name in ('sparse24','dense36'):
                command(name+'_cpu_selftests', [sys.executable, '-B', 'tools/run_runtime_selftests.py',
                        '--bundle', str(out/name), '--compiler', str(args.compiler.resolve()),
                        '--out', str(out/(name+'-selftests'))])
        else:
            records.append({'name': 'compiled_cpu_selftests', 'status': 'NOT RUN',
                            'reason': 'No C++17 compiler specified; device-free compiled tests are separate.'})
        result['status'] = 'PASS'
    except Exception as error:
        result['error'] = str(error)
        raise
    finally:
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
        print(json.dumps({'status': result['status'], 'report': str(out/'result.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
