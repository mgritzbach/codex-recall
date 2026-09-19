# Contributing

Run the unittest suite and package checks before proposing changes. Add synthetic fixtures for any new transcript schema. Never attach real conversation logs, private archive files, access tokens or personal paths to a pull request or issue.

Keep recording deterministic. Model calls, embeddings and network dependencies must never become implicit maintenance steps. Search-budget choices must remain explicit. Test incremental behavior, crash recovery, duplicate representations, archived tasks and platform-specific paths when touching the parser or installer.

The skill should remain short. Detailed implementation and scheduling guidance belongs in docs. Document observable limitations instead of promising complete recall.

For a release: update the manifest and CLI version together, run CI, create a version tag, and attach a source ZIP containing only tracked files. Do not include runtime data. If the project gets a public GitHub home, add the actual verified repository URL to the manifest and README then.
