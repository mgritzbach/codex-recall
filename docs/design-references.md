# Design references

Reviewed on 2026-09-19. Popularity is not evidence of correctness. These are established examples, not a claim to have exhaustively ranked every Codex plugin.

| Reference | What informed this package |
| --- | --- |
| [OpenAI Plugins](https://github.com/openai/plugins) | Native `.codex-plugin/plugin.json`, discoverable `skills/`, optional companion resources and clear installation guidance |
| [Superpowers](https://github.com/obra/superpowers) | Separate skill instructions, hooks, platform installation notes, tests, licensing, contribution guidance and releases; its repository showed roughly 288,600 stars when inspected |
| [OpenAI skills catalog](https://github.com/openai/skills) | Focused skill folders and supporting scripts; the repository is deprecated and points to OpenAI Plugins |
| [Codex hooks](https://learn.chatgpt.com/docs/hooks) | Stop lifecycle, transcript path input, background execution, JSON output contract and user trust requirements |
| [Plugin packaging](https://developers.openai.com/plugins/build/plugins) | Compatibility manifest and shareable package structure |

This implementation deliberately uses no network service, embeddings, mandatory model calls or broad session-start prompt injection. The README explains the user-facing outcome and installation. The skill loads only for history tasks. Detailed operational information lives in docs. Runtime history stays outside the repository.

The shareable release contains code, synthetic tests and documentation only. A public GitHub repository has not been created by this package. A maintainer can publish the source, run the supplied CI, tag a release and distribute the release ZIP. No fabricated repository URL is included in the manifest.
