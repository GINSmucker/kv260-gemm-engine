"""B1 image-mode/geometry parity and ARM raw-image regression checks."""
import argparse
import hashlib
from pathlib import Path
import platform
import numpy as np
from PIL import Image
from .common import sha,utc,write_json
from .preprocess import preprocess

def main():
    p=argparse.ArgumentParser();p.add_argument("--bundle",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True);records=[]
    for i in range(20):
        x=preprocess(a.bundle/f"regression/{i:02d}.JPEG")
        expected=np.load(a.bundle/f"regression/{i:02d}_input.npy")
        exact=np.array_equal(x,expected)
        records.append({"image_index":i,"exact":exact,"tensor_sha256":hashlib.sha256(x.tobytes()).hexdigest(),"max_abs":float(np.max(np.abs(x-expected)))})
    if platform.system()=="Windows":
        import timm
        transform=timm.data.create_transform(input_size=(3,224,224),mean=(0.485,0.456,0.406),std=(0.229,0.224,0.225),interpolation="bicubic",crop_pct=0.9,is_training=False)
        rng=np.random.default_rng(20261001)
        for mode,shape in [("RGB",(319,503,3)),("L",(231,289)),("RGBA",(529,248,4)),("RGB",(224,224,3)),("RGB",(10,90,3))]:
            arr=rng.integers(0,256,shape,dtype=np.uint8);im=Image.fromarray(arr,mode=mode);path=a.output/(mode+"_"+str(shape[0])+".png");im.save(path)
            ours=preprocess(path);expected=transform(im.convert("RGB")).unsqueeze(0).numpy()
            records.append({"mode":mode,"shape":list(shape),"exact":bool(np.array_equal(ours,expected)),"max_abs":float(np.max(np.abs(ours-expected)))})
    bad=a.output/"bad_image.png";bad.write_bytes(b"this is not an image")
    rejected=False
    try:preprocess(bad)
    except (OSError,ValueError):rejected=True
    status="PASS" if all(r["exact"] for r in records) and rejected else "FAIL"
    write_json(a.output/"preprocess_parity.json",{"status":status,"host_utc":utc(),"architecture":platform.machine(),"records":records,"bad_image_rejected":rejected,"inference_calls_for_bad_image":0})
    print(f"preprocessing {status}: {len(records)} cases; bad image rejected={rejected}",flush=True)
    if status!="PASS":raise ValueError("Preprocess gate failed")

if __name__=="__main__":main()
