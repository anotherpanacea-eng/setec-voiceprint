# The Laundering Vocabulary

A family of related failure modes in which a surface form does work that should have been done at a deeper level, with the surface form's presence licensing readers, institutions, or auditors to treat the deeper work as completed.

The unifying move across the four laundering types: each one **invokes a form of authority to bypass scrutiny**. The reader, or the institution, or the peer auditor, defers to the surface form because the surface form looks like the marker of work that has been done elsewhere. The work has not been done. The surface form has been deployed as if it were the evidence of work.

The four laundering moves catalogued here all surface in AI-assisted writing because instruction-tuned language models have learned that these surface forms are markers of professional / careful / authoritative writing, and produce them on demand without doing the underlying work the markers used to certify.

## Calibration laundering

**Originating record.** Glass-Box Stylometry Sequence Post 3, "Verdict Inflation and Calibration Laundering."

**The authority invoked.** Mathematical / statistical authority. Calibration is the discipline of pinning a verdict to an empirical anchor: this threshold catches 70% of AI-generated prose in this corpus at this false-positive rate. The number is load-bearing. Calibration laundering is when a writer (or a model) deploys the *surface form* of calibration — confidence levels, precision figures, threshold cutoffs — without having done the empirical work to ground them.

**The diagnostic shape.** Numbers appear in the prose. The numbers do not trace back to a labeled corpus, a documented test set, or a published validation harness. The numbers feel calibrated. They are decoration.

**Operational expression in SETEC.** The `provisional=True` / `provenance=None` invariant in `COMPRESSION_HEURISTICS` enforces calibration honesty at the framework level: the framework's signal thresholds carry no calibration claim, and validation harness outputs always document the calibration anchor (RAID v1, EditLens v1, etc.) by name. The "Stylometry to the people" policy is the formalization: ship the methodology, ship the audit trail, ship the band, but never ship the verdict.

## Procedural laundering

**Originating record.** Glass-Box Stylometry Sequence Post 7, "The Measured Writer."

**The authority invoked.** Administrative / institutional authority. Procedural laundering deploys the surface form of process — a methodology section, a stated workflow, a citation discipline — to license claims the underlying work cannot support. The process is real; the work behind the process is missing or incomplete.

**The diagnostic shape.** A writer describes their methodology in detail. The methodology section reads like the methodology of a researcher who did the work. The conclusions then arrive as if the methodology had been executed. They have not been. The procedure has been written about; it has not been carried out.

**Distinguished from genuine methodology.** Genuine methodology produces conclusions that are constrained by what the methodology can support. Procedural laundering produces conclusions that exceed what the methodology can support — but the methodology is described in enough detail to give the reader the impression that the constraint was honored.

## Audit laundering

**Originating record.** Glass-Box Stylometry Sequence methodology.md Part III audit-laundering postmortem.

**The authority invoked.** Peer-model authority. When one language model audits the output of another (or audits its own output via a self-critique loop), the audit produces a record. The record reads as evidence: "this output was audited and found acceptable." The audit was a stochastic forward pass. The model running the audit has the same priors as the model that produced the output. The audit was performed; the audit did not do auditing work.

**The diagnostic shape.** A document includes an audit step. The audit's verdict appears in the document. The audit was performed by a model that shares the original output's biases and blind spots. The reader is invited to defer to the audit verdict because the verdict was issued by something doing the procedural shape of auditing.

**Why it works rhetorically.** The procedural form of auditing — "we ran this through a second pass," "this was reviewed by an independent model," "the system was prompted to identify weaknesses" — invokes peer-review-style authority. The reader's heuristic ("a second set of eyes caught what the first set missed") doesn't fire on whether the second set of eyes is structurally capable of catching what the first missed.

## Image laundering / Aesthetic authority laundering

**Originating record.** Glass-Box Stylometry Sequence Post 4 v2 §IV. Operationally defined as AIC-8 in this framework (see `aic-flags.md`).

**The authority invoked.** Aesthetic / observational authority. The vividness of the image intimidates the reader into assuming the underlying intellectual work has been done. "The machinery of grief," "the architecture of attention," "the topology of memory" — each phrase pairs a prestige-domain word (machinery, architecture, topology) with an abstract experiential term (grief, attention, memory) at high concreteness gap and low semantic similarity. The pairing produces an image that *feels* observed, *feels* earned, *feels* like the writer has spent time inside the experience and is reporting its structure.

The image is not the work. The image is the marker of work that has not been done.

**The diagnostic shape.** Image conjunctions arrive at elevated density relative to a register-matched baseline. They scatter across many prestige domains within a short document, rather than concentrating around a thematic commitment. They are decorative rather than load-bearing: the surrounding argument or narrative does not depend on the specific imagery; the image conjunctions could be substituted for one another without changing what the prose is doing.

The detection apparatus is in `scripts/image_conjunction.py` and `scripts/prestige_metaphor.py`. The compound diagnostic (high concreteness gap + low embedding similarity + scattered domain distribution + paragraph-final co-occurrence with kicker shapes) isolates the deliberate-juxtaposition pattern from genuine literary image-making, where image density is similar but the images cluster thematically and the scaffolding domains track theoretical commitments.

**Distinguished from genuine literary imagery.** Literary fiction earns its image-conjunction density when the images do thematic work — a novel about labor that returns to machinery metaphors, a memoir about navigation that returns to cartography. The signal is concentrated domain entropy: the writer is committed to a particular imagistic register, and the images amplify that commitment. Aesthetic-authority laundering scatters: every paragraph reaches for a different intellectually-serious domain, and the diagnostic feel is *metaphor confetti* rather than a coherent imagistic argument.

---

## Why these four belong together

The four laundering types operate on the same rhetorical mechanism: deploy a surface form that historically marked the completion of underlying work, and exploit the reader's deference to that surface form. The forms differ — numbers (calibration), procedure descriptions (procedural), audit records (audit), vivid imagery (aesthetic) — but the move is identical.

The diagnostic move that catches all four is the same: ask whether the surface form is doing the work it claims, or whether the writer has substituted the surface form for the work. The frequency-elevation framing in the AIC-8/9 spec generalizes: when a laundering surface form appears at elevated frequency against a register-matched baseline, the elevation itself is the diagnostic. Source triage refines per-instance.

## Related references

- `aic-flags.md` — full AIC-8 entry (image laundering operationalized) plus AIC-9 (closure inflation as a related rhetorical-bankruptcy mode).
- `source-triage.md` — per-instance refinement once the frequency-elevation flag has fired.
- `internal/SPEC_aic_8_9_implementation.md` — the spec that operationalized aesthetic-authority laundering as detectable signals.
- `scripts/image_conjunction.py`, `scripts/prestige_metaphor.py`, `scripts/aesthetic_authority_audit.py` — the detection apparatus.
