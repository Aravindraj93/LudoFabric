# Recovering interrupted jobs

On Windows, a dashboard reader, antivirus scanner, or other process can briefly lock a checkpoint
against replacement. Writes retry for a short bounded interval. If replacement stays blocked,
the old checkpoint remains intact and the new JSON is preserved in a `.tmp` file whose path appears
in the error. Close the program holding the file and check write/delete permissions before resuming.
Do not replace a checkpoint from a temporary file without first checking which version is newer.


Progress belongs to the project, not an AI chat session. Keep both folders together:

```text
UnityProjects/
  CoinDash/             # Unity assets, packages and settings
  CoinDash.agent/       # request, design, plan, task checkpoints, answers and logs
```

The request is saved before the first model call. State is written atomically; completed tasks
remain completed on restart. Exact AI requests have a disk response cache, and production task
outputs are journaled before applying files, allowing interrupted writes to be replayed.
This is recovery at task/phase boundaries, not restoration of an interrupted model's hidden reasoning.
An unfinished model response must be requested again. Back up the Unity project and `.agent` folder together.

In the dashboard, select a saved project and use **Continue planning**, **Build in Unity**, or **Resume**.
For a checkpoint outside the configured projects root, use **Open saved project** and paste its full
Unity project folder path. The folder name must currently contain only letters, numbers, `_` or `-`.
A Unity project without its adjacent `.agent` checkpoint cannot automatically recover its original plan.
Project registrations and last job status survive restarts in the local, git-ignored `.studio/` folder.

```powershell
python main.py resume --project "C:\UnityProjects\CoinDash"
```

By default, an AI usage limit saves the pending request and pauses the job immediately. Close the tool,
wait for your account allowance to reset, reopen it and resume the project. No background work occurs
when the server is closed. The UI's suggested retry time uses a configurable delay, not provider quota telemetry.

```dotenv
CODEX_WAIT_ON_LIMIT=0
CODEX_LIMIT_RETRY_MINUTES=300
```

To keep the worker open and retry every 15 minutes instead, set `CODEX_WAIT_ON_LIMIT=1` and configure
`CODEX_LIMIT_WAIT_MINUTES`. Account limits can include weekly allowances, so five hours is not a
guarantee of recovery. Changing provider is an explicit user choice; the tool does not bypass limits.

The Codex integration uses `codex exec --ignore-user-config` so it retains CLI authentication while
avoiding inherited app model, plugin and MCP settings. Leave `MODEL_*` empty to use the CLI default,
or set a model your account supports. An older CLI without this flag must be updated.

