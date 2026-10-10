"""Pillow/NumPy implementation of the frozen timm eval transform."""
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps

SPEC = {"schema": "b_preprocess_v1", "input_shape": [1, 3, 224, 224], "mode": "RGB",
        "resize_short_side": 248, "resize_rule": "int(224 / 0.9); longer=int(short*long/short_original)",
        "interpolation": "Pillow BICUBIC", "center_crop": "round((height-224)/2),round((width-224)/2),ties-even",
        "crop_pct": 0.9, "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225],
        "division": "uint8 to FP32 then divide by FP32(255)", "layout": "FP32 NCHW",
        "exif": "no automatic transpose, matches timm input.convert(RGB)",
        "formats": ["JPEG", "PNG", "BMP", "WEBP"], "invalid_image": "reject before inference"}

def preprocess(image):
    with Image.open(image) as original:
        if original.format not in SPEC["formats"]:
            raise ValueError("Unsupported image format")
        original.load()
        rgb = original.convert("RGB")
    w, h = rgb.size
    if min(w, h) <= 0:
        raise ValueError("Empty image")
    if (w <= h and w != 248) or (h < w and h != 248):
        size = (248, int(248 * h / w)) if w <= h else (int(248 * w / h), 248)
        rgb = rgb.resize(size, resample=Image.Resampling.BICUBIC)
    w, h = rgb.size
    left, top = round((w - 224) / 2), round((h - 224) / 2)
    rgb = rgb.crop((left, top, left + 224, top + 224))
    x = np.asarray(rgb, dtype=np.uint8).transpose(2, 0, 1).astype(np.float32) / np.float32(255)
    x = (x - np.array(SPEC["mean"], dtype=np.float32)[:, None, None]) / np.array(SPEC["std"], dtype=np.float32)[:, None, None]
    return np.ascontiguousarray(x[None], dtype=np.float32)
