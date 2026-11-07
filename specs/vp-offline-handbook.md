# VP-HBK-1: an offline stylometry handbook for the Voiceprint app (planning spec)

> One guide entry per public tool, saying what question it answers, what it needs, how to read it and what it can't claim. Packaged to read and search offline inside the app.

- **Status:** Draft, planning only. Fleet card VP-HBK-1, admitted 2026-11-06, seat OCA.
- **Authority:**
  - **Owner, Nov 6:** the app should include a stylometry handbook covering every tool in the arsenal.
  - **The card's gate:** inventory and reconcile the existing sources first; confirm the authoritative source and reuse rights; never publish private working prose; package for offline use and keep each method's limits; editorial and build approval come after this spec.
- **Linked specs:** VP-APP-1 (the app; not written yet), VP-VID-1 (`vp-explainer-videos.md`; the videos link to handbook entries), CB-13 (hermetic mode).

## Sources today (inventory and reconciliation)

| Source | Where | Covers | Role in the handbook |
|---|---|---|---|
| **Capability cards** | `plugins/setec-voiceprint/capabilities.d/*.yaml` | 147 entries: purpose, `use_when`, `do_not_use_when`, inputs, compute tier, dependencies, status, handoff | **Authoritative** for what each tool does, needs and refuses. Each entry's metadata is rendered from the card, never retyped. |
| **Signals glossary** | `plugins/setec-voiceprint/references/signals-glossary.md` | Signal path, polarity, calibration status and provenance. The header says 105 signals in 24 families, but the family rows sum to 110: unreconciled. | Authoritative at the signal level. It becomes the handbook's signal index, linked from each tool entry. |
| **Scripts catalog** | `plugins/setec-voiceprint/scripts/README.md` | About 16,000 words of CLI usage. Its introduction says five task surfaces, but it has six numbered sections: unreconciled. | The source for setup and CLI. The handbook links to it or condenses it, and keeps no second copy. |
| **The maintainer's external primer** | private, in development (the glossary's own pointer) | Long-form teaching, partial | **Not published.** Its prose is used only with the owner's explicit reuse ruling (owner call 1). Entries are written fresh from public sources otherwise. |
| **Glass-Box Stylometry sequence** | anotherpanacea.com (public posts) | Teaching essays and worked cases | Public. It may be linked or quoted briefly, with attribution (owner call 2 for any longer reuse). |
| **Evidence packs** | `docs/evidence-packs/*.html` | Two public worked cases | Candidate worked examples (owner call 2) |

**Reconciliation.** The glossary indexes **signals**; the cards index **tools**. The handbook is organised by tool, one entry per user-facing capability. Each entry lists the glossary signals that tool reports, linked to the signal index, so the two layers stay distinct and cross-linked. Where the glossary and a card disagree on a status, the card is the tool's status and the glossary is the signal's; the entry shows both.

## Coverage

There are 147 capability entries. The rule follows each entry's documented disposition, not one field.

- **Out:**
  - the 5 entries with `handoff: internal`;
  - the 6 `todo` entries hidden under the owner's 2026-10-10 ruling (user-facing tools only): `acquire_corpus_template`, `acquisition_core`, `calibration_survey`, `compose_evidence_pack`, `length_bootstrap` and `train_xgboost`;
  - `acquire_imessage_sent_atomic`, which is WIP and hidden pending its owner-authorized smoke.

  These are scaffolds, libraries or maintainer tools, not future public tools, and get no entry.
- **In, marked "blocked":** the 5 user-facing `todo` entries whose `todo_reason` names a fix still owed: `adversarial_robustness_card`, `before_after_restoration`, `confounder_audit`, `evidentiary_conditions_gate` and `voice_validation_harness`. Each entry shows what the tool would answer and that it doesn't run yet.
- **In:** every other entry. Acquisition, setup and corpus-hygiene tools get a shorter operator form: question, inputs, setup and limits.
- **Count:** the builder lists the in-set from the cards at build time. The handbook doesn't hard-code a number that can drift. The figures above are as of this spec's base commit.
- **Calibration status and availability are separate.** Status (`heuristic`, `literature_anchored` and so on) says how well-grounded a tool's output is. Availability says whether it runs in the app. An entry shows both, and never uses one for the other.

## The entry template

Every tool entry has these sections, in this order:

