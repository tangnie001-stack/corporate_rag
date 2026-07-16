Status: DONE
Commits:
- 1f7b46f feat: integrate ChunkQualityScorer into document upload pipeline
Verification:
- ruff: All checks passed!
- compile: OK (exit 0)
Concerns: The core integration code (imports, eval block, dedup copy) was already present in HEAD~1 from earlier task sessions. This commit applies formatting/line-length compliance on top of that foundation.
