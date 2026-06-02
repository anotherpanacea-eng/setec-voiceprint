<#
.SYNOPSIS
    Generic Tier-4 (surprisal + Binoculars + integrated compression) runner for
    SETEC, GPU-aware, for Windows. No corpus/manuscript baked in -- pass your own.

.DESCRIPTION
    Runs SETEC's Tier-4 audits against an arbitrary text file:
      [gate]  optional gpt2 validity gate on a short excerpt (catches numerically
              broken GPU backends -- e.g. DirectML, which reports plausible-looking
              but wrong surprisal; ROCm/CUDA/MPS/CPU pass)
      [1]     surprisal_audit.py   -> <stem>_Surprisal_Tier4_<tag>.json
      [2]     binoculars_audit.py  -> <stem>_Binoculars_<tag>.{json,md}   (unless -NoBinoculars)
      [3]     variance_audit.py --tier4 (integrated compression call)      (only with -Variance)

    GPU selection: on a multi-GPU box (e.g. an AMD APU iGPU + a discrete card) the
    integrated GPU is often device 0 and crashes on kernel launch under
    ROCm-on-Windows. This runner auto-detects the discrete GPU (largest VRAM, not an
    APU) and masks everything else via HIP_VISIBLE_DEVICES / CUDA_VISIBLE_DEVICES so
    the chosen device becomes cuda:0. Override with -GpuIndex, or force CPU with -Cpu.

    Tier-4 bands are PROVISIONAL per SETEC policy: treat output as a MEASUREMENT,
    not a verdict.

.PARAMETER Manuscript
    Path to the .txt/.md file to audit. (Required.)

.PARAMETER OutDir
    Directory for output JSON/MD. Created if absent. (Required.)

.PARAMETER Python
    Path to the python.exe to use. On AMD/Windows this should be your ROCm
    (TheRock) venv python, which has a working GPU torch + transformers + numpy.
    Default: "python" on PATH.

.PARAMETER Model
    Surprisal model alias or HF id (gpt2, tinyllama, llama32_1b, ...). Default tinyllama.

