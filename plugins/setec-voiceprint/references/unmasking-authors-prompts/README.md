# Zero-shot authorship prompts (vendored from Desai, Bölöni-Turgut et al. 2026)

These prompt templates are copied byte-for-byte from the paper's public
repository so SETEC can run the same zero-shot reads offline. Nothing in the
`RQ*/` directories has been edited.

- Paper: *Frontier models need only one text to unmask its human or AI author*,
  arXiv:2611.26216v1 (19 Nov 2026), <https://arxiv.org/abs/2611.26216>
- Source: <https://github.com/denizbt/unmasking_authors>, `prompts/` at commit
  `75f4f480eb699d13d24c0b575398b6f6ef54bb86`
- License: MIT, copyright (c) 2026 Deniz BT. The license text is in
  [`LICENSE`](LICENSE) beside these files and must travel with them. The
  upstream prompt index is kept as [`UPSTREAM-README.md`](UPSTREAM-README.md).

## What SETEC wires

`zero_shot_provenance_read` (surface `zero_shot_provenance_read`) renders,
sends or ingests two of these templates:

| Task flag | Template | Paper |
|---|---|---|
| `--task ideas-prose` | `RQ1/joint.txt` (who originated the ideas, who wrote the prose) | §2, App. B |
| `--task which-model` | `RQ2/holistic.txt` (developer, family, version) | §3, App. C |

The surface hashes the template it used (`results.prompt.sha256`) so a run
can be matched to this exact text.

## What SETEC does not wire

The `RQ3/` templates (name the human author; familiarity probes; the
style-obscuring paraphrase) are kept here as reference only. The paper shows
they unmask pseudonymous and anonymous writers (§4 to §5), and SETEC's
authorized-use posture does not ship a turnkey tool for naming a person from
their text. A researcher with a legitimate attribution question can render
these files by hand.

The explanation-agreement prompts (`RQ1/explanation_analysis/`) are kept for
replication of the paper's §2.3 analysis; no surface calls them.

## Reading the paper's numbers correctly

The paper's detection accuracy depends on its structured prompt: with a bare
"is this AI?" question GPT-6.1 Sol wrongly called 22.9% of human ideas AI,
against 2 to 3% with the structured prompt (Table 12). On fresh news that
Pangram 4.1 labels human, frontier readers still flagged 0.5% to 5.0%
(Table 13). Treat a read as one reader's account of the evidence, never as a
determination about a person.
