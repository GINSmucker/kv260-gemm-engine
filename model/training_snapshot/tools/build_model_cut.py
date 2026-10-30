"""Partition the exact W8A8 graph at one integer MatMul for a callable GEMM backend."""
from pathlib import Path
import argparse,copy,sys
sys.dont_write_bytecode=True
import onnx
from onnx import helper
from release_common import ROOT,read,save,sha

def build(root,layer_index=2):
    root=Path(root).resolve();source=root/'models/final/model_w8a8.onnx'
    model=onnx.shape_inference.infer_shapes(onnx.load(source),strict_mode=True)
    infos={v.name:v for v in [*model.graph.input,*model.graph.value_info,*model.graph.output]}
    index=next(i for i,n in enumerate(model.graph.node) if n.name==f'BQL_{layer_index}_yi')
    node=model.graph.node[index];assert node.op_type=='MatMul'
    earlier=list(model.graph.node[:index]);later=list(model.graph.node[index+1:])
    inits={v.name for v in model.graph.initializer};produced={v for n in later for v in n.output}
    frontier=sorted({v for n in later for v in n.input if v and v not in produced and v not in inits})
    assert node.output[0] in frontier
    prefix_outputs=sorted((set(frontier)-{node.output[0]})|{node.input[0]})
    def make(nodes,inputs,outputs,name):
        dest=copy.deepcopy(model);dest.graph.name=name
        del dest.graph.node[:];dest.graph.node.extend(nodes)
        del dest.graph.input[:];dest.graph.input.extend(copy.deepcopy(infos[v]) for v in inputs)
        del dest.graph.output[:];dest.graph.output.extend(copy.deepcopy(infos[v]) for v in outputs)
        needed={v for n in nodes for v in n.input}|set(outputs)
        keep=[v for v in dest.graph.initializer if v.name in needed]
        del dest.graph.initializer[:];dest.graph.initializer.extend(keep)
        valid={v for n in nodes for v in n.output}|set(inputs)|{v.name for v in keep}
        vi=[v for v in dest.graph.value_info if v.name in valid and v.name not in set(inputs+outputs)]
        del dest.graph.value_info[:];dest.graph.value_info.extend(vi)
        onnx.checker.check_model(dest);return dest
    folder=root/f'runtime/cuts/layer_{layer_index:02d}';folder.mkdir(parents=True,exist_ok=True)
    prefix=make(earlier,[v.name for v in model.graph.input],prefix_outputs,'prefix_before_integer_gemm')
    suffix=make(later,frontier,[v.name for v in model.graph.output],'suffix_after_integer_gemm')
    onnx.save(prefix,folder/'prefix.onnx');onnx.save(suffix,folder/'suffix.onnx')
    layer=next(v for v in read(root/'parameters/layers.json') if v['index']==layer_index)
    descriptor={'schema_version':'B_GEMM_BACKEND_CUT_V1','layer_index':layer_index,'layer_id':layer['layer_id'],
        'route':layer['route'],'source_model_sha256':sha(source),'quantized_input':node.input[0],
        'integer_matmul_float_output':node.output[0],'prefix_outputs':prefix_outputs,'suffix_inputs':frontier,
        'prefix':(folder/'prefix.onnx').relative_to(root).as_posix(),
        'suffix':(folder/'suffix.onnx').relative_to(root).as_posix(),
        'postprocess':'Original suffix preserves INT32 cast, row scale, channel scale, bias and all later operations',
        'hardware_accessed':False}
    save(folder/'cut.json',descriptor);return descriptor

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=ROOT);p.add_argument('--layer',type=int,default=2)
    a=p.parse_args();print(build(a.root,a.layer))
