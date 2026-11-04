### Changed

Move the optional Brysbaert fetcher into `setec.surfaces` with its historical
launcher and unchanged opt-in acquisition/conversion behavior. Resolve its
default output through the existing plugin data path so the fetcher and loader
continue sharing the same install location and validation floor.
