# RUNBOOK: Tier-4 surprisal backend install (cross-platform)

**Audience**: an operator who wants to enable Tier 4 (surprisal)
metrics — either to run `variance_audit.py --tier4` on a manuscript,
to run the C.3 standalone `surprisal_audit.py`, or to calibrate
load-bearing Tier-4 thresholds against a local corpus.

**Scope**: picking and installing the right PyTorch wheel for the
host's accelerator. The framework supports five reasonable paths;
this RUNBOOK walks each one and documents the fallback ladder when
the preferred path fails. The `requirements-surprisal.txt` file
ships the pinned `transformers` / `tokenizers` / `torch` versions
that layer on top of whichever wheel you pick here.

**Out of scope**: per-OS Python install (use your distro's package
manager or `pyenv`); HuggingFace auth for gated weights (Llama 3.2
1B needs an HF token; the four other §6.4 candidates do not); the
calibration toolchain itself (see `RUNBOOK_multi_machine_sync.md`
and `launchd/RUNBOOK_macos_nightly.md`).

**Time to install**: 5-10 minutes on a well-supported path (MPS,
CUDA, CPU-only); 30-60 minutes on a less-well-supported path (WSL2
+ ROCm fresh from a Windows-only host).

---

## 0. Decision table

| Host                          | Python   | Backend           | Path |
|-------------------------------|----------|-------------------|------|
| Apple Silicon (M1/M2/M3/M4)   | 3.10-3.12| MPS               | C    |
| NVIDIA GPU on Linux           | 3.10-3.12| CUDA 12.x         | B    |
| NVIDIA GPU on Windows         | 3.10-3.12| CUDA 12.x (native)| B    |
| AMD GPU on Linux (native)     | 3.10-3.12| ROCm 6.x          | A    |
| AMD GPU on Windows            | 3.10-3.12| WSL2 + ROCm 6.x   | A    |
| AMD GPU on Windows (no WSL2)  | 3.10-3.12| torch-directml    | D    |
| Intel iGPU / discrete on Win  | 3.10-3.12| torch-directml    | D    |
| No GPU, or unsupported GPU    | 3.10-3.13| CPU-only          | E    |

**Python version constraint (2026-05)**: torch wheels for accelerators
lag the Python release cycle by ~6 months. As of this writing:

  * **Python 3.10, 3.11, 3.12**: all five backends have wheels.
  * **Python 3.13**: only CPU-only and the very latest MPS wheels
    have shipped. ROCm 6.x and CUDA 12.x wheels for 3.13 are
    incomplete. **Recommend 3.11 or 3.12** for any accelerator path.
  * **Python 3.9 and earlier**: out of support for current `torch`;
    don't go below 3.10.

If your distro defaults to 3.13 (recent Ubuntu, recent Homebrew),
install a 3.11 or 3.12 alongside it via `pyenv`, `uv`, or the
distro's alt-Python package. Don't fight the wheel-availability
gap.

---

## 1. Path A — AMD GPU via ROCm 6.x (Linux native or WSL2)

This is the **recommended path for AMD GPUs**. ROCm is AMD's
CUDA-equivalent runtime. PyTorch's ROCm wheel runs CUDA-API code
unmodified — `torch.cuda.is_available()` returns `True` and the
framework's surprisal backend uses it transparently.

### 1.1 On native Linux

```bash
# 1. Install ROCm 6.x from AMD's repo. Ubuntu 22.04 / 24.04 example:
wget https://repo.radeon.com/amdgpu-install/6.2/ubuntu/jammy/amdgpu-install_6.2.60200-1_all.deb
sudo apt install ./amdgpu-install_6.2.60200-1_all.deb
sudo amdgpu-install --usecase=rocm

# 2. Add your user to the render + video groups (required for /dev/kfd):
sudo usermod -aG render,video $USER
# Log out + back in for group membership to take effect.

# 3. Verify ROCm sees the GPU:
rocminfo | grep gfx
# Should list at least one Agent with a gfx<NNN> name.

# 4. Install the ROCm PyTorch wheel.
pip install --index-url https://download.pytorch.org/whl/rocm6.0 torch

# 5. Verify torch sees the GPU.
python3 -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# Expect: True <gfx string>
```

### 1.2 On Windows via WSL2

