# Atomic iMessage exporter seam fixture

This public fixture contains synthetic prose only. It has the retained-row layout
and sidecar fields consumed by `author_corpus_export` after an atomic acquisition
run. The three events are intentionally all dated `2020-01-02`:

- two independent events belong to one chat;
- one event belongs to a different chat;
- the first and third have different exact bytes but the same normalized text.

The exporter test copies this tree under its private test root, then proves that
bounded export selects one event rather than closing over its chat/day peer. The
paired Voicewright fixture is generated from this tree through the exporter; its
non-skipping test loads that package through the real `plan_register_splits` to
verify the downstream duplicate-component lock.

The paired package is frozen at package hash
`sha256:8a8228672661f0f7391457f3e741521c8433b155525f492a9d4fabc732322f88`
and source snapshot
`sha256:28aac74db65eaebd9f83e45c49ce1a06385d54006c930db81f8658d265a45222`;
the producer test pins both values.
