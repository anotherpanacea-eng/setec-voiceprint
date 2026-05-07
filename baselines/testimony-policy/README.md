# Testimony / Policy Baseline

Reference corpus for testimony, briefs, and policy memo register. Used by `variance_audit.py --baseline-dir baselines/testimony-policy/`.

## What belongs here

Plain-text testimony before legislative bodies, policy briefs from advocacy organizations, white papers, and amicus briefs (text portions only).

## Suggested compilation

**Public testimony archives.** DC Council hearing testimony (lims.dccouncil.gov publishes testimony for committee hearings), Congressional Record testimony, state legislative archives. Strip identifying headers, hearing metadata, and tabular data.

**Advocacy briefs.** Vera Institute publications, Sentencing Project reports, Center for American Progress briefs (open content), Brennan Center reports. Many publish CC-licensed or open-text versions.

**Amicus briefs.** Selected amicus briefs from cases of interest, with citations and tables stripped. The argument prose is what the baseline captures.

**Policy white papers.** Brookings, Urban Institute, RAND public documents.

## What does not belong

- Form letters and template testimony. The whole point of the baseline is to capture variance, not absorb the templates the skill is meant to detect.
- Press releases. Different register; usually heavily smoothed by communications staff.
- Bill text or statute language. Legal-form prose has its own conventions.
- Material with extensive tabular content. Strip tables before including.

## Minimum size

15-20 pieces, each 1,500+ words. Testimony tends to run shorter than policy briefs; mix the two for length range.

## Personal-baseline note

For a personal testimony/policy baseline, use the writer's own prior testimony, policy memos, briefs, or advocacy writing in the same institutional register. Prior work establishes:

- Function-word fingerprint specific to that written voice and institution/register
- Sentence-length distribution that matches what reads as natural testimony or policy prose for that writer
- Connective-density baseline that matches the rhetorical pacing of the writer's advocacy work

Place these outside the repo in a private personal-baseline directory with clear naming and manifest metadata. When you run the variance audit on a current draft, point at that personal directory rather than `baselines/testimony-policy/` to get the most useful comparison.

Layer A is one diagnostic. Institution-specific voice guidance and qualitative editorial criteria should live in the private project workspace rather than in this public baseline scaffold.
