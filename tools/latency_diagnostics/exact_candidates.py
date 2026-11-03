"""Isolated CPU candidates; no FPGA, accepted runtime, service or config changes."""
import hashlib
import io
import json
import os
import stat


def preprocess(raw, dataset):
    np, Image = dataset.dependencies()
    with Image.open(io.BytesIO(raw)) as source:
        dataset.need(source.format=='JPEG' and source.width*source.height<=40000000,'bounded JPEG required')
        rgb=source if source.mode=='RGB' else source.convert('RGB')
        try:
            digest=hashlib.sha256(str(rgb.size).encode()+b'\0')
            for top in range(0,rgb.height,128):
                with rgb.crop((0,top,rgb.width,min(top+128,rgb.height))) as strip:
                    digest.update(strip.tobytes())
            resized,crop=dataset.resize_crop_box(*rgb.size)
            with rgb.resize(resized,Image.Resampling.BICUBIC) as scaled:
                with scaled.crop(crop) as clipped:
                    array=np.asarray(clipped,dtype=np.float32)/np.float32(255.0)
            array=(array-np.asarray(dataset.PREPROCESSING['mean'],dtype=np.float32))/np.asarray(dataset.PREPROCESSING['std'],dtype=np.float32)
            tensor=np.ascontiguousarray(array.transpose(2,0,1)[None],dtype=np.float32)
            dataset.need(tensor.shape==(1,3,224,224) and bool(np.isfinite(tensor).all()),'invalid preprocessed tensor')
            stream=io.BytesIO();np.save(stream,tensor,allow_pickle=False)
            return stream.getvalue(),tensor.tobytes(order='C'),digest.hexdigest()
        finally:
            if rgb is not source:
                rgb.close()


class ExactBundleAudit:
    """Retains every original SHA, lstat and metadata check; caches path strings only."""
    def __init__(self, bundle, runtime):
        self.bundle,self.runtime=bundle,runtime
        self.paths={}

    def unchanged(self, root):
        bundle,r=self.bundle,self.runtime
        r.require(r.ordinary(root)==bundle['root'],'verified bundle belongs to another root')
        r.require(r.identity(json.dumps(bundle['manifest'],sort_keys=True,separators=(',',':'),allow_nan=False).encode())==bundle._manifest_pin,'verified manifest object changed')
        r.require(isinstance(bundle['graph_bytes'],bytes) and r.identity(bundle['graph_bytes'])==bundle['manifest']['files'][r.GRAPH]
                  and isinstance(bundle.reference_bytes,bytes) and r.identity(bundle.reference_bytes)==bundle['manifest']['files']['reference.onnx'],
                  'verified in-memory graph/reference bytes changed')
        inventory=set();pending=[(str(bundle['root']),'')];directories=0
        while pending:
            folder,prefix=pending.pop();directories+=1
            r.require(directories<=256,'model directory inventory too large')
            with os.scandir(folder) as members:
                for member in members:
                    info=os.lstat(member.path)
                    r.require(not stat.S_ISLNK(info.st_mode) and not getattr(info,'st_file_attributes',0)&r.REPARSE,'linked model member refused')
                    name=prefix+member.name
                    if stat.S_ISDIR(info.st_mode):pending.append((member.path,name+'/'))
                    else:
                        r.require(stat.S_ISREG(info.st_mode),'nonregular model member refused')
                        inventory.add(name)
                        r.require(len(inventory)<=128,'model file inventory too large')
        r.require(inventory==set(bundle._metadata),'verified bundle inventory changed')
        for name,expected in bundle._metadata.items():
            if name not in self.paths:
                path=bundle['root']/name
                self.paths[name]=(str(path),tuple(str(p) for p in (path,*path.parents)))
            path,ancestors=self.paths[name]
            for part in ancestors:
                info=os.lstat(part)
                r.require(not stat.S_ISLNK(info.st_mode) and not getattr(info,'st_file_attributes',0)&r.REPARSE,'symlink/reparse path refused')
            r.require(r.file_metadata(os.lstat(path))==expected,'verified bundle file identity changed: '+name)
        return bundle
