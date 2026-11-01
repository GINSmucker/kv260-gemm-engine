"""Stage independently identified candidates; never edit the frozen release or install."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
BASE = REPO / 'board/runtime/snapshots/sparse24'


def pin(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('candidate source anchor differs: ' + old[:80])
    return source.replace(old, new, 1)


def plugin(source):
    source = replace_once(source, '#include "client.h"', '#include "client.h"\n#include "diagnostic.hpp"')
    source = replace_once(source, '    uint64_t run=0;', '    opt50_diagnostic::Linear diagnostic;\n    uint64_t run=0;')
    source = replace_once(source, '            auto entered=Clock::now();',
                          '            auto entered=Clock::now();\n            uint64_t input_synced=0,ipc_returned=0;')
    source = replace_once(source, '            session->client->sync_input(s.x_bytes());',
                          '            session->client->sync_input(s.x_bytes());\n            input_synced=opt50_diagnostic::now();')
    source = replace_once(source, '            auto result=session->client->call(request);',
                          '            auto result=session->client->call(request);\n            ipc_returned=opt50_diagnostic::now();')
    source = replace_once(source, '            const char* directory=std::getenv("KV5_DUMP");', '''            auto stamp=[](auto value) {return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(value.time_since_epoch()).count());};
            session->diagnostic.record(index,{stamp(entered),stamp(quant_begin),stamp(quant_end),
                use_pl?input_synced:stamp(quant_end),use_pl?ipc_returned:stamp(quant_end),stamp(gemm_end),stamp(end)});
            const char* directory=std::getenv("KV5_DUMP");''')
    source = replace_once(source, '            s.timing={}; s.layers={};',
                          '            s.timing={}; s.layers={}; s.diagnostic.reset();')
    return replace_once(source, 'EXPORT int boundary_layers(void* handle,uint64_t* out) {', '''EXPORT int opt50_diagnostic_linear_v1(void* handle,uint64_t* out,size_t count) {
    return action(handle,[&](Session& s) {
        require(out && count==49*7 && !s.active && !s.failed && s.run && s.next==49,"diagnostics after complete graph only");
        std::copy(s.diagnostic.stamps.begin(),s.diagnostic.stamps.end(),out);
    });
}
EXPORT int boundary_layers(void* handle,uint64_t* out) {''')


def owner(source):
    source = replace_once(source, '#include "kernel.h"', '#include "kernel.h"\n#include "diagnostic.hpp"')
    source = replace_once(source, '    State state_;', '    State state_;\n    opt50_diagnostic::Control diagnostic_;')
    source = replace_once(source, 'checks_.reset_for_graph();trace_.reset();',
                          'checks_.reset_for_graph();trace_.reset();diagnostic_.reset();')
    source = replace_once(source, 'const auto raw=trace_.json(state_,checks_,opt50_gate::checked().boot);journal_->publish(state_.run,raw);', '''auto raw=trace_.json(state_,checks_,opt50_gate::checked().boot);
            need(raw.size()>2 && raw.substr(raw.size()-2)=="}\\n","diagnostic receipt suffix");
            raw.resize(raw.size()-2);raw+=",\\\"diagnostic_control_v1\\\":"+diagnostic_.json()+"}\\n";
            journal_->publish(state_.run,raw);''')
    source = replace_once(source, 'try{execute_layer(*this,state_,layer);}',
                          'try{diagnostic_.mark(layer,0);execute_layer(*this,state_,layer);diagnostic_.mark(layer,4);}')
    source = replace_once(source, 'void start_intent(unsigned layer){(void)layer;',
                          'void start_intent(unsigned layer){diagnostic_.mark(layer,1);')
    source = replace_once(source, 'gemm_->write_register(offset,value);PERF_TRACE(',
                          'gemm_->write_register(offset,value);if(graph_hot_&&offset==0)diagnostic_.mark(current_layer_,2);PERF_TRACE(')
    source = replace_once(source, 'void completion_receipt(unsigned layer){(void)layer;',
                          'void completion_receipt(unsigned layer){diagnostic_.mark(layer,3);')
    return replace_once(source, 'void pause(){need(', 'void pause(){diagnostic_.pause(current_layer_);need(')


def crypto(source):
    source = replace_once(source, '#include "kernel.h"', '#include "kernel.h"\n#include "sha256_crypto.h"')
    return replace_once(source, 'const auto data_sha=sha(payload(1)),meta_sha=sha(payload(2));',
                        'const auto data_sha=kv260_crypto_sha256(map_[1]+guard,caps_[1]),meta_sha=kv260_crypto_sha256(map_[2]+guard,caps_[2]);')


def stage(bundle, output, variant):
    bundle, output = Path(bundle).resolve(), Path(output).absolute()
    if variant not in ('baseline','diagnostic','openssl-info'):
        raise ValueError('unknown variant')
    if output.exists() or output.is_relative_to(bundle) or bundle.is_relative_to(output):
        raise ValueError('new output outside baseline required')
    spec = importlib.util.spec_from_file_location('_opt50_frozen_verifier', BASE/'verify_tools.py')
    verifier = importlib.util.module_from_spec(spec); spec.loader.exec_module(verifier)
    original = verifier.verify(bundle)
    # Also bind the restored source to this checkout's frozen source catalog.
    catalog = json.loads((BASE/'tools_manifest.json').read_bytes())
    if original != catalog:
        raise ValueError('restored release manifest differs')
    shutil.copytree(bundle/'source', output/'source')
    shutil.copytree(bundle/'model', output/'model')
    for name in ('test_model_core.cpp', 'test_performance_core.cpp', 'model_runtime.py'):
        shutil.copyfile(bundle/name, output/name)
    shutil.copyfile(bundle/'tools_manifest.json', output/'baseline_tools_manifest.json')
    shutil.copyfile(HERE/'diagnostic.hpp', output/'source/diagnostic.hpp')
    if variant == 'diagnostic':
        for name, transform in (('b_plugin.cpp', plugin), ('kernel.cpp', owner)):
            path = output/'source'/name
            path.write_text(transform(path.read_text(encoding='utf-8')), encoding='utf-8', newline='\n')
    elif variant == 'openssl-info':
        path = output/'source/kernel.cpp'
        path.write_text(crypto(path.read_text(encoding='utf-8')), encoding='utf-8', newline='\n')
    elif variant != 'baseline':
        raise ValueError('unknown variant')
    files = {p.relative_to(output).as_posix(): pin(p.read_bytes()) for p in sorted(output.rglob('*')) if p.is_file()}
    result = {'schema': 'opt50_final_candidate_v1', 'variant': variant, 'status': 'NOT RUN',
              'hardware_executed': False, 'baseline_manifest': pin((bundle/'tools_manifest.json').read_bytes()),
              'recipe': pin(Path(__file__).read_bytes()), 'diagnostic_header': pin((HERE/'diagnostic.hpp').read_bytes()),
              'tools': {p.name:pin(p.read_bytes()) for p in sorted(HERE.glob('*.py'))},
              'files': files, 'source_model_sha256': 'ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43',
              'PL_calls_per_graph': 36, 'max_graphs': 50, 'window_seconds': 900,
              'installation': 'NOT RUN', 'default_release_changed': False}
    (output/'candidate_manifest.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--variant', choices=('baseline', 'diagnostic', 'openssl-info'), required=True)
    args = parser.parse_args()
    result = stage(args.bundle, args.out, args.variant)
    print(json.dumps({k:v for k,v in result.items() if k != 'files'}, indent=2))
