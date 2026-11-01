"""Exact preprocessing with read-only RGB hashing on one separate ARM CPU."""
import hashlib
import io
import os
import threading


def preprocess(raw,dataset):
    np,Image=dataset.dependencies()
    with Image.open(io.BytesIO(raw)) as source:
        dataset.need(source.format=='JPEG' and source.width*source.height<=40000000,'bounded JPEG required')
        rgb=source if source.mode=='RGB' else source.convert('RGB')
        worker=None;result=[];errors=[]
        try:
            # Complete lazy JPEG decoding before concurrent read-only crop/resize.
            rgb.load()
            def digest_rgb():
                try:
                    os.sched_setaffinity(0,{1})
                    digest=hashlib.sha256(str(rgb.size).encode()+b'\0')
                    for top in range(0,rgb.height,128):
                        with rgb.crop((0,top,rgb.width,min(top+128,rgb.height))) as strip:
                            digest.update(strip.tobytes())
                    result.append(digest.hexdigest())
                except BaseException as error:
                    errors.append(error)
            worker=threading.Thread(target=digest_rgb,name='opt50-rgb-sha')
            worker.start()
            resized,crop=dataset.resize_crop_box(*rgb.size)
            with rgb.resize(resized,Image.Resampling.BICUBIC) as scaled:
                with scaled.crop(crop) as clipped:
                    array=np.asarray(clipped,dtype=np.float32)/np.float32(255.0)
            array=(array-np.asarray(dataset.PREPROCESSING['mean'],dtype=np.float32))/np.asarray(dataset.PREPROCESSING['std'],dtype=np.float32)
            tensor=np.ascontiguousarray(array.transpose(2,0,1)[None],dtype=np.float32)
            dataset.need(tensor.shape==(1,3,224,224) and bool(np.isfinite(tensor).all()),'invalid preprocessed tensor')
            stream=io.BytesIO();np.save(stream,tensor,allow_pickle=False)
            worker.join()
            if errors:raise errors[0]
            return stream.getvalue(),tensor.tobytes(order='C'),result[0]
        finally:
            if worker is not None and worker.ident is not None:worker.join()
            if rgb is not source:rgb.close()
