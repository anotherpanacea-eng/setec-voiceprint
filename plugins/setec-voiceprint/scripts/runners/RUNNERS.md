# SETEC Tier-4 runners

Thin, parameterized wrappers that drive SETEC's Tier-4 audits
(`surprisal_audit.py`, `binoculars_audit.py`, `variance_audit.py --tier4`)
end-to-end against an arbitrary text file. No corpus is baked in — you pass the
target and output dir.

> **Tier-4 bands are PROVISIONAL** per the Stylometry-to-the-people policy.
> Treat all output as a *measurement*, not an authorship verdict.

## Files

| File | Platform | Purpose |
|---|---|---|
| `run_tier4.ps1` | Windows (PowerShell 5.1+) | GPU-aware runner: auto-selects a discrete GPU, validity-gates the backend, runs the three audits. |

(The repo also ships `requirements-surprisal.txt` and
`calibration/RUNBOOK_tier4_install.md` for installing a torch backend.)

## `run_tier4.ps1`

```powershell
# AMD / ROCm (e.g. Radeon RX 7900 XT via a TheRock venv) — full default pass:
.\run_tier4.ps1 -Manuscript C:\path\book.md -OutDir C:\out `
    -Python D:\Code\my-rocm-venv\Scripts\python.exe -Variance

# NVIDIA / CUDA venv:
.\run_tier4.ps1 -Manuscript C:\path\book.md -OutDir C:\out `
    -Python C:\venvs\cuda\Scripts\python.exe

# Fast first look (small model, 20k-char excerpt, surprisal only):
.\run_tier4.ps1 -Manuscript C:\path\book.md -OutDir C:\out `
    -Python ... -Model gpt2 -Excerpt -NoBinoculars

# CPU fallback (slow but always correct):
.\run_tier4.ps1 -Manuscript C:\path\book.md -OutDir C:\out -Python ... -Cpu
```

### Outputs (in `-OutDir`)

- `<name>_Surprisal_Tier4_<tag>.json`
- `<name>_Binoculars_<tag>.{json,md}`  (unless `-NoBinoculars`)
- `<name>_Variance_Tier4_<tag>.json`  (only with `-Variance`)

`<name>` defaults to the manuscript filename stem (override with `-Name`);
`<tag>` defaults to today (override with `-Tag`).

### Key parameters

| Param | Default | Notes |
|---|---|---|
| `-Manuscript` | (required) | `.txt`/`.md` to audit. |
| `-OutDir` | (required) | Created if absent. |
| `-Python` | `python` | **Point this at a venv whose torch has a working GPU backend.** |
| `-Model` | `tinyllama` | Surprisal model alias (`gpt2`, `tinyllama`, `llama32_1b`, …). |
| `-GpuIndex` | `-1` (auto) | Physical GPU index; auto picks the discrete card. |
| `-Cpu` | off | Force CPU (hides all GPUs). |
| `-Excerpt` / `-ExcerptChars` | off / 20000 | Audit only the first N chars. |
| `-Variance` | off | Also run the integrated compression call. |
| `-NoBinoculars` / `-NoGate` | off | Skip Binoculars / the validity gate. |

## Two design decisions worth knowing

### 1. Discrete-GPU auto-selection (the iGPU trap)

On a box with both an integrated GPU (AMD APU) and a discrete card, the iGPU is
often **device 0**. Under ROCm-on-Windows it enumerates fine but **faults on
kernel launch** (`0xC0000005` access violation) — so a naive `torch.device("cuda")`
crashes. The runner probes device properties (safe — no kernel), and picks the
*discrete* device with the most VRAM — explicitly excluding APUs/iGPUs (name
contains `(TM) Graphics`, or arch `gfx103x` / `gfx90c` / `gfx902`) — then masks
everything else via `HIP_VISIBLE_DEVICES` / `CUDA_VISIBLE_DEVICES` so the chosen
card becomes `cuda:0`.

If **no discrete GPU** is visible (e.g. an APU-only host, or one where every
visible device matches the integrated heuristic), auto-detect does **not** fall
back to the crashy integrated GPU — it forces **CPU** (hiding all devices) so the
backend can't grab the iGPU. Override either way with `-GpuIndex N` (target a
specific physical device, even an iGPU, at your own risk) or `-Cpu`.

### 2. Validity gate (don't trust a silently-wrong backend)

Some GPU backends compute *plausible-looking but numerically wrong* surprisal —
notably **DirectML**, observed giving gpt2 mean surprisal ≈ 16.85 bits when the
correct value is ≈ 4.4–4.7. Before the real run, the runner scores a 20k-char
gpt2 excerpt and aborts if the mean falls outside `[1, 12]` bits (tunable via
`-GateMeanMin`/`-GateMeanMax`). ROCm, CUDA, MPS and CPU pass this; broken
backends are caught. Skip with `-NoGate` only if you know your backend is sound.

## PowerShell 5.1 note

These audits print benign warnings to stderr (tokenizer `>1024`, `torch_dtype`
deprecation). In PS 5.1, native stderr under `$ErrorActionPreference='Stop'` is
promoted to a *terminating* `NativeCommandError` even on exit 0 — so the runner
drops to `Continue` around each native call and gates on `$LASTEXITCODE`, and
sets `TRANSFORMERS_VERBOSITY=error` to quiet the chatter.
