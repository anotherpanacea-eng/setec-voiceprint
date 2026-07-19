### Fixed

**`acquire_imessage_sent_atomic.py` — scope completed-run identity validation to message bodies.** The validator now scans only free-form `.txt` row bodies, requires non-digit boundaries around digit-only identifiers, and accepts a canonical owner-adjudication file that excludes rejected rows from the identity scan while reporting only aggregate counts and the file digest.