.PARAMETER ScriptsDir
    Directory holding surprisal_audit.py etc. Default: the parent of this script
    (i.e. the repo's scripts/ dir), so the runner uses its co-located audit scripts.

.PARAMETER GpuIndex
    Physical GPU index to use. -1 (default) = auto-detect the discrete GPU.

.PARAMETER Cpu
    Force CPU (hide all GPUs). Slow but always correct.

.PARAMETER Excerpt
    Audit only the first -ExcerptChars characters (fast first look).

.PARAMETER Variance
    Also run variance_audit.py --tier4 (the integrated 0/N compression call).

.PARAMETER NoBinoculars
    Skip the Binoculars audit.

.PARAMETER NoGate
    Skip the gpt2 validity gate.

.PARAMETER Tag
    Filename tag for outputs. Default: today (yyyy-MM-dd).

.PARAMETER Name
    Output basename override. Default: the manuscript filename stem. Use this to
    pin output names independent of the input filename.

.EXAMPLE
    .\run_tier4.ps1 -Manuscript C:\book.md -OutDir C:\out `
        -Python D:\Code\my-rocm-venv\Scripts\python.exe -Variance

.EXAMPLE
    .\run_tier4.ps1 -Manuscript C:\book.md -OutDir C:\out -Model gpt2 -Excerpt -NoBinoculars
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Manuscript,
    [Parameter(Mandatory = $true)][string]$OutDir,
    [string]$Python = "python",
    [string]$Model = "tinyllama",
    [string]$ScriptsDir,
    [int]$GpuIndex = -1,
    [switch]$Cpu,
    [switch]$Excerpt,
    [int]$ExcerptChars = 20000,
    [switch]$Variance,
    [switch]$NoBinoculars,
    [switch]$NoGate,
    [string]$Scorer = "tinyllama",
    [string]$Observer = "gpt2",
    [double]$GateMeanMin = 1.0,
    [double]$GateMeanMax = 12.0,
    [string]$Tag = (Get-Date -Format 'yyyy-MM-dd'),
    [string]$Name
)

$ErrorActionPreference = 'Stop'

function Say([string]$m) { Write-Host ">> $m" }

# Native-process helpers. In Windows PowerShell 5.1, a native exe writing to
# stderr while $ErrorActionPreference='Stop' is promoted to a terminating
# NativeCommandError even on exit 0 -- and these audits emit benign warnings
# (tokenizer >1024, torch_dtype deprecation) to stderr. So drop to 'Continue'
# around the call and gate on $LASTEXITCODE ourselves.
function Invoke-NativeShown([string]$Exe, [string[]]$ScriptArgs, [string]$Label) {
    if ($Label) { Say $Label }
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    # render stdout + any residual stderr as plain text (ErrorRecord.ToString()
    # is just the message, not the red "At line.../CategoryInfo" wrapper).
    try { & $Exe @ScriptArgs 2>&1 | ForEach-Object { Write-Host ($_.ToString()) }; $code = $LASTEXITCODE }
    finally { $ErrorActionPreference = $prev }
    if ($code -ne 0) { throw "$Label FAILED (exit $code)" }
}
function Invoke-NativeCaptured([string]$Exe, [string[]]$ScriptArgs) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $out = & $Exe @ScriptArgs 2>$null; $code = $LASTEXITCODE }
    finally { $ErrorActionPreference = $prev }
    return [pscustomobject]@{ Out = $out; Code = $code }
}

# ---- resolve & validate inputs ---------------------------------------------
if (-not $ScriptsDir) { $ScriptsDir = Split-Path -Parent $PSScriptRoot }   # runners/ -> scripts/
$Manuscript = (Resolve-Path -LiteralPath $Manuscript).Path
if (-not (Test-Path -LiteralPath $Manuscript)) { throw "Manuscript not found: $Manuscript" }
foreach ($s in @('surprisal_audit.py','binoculars_audit.py','variance_audit.py')) {
    if (-not (Test-Path -LiteralPath (Join-Path $ScriptsDir $s))) { throw "Missing $s in ScriptsDir: $ScriptsDir" }
}
if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
$OutDir = (Resolve-Path -LiteralPath $OutDir).Path
# output basename: -Name override, else the manuscript filename stem.
$stem = if ($Name) { $Name } else { [System.IO.Path]::GetFileNameWithoutExtension($Manuscript) }

# keep HuggingFace weights wherever they already are (do NOT override HF_HOME).
$env:PYTHONUTF8 = '1'
# quiet transformers' benign chatter (>1024 tokenizer notice, torch_dtype/logits
# deprecations) so they don't surface as scary red stderr in the console.
$env:TRANSFORMERS_VERBOSITY = 'error'
$env:TOKENIZERS_PARALLELISM = 'false'

Say "manuscript : $Manuscript"
Say "out dir    : $OutDir"
Say "scripts    : $ScriptsDir"
Say "python     : $Python"
Say "model      : $Model   tag: $Tag"

# ---- GPU selection ----------------------------------------------------------
# Clear any inherited masks so detection sees all devices.
Remove-Item Env:HIP_VISIBLE_DEVICES  -ErrorAction SilentlyContinue
Remove-Item Env:CUDA_VISIBLE_DEVICES -ErrorAction SilentlyContinue
Remove-Item Env:SETEC_SURPRISAL_DEVICE -ErrorAction SilentlyContinue

if ($Cpu) {
    Say "device     : CPU (forced) -- hiding all GPUs"
    $env:CUDA_VISIBLE_DEVICES = '-1'
    $env:HIP_VISIBLE_DEVICES  = ''
}
else {
    if ($GpuIndex -lt 0) {
        # auto-detect the discrete GPU (largest VRAM, de-prioritising APUs/iGPUs).
        $probe = @'
import sys
try:
    import torch
except Exception as e:
    print("SETEC_GPU_INDEX=cpu"); print("probe: torch import failed: %r" % (e,)); sys.exit(0)
if (not torch.cuda.is_available()) or torch.cuda.device_count() == 0:
    print("SETEC_GPU_INDEX=cpu"); sys.exit(0)
best_i, best_score, best_desc = -1, None, ""
for i in range(torch.cuda.device_count()):
    try:
        p = torch.cuda.get_device_properties(i)
        name = (p.name or ""); arch = (getattr(p, "gcnArchName", "") or "")
        mem = int(getattr(p, "total_memory", 0))
    except Exception as e:
        print("probe: dev %d props failed: %r" % (i, e)); continue
    score = float(mem); low = name.lower()
    # APUs/integrated parts (RDNA2/3 iGPU) frequently fault on kernel launch under
    # ROCm-on-Windows; push them to the bottom so a discrete card wins.
    if ("(tm) graphics" in low) or ("integrated" in low): score -= 1e15
    if arch.startswith("gfx103") or arch.startswith("gfx90c"): score -= 1e15
    if (best_score is None) or (score > best_score):
        best_i, best_score, best_desc = i, score, "%s [%s, %.1fGB]" % (name, arch, mem/1024**3)
print("SETEC_GPU_INDEX=%d" % best_i)
print("probe: selected physical device %d = %s" % (best_i, best_desc))
'@
        $probePath = Join-Path ([System.IO.Path]::GetTempPath()) ("setec_gpu_probe_{0}.py" -f $PID)
        [System.IO.File]::WriteAllText($probePath, $probe, [System.Text.UTF8Encoding]::new($false))
        try   { $probeOut = (Invoke-NativeCaptured $Python @($probePath)).Out }
        finally { Remove-Item -LiteralPath $probePath -ErrorAction SilentlyContinue }
        $line = $probeOut | Where-Object { $_ -match 'SETEC_GPU_INDEX=' } | Select-Object -Last 1
        $desc = $probeOut | Where-Object { $_ -match '^probe: selected' } | Select-Object -Last 1
        if (-not $line) { throw "GPU auto-detect failed (no SETEC_GPU_INDEX in probe output). Pass -GpuIndex or -Cpu." }
        $val = ($line -split '=')[-1].Trim()
        if ($val -eq 'cpu') { $GpuIndex = -1; Say "device     : no GPU detected -> CPU" }
        else { $GpuIndex = [int]$val; if ($desc) { Say "device     : auto -> $desc (physical index $GpuIndex)" } }
    }
    else {
        Say "device     : GPU physical index $GpuIndex (explicit)"
    }
    if ($GpuIndex -ge 0) {
        # Mask so ONLY the chosen device is visible; it becomes cuda:0.
        $env:HIP_VISIBLE_DEVICES   = "$GpuIndex"
        $env:CUDA_VISIBLE_DEVICES  = "$GpuIndex"
        $env:SETEC_SURPRISAL_DEVICE = 'cuda:0'
    }
}

# ---- prepare target (full or excerpt) --------------------------------------
$target = $Manuscript
$tmpExcerpt = $null
if ($Excerpt) {
    $tmpExcerpt = Join-Path ([System.IO.Path]::GetTempPath()) ("setec_excerpt_{0}.md" -f $PID)
    $raw = [System.IO.File]::ReadAllText($Manuscript)
    $n = [Math]::Min($ExcerptChars, $raw.Length)
    [System.IO.File]::WriteAllText($tmpExcerpt, $raw.Substring(0, $n), [System.Text.UTF8Encoding]::new($false))
    $target = $tmpExcerpt
    Say "excerpt    : first $n chars -> $tmpExcerpt"
}

try {
    # ---- [gate] gpt2 validity gate -----------------------------------------
    if (-not $NoGate) {
        $gateExcerpt = Join-Path ([System.IO.Path]::GetTempPath()) ("setec_gate_{0}.md" -f $PID)
        $gateJson    = Join-Path ([System.IO.Path]::GetTempPath()) ("setec_gate_{0}.json" -f $PID)
        $raw = [System.IO.File]::ReadAllText($Manuscript)
        $n = [Math]::Min(20000, $raw.Length)
        [System.IO.File]::WriteAllText($gateExcerpt, $raw.Substring(0, $n), [System.Text.UTF8Encoding]::new($false))
        try {
            Invoke-NativeShown $Python @((Join-Path $ScriptsDir 'surprisal_audit.py'), $gateExcerpt,
                '--model','gpt2','--sliding-window','--json','--out',$gateJson) `
                "[gate] gpt2 surprisal validity check on a 20k-char excerpt"
            $g = Get-Content -Raw -LiteralPath $gateJson | ConvertFrom-Json
            $mean = [double]$g.results.summary.mean_surprisal_bits
            if ([double]::IsNaN($mean) -or $mean -lt $GateMeanMin -or $mean -gt $GateMeanMax) {
                throw ("VALIDITY GATE FAILED: gpt2 mean surprisal = {0:N3} bits, outside [{1},{2}]. " -f $mean,$GateMeanMin,$GateMeanMax) +
                      "The GPU backend is likely producing numerically broken surprisal (cf. DirectML). " +
                      "Re-run with -Cpu, or fix the GPU torch build. (Skip this check with -NoGate.)"
            }
            Say ("[gate] OK -- gpt2 mean surprisal {0:N3} bits (within [{1},{2}])" -f $mean,$GateMeanMin,$GateMeanMax)
        }
        finally {
            Remove-Item -LiteralPath $gateExcerpt,$gateJson -ErrorAction SilentlyContinue
        }
    }

    # ---- [1] surprisal ------------------------------------------------------
    $surOut = Join-Path $OutDir ("{0}_Surprisal_Tier4_{1}.json" -f $stem,$Tag)
    Invoke-NativeShown $Python @((Join-Path $ScriptsDir 'surprisal_audit.py'), $target,
        '--model',$Model,'--sliding-window','--json','--out',$surOut) `
        "[1] surprisal audit (model=$Model)"
    Say "    wrote $surOut"

    # ---- [2] binoculars -----------------------------------------------------
    if (-not $NoBinoculars) {
        $binJson = Join-Path $OutDir ("{0}_Binoculars_{1}.json" -f $stem,$Tag)
        $binMd   = Join-Path $OutDir ("{0}_Binoculars_{1}.md"   -f $stem,$Tag)
        Invoke-NativeShown $Python @((Join-Path $ScriptsDir 'binoculars_audit.py'), $target,
            '--scorer',$Scorer,'--observer',$Observer,'--out',$binJson,'--out-md',$binMd) `
            "[2] Binoculars audit (scorer=$Scorer, observer=$Observer)"
        Say "    wrote $binJson + .md"
    }

    # ---- [3] integrated compression (variance --tier4) ----------------------
    if ($Variance) {
        $varOut = Join-Path $OutDir ("{0}_Variance_Tier4_{1}.json" -f $stem,$Tag)
        Say "[3] variance audit --tier4 (integrated compression call, model=$Model)"
        # variance_audit.py has no --out; --json prints to stdout. Capture and
        # write as UTF-8 (no BOM) so downstream json.load() is happy.
        $vr = Invoke-NativeCaptured $Python @((Join-Path $ScriptsDir 'variance_audit.py'), $target, '--tier4', '--surprisal-model', $Model, '--json')
        if ($vr.Code -ne 0) { throw "variance audit FAILED (exit $($vr.Code))" }
        [System.IO.File]::WriteAllText($varOut, (($vr.Out) -join "`n"), [System.Text.UTF8Encoding]::new($false))
        Say "    wrote $varOut"
    }
}
finally {
    if ($tmpExcerpt) { Remove-Item -LiteralPath $tmpExcerpt -ErrorAction SilentlyContinue }
}

Say "done. Outputs in: $OutDir"
Get-ChildItem -LiteralPath $OutDir -Filter "$stem*$Tag*" | Select-Object Length, Name | Format-Table -AutoSize
