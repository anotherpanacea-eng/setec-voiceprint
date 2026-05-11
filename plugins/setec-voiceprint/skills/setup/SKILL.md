---
name: setup
description: >
  First-run dependency check for the SETEC framework. Use when the user
  has just installed the plugin, asks about "setting up SETEC,"
  reports a "module not found" error from a SETEC script, asks "what
  does this plugin need to run," asks how to install spaCy / pypdf /
  ocrmypdf for SETEC, asks why a feature isn't working, or asks for
  install commands. Also triggers on "missing dependency,"
  "ImportError" from a setec-voiceprint script, "first-time setup,"
  "is everything installed," or "do I need anything else for SETEC."
version: 1.0.0
---

# SETEC Setup Skill

This skill surveys the user's environment for the four SETEC dependency tiers, reports what's installed and what's missing, asks for permission, and runs the installs the user authorizes. The goal is to catch "module not found" errors up front rather than mid-pipeline.

The framework is opt-in by tier. A user running only smoothing-diagnosis doesn't need acquisition deps; a user running only acquisition doesn't need calibration deps. The skill identifies which tier(s) the user actually needs and proposes only those installs.

## What this skill licenses, and what it does not

- **Licenses:** running `dependency_check.py`, presenting its findings to the user, and — *only with explicit permission for each tier* — running the corresponding `pip install` / `python -m spacy download` / system-package-manager commands.
- **Does not license:** running `pip install` for an unspecified set of packages, modifying the user's Python environment without confirmation, downloading large models (e.g. SBERT) without surfacing the size cost, or upgrading core deps the user already has working.

The user owns the environment. The skill proposes; the user disposes.

## Tier model

| Tier | When needed | Install command |
|---|---|---|
| **Core stylometry** | Every diagnostic script (variance audit, voice distance, manifest validation, etc.). spaCy + en_core_web_sm + scipy + scikit-learn + statsmodels. | `pip install -r plugins/setec-voiceprint/requirements.txt` then `python -m spacy download en_core_web_sm` |
| **Acquisition** | The five impostor-pool acquisition scripts (`acquire_blog`, `acquire_blogger_takeout`, `acquire_magazine`, `pdf_inventory`, `pdf_extract`). requests + feedparser + bs4 + lxml + python-dateutil + pypdf. | `pip install -r plugins/setec-voiceprint/requirements-acquisition.txt` |
| **OCR (optional within acquisition)** | Only when extracting text from image-only / mixed PDFs. ocrmypdf + tesseract + ghostscript + qpdf. | `pip install ocrmypdf` plus the system-binary install for the user's platform (see below). |
| **Calibration** | Only when re-deriving thresholds from EditLens / RAID / MAGE. huggingface_hub + pyarrow. | `pip install -r plugins/setec-voiceprint/requirements-calibration.txt` |
| **Optional power-ups** | sentence-transformers (calibrated Tier 3 cohesion), textstat (better FKGL), nltk (Brown corpus for idiolect). All commented in `requirements.txt`. | `pip install <package>` per power-up. |

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
- "Tier 3 cohesion" / "SBERT" / "sentence-transformers" / "Brown corpus" → **optional tier**.

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

The user may say "yes to core only" or "yes to acquisition, skip OCR" — honor each granular choice.

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
- **Never modify the user's `requirements.txt` or other repo files** during install. The skill's job is to run `pip install`, not to add packages to the user's project.
- **Never claim a dep is "broken" when it's just missing.** A `ModuleNotFoundError` means the package isn't installed; it doesn't mean the framework's wiring is wrong.

## Common scenarios

### Fresh install, "set up SETEC"

Run `dependency_check.py` for all tiers. Report state. Default proposal: install core + acquisition (these cover 90% of typical use). Ask about calibration / OCR / optional separately.

### "I'm getting ModuleNotFoundError on `bs4`"

Run `dependency_check.py --tier acquisition`. Show the missing-acquisition-deps list. Propose `pip install -r requirements-acquisition.txt`. Ask before running.

### "I want to OCR these scanned PDFs"

Run `dependency_check.py --tier ocr`. The Python side is `pip install ocrmypdf`. The system-binary side requires platform-specific commands (Homebrew on macOS, apt/yum on Linux, manual on Windows). Walk through the platform path the user is on; ask separately for Python install permission and system-binary install permission.

### "Why is Tier 3 cohesion using TF-IDF instead of SBERT?"

Run `dependency_check.py --tier optional`. Surface that `sentence-transformers` is missing; explain that it's optional (TF-IDF works as a fallback) and that installing pulls in `torch` (~2 GB). Let the user decide.

### "I'm running on Windows and OCR doesn't work"

Likely cause: missing tesseract / ghostscript / qpdf system binaries. `dependency_check.py --tier ocr` confirms which are absent. Walk the user through chocolatey or manual install for the missing binaries.

## What to do when the user says no

If the user declines an install, that's fine — record the answer. They may not need that tier. Future SETEC commands that try to use the missing tier will fail with clear messages from the relevant scripts (each acquisition script reports `module not installed; install with pip install -r requirements-acquisition.txt`).

The setup skill's job is to surface options, not to enforce a particular install posture.

## Self-test

Verify the skill is wired up:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dependency_check.py" --help
```

Should produce the usage block. If `python3` isn't on PATH or the script can't be found, report the precise error before any tier survey.
