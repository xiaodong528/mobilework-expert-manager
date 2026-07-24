# Legacy Compliance Plugin

The reviewer loads policy, checks API diffs, records evidence, and returns pass, fail, or needs-review.
The old host expected files under `${CLAUDE_PLUGIN_ROOT}` and `.claude/settings.local.json`; those paths are not portable.
