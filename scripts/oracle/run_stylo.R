#!/usr/bin/env Rscript
# run_stylo.R
# R `stylo` side of the stylometry oracle test (issue #4).
#
# Two phases:
#
#   Phase A: distance correctness on identical input.
#     Read SETEC's frequency table from setec_function_word_freqs.csv,
#     run stylo::dist.delta and stylo::dist.cosine on it, and write
#     the resulting distance matrices. This isolates the distance math
#     from feature-selection and tokenization differences.
#
#   Phase B: end-to-end on raw text.
#     Have stylo run its own pipeline (its own tokenization, its own
#     corpus-derived MFW selection at the same N as SETEC's fixed list)
#     against the raw .txt files in scripts/test_data/federalist_oracle/.
#     The resulting distance matrix shows what stylo would report on
#     the same fixture if a user ran it natively.
#
# Outputs (under scripts/oracle/results/):
#
#   stylo_distances_phase_a.csv               Phase A function-word delta +
#                                             cosine, computed on SETEC's freq
#                                             table
#   stylo_distances_phase_b.csv               Phase B full-pipeline distances
#                                             (function words)
#   stylo_phase_b_mfw.csv                     The corpus-derived MFW stylo
#                                             selected in Phase B
#   stylo_distances_phase_a_char{3,4,5}.csv   Phase A per-n char-ngram delta +
#                                             cosine on SETEC's per-n freq
#                                             tables
#   stylo_distances_phase_a_pos_trigrams.csv  Phase A POS-trigram delta +
#                                             cosine on SETEC's freq table
#   stylo_distances_phase_a_dep_ngrams.csv    Phase A dep-n-gram delta +
#                                             cosine on SETEC's freq table
#   stylo_pos_trigram_freqs.csv               Phase A' POS-trigram freq table
#                                             rebuilt from parse TSVs in R
#                                             (compare cell-by-cell against
#                                             SETEC's setec_pos_trigram_freqs)
#   stylo_dep_ngram_freqs.csv                 Phase A' dep-n-gram freq table
#                                             rebuilt from parse TSVs in R
#
# Run:
#
#   Rscript scripts/oracle/run_stylo.R
#
# Prerequisites:
#
#   install.packages("stylo")     # one-time

suppressPackageStartupMessages({
  if (!requireNamespace("stylo", quietly = TRUE)) {
    stop("R package 'stylo' is not installed. Run install.packages('stylo') and re-run.")
  }
  library(stylo)
})

# Resolve this script's directory regardless of invocation: Rscript
# (commandArgs trailingOnly=FALSE includes "--file=") or source().
get_script_dir <- function() {
  args <- commandArgs(trailingOnly = FALSE)
  file_arg <- grep("^--file=", args, value = TRUE)
  if (length(file_arg) > 0) {
    return(dirname(normalizePath(sub("^--file=", "", file_arg[1]))))
  }
  if (sys.nframe() >= 1) {
    fr <- try(sys.frame(1), silent = TRUE)
    if (!inherits(fr, "try-error") && !is.null(fr$ofile)) {
      return(dirname(normalizePath(fr$ofile)))
    }
  }
  normalizePath(".")
}
here <- get_script_dir()
output_dir <- file.path(here, "results")
dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)

repo_root <- normalizePath(file.path(here, "..", ".."))
fixture_dir <- file.path(repo_root, "scripts", "test_data", "federalist_oracle")
freq_csv <- file.path(output_dir, "setec_function_word_freqs.csv")

if (!file.exists(freq_csv)) {
  stop(paste0(
    "Frequency table CSV not found at ", freq_csv, ". ",
    "Run scripts/oracle/setec_to_stylo.py first."
  ))
}

# ---- Phase A: distance computation on SETEC's frequency table ------

cat("Phase A: reading SETEC's frequency table from\n  ", freq_csv, "\n", sep = "")
freq_df <- read.csv(freq_csv, stringsAsFactors = FALSE, check.names = FALSE)
doc_ids <- freq_df$doc_id
freq_mat <- as.matrix(freq_df[, -1, drop = FALSE])
rownames(freq_mat) <- doc_ids
mode(freq_mat) <- "numeric"
cat("  ", nrow(freq_mat), "documents x ", ncol(freq_mat), "function words\n",
    sep = "")

# stylo::dist.delta expects rows = documents, cols = features. Z-scores
# the columns across the corpus, then takes mean absolute difference
# pairwise. Returns a 'dist' object.
phase_a_delta <- as.matrix(stylo::dist.delta(freq_mat))
phase_a_cosine <- as.matrix(stylo::dist.cosine(freq_mat))

