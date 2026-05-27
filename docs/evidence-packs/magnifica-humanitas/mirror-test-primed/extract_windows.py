#!/usr/bin/env python3
"""Extract prefix windows and target continuations for the mirror test.
Pangram-flagged sections per Linch's analysis: par 7 (Babel screenshot),
par 10 (Babel syndrome quote), par 100 (genuinely helpful quote),
par 107 (alignment quote)."""
import re
from pathlib import Path

src = Path("/sessions/vibrant-charming-babbage/mnt/Claude Cowork Working Folder/Writing/stylometry sequence/magnifica-humanitas/texts/magnifica-humanitas-en.txt")
text = src.read_text(encoding="utf-8")

# Split by paragraph markers "N. " at line start (allowing leading whitespace)
para_pattern = re.compile(r'^(\d+)\.\s+', re.MULTILINE)
matches = list(para_pattern.finditer(text))
print(f"Found {len(matches)} numbered paragraphs.")

paragraphs = {}
for i, m in enumerate(matches):
    num = int(m.group(1))
    start = m.start()
    end = matches[i+1].start() if i+1 < len(matches) else len(text)
    paragraphs[num] = text[start:end].strip()

print(f"Paragraph numbers range: {min(paragraphs.keys())} to {max(paragraphs.keys())}")
print(f"Sample par 7 head: {paragraphs[7][:200]}")
print()

# Strip footnote markers like [1], [122] etc. for prediction-blind text
def clean(t):
    # Remove standalone footnote markers in brackets
    t = re.sub(r'\s*\[\d+\]\s*', ' ', t)
    # Normalize whitespace
    t = re.sub(r'\s+', ' ', t).strip()
    return t

# For each target paragraph, build:
#  prefix = text of preceding paragraphs/sections, last ~500 words
#  target = first 150 words of the target paragraph (cleaned)

def words(s, n=None):
    ws = s.split()
    return ws[:n] if n else ws

def join_paragraphs_up_to(stop_num):
    # Concatenate paragraphs from 1 up to but not including stop_num
    out = []
    for k in sorted(paragraphs.keys()):
        if k >= stop_num:
            break
        out.append(clean(paragraphs[k]))
    return " ".join(out)

targets = {
    "W1_par7_Babel": 7,
    "W2_par10_BabelSyndrome": 10,
    "W3_par100_GenuinelyHelpful": 100,
    "W4_par107_Alignment": 107,
}

windows = {}
for name, pnum in targets.items():
    full_prefix = join_paragraphs_up_to(pnum)
    # Get target paragraph cleaned and take first 150 words (continuation target)
    tgt_full = clean(paragraphs[pnum])
    # The target continuation is the FIRST 150 words of the target paragraph.
    # The opening of the target paragraph follows directly from prefix end.
    tgt_words = tgt_full.split()
    # Strip the leading "N. " marker from the target so target text starts cleanly
    if tgt_words and re.match(r'^\d+\.$', tgt_words[0]):
        tgt_words = tgt_words[1:]
    target_150 = " ".join(tgt_words[:150])
    # Prefix: last 500 words of all previous text
    prefix_words = full_prefix.split()
    prefix_500 = " ".join(prefix_words[-500:])
    windows[name] = {
        "target_para": pnum,
        "prefix_500w": prefix_500,
        "prefix_word_count": len(prefix_words),
        "target_150w": target_150,
        "target_word_count": len(target_150.split()),
    }

# Save each window's prefix and target separately
out_dir = Path("/sessions/vibrant-charming-babbage/mnt/Claude Cowork Working Folder/Writing/stylometry sequence/magnifica-humanitas/mirror_test")
out_dir.mkdir(exist_ok=True)
for name, w in windows.items():
    (out_dir / f"{name}_prefix.txt").write_text(w["prefix_500w"], encoding="utf-8")
    (out_dir / f"{name}_target.txt").write_text(w["target_150w"], encoding="utf-8")

# Summary
print("=== WINDOW SUMMARIES ===")
for name, w in windows.items():
    print(f"\n{name} (par {w['target_para']}):")
    print(f"  prefix words available: {w['prefix_word_count']} (using last 500)")
    print(f"  target words: {w['target_word_count']}")
    print(f"  prefix tail (last 30 words): ...{' '.join(w['prefix_500w'].split()[-30:])}")
    print(f"  target opening (first 30 words): {' '.join(w['target_150w'].split()[:30])}...")
