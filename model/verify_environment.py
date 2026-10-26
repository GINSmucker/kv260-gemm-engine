"""Offline environment checks: synthetic tensors only, no pretrained model or board."""
import importlib.metadata
import json
import sys


def main():
    if sys.prefix == sys.base_prefix:
        raise RuntimeError("Run this script with h576_sparse24/.venv/Scripts/python.exe")

    expected = {
        "numpy": "1.26.4",
        "onnx": "1.17.0",
        "onnxruntime": "1.22.1",
        "Pillow": "12.3.0",
        "timm": "1.0.20",
        "safetensors": "0.8.0",
        "torch": "2.12.1+cpu",
        "torchvision": "0.27.1+cpu",
    }
    versions = {name: importlib.metadata.version(name) for name in expected}
    for name, version in expected.items():
        if versions[name] != version:
            raise RuntimeError(f"{name}: expected {version}, got {versions[name]}")

    import numpy as np
    import onnx
    import onnxruntime as ort
    import PIL.Image
    import safetensors.torch
    import timm
    import torch
    import torchvision

    if torch.version.cuda is not None:
        raise RuntimeError("Expected the CPU-only PyTorch build")

    # Verify NumPy interchange, CPU matrix multiplication and autograd.
    x = np.array([[1, 2, 3], [-2, 0, 4]], dtype=np.float32)
    w = np.array([[2, -1], [0, 3], [-1, 2]], dtype=np.float32)
    golden = x @ w
    tx = torch.from_numpy(x.copy()).requires_grad_(True)
    tw = torch.from_numpy(w.copy()).requires_grad_(True)
    result = tx @ tw
    np.testing.assert_array_equal(result.detach().numpy(), golden)
    result.sum().backward()
    if tx.grad is None or tw.grad is None:
        raise RuntimeError("CPU autograd check failed")

    # Test ONNX export representation and ORT execution without a model download.
    graph = onnx.helper.make_graph(
        [onnx.helper.make_node("MatMul", ["X", "W"], ["Y"])],
        "environment_matmul",
        [onnx.helper.make_tensor_value_info("X", onnx.TensorProto.FLOAT, [2, 3])],
        [onnx.helper.make_tensor_value_info("Y", onnx.TensorProto.FLOAT, [2, 2])],
        [onnx.numpy_helper.from_array(w, name="W")],
    )
    model = onnx.helper.make_model(
        graph, opset_imports=[onnx.helper.make_opsetid("", 17)], ir_version=9
    )
    onnx.checker.check_model(model)
    session = ort.InferenceSession(
        model.SerializeToString(), providers=["CPUExecutionProvider"]
    )
    np.testing.assert_array_equal(session.run(None, {"X": x})[0], golden)

    # In-memory weight serialization and image preprocessing primitives.
    blob = safetensors.torch.save({"weight": tw.detach().contiguous()})
    torch.testing.assert_close(safetensors.torch.load(blob)["weight"], tw.detach())
    image = PIL.Image.new("RGB", (256, 256))
    if image.resize((224, 224)).size != (224, 224):
        raise RuntimeError("Pillow resize check failed")

    print(json.dumps({
        "status": "PASS",
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "versions": versions,
        "checks": [
            "imports_including_timm_and_torchvision",
            "numpy_torch_cpu_matmul",
            "torch_cpu_backward",
            "onnx_checker_and_ort_cpu_matmul",
            "safetensors_in_memory_roundtrip",
            "pillow_resize",
        ],
        "cuda_build": torch.version.cuda,
        "model_downloaded": False,
        "board_accessed": False,
        "pruning_started": False,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