# Write a long-format CSV mirroring SETEC's setec_distances.csv shape.
write_long <- function(mat, metric, path, append = FALSE) {
  rn <- rownames(mat)
  cn <- colnames(mat)
  rows <- list()
  for (i in seq_along(rn)) {
    for (j in seq_along(cn)) {
      rows[[length(rows) + 1L]] <- data.frame(
        doc_a = rn[i], doc_b = cn[j], metric = metric,
        value = sprintf("%.10f", mat[i, j]),
        stringsAsFactors = FALSE
      )
    }
  }
  out_df <- do.call(rbind, rows)
  write.table(
    out_df, path,
    sep = ",", row.names = FALSE,
    col.names = !append, append = append, quote = TRUE
  )
}

phase_a_path <- file.path(output_dir, "stylo_distances_phase_a.csv")
file.create(phase_a_path)  # truncate
write_long(phase_a_delta, "burrows_delta", phase_a_path, append = FALSE)
write_long(phase_a_cosine, "cosine_distance", phase_a_path, append = TRUE)
cat("  wrote ", phase_a_path, "\n", sep = "")

# ---- Phase B: full pipeline on raw text ----------------------------

cat("\nPhase B: stylo's full pipeline on raw .txt files in\n  ",
    fixture_dir, "\n", sep = "")

txt_paths <- list.files(
  fixture_dir, pattern = "\\.txt$", full.names = TRUE
)

# stylo::load.corpus reads the directory; corpus tokenization uses
# stylo's defaults (lowercase, alphanumeric, no stopword filtering).
old_wd <- getwd()
setwd(fixture_dir)
on.exit(setwd(old_wd), add = TRUE)

corpus <- stylo::load.corpus(
  files = basename(txt_paths),
  corpus.dir = ".",
  encoding = "UTF-8"
)
parsed <- stylo::txt.to.words(corpus)

# Match the size of SETEC's function-word vocabulary so the comparison
# stays at the same dimensionality.
mfw_n <- ncol(freq_mat)
freq_list <- stylo::make.frequency.list(parsed, head = mfw_n)
freq_table_b <- stylo::make.table.of.frequencies(parsed, features = freq_list)

cat("  stylo selected ", length(freq_list), " corpus-derived MFW (top by ",
    "total frequency)\n", sep = "")

phase_b_delta <- as.matrix(stylo::dist.delta(freq_table_b))
phase_b_cosine <- as.matrix(stylo::dist.cosine(freq_table_b))

# Strip stylo's default ".txt" suffix from row/col names so they
# line up with SETEC's doc_ids.
clean_names <- function(x) sub("\\.txt$", "", x)
rownames(phase_b_delta) <- clean_names(rownames(phase_b_delta))
colnames(phase_b_delta) <- clean_names(colnames(phase_b_delta))
rownames(phase_b_cosine) <- clean_names(rownames(phase_b_cosine))
colnames(phase_b_cosine) <- clean_names(colnames(phase_b_cosine))

phase_b_path <- file.path(output_dir, "stylo_distances_phase_b.csv")
file.create(phase_b_path)
write_long(phase_b_delta, "burrows_delta", phase_b_path, append = FALSE)
write_long(phase_b_cosine, "cosine_distance", phase_b_path, append = TRUE)
cat("  wrote ", phase_b_path, "\n", sep = "")

mfw_path <- file.path(output_dir, "stylo_phase_b_mfw.csv")
write.csv(
  data.frame(rank = seq_along(freq_list), word = freq_list,
             stringsAsFactors = FALSE),
  mfw_path, row.names = FALSE
)
cat("  wrote ", mfw_path, "\n", sep = "")

# ---- Phase A char-ngrams: distance correctness on identical input --

# SETEC separates char-ngrams into per-n families (3, 4, 5) with
# per-n caps (default 200) and per-n normalization. The oracle
# reflects that: one frequency table per n, each tested independently.
# This phase tests distance-math correctness on identical input; an
# end-to-end Phase B for char-ngrams (stylo's own char-ngram
# tokenization vs. SETEC's) is roadmap.

