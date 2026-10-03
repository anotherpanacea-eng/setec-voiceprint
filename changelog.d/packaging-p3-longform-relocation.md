### Changed

Move the whole narrative long-form segmenter and StoryScope polarity contract
into `setec.core`, retaining ordinary legacy alias launchers and unchanged
segmentation, projection, refusal and existing digest behavior. Preserve legacy
record/class/exception serialization globals and package-only annotation lookup.
Adjust only the two exact launcher anchors in the existing migration check and
count both necessary detached-runpy bootstraps in the existing sys.path ceiling.
No caller, capability, fixture, model, training or study change. This is a P3
constituent under GX-A2/Fleet73, not complete packaging qualification; PATCH class.
