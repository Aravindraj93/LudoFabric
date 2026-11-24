# Security

LudoFabric is an experimental local development tool. It invokes provider CLIs and Unity, and runs
generated C# Editor code. Use disposable or backed-up projects and review generated code.

The dashboard binds to `127.0.0.1`. It is not an authenticated multi-user service: do not expose or
reverse-proxy it to a network. Hosted or desktop distribution needs additional origin validation,
authentication, and worker isolation.

Keep `.env`, provider authentication files, signing keys, private context, and logs private.
AI requests transmit selected prompts, source context, and screenshots to the configured provider.

Use GitHub's **Report a vulnerability** flow if enabled. Otherwise open a minimal issue asking for
a private reporting channel without posting exploit details, secrets, or private user data.

There is no formal support SLA or completed security audit at this stage.