cat("\nPhase A char-ngrams: distance correctness on identical input\n")
char_ngram_ns <- c(3, 4, 5)
for (n in char_ngram_ns) {
  in_csv <- file.path(output_dir, sprintf("setec_char%d_freqs.csv", n))
  if (!file.exists(in_csv)) {
    cat(sprintf("  skip n=%d: %s missing\n", n, basename(in_csv)))
    next
  }
  char_df <- read.csv(in_csv, stringsAsFactors = FALSE, check.names = FALSE)
  doc_ids_char <- char_df$doc_id
  char_mat <- as.matrix(char_df[, -1, drop = FALSE])
  rownames(char_mat) <- doc_ids_char
  mode(char_mat) <- "numeric"

  delta <- as.matrix(stylo::dist.delta(char_mat))
  cosine <- as.matrix(stylo::dist.cosine(char_mat))

  out_csv <- file.path(
    output_dir, sprintf("stylo_distances_phase_a_char%d.csv", n)
  )
  file.create(out_csv)
  write_long(delta, "burrows_delta", out_csv, append = FALSE)
  write_long(cosine, "cosine_distance", out_csv, append = TRUE)
  cat(sprintf(
    "  n=%d: %d documents x %d char-ngrams -> %s\n",
    n, nrow(char_mat), ncol(char_mat), basename(out_csv)
  ))
}

# ---- Phase A POS / dep: distance correctness on identical input ----
#
# stylo doesn't natively do POS or dependency parsing, so the parser
# of record stays spaCy on the SETEC side. What this block tests is
# the *distance math* on the per-document frequency tables SETEC
# produces. Phase A' below tests *frequency-table construction* by
# rebuilding the n-grams in R from the parse TSVs and comparing
# cell-by-cell against SETEC's exports.

cat("\nPhase A POS / dep: distance correctness on identical input\n")
pos_dep_families <- list(
  list(
    name = "pos_trigrams",
    in_csv = "setec_pos_trigram_freqs.csv",
    out_csv = "stylo_distances_phase_a_pos_trigrams.csv"
  ),
  list(
    name = "dep_ngrams",
    in_csv = "setec_dep_ngram_freqs.csv",
    out_csv = "stylo_distances_phase_a_dep_ngrams.csv"
  )
)
for (fam in pos_dep_families) {
  in_path <- file.path(output_dir, fam$in_csv)
  if (!file.exists(in_path)) {
    cat(sprintf("  skip %s: %s missing (run setec_to_stylo.py with spaCy)\n",
                fam$name, fam$in_csv))
    next
  }
  fam_df <- read.csv(in_path, stringsAsFactors = FALSE, check.names = FALSE)
  doc_ids_fam <- fam_df$doc_id
  fam_mat <- as.matrix(fam_df[, -1, drop = FALSE])
  rownames(fam_mat) <- doc_ids_fam
  mode(fam_mat) <- "numeric"

  delta <- as.matrix(stylo::dist.delta(fam_mat))
  cosine <- as.matrix(stylo::dist.cosine(fam_mat))

  out_path <- file.path(output_dir, fam$out_csv)
  file.create(out_path)
  write_long(delta, "burrows_delta", out_path, append = FALSE)
  write_long(cosine, "cosine_distance", out_path, append = TRUE)
  cat(sprintf(
    "  %s: %d documents x %d features -> %s\n",
    fam$name, nrow(fam_mat), ncol(fam_mat), basename(out_path)
  ))
}

# ---- Phase A' POS / dep: independent n-gramming from parse TSVs ----
#
# Read SETEC's per-document spaCy parse exports (parses/<doc_id>.tsv)
# and rebuild POS-trigram and dep-n-gram frequency tables in R from
# scratch. Same per-sentence reset, same key format, same top-K
# corpus-derived selection. Compare.py reads both SETEC's
# setec_pos_trigram_freqs.csv and the stylo_pos_trigram_freqs.csv
# this block writes; cell-by-cell agreement verifies the
# n-gramming + frequency-table-construction code path independently
# of the spaCy parse itself (which is the parser of record on both
# sides).

cat("\nPhase A' POS / dep: independent n-gramming from parse TSVs\n")

pos_dep_top_k <- 300

# Rebuild POS-trigram frequencies for one document's parse records.
# Mirrors stylometry_core.pos_trigram_features: per-sentence reset,
# is_space tokens already filtered out by setec_to_stylo.py before
# the TSV was written.
build_pos_trigrams <- function(parse_df) {
  keys <- character()
  by_sent <- split(parse_df$pos, parse_df$sent_idx)
  for (tags in by_sent) {
    n_t <- length(tags)
    if (n_t >= 3) {
      seq_idx <- seq_len(n_t - 2)
      grams <- sprintf("pos:%s-%s-%s",
                       tags[seq_idx], tags[seq_idx + 1], tags[seq_idx + 2])
      keys <- c(keys, grams)
    }
  }
  if (length(keys) == 0) return(setNames(numeric(0), character(0)))
  tab <- table(keys)
  total <- sum(tab)
  setNames(as.numeric(tab) / total, names(tab))
}

