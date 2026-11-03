"""CPU-only, original JPEG diagnostics; does not create an owner or load a DLL."""
import hashlib
import io
import json
import os
from pathlib import Path
import resource
import statistics
import sys
import time

sys.path.insert(0, '/opt/opt50-demo-v1/private')
import dataset
from PIL import Image
import numpy as np


def profile(raw, variant='original'):
    timings = {}
    last = time.perf_counter_ns()
    def mark(name):
        nonlocal last
        now = time.perf_counter_ns()
        timings[name] = (now-last)/1e6
        last = now
    np_, Image_ = dataset.dependencies()
    mark('dependencies')
    with Image_.open(io.BytesIO(raw)) as source:
        mark('open_header')
        source.load()
        mark('JPEG_decode')
        rgb = source if variant in ('reuse_rgb','strip_hash') and source.mode=='RGB' else source.convert('RGB')
        mark('RGB_convert_copy')
        try:
            if variant=='strip_hash':
                digest = hashlib.sha256()
                digest.update(str(rgb.size).encode()+b'\0')
                for top in range(0,rgb.height,128):
                    with rgb.crop((0,top,rgb.width,min(top+128,rgb.height))) as strip:
                        digest.update(strip.tobytes())
                rgb_sha = digest.hexdigest()
                mark('RGB_tobytes_and_SHA256')
            else:
                pixels = rgb.tobytes()
                mark('RGB_tobytes')
                if variant!='original':
                    digest = hashlib.sha256()
                    digest.update(str(rgb.size).encode()+b'\0')
                    digest.update(pixels)
                    rgb_sha = digest.hexdigest()
                else:
                    rgb_sha = hashlib.sha256(str(rgb.size).encode()+b'\0'+pixels).hexdigest()
                del pixels
                mark('RGB_SHA256')
            resized, crop = dataset.resize_crop_box(*rgb.size)
            with rgb.resize(resized, Image.Resampling.BICUBIC) as scaled:
                mark('BICUBIC_resize')
                with scaled.crop(crop) as clipped:
                    array = np.asarray(clipped, dtype=np.float32)/np.float32(255.0)
            mark('crop_array')
            array = (array-np.asarray(dataset.PREPROCESSING['mean'],dtype=np.float32))/np.asarray(dataset.PREPROCESSING['std'],dtype=np.float32)
            tensor = np.ascontiguousarray(array.transpose(2,0,1)[None],dtype=np.float32)
            assert tensor.shape==(1,3,224,224) and bool(np.isfinite(tensor).all())
            mark('normalize_layout_validate')
            stream=io.BytesIO();np.save(stream,tensor,allow_pickle=False)
            result=(stream.getvalue(),tensor.tobytes(order='C'),rgb_sha)
            mark('serialize')
        finally:
            if rgb is not source:
                rgb.close()
    timings['total_ms']=sum(timings.values())
    return result,timings


def main():
    # Match the supervisor thread's validated CPU0 affinity without changing it.
    os.sched_setaffinity(0,{0})
    root=Path(sys.argv[1]); output=Path(sys.argv[2]);rows=[]
    files=sorted(root.glob('*.jpg'))
    for file in files:
        raw=file.read_bytes()
        wall=time.perf_counter_ns();cpu=time.process_time_ns()
        reference=dataset.preprocess(raw)
        original={'wall_ms':(time.perf_counter_ns()-wall)/1e6,'cpu_ms':(time.process_time_ns()-cpu)/1e6}
        variants=['original','split_hash','reuse_rgb','strip_hash']
        for variant in variants:
            samples=[]
            for repeat in range(3):
                cpu=time.process_time_ns()
                actual,timing=profile(raw,variant)
                timing['process_cpu_ms']=(time.process_time_ns()-cpu)/1e6
                assert actual==reference, 'NPY bytes, tensor bytes or RGB digest differ'
                samples.append(timing)
            rows.append({'file':file.name,'bytes':len(raw),'image_sha256':hashlib.sha256(raw).hexdigest(),
                         'original':original,'variant':variant,
                         'all_outputs_identical':True,'samples':samples})
        print(json.dumps({'file':file.name,'original':original,'median':[(r['variant'],statistics.median(s['total_ms'] for s in r['samples'])) for r in rows[-len(variants):]]}),flush=True)
    value={'scope':'CPU-only original JPEGs; same board Python/Pillow/NumPy, bicubic/normalization; no FPGA or global configuration change',
           'affinity':sorted(os.sched_getaffinity(0)),'rows':rows,'maxrss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    output.write_text(json.dumps(value,indent=2))


if __name__=='__main__':main()
