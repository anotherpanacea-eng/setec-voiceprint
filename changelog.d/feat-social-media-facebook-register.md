### Changed

**The transitional `social_media_facebook` umbrella is retired before
release.** Use the audience/composition-specific
`social_media_facebook_posts` or `social_media_facebook_comments` leaf. The
retired name is a hard validator **error**, not the warning an unrecognized
register would draw, so an existing row cannot silently keep conflating
public composed posts with public responsive comments; the register
composition sweep refuses such a manifest outright. Restamp before the next
sweep.
