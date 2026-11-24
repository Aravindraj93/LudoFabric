# Contributing to LudoFabric

Start with a small, reproducible change. For large features, open an issue describing the problem,
expected behavior, and approach before implementation. Treat contributors respectfully.

## Setup and validation

Use Python 3.11+, create a virtual environment, and install `requirements.txt` as shown in the README.
Copy `.env.example` to `.env` for local configuration.

```powershell
python -m unittest discover -s tests -v
python -m compileall -q gameagent webapp main.py
python main.py --help
```

These checks need no Unity or provider login. For Unity changes, also validate in a disposable
project and include the Editor and package versions. Keep mock coverage separate from live AI requests.

Optional browser checks: `tools/test_office.py` requires Playwright and Chromium. It starts a separate
server and mocks jobs rather than invoking AI or Unity.

## Pull requests and bug reports

- Explain the problem, resulting behavior, and checks performed.
- Include regression coverage for recovery, state transitions, and compatibility fixes.
- Preserve checkpoint compatibility, `gameagent`, and `GameAgent.Bridge`, or supply an explicit migration.
- Retain third-party notices and document new dependencies.
- Exclude credentials, generated projects, private prompts/logs, Unity caches, and signing keys.
- For bugs, supply versions, reproduction steps, and a minimal redacted error excerpt.

Never upload `.env` or provider authentication directories. See [SECURITY.md](SECURITY.md).
