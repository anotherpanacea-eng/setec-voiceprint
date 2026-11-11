# VP-APP-1: a usable offline Voiceprint app (planning spec)

> A desktop app that runs Voiceprint's tools on the user's own machine, picks the right tool for a question, and explains the result, with no network and no Claude in the loop.

- **Status:** Draft, planning only. Fleet card VP-APP-1.
- **Authority:**
  - **Owner, Nov 6:** a usable offline app rather than a toolkit users must point Claude at. LuAR, LAPD and surprisal are minimum alpha requirements, not the full arsenal or final acceptance. Unresolved readiness blocks alpha acceptance; it does not move a required tool to Future.
  - **The card's gate:** planning only. No model runs, paid services, private-text egress, implementation, publication or release. Architecture and final alpha acceptance remain owner decisions.
- **Linked specs:**
  - VP-HBK-1, `vp-offline-handbook.md`, the handbook
  - VP-VID-1, `vp-explainer-videos.md`, the videos
  - CB-13, the hermetic construction-site groundwork, building on the built `svp-hermetic-mode-amendment.md`

## Users and the first usable workflow

A writer, editor or teacher who wants to check prose they hold locally, without sending it anywhere.

**The first workflow:** *"Does this draft still sound like me?"*
1. The user picks a folder of their own earlier writing (the baseline) and a draft.
2. The app runs the alpha set's style-embedding consistency check (`voice_fingerprint`, card tier `optional`) and the `core`-tier voice-distance and punctuation-cadence profiles.
3. It shows the results with plain-language explanations and their limits.

It never gives a verdict, only how the draft sits against the baseline and what each measure can't say. This uses tools that already exist and already refuse verdicts, and it needs no impostor corpus.

**Tool selection** after that: the app offers the existing `capabilities.py recommend --situation …` routing as a question-first picker, and lists only tools that can run offline in the installed tiers.

**Interpretation:** `explain.py` (spec 16) supplies plain-language section names and the license text, but on its own it shows no measurements and omits warnings and `claim_license.additional_caveats`. So the app's result view shows four things, then links to the handbook entry and video:
- the measurements themselves: for the first workflow, the baseline-consistency cosine distribution and the voice-distance and cadence figures against the baseline;
- every warning in the envelope;
- the claim license with its additional caveats;
- the `explain.py` text.

## The arsenal by app tier