This is the path users with an AMD GPU on a Windows daily-driver
should take. Native AMD-on-Windows ROCm is not supported by PyTorch
upstream (it's roadmap, not yet shipped). WSL2 gives you a real
Linux kernel + GPU passthrough.

```powershell
# 0. (Windows side, once.) WSL2 with GPU passthrough requires
#    Windows 11 22H2+ and a recent AMD driver. Update the AMD
#    Software ("Adrenalin") to the latest stable build first.
wsl --install -d Ubuntu-22.04
# Reboot if prompted.

# 1. Launch WSL Ubuntu and verify GPU passthrough is live:
#    (from inside the WSL Ubuntu shell)
ls /dev/dri
# Expect: card0 renderD128 (the dri devices proxied from Windows).
```

Inside the WSL Ubuntu shell, follow the same steps as §1.1 from
step 1 onward. ROCm 6.2 is the first version with reliable WSL2
support — older ROCm releases may install but fail at first
`torch.cuda.is_available()` call.

### 1.3 GPU support matrix

ROCm doesn't support every AMD GPU. The `rocminfo` output names
your GPU's `gfx` architecture; cross-reference against
[ROCm's hardware support matrix](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/reference/system-requirements.html).

Known-good gfx targets (2026-05): `gfx900`, `gfx906`, `gfx908`,
`gfx90a`, `gfx940`, `gfx941`, `gfx942`, `gfx1030`, `gfx1100`,
`gfx1101`, `gfx1102`. Consumer 7000-series RDNA3 (`gfx1100/1101`)
works; consumer RDNA2 (`gfx1030`) works; older RDNA1
(`gfx1010/1011/1012`) does NOT (fall back to Path D or E).

If `rocminfo` lists your GPU but PyTorch ignores it, try setting
`HSA_OVERRIDE_GFX_VERSION=11.0.0` (or `10.3.0` for RDNA2) before
launching Python. Some consumer cards report a gfx string that's
slightly off from the supported version; the override forces a
match.

### 1.4 Layer the framework deps

```bash
# From the SETEC repo root:
pip install -r plugins/setec-voiceprint/requirements-surprisal.txt
```

This installs `transformers` + `tokenizers` on top of the ROCm
torch wheel. pip will see torch is already satisfied and skip it.

---

## 2. Path B — NVIDIA CUDA 12.x

The well-trodden path. PyTorch publishes a wheel against each
shipped CUDA minor version; pick the one matching your installed
CUDA toolkit (or skip the toolkit install entirely — the torch
wheel bundles the runtime libraries it needs).

```bash
# Linux or Windows native, same command:
pip install --index-url https://download.pytorch.org/whl/cu121 torch

# Verify:
python3 -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# Expect: True <NVIDIA card name>
```

If `torch.cuda.is_available()` returns `False`:

  * Check the driver version: `nvidia-smi` should show a driver
    that supports CUDA 12.x (driver >= 525). Older drivers need
    the `cu118` wheel index instead.
  * On Linux, check that the user has access to `/dev/nvidia*`.
  * On WSL2, ensure the NVIDIA Windows driver is recent and the
    WSL CUDA runtime is installed (`sudo apt install nvidia-cuda-toolkit`
    inside WSL).

Then layer the framework deps:

```bash
pip install -r plugins/setec-voiceprint/requirements-surprisal.txt
```

---

## 3. Path C — Apple Silicon MPS

The simplest path of all five. Apple's Metal Performance Shaders
backend is included in the default `torch` wheel; no index override.

```bash
pip install torch

# Verify:
python3 -c "import torch; print(torch.backends.mps.is_available())"
# Expect: True

pip install -r plugins/setec-voiceprint/requirements-surprisal.txt
```

**Caveat**: PyTorch's MPS backend doesn't yet implement every
op the CUDA backend does. On a model that hits an unimplemented
op you'll see `NotImplementedError: The operator 'aten::<foo>' is
not currently implemented for the MPS device.` Workaround: set
`PYTORCH_ENABLE_MPS_FALLBACK=1` in the environment to fall those
ops back to CPU. The framework's surprisal backend doesn't trigger
this with TinyLlama / GPT-2 small as of 2026-05; with Phi-3 Mini
you may hit it occasionally.

---

## 4. Path D — torch-directml (Windows cross-vendor fallback)

When ROCm install collapses (consumer AMD card not on the support
list, WSL2 unavailable on a corporate-locked Windows host) or you
have an Intel GPU, `torch-directml` is the escape hatch. It runs
PyTorch ops through DirectX 12 on any DX12-capable GPU — AMD,
Intel, or NVIDIA, all vendors via one API.

```powershell
# Windows native, no WSL needed:
pip install torch-directml
```

**Important limitation as of v1.59.x**: the framework's
`surprisal_backend.py` doesn't yet wire DirectML in automatically.
It calls `model(input_ids)` without a device move, which sends
work to CPU even when DirectML is installed. To use Path D today
you need to either:

  * Wait for the DirectML support that's on the roadmap, or
  * Patch `surprisal_backend._load` locally to call
    `self._model.to(torch_directml.device())` and similarly move
    `input_ids` before the forward pass.

If you patch locally, leave a note in your PROVENANCE block — the
`identifier_block()` won't reflect that DirectML was used unless
you also extend it.

Until the framework's DirectML support lands, treat Path D as
"works for ad-hoc smoke tests; doesn't yet integrate cleanly into
`variance_audit.py --tier4`." Prefer Path A (WSL2 + ROCm) for AMD
on Windows.

---

## 5. Path E — CPU-only

The universal fallback. Always works, always slow.

```bash
pip install --index-url https://download.pytorch.org/whl/cpu torch
pip install -r plugins/setec-voiceprint/requirements-surprisal.txt
```

**Performance expectations**: on a modern x86 desktop (Ryzen 5800X
or Intel i7-12700K class), TinyLlama 1.1B teacher-forced inference
runs at roughly **20-100 tokens/sec** depending on AVX-512 / AVX2
availability. That's tractable for:

  * Single-manuscript audits (a 100K-word manuscript ≈ 130K tokens
    ≈ 20-100 minutes).
  * Sample-size Tier-4 calibration (10K RAID rows at ~500 tokens
    each ≈ 1-3 hours).

It's impractical for:

  * Full RAID-scale calibration (8M rows ≈ 5-50 *days* on CPU vs
    hours on a GPU).
  * Large-model candidates (Phi-3 Mini 3.8B is ~3x slower than
    TinyLlama on CPU; Llama 3.2 1B is similar to TinyLlama).

For full-corpus calibration, prefer any GPU path. For
single-manuscript audits, CPU is fine.

---

## 6. Smoke test (all paths)

Copy-paste this after install to confirm the full Tier-4 chain
works end-to-end. Loads TinyLlama, scores one sentence, prints
the surprisal series.

```python
# smoke_test_tier4.py
from surprisal_backend import SurprisalBackend

backend = SurprisalBackend(model_id="tinyllama")
text = "The quick brown fox jumps over the lazy dog."
series = backend.score_text(text)
print(f"tokens scored: {len(series)}")
print(f"mean surprisal (bits): {sum(series)/len(series):.2f}")
print(f"identifier_block: {backend.identifier_block()}")
```

Run from the SETEC repo:

```bash
cd plugins/setec-voiceprint/scripts
python3 smoke_test_tier4.py
```

**Expected output** (first run): the model downloads (~500 MB for
TinyLlama; one-time, cached at `~/.cache/huggingface/hub/`), then:

```
tokens scored: 10  (give or take, depending on tokenizer)
mean surprisal (bits): 6.21  (give or take)
identifier_block: {'id': 'TinyLlama/TinyLlama-1.1B-...', 'revision': None, 'alias': 'tinyllama', 'deterministic_mode': True, 'method': 'transformers-causal-lm'}
```

**Failure modes**:

  * `SurprisalBackendError: transformers is not installed` → the
    framework deps layer wasn't installed. Run
    `pip install -r requirements-surprisal.txt`.
  * `OSError: We couldn't connect to 'https://huggingface.co'` →
    network issue or HF rate-limit. Retry after a few minutes,
    or set `HF_HUB_OFFLINE=1` if you've pre-cached the model.
  * `RuntimeError: HIP error: ...` (Path A) → ROCm runtime mismatch.
    Check `rocminfo` and the gfx-override env var (§1.3).
  * `RuntimeError: CUDA out of memory` (Path B, large model) →
    install `accelerate` (uncomment in `requirements-surprisal.txt`)
    and reload at fp16. TinyLlama and GPT-2 small don't need this.

---

## 7. Fallback ladder

When the preferred path fails partway through, the ladder is:

  1. **Preferred GPU path** (A, B, or C). Best perf.
  2. **CPU-only** (Path E). Always works; slow but tractable for
     sample-size work.
  3. **Smaller model**. If memory is the constraint, switch from
     a 1B+ candidate to GPT-2 small (124M). `--surprisal-model gpt2`
     loads in <1 GB.
  4. **Skip Tier 4**. The framework runs cleanly with Tier 1 + 2 + 3
     only; Tier 4 is opt-in. `variance_audit.py` without `--tier4`
     produces a complete report minus the surprisal block.

Don't burn an afternoon debugging ROCm on an unsupported consumer
card when CPU-only + a smaller model gets you to the same audit
in an hour.

---

## 8. Performance expectations (rough)

Single-text Tier-4 throughput, TinyLlama 1.1B, batch size 1
(the framework default):

| Backend                       | Tokens/sec |
|-------------------------------|------------|
| NVIDIA RTX 4090 (CUDA)        | 3000-6000  |
| NVIDIA RTX 3080 (CUDA)        | 1500-3000  |
| AMD 7900 XTX (ROCm)           | 2000-4000  |
| AMD 6800 XT (ROCm)            | 1000-2000  |
| Apple M3 Max (MPS)            | 800-1500   |
| Apple M1 (MPS)                | 300-600    |
| Modern x86 CPU (Path E, AVX2) | 30-80      |
| torch-directml (any GPU)      | ~50% of vendor-native (rough; varies wildly) |

These are order-of-magnitude figures from operator reports and
the §6.4 fixture suite. Your numbers will differ based on
power profile, thermal state, batch size, and model choice.
Larger candidates (Phi-3 Mini 3.8B) scale roughly inversely with
parameter count.

---

## 9. Common gotchas

### 9.1 Python 3.13 wheel gap

Installing on a fresh Python 3.13 will work for CPU-only and may
work for MPS, but ROCm and CUDA wheels for 3.13 are not yet
published as of 2026-05. If `pip install --index-url
https://download.pytorch.org/whl/rocm6.0 torch` reports "no
matching distribution found," you're on a too-new Python. Drop
to 3.11 or 3.12 via `pyenv`, `uv venv --python=3.12`, or your
distro's `python3.12` package.

### 9.2 `torch.cuda.is_available()` returns True on ROCm

This is expected, not a bug. PyTorch's ROCm wheel shims the CUDA
API onto HIP under the hood. The framework's surprisal backend
doesn't care; it just calls `model(...)` and lets torch route.

### 9.3 First load downloads weights

First call to `score_text()` triggers a download of the model
weights from HuggingFace (~500 MB for TinyLlama, ~2.5 GB for
Llama 3.2 1B, ~7.6 GB for Phi-3 Mini). Subsequent loads hit the
cache at `~/.cache/huggingface/hub/`. Pre-warm on a fast network
before taking the host offline.

### 9.4 HuggingFace gated weights

Of the five §6.4 candidates, only Llama 3.2 1B requires HF auth.
If you pick `--surprisal-model llama32_1b` you'll need:

```bash
pip install huggingface_hub
huggingface-cli login  # paste your HF token
```

The other four candidates (`tinyllama`, `gpt2`, `qwen25_1_5b`,
`phi3_mini`) load without auth.

### 9.5 Deterministic mode warnings

The backend enables `torch.use_deterministic_algorithms(True,
warn_only=True)` on first load (per SPEC §3.4). You may see a
warning like:

```
UserWarning: ... operator does not have a deterministic implementation...
```

This is `warn_only=True` behavior — the op falls back to a
non-deterministic implementation rather than crashing. Tier-4
surprisal series may differ slightly run-to-run on the affected
ops. If you need strict reproducibility for a load-bearing audit,
upgrade the offending warning to a hard error by toggling
`deterministic=False` and pinning random seeds at the call site
instead.

### 9.6 `torch` install size

A ROCm or CUDA torch wheel is **2-4 GB on disk** even before any
model weights. Plan disk space accordingly — `pip install torch`
on a small partition will fail mid-download with cryptic errors.

### 9.7 WSL2 GPU passthrough requires a recent driver

If `ls /dev/dri` inside WSL2 Ubuntu doesn't show `card0
renderD128`, your AMD Windows driver is too old. Update AMD
Adrenalin to a 2024-or-later release. Same applies to NVIDIA on
WSL2: needs a 2023+ Game Ready or Studio driver with WSL
support.

---

## 10. After install: where to go next

  * **Run a sample-size Tier-4 audit**: see §6 smoke test, then
    `variance_audit.py --tier4 path/to/manuscript.md > out.json`.
  * **Calibrate operational Tier-4 thresholds**: see
    `RUNBOOK_multi_machine_sync.md` and the calibration
    toolchain. The shipped Tier-4 bands in `COMPRESSION_HEURISTICS`
    are PROVISIONAL; load-bearing thresholds require local
    calibration on the operator's register mix.
  * **Pick an operational model**: run the §6.4 fixture suite
    (`internal/SPEC_surprisal_model_choice.md`) against your
    register mix to decide which of the five candidates is the
    best CLI default for your work. The framework ships
    `tinyllama` as the conservative default (smallest footprint,
    documented training cutoff), but the fixture-suite is the
    load-bearing decision for any audit you publish.
