# Prompts

Detection, attribution, and supporting analysis prompts used for the results reported in the paper, organized by research question.

| Section | Main prompts | Supporting prompts | Paper |
| --- | --- | --- | --- |
| [RQ1: Human or AI?](RQ1/README.md) | [Joint ideas/prose detection](RQ1/joint.txt) | Explanation agreement | §2; App. B |
| [RQ2: Which AI?](RQ2/README.md) | [Holistic attribution](RQ2/holistic.txt) | — | §3; App. C |
| [RQ3: Which human?](RQ3/README.md) | [Human author](RQ3/holistic.txt), [Reddit username](RQ3/reddit_holistic.txt) | Familiarity probes and paraphrasing | §4, §5; App. D |

Each section README lists the paper sections that report results from each prompt.

## Using the templates

Files retain their original wording, placeholders, role delimiters and whitespace. `===== SYSTEM =====` and `===== USER =====` separate messages in templates that contain both roles; they are not instructions to send the entire file as one system message. RQ2's holistic file contains system instructions alone; the document is supplied separately as the user message. Match the original run's input formatting when reproducing an experiment: a preserved template is not necessarily a byte-for-byte submitted API request.

Replace `{doc}` or `{document}` with the document specified by that template. `{name}` is a name used by a familiarity probe. Reddit familiarity's `{doc}` contains the platform and account name, not a comment. Keep literal `<text>` wrappers wherever the applicable template/run uses them. Attribution inputs contain the text alone, without gold identities or source metadata.

## Provenance and coverage

Files were copied byte-for-byte from the research repository's experiment templates. Identical templates may appear under different names, such as the reviews and arxiv familiarity probes.

This collection covers the final templates behind the results reported in the paper. The explanation-agreement prompts (§2.3, App. B.3.2) are included. The cited-cue feature analysis (App. B.3.3) is not. Only the final version of each prompt is included: earlier versions, including P1–P3 of the prompt-development comparison (App. B.3), are not. Neither are archived or unreported experiments. Dataset-specific generation requests (including StoryScope story prompts, news headlines and rendered op-ed requests), benchmark text, private reviews, author identities, model responses, and per-document analysis payloads are not bundled here. Built-in detector prompts that were never exported are unavailable. This is a template collection, not a complete archive of submitted requests.
