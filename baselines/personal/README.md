# Personal Baseline

The writer's own prior unedited work, organized by register. The most useful baseline because it captures the writer's idiolect rather than a genre aggregate.

## Why personal beats genre

Burrows' Delta and POS-bigram KL stay stable across topics for the same writer. A writer's function-word fingerprint and sentence-length distribution persist across decades. The cleanest signal for "this draft has been smoothed" is "this draft's distribution looks unlike the writer's prior distribution."

When a personal baseline is available, prefer it over the genre baseline. The variance audit reports z-scores; |z| > 1.0 against a personal baseline is a much stronger signal than against a genre aggregate.

## Suggested structure

Subdirectories by register, or flat with naming convention. The script reads all `*.txt` files in the directory it's pointed at, so flat or nested both work.

Suggested flat naming:

```
testimony_2022-03_topic-a.txt
policy_2021-11_topic-b.txt
philosophy_2019_article-draft.txt
blog_2020-04_longform-essay.txt
fiction_project-a_chapter-01-draft.txt
fiction_project-b_scene-02-draft.txt
```

Or nested:

```
personal/
├── testimony-policy/
├── philosophy/
├── blog/
└── fiction/
```

When running the audit, point at the relevant subdirectory (`--baseline-dir ../private-baselines/personal/blog/`) for the cleanest register-matched comparison.

## Minimum size

3-5 files in the relevant register, each 2,000+ words, gives a usable baseline. More is better. The aggregate SD calculation needs at least 5 files to be meaningful; with fewer, z-scores will be unstable.

## What to include

**Testimony/policy register:** prior testimony, policy briefs, and internal memos that were authored solo and predate routine AI-assisted drafting.

**Academic register:** prior published articles, conference papers, dissertation chapters, or long scholarly drafts in the same field/register.

**Blog/essay register:** prior long-form posts or essays. Sampling across years gives a more stable register baseline.

**Fiction register:** prior drafts in the same genre, POV, and prose register that predate routine AI-assisted drafting. Keep AI-assisted or mixed-provenance drafts out of the baseline unless they are explicitly labeled for validation rather than voice profiling.

## What not to include

- Material drafted with substantial AI assistance. Defeats the purpose.
- Co-authored material where the co-author wrote substantial portions. The function-word fingerprint will be muddled.
- Material in registers you no longer work in. Outdated registers may produce false alarms when current work has legitimately shifted.

## Updating

The personal baseline should be updated periodically. If your idiolect has genuinely shifted (you're writing more concisely now, you've adopted new collocations, you've moved to a different register), refresh the baseline with recent unedited work. The point is to catch deviations from your current writing, not your writing-of-five-years-ago.
