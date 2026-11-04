# Lessons

- [2026-11-04] Wrote the literal Anthropic key prefix in a commit message (6459ba7) while describing a key check, so the history audit grep now reports a false positive on a clean repo → Never write secret-prefix literals (Anthropic, OpenAI, GitHub token prefixes) in commit messages, docs, or code comments; name them in words instead. To audit file contents only, skip commit messages and split the pattern so this file never matches itself: `git log --all -p --format= | grep -c 'sk''-ant'`.
