# Baselines

Genre-binned reference corpora for Layer A distributional comparison. The `variance_audit.py` script accepts a `--baseline-dir` argument; with a baseline, the script reports z-scores against the baseline distribution rather than absolute-threshold flagging.

## Why genre-binned

A literary fiction draft and a policy brief draft can both score "Lightly smoothed" despite very different absolute statistics. Sentence-length variance, FKGL distribution, and connective density all depend on register. The relevant comparison is against the writer's own register, not "human writing in general."

## Structure

```
baselines/
├── README.md                      (this file)
├── literary-fiction/              public-domain or contemporary literary fiction
│   ├── README.md
│   └── *.txt
├── academic-philosophy/           scholarly writing in philosophy
│   ├── README.md
│   └── *.txt
├── blog-essay/                    personal essay, long-form blog, op-ed
│   ├── README.md
│   └── *.txt
├── testimony-policy/              testimony, briefs, policy memos
│   ├── README.md
│   └── *.txt
└── personal/                      writer's own prior unedited work (optional)
    ├── README.md
    └── *.txt
```

Each subdirectory contains plain-text files. The script aggregates per-statistic mean and SD across all files in the supplied directory.

## Minimum corpus size

- Per file: ideally 2,000-10,000 words. Short files yield noisy per-file statistics.
- Per genre: at least 8-10 files. Fewer than 5 files makes the aggregate SD unreliable.
- For a personal baseline (writer's own prior work): 3-5 files of 3,000+ words each is a usable starting point. The personal baseline is the strongest comparison because it captures the writer's own variance signature.

## Quality criteria

- Files should be in the target register and genre.
- Files should be unedited by AI (or as close to that as possible). The point of a baseline is to capture human variance.
- Avoid excerpts. The variance signals depend on document-internal distributional structure, which excerpts truncate.
- Strip metadata, footnotes, citations, and other artifacts that aren't prose. Pure narrative or argumentative text only.

## Compilation strategy (suggested)

**Literary fiction.** Project Gutenberg public-domain authors (Henry James, Edith Wharton, Virginia Woolf, Hemingway, Faulkner) plus contemporary writers if available in plain text. Include some range of styles; a baseline that's only Gutenberg will skew old-fashioned and may not match contemporary literary fiction conventions.

**Academic philosophy.** Open-access journals (Philosophers' Imprint, Ergo, Analytic Philosophy). Pre-1990 work by major analytic figures often survives in plain text. Include range across analytic-continental, history-of-philosophy, applied ethics.

**Blog/essay.** Personal essay archives, long-form journalism (n+1, The Point, Aeon archives where licensing permits), op-eds. Avoid news-style reporting; the register is different.

**Testimony/policy.** Public testimony archives (Council hearings, Congressional records), policy briefs from advocacy organizations, white papers. Strip headers, footers, and tabular data.

**Personal.** The writer's own prior unedited work in the relevant register: prior fiction drafts, essays, academic work, testimony, policy writing, or other register-matched material. The date cutoff is project-specific; the goal is text that predates routine AI-assisted drafting.

## Using a baseline

```
python3 scripts/variance_audit.py draft.txt --baseline-dir baselines/literary-fiction/
```

The output will include z-scores for each available signal. |z| > 1.0 is flagged as meaningful in the human-readable summary. Negative z-scores indicate the draft falls below the baseline mean (which, depending on the signal, may mean compression).

## v1 ships empty

This skill v1 ships the directory structure and READMEs but not corpora themselves, because corpus compilation involves licensing, encoding, and cleanup work that should be done as a separate session. The skill works without baselines via the heuristic thresholds in `variance_audit.py`; baselines refine the diagnostic.

When you're ready to populate, the per-genre READMEs document specific suggestions and any constraints.
