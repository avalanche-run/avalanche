# Installation

```sh
uv add avalanche-ai
uv run ava --help
```

Plain installation keeps workflow execution, `ava dev`, the local operator and TUI.
Add only the features your project uses; combine names, for example
`uv add 'avalanche-ai[agents,integrations]'`.

| Extra | Purpose |
| --- | --- |
| `agents` | PredictRLM agent steps, including the Codex LM and Deno runtime. |
| `dataframes` | Polars dataframes, Dataframely typed schemas and Arrow conversion. |
| `integrations` | MCP server connections and OAuth logins. |
| `typesafe` | TypeSafe classification and evaluation. |
| `openai` | OpenAI agent runtime and Decisions classifier; includes `agents`. |
| `gemini` | Gemini Decision classifier. |
| `claude` | Claude agent runtime; includes `agents`. |

Starter projects select `agents` for their included agent step. Other projects
must declare their own extras. Missing feature dependencies report the matching
`uv add 'avalanche-ai[<extra>]'` command on first use.
