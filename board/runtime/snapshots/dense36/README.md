# Original Dense versus Sparse24 on the same500 images

This isolated release evaluates the unpruned H576 Dense W8A8 reference
`f2f203362a337a357028b8b99c853cd39bdc812cf1da41775c9e233907d5f415`.
It uses the resident fd96539d OPT50 firmware in mode0 for all36 PL layers;
13 linear layers and the remaining graph execute on PS. No Vivado run,
firmware switch, reset, unload or old-record rewrite is included.

The original public H576 fused graph is retained byte-for-byte. Its191-node
topology matches the scheduling/fusions used in the aligned Sparse24 graph.
Offline original CPU4 versus independent standard-ONNX integer expansion
matches all3000 logits on two development inputs and zero. These are local
mathematical checks, not board acceptance.

The larger uncompressed PL weights require new native ARM compilation.
The plugin arithmetic and client/quantization/layout/GELU implementation are
unchanged; the plugin model-identity literal is rebound to original Dense.
Separate model catalog, Dense36 policy, capacities and private
execution namespace prevent accidental reuse of Sparse binaries or results.

Use the existing1800-image corpus, selecting ordinals0..499 only. RGB resize,
crop, normalization, W8A8 rounding, ORT4/1 threads, CPU affinity, BEGIN/END and
client synchronization remain as in the Sparse acceptance. Each model is
compared to its own CPU reference; logits from different weights may differ.

Each graph has36 Dense starts and0 Sparse starts. First5 closed bitwise-exact
images are the pilot, followed by bounded chunks of at most50 through500.
Every chunk uses a fresh900-second plan; consumed/uncertain runs never replay.
The original guardian, global companion lock, static/guard/drain checks and
normal owner exit requirements are retained. No CPU fallback exists.

The final report checks both models' saved logits and exact image/label/input
SHA identities. Full500 accuracy is paired. Primary latency comparison uses
the195 shared inputs306..500 recorded with the current Sparse PS revision;
full500 Sparse timings are descriptive because earlier versions differ.
This measures the original Dense versus trained Sparse model plus routing,
not sparse kernel speedup alone or full ImageNet validation accuracy.

Sparse500 evidence remains authoritative and unchanged at
`b24_acceptance500_reboot_exports_20261030_v1/action_a77e3fa15328`.
New records use `/var/lib/opt50-dense-model-v1` and
`/var/lib/opt50-dense-history-v1`; Sparse image counts are not incremented.
