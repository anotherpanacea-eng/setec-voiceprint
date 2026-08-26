# Dependency evaluation for Voiceprint #396

This packet evaluates three dependency seams that already exist in Voiceprint:
optional Trafilatura extraction, optional datasketch MinHash-LSH document
deduplication, and a proposed RapidFuzz confirmation step. It does not change
production dependencies or runtime behavior.

## Decisions

| Seam | Decision | Evidence |
| --- | --- | --- |
| Trafilatura 2.1.0 main-content extraction | Reject as the preferred extraction path; leave production unchanged pending removal/fix | It was deterministic, fail-soft, retained every required marker, leaked no forbidden markers, and its 3.36x median runtime remained below the 5x ceiling. However, the shipped BeautifulSoup path retained all six expected titles while the Trafilatura-enabled seam retained only one. That fails the predeclared no-title-regression gate. |
| Trafilatura feed extraction as a `feedparser` replacement | Reject | RSS and Atom link sets matched, but order differed for both and RSS deduplication differed. Link-only output also cannot provide the shipped `FeedItem` fields: title, date, body HTML, paid status, or raw byte length. |
| Exact-confirmed datasketch document seam (#407) | Keep as optional destructive hygiene; MinHash remains candidate infrastructure only | All locked tracks produced zero false-positive drops because production exact-confirmed every candidate edge. Candidate false negatives remain: 1.6.5 and 2.0.0 legacy retained 6 oracle duplicates; 2.0.0 default retained 3. This is not an exhaustive uniqueness certificate, and candidate recall can still change across a version/scheme boundary. |
| Persisted MinHash/LSH state | Rebuild or refuse across any unproven version/scheme boundary | The 1.6.5 signature matched 2.0.0 only with `scheme="legacy"`; 2.0.0's `affine32` default differed. The official 2.0 migration contract likewise requires rebuilding indexes when adopting the new default. |
| RapidFuzz 3.14.5 | Reject; do not add the dependency | The predeclared `fuzz.ratio(..., processor=None)` cutoff of 85 failed the accuracy gate and cannot recover candidates omitted by LSH. Its median score-only time was 6.88 s versus 0.94 s for exact Jaccard over the same already-materialized inputs, about 7.29x slower. |
| No-datasketch fallback | Keep current ownership | Base import remained clean; document mode and passage Stage A returned the existing clear optional-dependency error; exact passage Stage B remained available. Voiceprint may own approximate candidate generation, but a safety-relevant consumer must retain or recheck source-bound exact evidence. |

The #407 repair implements the packet's safe slice: explicit signature identity,
ephemeral index rebuild policy, LSH candidate generation, and production-observed
exact confirmation before destructive union. The locked rerun requires zero
false-positive drops while recording candidate misses rather than treating them
as permission to claim exhaustive uniqueness.

## Reproduce on macOS

The packet requires CPython 3.13.7. Network access is permitted only during the
bootstrap:

```sh
cd research/dependency-evaluations/issue-396
./bootstrap.sh
./evaluate-offline.sh --verify-fixtures
./evaluate-offline.sh
./evaluate-offline.sh --check
```

`bootstrap.sh` downloads artifacts through hash-locked requirement files. Because
`sgmllib3k` is published only as a source distribution, bootstrap builds its
pure-Python wheel with a fixed source epoch and hash-locked setuptools/wheel
backend, and refuses any output other than the recorded SHA-256.
`evaluate-offline.sh` then recreates three isolated environments
using `--no-index --require-hashes`, proves the macOS `sandbox-exec` network-denial
profile blocks a connection, and runs the controller inside that profile.

`results.json` is the canonical, byte-stable semantic record. It excludes paths,
timestamps, and performance samples. `timing.json` is the explicitly
non-byte-stable measurement record. `--check` reruns the semantic evaluation and
refuses any drift from `results.json`. Fixture hashes and redistribution metadata
are bound by `fixtures/manifest.json`; `--verify-fixtures` refuses corruption
before any score is produced.

## Accuracy and compatibility

The exhaustive corpus contains exactly 200 documents and 19,900 unordered pairs.
Six controls are the visible text of the redistributed public HTML fixtures; the
remaining cases are deterministic synthetic transformations.
Its 70 exact-Jaccard-positive pairs include exact duplicates, truncations,
boilerplate additions, reordered passages, examples immediately above and below
0.85, unrelated controls, and ten explicit A-B/B-C/A-not-C chains. Pair truth uses
the repository's real five-word shingle sets; cluster truth is the connected
components of only those exact-positive edges.

| Shipped/research track | Exact-edge precision | Exact-edge recall | False-positive drops | Retained oracle drops | Exact keep/drop |
| --- | ---: | ---: | ---: | ---: | --- |
| datasketch 1.6.5 default | 1.000000 | 0.914286 | 0 | 6 | No |
| datasketch 2.0.0 default (`affine32`) | 1.000000 | 0.957143 | 0 | 3 | No |
| datasketch 2.0.0 research-only `legacy` adapter | 1.000000 | 0.914286 | 0 | 6 | No |

`results.json` separately records raw LSH candidates, production-observed exact
confirmation edges, and all final co-clustered pairs, with precision, recall,
and complete FP/FN ledgers for each layer. It also records false-positive drops
and oracle duplicates retained because of candidate misses. The tracing wrapper
delegates to the real production classes, and every traced keep/drop result
matched an unwrapped control run.

## Mac scale results

Scale measurements are performance evidence, not an all-pairs accuracy claim.
Every scale document alternates words from a redistributed public fixture with
record-specific deterministic tokens; known duplicate pairs reuse the identical
mixed text.
The small rung has 1,000 documents, 50 known duplicate families, and 10,000
fixed-seed sampled negatives; the large rung has 5,000 documents, 250 families,
and the same negative sample size. All tracks recovered every known family,
produced zero sampled-negative false positives, stayed deterministic on repeated
small runs, completed within 120/600 seconds, and stayed below 4 GiB.

| Track | 1,000-doc median | Small peak RSS | 5,000-doc time | Large peak RSS |
| --- | ---: | ---: | ---: | ---: |
| datasketch 1.6.5 | 4.726 s | 131.2 MiB | 24.768 s | 318.5 MiB |
| datasketch 2.0.0 default | 6.718 s | 132.2 MiB | 30.088 s | 314.3 MiB |
| datasketch 2.0.0 legacy | 4.874 s | 134.2 MiB | 19.431 s | 332.7 MiB |

These bounded synthetic results do not establish behavior on larger or
adversarial corpora. A timeout or memory-ceiling breach is serialized as a
measured failure rather than skipped.

## Package and redistribution record

The evaluation locks exact artifacts and transitive versions for the tested
platform. Top-level metadata at evaluation time:

| Package | Version | Python metadata | License | Role |
| --- | --- | --- | --- | --- |
| Trafilatura | 2.1.0 | `>=3.10` | Apache-2.0 | Optional HTML/feed experiment; releases before 1.8 were GPL, so only the Apache-licensed boundary was evaluated. |
| Beautiful Soup | 4.14.3 | `>=3.7` | MIT | Current extraction control. |
| feedparser | 6.0.12 | `>=3.6` | BSD-2-Clause | Current feed-contract control. |
| datasketch | 1.6.5 | package metadata has no `Requires-Python` field | MIT | Current-behavior baseline. |
| datasketch | 2.0.0 | `>=3.9` | MIT | Upgrade/default-scheme track. |
| RapidFuzz | 3.14.5 | `>=3.10` | MIT | Proposed string confirmation experiment. |
| NumPy / SciPy | 2.5.2 / 1.18.0 | `>=3.12` | see package metadata and lock artifacts | Exact transitive versions for both datasketch environments. |

Primary references are the [Trafilatura 2.1.0 package record](https://pypi.org/project/trafilatura/2.1.0/),
[datasketch 2.0.0 package record](https://pypi.org/project/datasketch/2.0.0/),
[datasketch 2.0 migration documentation](https://ekzhu.com/datasketch/minhash.html),
and [RapidFuzz package record](https://pypi.org/project/RapidFuzz/).
Every checked-in fixture has its own source URL, retrieval date, attribution,
license identifier, license-evidence URL, and SHA-256 in the manifest. Federal
works are labeled `LicenseRef-US-Government-Work`; only locally authored fixture
arrangements are released as CC0.

## Limitations

- Synthetic transformations and mixed public/synthetic scale documents make edge
  cases reproducible but do not estimate their prevalence in a production corpus.
- The sampled-negative scale check is intentionally not exhaustive.
- Timing/RSS values are one Apple-silicon Mac snapshot and are not byte-stable.
- The research-only 2.0 `legacy` adapter proves compatibility with this locked
  workload; it does not authorize reading mixed or unidentified persisted state.
- No pickle crosses environment boundaries. Cross-version IPC is JSON plus raw
  fixture bytes, and LSH indexes are rebuilt per worker.
