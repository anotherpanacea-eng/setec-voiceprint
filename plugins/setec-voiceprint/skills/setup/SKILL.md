---
name: setup
description: >
  First-run dependency check for the SETEC framework. Use when the user
  has just installed the plugin, asks about "setting up SETEC,"
  reports a "module not found" error from a SETEC script, asks "what
  does this plugin need to run," asks how to install spaCy / pypdf /
  ocrmypdf / transformers / torch for SETEC, asks why a feature isn't
  working, or asks for install commands. Also triggers on "missing
  dependency," "ImportError" from a setec-voiceprint script,
  "first-time setup," "is everything installed," "do I need anything
  else for SETEC," "Tier 4 surprisal," "Binoculars," "external mirror,"
  or any question about installing dependencies for Surface 5
  (discrimination evidence).
version: 1.1.0
---

# SETEC Setup Skill

This skill surveys the user's environment for the five SETEC dependency tiers, reports what's installed and what's missing, asks for permission, and runs the installs the user authorizes. The goal is to catch "module not found" errors up front rather than mid-pipeline.

The framework is opt-in by tier. A user running only smoothing-diagnosis doesn't need acquisition deps; a user running only Binoculars / Tier 4 surprisal doesn't need calibration deps. The skill identifies which tier(s) the user actually needs and proposes only those installs.

## What this skill licenses, and what it does not

- **Licenses:** running `dependency_check.py`, presenting its findings to the user, and — *only with explicit permission for each tier* — running the corresponding `pip install` / `python -m spacy download` / system-package-manager commands.
- **Does not license:** running `pip install` for an unspecified set of packages, modifying the user's Python environment without confirmation, downloading large models (e.g. SBERT) without surfacing the size cost, or upgrading core deps the user already has working.

The user owns the environment. The skill proposes; the user disposes.

## Tier model