1. **Question it answers:** one sentence.
2. **Availability:** alpha, optional, blocked or future. It also gives the card's compute tier, kept separate from the dependency install group that `dependency_check` reports. Offline status is described per execution mode. An `api_llm` tool can still run offline in a supported local mode: `narrative_decision_audit` and `argument_decision_audit` accept precomputed manifest judges, a path the hermetic spec preserves. Its live-judge mode is not offline.
3. **Inputs:** what to supply, including a baseline or impostor corpus where needed, and the length floor.
4. **Worked example:** public-domain or synthetic text only, with the real output, run against the stated version.
5. **Reading the output:** what each reported number means, in plain words, and which glossary signals it carries.
6. **Failure modes:** from the card's `do_not_use_when` and known confounds (topic, register, memorization, short text, dialect).
7. **Calibration:** the status and its provenance from the glossary and card. Bands are labelled provisional where they are.
8. **What it does not license:** the `claim_license` block from the entry's own worked-example run, quoted verbatim with the mode and model that run used, plus the standing rule that no Voiceprint output is an AI or authorship verdict. The cards hold no license text: each surface builds its license at run time, and it can vary by mode or model. Quoting a real run's envelope is therefore the only faithful source, and no second license registry is created. Where a mode or model changes the license (for example `voice_fingerprint`'s encoder, or a precomputed judge), the entry quotes one run per mode it documents.
9. **See also:** related tools, the matching video (VP-VID-1), and the CLI section of the scripts catalog.

Sections 3, 6 (in part) and 7 are rendered from the card and glossary at build time. Section 8 is quoted from the worked-example run's envelope. Section 2's install footprint comes from measured package and model sizes, recorded with the build, because neither the cards nor `dependency_check` carry a size today. Sections 1, 4, 5 and 9 are written by hand.

## Alpha, optional and future

| Tier | Meaning | Proposed members |
|---|---|---|
| **Alpha** | Ships and works offline in the first app release | The minimum candidates LuAR (`voice_fingerprint`, with `cosine_explanation`) and surprisal (`surprisal_audit`), plus whatever `core`-tier tools VP-APP-1 includes. LAPD joins once it is a registered capability. Also a "How to read any result" chapter and a "No verdicts" chapter. |
| **Optional** | Ships, but needs an installable dependency group | Entries name the `dependency_check` group and the measured footprint |
| **Blocked** | A user-facing `todo` with a named fix owed | Entry shown as "doesn't run yet" |
| **Future** | Spec'd in `specs/`, not yet built | Entry shown as "not yet available" |
| **Mode-limited** | Some execution modes need a network (live API judges) | Entry names which modes work offline |

The alpha member list is settled jointly with VP-APP-1. This spec fixes only the rule that a handbook entry's tier must match what the app actually ships.

## Offline packaging, search and links

- **Format:** static HTML built from Markdown sources in the repo, with one page per entry plus the two chapters and the signal index. No runtime network: fonts are bundled or system, with no CDN.
- **Search:** a prebuilt client-side index, built with the handbook, over titles, questions, capability ids and signal paths.
- **Links from the app:** each entry's stable anchor is its capability id (`#voice_fingerprint`), and each signal's is its glossary path. The app links to `handbook/<id>.html`.
- **Versioning:** the handbook's version is the setec-voiceprint version it was built from, and each entry shows it. A release builds both together.
- **Freshness:** the build fails if a public capability has no entry, or an entry names a capability that doesn't exist. Rendered sections can't drift, because they are regenerated. A shipped handbook that silently omits a tool, or describes a removed one, is the failure this prevents. It extends what `tools/check_docs_freshness.py` already does for the changelog.

## Decisions for the owner

| # | Decision | Proposed |
|---|---|---|
| 1 | Reuse of the private primer's prose | No by default. Entries are written from public sources, and named primer passages are reused only on your ruling. |
| 2 | Reuse of the Glass-Box Stylometry posts and evidence packs as worked examples | Link and brief attributed quotation; longer reuse on your OK |
| 3 | Handbook license | `LICENSE-docs`, the repo's docs license |
| 4 | Alpha membership | Jointly with VP-APP-1, by the rule above |
| 5 | Where the source lives | `docs/handbook/` in this repo, built into the app package |

## Not in scope

- Writing the entries.
- The app's viewer (VP-APP-1).
- Videos (VP-VID-1).
- Promoting any capability's calibration status.
- Publishing the handbook outside the app.
