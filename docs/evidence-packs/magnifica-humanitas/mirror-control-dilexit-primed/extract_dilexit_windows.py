#!/usr/bin/env python3
"""Extract control windows from Dilexit Nos (Francis 2024), parallel methodology to Magnifica.

ARCHIVAL PROVENANCE: this script ran in the operator's working folder
when the evidence pack was produced; preserved here as the recipe
that generated the published prefix/target files in this directory.
The source corpus is intentionally NOT shipped in this repo (see the
publish handoff's do-not-publish list). To rerun: set SRC to your
own copy of Dilexit Nos (or override via the SRC env var) and OUT_DIR
to your desired output path.
"""
import os
import re
from pathlib import Path

src = Path(os.environ.get("SRC", "texts/dilexit-nos-en.txt"))
text = src.read_text(encoding="utf-8")
para_pattern = re.compile(r'^(\d+)\.\s+', re.MULTILINE)
matches = list(para_pattern.finditer(text))
paragraphs = {}
for i, m in enumerate(matches):
    num = int(m.group(1))
    start = m.start()
    end = matches[i+1].start() if i+1 < len(matches) else len(text)
    paragraphs[num] = text[start:end].strip()

print(f"Dilexit Nos: {len(paragraphs)} paragraphs.")
print(f"Range: {min(paragraphs.keys())} to {max(paragraphs.keys())}")

def clean(t):
    t = re.sub(r'\s*\[\d+\]\s*', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def join_paragraphs_up_to(stop_num):
    out = []
    for k in sorted(paragraphs.keys()):
        if k >= stop_num: break
        out.append(clean(paragraphs[k]))
    return " ".join(out)

# Parallel sampling: par 7, par 10, par 100, par 107
targets = {"C1_par7": 7, "C2_par10": 10, "C3_par100": 100, "C4_par107": 107}
windows = {}
for name, pnum in targets.items():
    full_prefix = join_paragraphs_up_to(pnum)
    tgt_full = clean(paragraphs[pnum])
    tgt_words = tgt_full.split()
    if tgt_words and re.match(r'^\d+\.$', tgt_words[0]):
        tgt_words = tgt_words[1:]
    target_150 = " ".join(tgt_words[:150])
    prefix_words = full_prefix.split()
    prefix_500 = " ".join(prefix_words[-500:])
    windows[name] = {"target_para": pnum, "prefix_500w": prefix_500,
                     "prefix_word_count": len(prefix_words), "target_150w": target_150,
                     "target_word_count": len(target_150.split())}

out_dir = Path(os.environ.get("OUT_DIR", "."))
out_dir.mkdir(exist_ok=True)
for name, w in windows.items():
    (out_dir / f"{name}_prefix.txt").write_text(w["prefix_500w"], encoding="utf-8")
    (out_dir / f"{name}_target.txt").write_text(w["target_150w"], encoding="utf-8")

print("=== DILEXIT NOS CONTROL WINDOWS ===")
for name, w in windows.items():
    print(f"\n{name} (par {w['target_para']}):")
    print(f"  prefix tail (last 30 words): ...{' '.join(w['prefix_500w'].split()[-30:])}")
    print(f"  target opening (first 30 words): {' '.join(w['target_150w'].split()[:30])}...")
    print(f"  target word count: {w['target_word_count']}")
