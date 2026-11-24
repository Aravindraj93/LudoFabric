# Web, Windows and cloud architecture


The current product is a local Python worker with a browser dashboard. The worker already reads local
files and launches Unity; a desktop wrapper is not needed to enable those capabilities. Closing a browser
tab leaves the server running. Closing the server interrupts execution; checkpoints enable later resume.

Recommended development order:

1. Keep one worker, checkpoint format and HTTP API for every frontend. The recovery implementation above
   is the first step. Add a persistent job queue and an optional background worker next, with a single-worker
   lock per Unity project, explicit cancellation and persisted `retry_at` scheduling. On startup, offer
   to continue interrupted jobs; do not silently start expensive work.
2. Add a Windows shell using **pywebview/WebView2** around the same dashboard. Keep it an optional dependency
   so web users do not need a desktop runtime. Package the Python worker, static assets and launcher into
   an installer. Add a native folder picker and tray/background behavior. Protect local mutation endpoints
   against cross-origin requests before distributing a desktop wrapper. This wrapper is proposed, not yet shipped.
3. Add a remote worker adapter only after local runs are reliable. Transfer the project and checkpoints,
   run Unity on a licensed build machine, and return logs, screenshots and downloadable build artifacts.
   Use separate storage for signing keys and cloud credentials; never include them in project bundles.
   A hosted web frontend needs a local companion worker for local Unity, or a remote Unity worker.

| Option | Suitable use | Main constraint |
|---|---|---|
| Local web dashboard | Lowest setup overhead; uses installed Unity and CLI login | Worker must stay running to execute jobs |
| Windows window, same UI | Installer, folder picker, tray/background controls | Packaging and WebView2 runtime; same AI limits |
| Codex Cloud | Coding and tests in a configured cloud environment while the PC sleeps | Account eligibility, environment setup and shared usage allowance |
| Unity Build Automation / licensed remote worker | Unity compilation, testing and downloadable builds | Build service setup, licensing, build costs and Android signing |

Codex Cloud is real, but it is not an automatic promise that an arbitrary Unity request will produce
a downloadable APK. Unity and its target modules must be available in the execution environment.
Use a dedicated Unity build service for reproducible release artifacts. Keep this local integration
separate from any future Codex Cloud connector; `codex exec` here does not submit cloud tasks.

The current pipeline's player build and smoke test target **Windows only**. Android generation prompts
do not change that. Android/AAB build selection, device validation, signing and store checks remain
separate implementation work before claiming Google Play release support.

Research references (checked November 24, 2026):

- [Codex Cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environments): reusable
  environments, setup, continuation across devices, resource and state-retention limits.
- [Using Codex with a ChatGPT plan](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan):
  cloud eligibility and tasks that continue while a local computer sleeps.
- [Usage and pricing](https://learn.chatgpt.com/docs/pricing): local/cloud shared allowance and account reset times.
- [Unity build artifacts](https://docs.unity.com/en-us/build-automation/check-build-results/overview):
  successful build output downloads.
- [pywebview architecture](https://pywebview.flowrl.com/guide/architecture): sharing a local web backend with a native window.

Run recovery regression checks without using AI credits or Unity:

```powershell
python -m unittest discover -s tests -v
```