# Rebuild dep-n-gram frequencies. Single normalization pool spanning
# n=2 and n=3 (matching stylometry_core.dependency_ngram_features).
build_dep_ngrams <- function(parse_df, ns = c(2, 3)) {
  keys <- character()
  by_sent <- split(parse_df$dep, parse_df$sent_idx)
  for (labels in by_sent) {
    n_l <- length(labels)
    for (n in ns) {
      if (n_l >= n) {
        seq_idx <- seq_len(n_l - n + 1)
        grams <- vapply(seq_idx, function(i) {
          paste(labels[i:(i + n - 1)], collapse = "-")
        }, character(1))
        keys <- c(keys, sprintf("dep%d:%s", n, grams))
      }
    }
  }
  if (length(keys) == 0) return(setNames(numeric(0), character(0)))
  tab <- table(keys)
  total <- sum(tab)
  setNames(as.numeric(tab) / total, names(tab))
}

# Aggregate per-doc named freq vectors into a wide top-K corpus-table.
# corpus_counts is the sum of relative-frequency contributions across
# documents (matches the SETEC-side ranking proxy in char_ngram_table /
# pos_trigram_table). Top-K by this rank, then per-doc renormalize
# within the subset so each row sums to ~1.0.
build_corpus_table <- function(per_doc, top_k) {
  all_feats <- unique(unlist(lapply(per_doc, names)))
  corpus_counts <- setNames(numeric(length(all_feats)), all_feats)
  for (feats in per_doc) {
    if (length(feats) > 0) {
      corpus_counts[names(feats)] <- corpus_counts[names(feats)] + feats
    }
  }
  ord <- order(corpus_counts, decreasing = TRUE)
  top <- names(corpus_counts)[ord][seq_len(min(top_k, length(corpus_counts)))]

  doc_ids <- names(per_doc)
  mat <- matrix(0, nrow = length(doc_ids), ncol = length(top),
                dimnames = list(doc_ids, top))
  for (doc_id in doc_ids) {
    feats <- per_doc[[doc_id]]
    if (length(feats) == 0) next
    common <- intersect(names(feats), top)
    if (length(common) > 0) {
      mat[doc_id, common] <- feats[common]
    }
    row_total <- sum(mat[doc_id, ])
    if (row_total > 0) {
      mat[doc_id, ] <- mat[doc_id, ] / row_total
    }
  }
  mat
}

write_freq_table_csv <- function(mat, out_path) {
  df <- data.frame(doc_id = rownames(mat), mat, check.names = FALSE,
                   stringsAsFactors = FALSE)
  # Format numeric columns to match the SETEC-side .10f precision so
  # cell-by-cell comparison in compare.py reads as floating-point
  # noise rather than print-formatting drift.
  for (col in colnames(mat)) {
    df[[col]] <- sprintf("%.10f", mat[, col])
  }
  write.csv(df, out_path, row.names = FALSE, quote = TRUE)
}

parse_dir <- file.path(output_dir, "parses")
if (!dir.exists(parse_dir)) {
  cat(sprintf("  skip: %s missing (run setec_to_stylo.py with spaCy first)\n",
              "parses/"))
} else {
  tsv_paths <- list.files(parse_dir, pattern = "\\.tsv$", full.names = TRUE)
  if (length(tsv_paths) == 0) {
    cat("  skip: no parse TSVs found\n")
  } else {
    pos_per_doc <- list()
    dep_per_doc <- list()
    for (tsv in tsv_paths) {
      doc_id <- sub("\\.tsv$", "", basename(tsv))
      parse_df <- read.delim(tsv, stringsAsFactors = FALSE,
                             check.names = FALSE, quote = "")
      pos_per_doc[[doc_id]] <- build_pos_trigrams(parse_df)
      dep_per_doc[[doc_id]] <- build_dep_ngrams(parse_df)
    }

    pos_mat <- build_corpus_table(pos_per_doc, pos_dep_top_k)
    dep_mat <- build_corpus_table(dep_per_doc, pos_dep_top_k)

    pos_path <- file.path(output_dir, "stylo_pos_trigram_freqs.csv")
    dep_path <- file.path(output_dir, "stylo_dep_ngram_freqs.csv")
    write_freq_table_csv(pos_mat, pos_path)
    write_freq_table_csv(dep_mat, dep_path)
    cat(sprintf("  POS-trigrams: %d documents x %d features -> %s\n",
                nrow(pos_mat), ncol(pos_mat), basename(pos_path)))
    cat(sprintf("  Dep-n-grams: %d documents x %d features -> %s\n",
                nrow(dep_mat), ncol(dep_mat), basename(dep_path)))
  }
}

cat("\nDone.\n")
