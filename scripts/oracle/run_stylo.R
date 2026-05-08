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
#   stylo_distances_phase_a.csv    Phase A delta + cosine distance matrices
#                                  computed on SETEC's frequency table
#   stylo_distances_phase_b.csv    Phase B full-pipeline distances
#   stylo_phase_b_mfw.csv          The corpus-derived MFW stylo selected
#                                  in Phase B (for comparison against
#                                  SETEC's fixed Mosteller-Wallace list)
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

cat("\nDone.\n")
