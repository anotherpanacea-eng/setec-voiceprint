### Changed

- distinct-diversity and homogeneity count words with the shared `count_words_alpha`, which behaves identically to their former tokenizer-based counter (same `[A-Za-z']+` tokens). Their public `_word_count` and `word_tokens` names are unchanged.
