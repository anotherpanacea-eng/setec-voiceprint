# Blog / Essay Baseline

Reference corpus for personal essay and long-form blog register. Used by `variance_audit.py --baseline-dir baselines/blog-essay/`.

## What belongs here

Plain-text personal essays, long-form blog posts, op-eds, and column-style writing. The register is more conversational than academic philosophy, more argumentative than literary fiction.

## Suggested compilation

**Long-form essay archives.** n+1 (open archives), The Point, Aeon (CC-licensed), Boston Review (some open content), Harper's archive (where accessible), London Review of Books (limited open content).

**Personal essay collections.** Joan Didion (Slouching Toward Bethlehem essays where licensing permits), Susan Sontag (Against Interpretation), James Baldwin essays, Zadie Smith (Feel Free essays), Rebecca Solnit. Public-domain or licensed open content.

**Long-form blogs.** Slate Star Codex / Astral Codex Ten archives (licensed permissively), LessWrong sequences (open), Tyler Cowen's MR archive (open), Slow Boring (open posts). Variation across these gives a robust register baseline.

**Op-ed.** Newspaper op-ed archives where licensing permits. Avoid pure news reporting; the register is different.

## What does not belong

- Newsletter chatty registers (Substack-style "hey friends" prose). Different variance signature.
- Tweet-thread compilations. Strip these out; the constraint distorts variance.
- Listicles or click-bait. Genre-marked compression.
- Editorial commentary (e.g., New York Times editorials). Often heavily smoothed by house style.

## Minimum size

10-15 pieces, each 2,000+ words. Essays run shorter than academic articles, so aim for more files to compensate.

## Personal-baseline note

For a personal blog/essay baseline, use the writer's own prior long-form posts or essays in the same register. Place those in a private personal-baseline directory and tag with project/register metadata in the manifest.
