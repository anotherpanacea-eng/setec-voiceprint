# Literary Fiction Baseline

Reference corpus for literary fiction register. Used by `variance_audit.py --baseline-dir baselines/literary-fiction/`.

## What belongs here

Plain-text excerpts from literary fiction, ideally chapter-length or longer. Drafts or unedited work preferred where available.

## Suggested compilation

**Public-domain (Project Gutenberg).** Henry James (The Portrait of a Lady, The Wings of the Dove), Edith Wharton (The Age of Innocence, Ethan Frome), Virginia Woolf (To the Lighthouse, Mrs Dalloway), Faulkner (As I Lay Dying), Hemingway (The Sun Also Rises). These give the older literary baseline. Some texts in the upper Gutenberg corpus need encoding cleanup.

**Contemporary, where available.** Sample chapters that have been released for promotional purposes; out-of-print works with rights reverted; writers who self-publish in plain text. Marilynne Robinson (Gilead), Cormac McCarthy (when available), Denis Johnson, George Saunders.

**Caveat.** A baseline weighted entirely toward early-20th-century literary fiction will produce thresholds that flag contemporary literary prose as compressed. Aim for a mix.

## What does not belong

- Genre fiction (thriller, romance, fantasy, SFF). These have different variance signatures and should have their own baselines.
- Translated work. Translation flattens variance signals; the comparison is against original-English distributions.
- Edited "for clarity" anthologies. The editing introduces compression artifacts that defeat the baseline's purpose.

## Minimum size

8-10 files, each 3,000+ words. 50,000 total words is a reasonable target for a stable per-statistic mean and SD.

## File naming

Free-form. The script reads any `*.txt` files in the directory. Suggested format: `author_title-fragment.txt` (e.g., `james_portrait_ch3.txt`).
