"""v5 product CLI; the temporary hardware loader remains a separate operation."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import struct
import time


def stop(path, weights):
    header=struct.Struct('<IHHQIIII')
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(150)
        s.connect(str(path)+'.ctl')
        for op,body in ((1,hashlib.sha256(Path(weights).read_bytes()).digest()),(7,b'')):
            s.sendall(header.pack(0x35564b,5,op,0,0,0,len(body),0)+body)
            data=bytearray()
            while len(data)<32:
                part=s.recv(32-len(data))
                if not part:raise RuntimeError('owner disconnected before stop response')
                data.extend(part)
            h=header.unpack(data)
            if h!=(0x35564b,5,op,0,0,0,0,0):raise RuntimeError('owner stop refused')


def main():
    p=argparse.ArgumentParser(description='KV260 v5 model runtime')
    commands=p.add_subparsers(dest='command',required=True)
    run=commands.add_parser('run')
    run.add_argument('--bundle',required=True); run.add_argument('--plugin',required=True)
    run.add_argument('--socket',required=True); run.add_argument('--input',required=True)
    run.add_argument('--run-id',type=int,required=True)
    run.add_argument('--graph',type=Path,default=Path(__file__).resolve().parent/'graphs/model_w8a8_pl.onnx')
    run.add_argument('--output',type=Path)
    end=commands.add_parser('stop'); end.add_argument('--socket',required=True); end.add_argument('--weights',required=True)
    a=p.parse_args()
    if a.command=='stop':
        stop(a.socket,a.weights); print(json.dumps(dict(status='STOP_ACCEPTED'))); return
    import numpy as np
    from runtime import Runner
    image=np.load(a.input,allow_pickle=False)
    with Runner(a.bundle,a.plugin,a.socket,a.graph) as runner:
        start=time.perf_counter_ns(); logits=runner.run(image,a.run_id).reshape(1000); elapsed=time.perf_counter_ns()-start
    top=np.argsort(-logits)[:5]
    result=dict(status='OK',run_id=a.run_id,inference_ms=elapsed/1e6,
                top5=[dict(index=int(i),logit=float(logits[i])) for i in top],logits=logits.tolist())
    if a.output:
        with a.output.open('x',encoding='utf-8') as f:json.dump(result,f);f.write('\n')
    print(json.dumps(result))


if __name__=='__main__':main()
