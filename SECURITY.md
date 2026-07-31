# Security

Never add API tokens, cookies, OAuth codes, client secrets, private keys,
seller exports, or real marketplace responses to commits, issues, pull
requests, or CI logs.

Secrets are read only from a local `.env` file or a protected CI environment.
The `.env` file and all generated data directories are excluded from Git.

If a credential may have been exposed, revoke it in the marketplace cabinet,
create a replacement with minimal permissions, and inspect Git history and CI
logs. Deleting the value in a later commit is not sufficient.

Report vulnerabilities to the repository owner through a private channel.
