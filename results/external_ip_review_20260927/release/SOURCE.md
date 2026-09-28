# Original GEMM IP source

- Repository: `GINSmucker/kv260-gemm-engine`.
- Release: `v0.1.0`, commit `206b8ce3273e1afbe974c114f5c90d52c5d6a394`.
- Asset: `gemm_top.zip`, 172768 bytes.
- SHA-256: `f40f4178d66ae47f4362544ded71557a3f783b615ec4ba771804d816d781b521`.
- `ip/` is the byte-preserving extraction of that asset; no GEMM RTL edits.

The dated path is retained because the hardware build script and release-contract test use it. It is a pinned build input, not a new release or a mutable research checkout.
During each isolated system build, only three `HAS_BURST` metadata values in the copied `component.xml` are changed from 0 to 1 to agree with the published INCR-burst RTL. The archive and `ip/` remain unchanged.
Run `python scripts/verify_f8_delivery.py` to verify the archive, extraction and tested delivery without accessing hardware.
