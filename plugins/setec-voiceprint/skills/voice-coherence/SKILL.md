---
name: voice-coherence
description: >
  Measure how far a target draft has drifted from a writer's or
  register's stylometric baseline. Use when the user asks to "compare
  this draft to my baseline," "measure voice distance," "build a voice
  profile," "voiceprint this corpus," "has my voice drifted," "Burrows
  Delta against my prior work," "feature-cluster mode," "function-word
  cluster directionality," "extract my idiolect," "idiolect detector,"
  "do not normalize these phrases," or any request to audit voice coherence
  against a personal or register-matched corpus. Also triggers on
  "voice distance," "voice profile," "voiceprint," "stylometric
  drift," "directional cluster," "productive roughness," "roughness
  drift," "compare my fragments or contractions to my baseline," "did
  editing sand off my roughness," or "idiolect."
version: 1.0.1
---

# Voice-Coherence Comparison (SETEC Surface 2)

This skill primarily measures the stylometric distance between a target text and a writer-shaped or register-shaped baseline. It also routes to the sibling `productive_roughness` surface when the operator wants six sentence-level roughness features compared with the writer's own stable work. These are *coherence* diagnostics, not *provenance* diagnostics: they do not adjudicate whether AI involvement, editing, register shift, time drift, or genuine voice change caused a divergence.

## What this surface licenses, and what it does not

- **Licenses:** "this draft has drifted from this baseline by this much, with the largest contributions in these feature clusters."
- **Does not license:** "AI involvement caused this drift," and not "the writer is no longer themselves." The diagnostic surface is the writer's own signature against their own baseline; cross-author or cross-register comparisons confound voice with topic.

## Scripts and when to use which

| Script | Scope | Use when |
|---|---|---|
| `voice_distance.py` | Target vs. baseline corpus | Asking how far a draft has drifted from a writer or register voiceprint |
| `voice_profile.py` | Baseline corpus | Producing a private human-readable voiceprint document from the writer's own prior work |
| `idiolect_detector.py` | Target corpus vs. reference corpus | Extracting distinctive words/phrases and a preservation list for revision prompts |
| `productive_roughness_audit.py` | Draft vs. writer baseline; sibling `productive_roughness` surface | Asking whether fragments, initial conjunctions, contractions, repetition, asides, or very-short sentences differ from this writer's stable pattern |

## Quick CLI

```bash
# Voice-distance against a register-matched private baseline
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/voice_distance.py" path/to/draft.txt --baseline-dir path/to/baseline/

# Manifest-driven baseline selection (preferred when a corpus_manifest.jsonl is available)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/voice_distance.py" path/to/draft.txt \
    --manifest path/to/corpus_manifest.jsonl \
    --use baseline \
    --register blog_essay \
    --persona anotherpanacea

# Build a private voice profile (refuses to write outside ai-prose-baselines-private/ unless --allow-public-output)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/voice_profile.py" \
    --baseline-dir path/to/private-baseline/ \
    --out path/to/private-baseline/voice_profile.md

# Extract a private idiolect preservation list
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/idiolect_detector.py" \
    --target-dir path/to/private-target-corpus/ \
    --reference-dir path/to/register-reference-corpus/ \
    --out path/to/ai-prose-baselines-private/target_idiolect.md \
    --preservation-output path/to/ai-prose-baselines-private/target_preserve.txt

# Baseline-relative productive-roughness deviations (writer baseline is required)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/productive_roughness_audit.py" \
    path/to/draft.md --baseline-dir path/to/writer-baseline/ --json

# Write the same audit as Markdown instead
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/productive_roughness_audit.py" \
    path/to/draft.md --baseline-dir path/to/writer-baseline/ \
    --out path/to/ai-prose-baselines-private/productive_roughness.md
```

## Privacy notice

Voice profiles, idiolect reports, productive-roughness reports, preservation lists, and personal baseline corpora are voice-cloning inputs. The signals these scripts compute (function-word distribution, character n-grams, POS trigrams, dependency-label n-grams, idiolectic phrases, and writer-specific roughness patterns) are exactly what a stylometric voice-cloning system consumes. The `voice_profile.py` and `idiolect_detector.py` scripts default to refusing output paths outside `ai-prose-baselines-private/` unless `--allow-public-output` is passed explicitly; `productive_roughness_audit.py` does not add that path guard, so keep its personal baseline and report private by operator practice. The `manifest_validator.py` enforces a privacy ratchet on `voice_profile`- and `idiolect`-tagged manifest entries. Treat voiceprints as cloning-grade inputs by default and keep them out of any public repository.

## Productive-roughness follow-up

`productive_roughness_audit.py` requires `--baseline-dir` containing the writer's own stable work; omitting it is a CLI usage error (exit 2), and there is no single-document or register-baseline mode. It reports exactly six per-sentence rates — fragments, sentence-initial coordinating conjunctions, contractions, adjacent-word repetition, interjections/asides, and very-short sentences — as `{draft, baseline_mean, baseline_sd, z}` for each feature.

A large `|z|` means only "different from this writer's baseline." It does not mean worse, smoother, AI-written, less authentic, or in need of revision, and movement toward `z=0` is not a pass criterion. `z: null` means the baseline has zero variance for that feature, so the deviation magnitude is undefined rather than zero. Drafts below 1,000 words receive a soft stability warning; read their per-sentence rates as directional and noisy. The audit requires spaCy plus `en_core_web_sm`; a missing parser/model or unusable baseline produces a clean `available: false` report and currently exits 0.

## Feature-cluster mode

`voice_distance.py` reports per-family Burrows-style Delta and cosine distance, plus a feature-cluster aggregator that surfaces directional drift in syntactic groupings (pronouns by person/number, deixis, modal subgroups, be/have/do auxiliaries, prepositions, conjunctions). Single-feature top-N catches loud individual outliers; cluster mode catches the more diagnostic case where a cluster of related features moves together at moderate magnitudes — the authorial-fingerprint signal the per-feature view fragments.

## Setup prerequisite

```bash
pip install -r "${CLAUDE_PLUGIN_ROOT}/requirements.txt"
python -m spacy download en_core_web_sm
```

Reference docs live at `${CLAUDE_PLUGIN_ROOT}/references/distributional-diagnostics.md` (signal math) and `${CLAUDE_PLUGIN_ROOT}/references/source-triage.md` (Layer C voice-attribution methodology, including the multi-register-narrator and "earned by frame" cases).
