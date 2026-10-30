"""Independent Dense saved-logit checks; no inference or device access."""
import io
from pathlib import Path
import numpy as np
import accuracy_metrics as metrics
import dataset
def recompute_records(root,records):
    root=Path(root);result=[]
    for row in records:
        folder=root/'samples'/('%06d'%row['ordinal'])
        cpu=np.load(io.BytesIO(dataset.read(folder/'cpu_reference.npy',16384)),allow_pickle=False)
        pl=np.load(io.BytesIO(dataset.read(folder/'model_logits.npy',16384)),allow_pickle=False)
        actual=metrics.compare(cpu,pl,row['label'])
        for key,value in actual.items():
            if key!='schema':metrics.need(row.get(key)==value,'saved Dense logit/rank differs: '+key)
        result.append(dict(row))
    return result