| Tier | When needed | Install command |
|---|---|---|
| **Core stylometry** | Every diagnostic script (variance audit, voice distance, manifest validation, etc.). spaCy + en_core_web_sm + scipy + scikit-learn + statsmodels. | `pip install -r requirements.txt` then `python -m spacy download en_core_web_sm` |
| **Acquisition** | The five impostor-pool acquisition scripts (`acquire_blog`, `acquire_blogger_takeout`, `acquire_magazine`, `pdf_inventory`, `pdf_extract`). requests + feedparser + bs4 + lxml + python-dateutil + pypdf. | `pip install -r requirements-acquisition.txt` |
| **OCR (optional within acquisition)** | Only when extracting text from image-only / mixed PDFs. ocrmypdf + tesseract + ghostscript + qpdf. | `pip install ocrmypdf` plus the system-binary install for the user's platform (see below). |
| **Calibration** | Only when re-deriving thresholds from EditLens / RAID / MAGE. huggingface_hub + pyarrow. | `pip install -r requirements-calibration.txt` |
| **Surprisal (Tier 4 + Binoculars)** | Tier 4 surprisal signals in `variance_audit.py --tier4` and `surprisal_audit.py`; the Binoculars two-model perplexity audit (`binoculars_audit.py`, `binoculars_calibrate.py`). transformers + tokenizers + torch. **~1.5–2 GB on disk** with torch's CUDA wheels — flag the cost before installing. Does **not** cover `external_mirror/` — see the External-mirror note below the table. | `pip install -r requirements-surprisal.txt` |
| **Optional power-ups** | sentence-transformers (calibrated Tier 3 cohesion via SBERT AND the default sbert distance metric in `external_mirror/compute_distances.py` — required for external-mirror's default path, not optional there), textstat (better FKGL), nltk (Brown corpus for idiolect). All commented in `requirements.txt`. | `pip install <package>` per power-up. |

All `requirements-*.txt` files live under `plugins/setec-voiceprint/`. Root-level symlinks (`requirements.txt`, `requirements-acquisition.txt`, `requirements-calibration.txt`, `requirements-surprisal.txt`) point at them so the commands above work from the repo root or from the plugin directory.

**External-mirror dependency footprint.** `external_mirror/` is a Surface 5 tool but does NOT live on the Surprisal tier's transformers + torch stack. Its dependency profile depends on which distance metric the operator wants: (a) the default sbert metric needs **`sentence-transformers`** from the Optional power-ups row above — that's the load-bearing install for external-mirror's default behavior, and it pulls in torch transitively; (b) the v2 metric stack (TF-IDF + POS-bigram + word-set Jaccard) uses sklearn + spaCy from the **core tier** plus stdlib, no extra install needed. An operator who only wants the v2 metrics can run external-mirror with just the core tier; an operator who wants the default sbert behavior needs core + sentence-transformers. Routing external-mirror users at the Surprisal tier alone installs the wrong stack (transformers + tokenizers + torch, none of which compute_distances.py imports directly).

## Workflow

When invoked, follow this exact sequence:

### Step 0: Locate the user's existing baselines folder

Before any tier check, find out where the user's `ai-prose-baselines-private` folder lives. A fresh SETEC instance running inside a git worktree, or on a machine where the baselines are synced via Obsidian / iCloud / Dropbox, will not see a sibling private folder next to the repo — and acquisition scripts that fall back to creating one will silently diverge from the user's real corpus.

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/baseline_discovery.py"
```

The script reads state only; it does not create folders. Its output names every `ai-prose-baselines-private/` directory it found under common locations (repo sibling, `~/Documents`, Obsidian / Dropbox / Google Drive / OneDrive / iCloud roots, and `~/`), summarises each (manifest entries, impostor personas, size, last-modified), and either:

- Recommends one folder and prints the `export SETEC_BASELINES_DIR="..."` line the user should add to their shell rc, or
- Reports that no folder was found and explains what will happen on first use.

Surface this to the user before tier installs:

> SETEC found an existing baselines folder at `/path/to/.../ai-prose-baselines-private` (33.6 MiB, 592 manifest entries). To make sure every future SETEC session writes into the same place — even from a worktree or a different machine — add this to your shell rc:
>
> ```
> export SETEC_BASELINES_DIR="/path/to/.../ai-prose-baselines-private"
> ```
>
> Want me to confirm the line and add it to `~/.zshrc`?

If the script lists a duplicate folder (e.g., one inside the repo and one synced elsewhere), call that out explicitly so the user can decide whether to delete the extra. **Never delete a folder on the user's behalf** — the discovery script is non-destructive; that posture extends to follow-up actions.

If the script reports nothing found, ask the user whether they have a baselines folder anywhere SETEC didn't look (e.g., an external drive). If not, default creation under `~/Documents/ai-prose-baselines-private/` on first acquisition run is fine — but tell them that's what will happen.

### Step 1: Detect what the user is trying to do

Read the user's request to identify which tier(s) they likely need:

- "Set up SETEC" / first-run / "is everything installed" → **all tiers**.
- "ModuleNotFoundError" mentioning `requests`, `feedparser`, `bs4`, `lxml`, `dateutil`, `pypdf` → **acquisition tier**.
- "ModuleNotFoundError" mentioning `spacy`, `scipy`, `sklearn`, `statsmodels` → **core tier**.
- "ModuleNotFoundError" mentioning `ocrmypdf` or "OCR not working" / "image-only PDF" → **OCR sub-tier**.
- "ModuleNotFoundError" mentioning `huggingface_hub` / "calibrate thresholds" / "EditLens" → **calibration tier**.
- "ModuleNotFoundError" mentioning `torch` / `transformers` / `tokenizers`, or any of: "Tier 4 surprisal," "Binoculars," "perplexity ratio," "cross-perplexity," "surprisal_audit," "binoculars_audit," `variance_audit.py --tier4` → **surprisal tier**.
- "ModuleNotFoundError: sentence_transformers" from `compute_distances.py` / `embedding_backend.py` / external-mirror, or any of: "external mirror," "external-mirror," "compose_evidence_pack," "sbert distance," `external_mirror/workflow.py` → **optional tier** (sentence-transformers is required for external-mirror's default sbert metric, not just for Tier 3 cohesion).
- "Tier 3 cohesion" / "SBERT" / "sentence-transformers" / "Brown corpus" → **optional tier**.
- "discrimination evidence" alone is ambiguous between Binoculars (surprisal tier) and external-mirror (optional tier — sentence-transformers). Ask which tool the user is reaching for before proposing an install.

When unsure, default to surveying **all tiers** — the script is fast.

### Step 2: Run the survey

Invoke `dependency_check.py` to get the current state. Use `--suggest` if you only want install commands for what's missing:

```bash
# Full survey, human-readable:
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dependency_check.py"

# Restrict to one tier:
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dependency_check.py" --tier acquisition

# JSON output (for parsing inside this skill):
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dependency_check.py" --json

# Just the install commands the skill should propose:
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dependency_check.py" --suggest
```

The script reports state but never installs anything itself. Exit code 0 = all required deps present; exit code 1 = something required is missing.

### Step 3: Show findings to the user

Format the output for the user. Group by tier. Distinguish required (blocking) from optional (suggestions). Include the install command(s) for each tier the user needs.

A typical message:

> Here's what's missing for the **acquisition tier** (which `acquire_blog.py` etc. need):
> - `requests` — HTTP client
> - `feedparser` — RSS / Atom parsing
> - `beautifulsoup4` + `lxml` — HTML parsing
> - `python-dateutil` — date parsing
> - `pypdf` — PDF inventory + text extraction
>
> Install with:
> ```
> pip install -r plugins/setec-voiceprint/requirements-acquisition.txt
> ```
>
> Want me to run that?

### Step 4: Ask for permission per tier

Never bundle multiple tiers into one yes/no. Ask explicitly per tier the user might want skipped:

> 1. Core stylometry (4 packages + 1 spaCy model — needed for every diagnostic) — install? **y/n**
> 2. Acquisition (6 packages — needed for impostor-pool acquisition) — install? **y/n**
> 3. OCR (Python + 3 system binaries via Homebrew/apt/manual — only for image-only PDFs) — install? **y/n**
> 4. Calibration (2 packages — only for re-deriving thresholds) — install? **y/n**
> 5. Surprisal (Tier 4 + Binoculars) (3 packages — transformers + tokenizers + torch, **~1.5–2 GB on disk** — needed for Tier 4 surprisal and Binoculars; NOT external-mirror) — install? **y/n**
> 6. Optional power-ups (sentence-transformers ~2 GB unlocks SBERT Tier 3 cohesion AND external-mirror's default sbert distance metric; textstat tightens FKGL; nltk adds Brown reference corpus) — install which? **per-power-up y/n**

The user may say "yes to core only" or "yes to acquisition, skip OCR and Surprisal" — honor each granular choice. If the user wants external-mirror specifically, route them to the **Optional** ask above (sentence-transformers), not the Surprisal tier — those install different stacks. The Surprisal tier has the largest single install footprint; always name the disk cost when proposing it.

### Step 5: Run the installs the user authorized

Only the commands the user explicitly approved. For each:

1. Print the exact command before running it ("running: `pip install -r requirements.txt`").
2. Run it via the user's shell.
3. Capture the output and surface any errors to the user immediately.
4. If a step fails (network error, permission issue, conflicting versions), stop and ask the user how to proceed — don't silently skip and continue.

### Step 6: Verify

After the user-authorized installs complete, re-run `dependency_check.py` to verify the tier(s) the user installed are now green. Report the result.

## Platform-specific install hints

The dependency-check script's `--suggest` output is platform-aware. Here's the user-facing version:

### macOS

System binaries are easiest via Homebrew:

```bash
brew install tesseract ghostscript qpdf
```

If the user doesn't have Homebrew: `https://brew.sh` install instructions.

For Python deps, prefer `pip install --user` if the user isn't in a virtualenv (avoids permission issues). If they ARE in a venv, plain `pip install` is fine.

### Linux

Most distributions ship the OCR binaries via the package manager:

```bash
# Debian / Ubuntu:
sudo apt-get install tesseract-ocr ghostscript qpdf

# RHEL / Fedora:
sudo yum install tesseract ghostscript qpdf
```

### Windows

System binaries are the friction point. Two options:

1. **Chocolatey** (preferred, scriptable):
   ```
   choco install tesseract ghostscript qpdf
   ```

2. **Manual installers**:
   - tesseract: https://github.com/UB-Mannheim/tesseract/wiki
   - ghostscript: https://www.ghostscript.com/releases/
   - qpdf: https://qpdf.sourceforge.io/

Python deps install identically across platforms via `pip install`. The user must ensure they're using the same Python executable that will run SETEC scripts (a fresh `pip install` in a different Python's site-packages won't help).

## Safety rules

- **Never run `pip install` without a tier-level user confirmation.** The user grants permission per tier; the skill executes only what's authorized.
- **Never run `sudo apt-get install` without explicit per-command confirmation.** System-binary installs require root on Linux; the user must be aware.
- **Never auto-install `sentence-transformers`** without surfacing the install size (~2 GB with torch dependencies). Always tell the user the size cost.
- **Never auto-install the Surprisal tier** without surfacing the install size (~1.5–2 GB for transformers + tokenizers + torch). It's the largest install in the framework; the user must see the cost before approving.
- **Never modify the user's `requirements.txt` or other repo files** during install. The skill's job is to run `pip install`, not to add packages to the user's project.
- **Never claim a dep is "broken" when it's just missing.** A `ModuleNotFoundError` means the package isn't installed; it doesn't mean the framework's wiring is wrong.

## Common scenarios

### Fresh install, "set up SETEC"

Run `dependency_check.py` for all tiers. Report state. Default proposal: install core + acquisition (these cover the four core diagnostic surfaces and the impostor-pool workflow). Ask about calibration / OCR / surprisal / optional separately. For Surprisal specifically, always name the ~1.5–2 GB install footprint when proposing it; users who only run the four prose-only surfaces don't need it.

### "I'm getting ModuleNotFoundError on `bs4`"

Run `dependency_check.py --tier acquisition`. Show the missing-acquisition-deps list. Propose `pip install -r requirements-acquisition.txt`. Ask before running.

### "I want to OCR these scanned PDFs"

Run `dependency_check.py --tier ocr`. The Python side is `pip install ocrmypdf`. The system-binary side requires platform-specific commands (Homebrew on macOS, apt/yum on Linux, manual on Windows). Walk through the platform path the user is on; ask separately for Python install permission and system-binary install permission.

### "Why is Tier 3 cohesion using TF-IDF instead of SBERT?"

Run `dependency_check.py --tier optional`. Surface that `sentence-transformers` is missing; explain that it's optional (TF-IDF works as a fallback) and that installing pulls in `torch` (~2 GB). Let the user decide. If the user is also running `external_mirror/`, note that the sbert distance path requires `sentence-transformers` too — installing it once unlocks both.

### "I'm running on Windows and OCR doesn't work"

Likely cause: missing tesseract / ghostscript / qpdf system binaries. `dependency_check.py --tier ocr` confirms which are absent. Walk the user through chocolatey or manual install for the missing binaries.

### "I want to run Binoculars or Tier 4 surprisal"

Run `dependency_check.py --tier surprisal`. Surface the missing deps (transformers / tokenizers / torch); name the install footprint up front (**~1.5–2 GB**, dominated by torch's CUDA wheels). Propose `pip install -r requirements-surprisal.txt`. Ask before running. Note that GPU acceleration (CUDA / ROCm / MPS) is optional — CPU wheels suffice for the framework's default `tinyllama` + `gpt2` Binoculars pair; the GPU path matters when scoring large corpora through `variance_audit.py --tier4` or `calibration_survey.py --tier4`.

### "I want to run external_mirror"

This is a Surface 5 tool but does NOT live on the Surprisal tier — `compute_distances.py` does not import transformers, tokenizers, or torch directly. Two paths depending on which distance metric the operator wants:

- **Default sbert metric** (the v1 path, ~0.71 sbert AUC at ctx=1500 on the published Granta validation target): requires `sentence-transformers` from the Optional tier. Run `dependency_check.py --tier optional`; if missing, propose `pip install sentence-transformers` and name the size (~2 GB; pulls in torch transitively). Ask before running.
- **v2 metric stack only** (TF-IDF + POS-bigram cosine + POS-bigram Jaccard + word-set Jaccard, no sbert): runs on the **core tier alone** — `scikit-learn` + `spaCy` from `requirements.txt` plus stdlib. If the user has core installed, external-mirror's v2 metrics work without any additional install. The v2 metric stack is what landed in PR #113; it's the operator-side fallback for environments where the sbert install isn't acceptable.

The reviewer-caught pitfall: routing an external-mirror user to `--tier surprisal` installs transformers + tokenizers + torch but NOT sentence-transformers, so the default sbert metric still fails. Always reach for the Optional tier (or v2-only) for external-mirror.

### "ImportError: torch" from a Surface 5 script

Two possible causes depending on which Surface 5 tool surfaced the error:

- From `binoculars_audit.py` / `binoculars_calibrate.py` / `surprisal_audit.py` / `variance_audit.py --tier4`: missing the Surprisal tier. Run `dependency_check.py --tier surprisal`; `pip install -r requirements-surprisal.txt` resolves it. If the user is in a constrained environment (no GPU, low disk) and only needs Binoculars on small targets, the CPU-only torch wheel (~750 MB) is sufficient; the framework's surprisal_backend auto-resolves the right device at load time.
- From `external_mirror/compute_distances.py` / `embedding_backend.py`: missing `sentence-transformers` (torch is transitive through it). Run `dependency_check.py --tier optional`; `pip install sentence-transformers` resolves it.

## What to do when the user says no

If the user declines an install, that's fine — record the answer. They may not need that tier. Future SETEC commands that try to use the missing tier will fail with clear messages from the relevant scripts (each acquisition script reports `module not installed; install with pip install -r requirements-acquisition.txt`).

The setup skill's job is to surface options, not to enforce a particular install posture.

## Self-test

Verify the skill is wired up:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dependency_check.py" --help
```

Should produce the usage block. If `python3` isn't on PATH or the script can't be found, report the precise error before any tier survey.
