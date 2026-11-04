### Fixed

Give the zero-install export fixture key a private Windows ACL. The bare-copy
gate now gives its synthetic export key an owner-only Windows ACL, matching the
exporter's existing test fixture. The real author_corpus_export privacy check
and expected successful envelope stay unchanged. This repairs the native
author-export fixture; Gmail pipeline's separate native failure remains open.