The full inventory is the capability entries in `capabilities.d/` (147 at this spec's base commit). Their public coverage, minus the owner-hidden and internal entries, is set in the handbook spec. The app tiers describe product scope; readiness is tracked separately. LuAR, LAPD and surprisal are required for alpha. The first workflow alone does not satisfy that minimum.

| Tier | Rule | Members (proposed) |
|---|---|---|
| **Alpha** | Required scope. Before alpha acceptance, every member must run fully offline on the target hardware, have verified redistribution rights where needed, and have no unresolved implementation or registration blocker | **LuAR** (`voice_fingerprint`, `cosine_explanation`; card tier `optional`), **surprisal** (`surprisal_audit`; card tier `surprisal`), **LAPD** (required but blocked; see below), and the `core`-tier tools the first workflow uses (`voice_distance`, `punctuation_cadence_audit`) |
| **Optional** | Offline, but a heavier install or a narrower use | Other `surprisal`-tier tools (`variance_audit`, `binoculars_audit`, `fast_detect_curvature`), the StyleDistance encoder for `voice_fingerprint`, and the rest of the `core` tools |
| **Future** | Outside the required alpha scope and not yet ready: an unreleased checkpoint or an unverified license | `muar` (no public checkpoint, `_UNRELEASED_MODEL_IDS`), STAR (no license; private research only), Wegmann (no license tag) |
| **Not offline** | Needs a live API | Live-judge modes of `api_llm` tools. Their precomputed-manifest modes stay offline (handbook spec). |

### The three minimum alpha requirements

| Requirement | What exists | Model and license | Weights on disk | Ready? |
|---|---|---|---|---|
| **LuAR** | `voice_fingerprint`, surface `authorship_embedding`, default encoder `luar` → `rrivera1849/LUAR-MUD` | Apache-2.0 (from the model's Hugging Face license tag, read 2026-11-06) | about 330 MB (`model.safetensors`) | Yes, pending the hardware measurement below |
| **Surprisal** | `surprisal_audit`, surface `smoothing_diagnosis`, default backend `tinyllama` → `TinyLlama/TinyLlama-1.1B-intermediate-step-1431k-3T` | Apache-2.0 (Hugging Face tag, read 2026-11-06) | about 4.4 GB (`model.safetensors`) | Yes, but heavy: the largest currently quantified download, and needs the `surprisal` dependency tier (torch, transformers) |
| **LAPD** | **Not a registered capability.** There is no `capabilities.d` entry, script or surface in this repo. The card links arXiv:2604.16923. | unknown | unknown | **No: required for alpha, blocked.** Capability registration, weights and licensing remain unresolved (below). |

The license tags are read from each model's own Hugging Face metadata. Before any packaging, the builder re-verifies them against the model card and repository license, and records the result with the release. StyleDistance (`StyleDistance/styledistance`) carries an MIT tag and about 249 MB of weights.

### Reconciling LAPD

LAPD becomes an app tool only through the normal capability path:
1. a spec naming its method, inputs, outputs and claim license;
2. a registered surface and `capabilities.d` entry;
3. verified weights and license.

Until these blockers are resolved and its offline implementation passes acceptance, LAPD remains **required for alpha but unavailable**. Alpha cannot be accepted without it; only an explicit owner scope change could remove that requirement. No app screen, handbook entry or video describes it as available. This planning correction does not implement or register LAPD.

## Installation and model setup

- **Install:**
  - The app ships the Python runtime, Voiceprint, and the `core` dependency group with its spaCy model. The alpha set also needs the `surprisal` group (torch, transformers); `voice_fingerprint` needs `transformers` too (its card), and the LUAR checkpoint loads with `trust_remote_code` and `einops` (CHANGELOG, spec 02).
  - Model weights install separately, by tier. Alpha needs LUAR-MUD (about 330 MB), TinyLlama (about 4.4 GB), and the LAPD artifacts still to be identified and verified. LAPD pack size and dependencies are unresolved alpha blockers.
- **Two install routes** (owner decision 2):
  - **(a) Offline bundle:** everything in one installer. The existing LUAR/TinyLlama estimates imply roughly 5 GB or more before unresolved LAPD artifacts and runtime overhead; a complete alpha bundle size cannot yet be stated.
  - **(b) Base app plus model packs:** a model pack is downloaded once, or copied from a file for machines that stay offline. The app verifies each pack's sha256 against the release manifest before use.
- **Runtime:** no network. Models load from the app's local store only, never a remote hub at run time.
- **Testing without models:** the built hermetic mode (`setec_hermetic_backend=stub`) already refuses the judge, embedding and surprisal backends before import, and CB-13 closes the remaining construction sites. App CI uses it. Hermetic tests prove refusals and plumbing, not real-model behavior.

## Hardware (measured before alpha; not measured here)

This card authorizes no model runs, so every figure below is **unmeasured**. A separate, authorized measurement run fills the table before alpha acceptance.

| Tool | Disk | Peak RAM | Time per 1,000 words, CPU only | Reference machines |
|---|---|---|---|---|
| `voice_fingerprint` (LUAR-MUD) | weights about 330 MB, plus the runtime | unmeasured | unmeasured | an Apple-silicon laptop (Code-Mac class) and a Windows x86 desktop (Code-PC class) |
| `surprisal_audit` (TinyLlama 1.1B) | weights about 4.4 GB, plus the runtime | unmeasured | unmeasured | same |
| LAPD (required, blocked) | unknown pending verified artifacts | unmeasured | unmeasured | same; measurement awaits registration, weights and licensing resolution |
| first-workflow `core` tools | small | unmeasured | unmeasured | same |

**Measurement protocol** (for the authorized run):
- public-domain text only, at 1,000, 5,000 and 20,000 words;
- three repeats each;
- peak resident memory and wall time, CPU only, then GPU or Apple-silicon acceleration where present;
- reported with the machine, OS, Python and package versions.

## Disconnected real-model acceptance (the alpha bar, for the owner to ratify)

**Prerequisite:** resolve the required LAPD capability registration, implementation, weights and licensing blockers. A LuAR-plus-surprisal subset is not an accepted alpha.

On a clean machine with networking **disabled** before install:
1. Install by the chosen route.
2. Run the first workflow on a public-domain fixture.
3. Run each alpha tool once, including LuAR, LAPD and surprisal. An unavailable required tool blocks acceptance rather than being skipped.

**Pass** when four things hold:
- every run returns a valid `build_output()` envelope with its claim license;
- outputs match the reference envelopes recorded on the build machine, **after machine-specific fields are normalized**. Path-bearing fields such as `target.path` and any other input path are reduced to the file's name, and timestamps and run ids are dropped. Then deterministic measurements and claim licenses must match exactly, and model measurements within a stated tolerance;
- a network monitor records zero outbound connections;
- the user's text never leaves the app's data folder.

## Packaging options (owner decision 1)

| Option | Shape | For | Against |
|---|---|---|---|
| **A. Desktop shell with a Python sidecar** | A Tauri app (as `apodictic-tauri`) with Voiceprint as a bundled sidecar | The fleet already ships one; native installers; a small UI layer | Must bundle a Python runtime and torch per platform; signing and notarization (as AP-SIGN) |
| **B. Local web app** | `pip install` or a packaged runtime; the app serves a page on localhost | Simplest build; one codebase | Install friction for non-technical users; "a page on localhost" can feel unlike an app |
| **C. CLI plus handbook only** | Today's toolkit, packaged | Least work | Not what the owner asked for: users would still need to know which tool to run |

**Proposed:** A, reusing the `apodictic-tauri` sidecar pattern, with B as the development harness.

## Decisions for the owner

| # | Decision | Proposed |
|---|---|---|
| 1 | Packaging | A, a desktop shell with a Python sidecar |
| 2 | Install route | (b) base app plus model packs, with an offline file option |
| 3 | Additional alpha membership | Minimum fixed by the owner: LuAR, LAPD and surprisal. Proposed additions: the first workflow's core tools. Resolve LAPD readiness before alpha acceptance; it is not deferred. |
| 4 | Authorize the hardware measurement run | A separate go, public-domain text only |
| 5 | Acceptance bar | Disconnected real-model acceptance as above |

## Not in scope

- Building the app.
- Running models.
- Registering LAPD.
- Writing handbook entries or videos.
- Publishing or releasing anything.
