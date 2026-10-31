# Hugging Face: Database Versioning

Repo: [XOR-2025/paperhunter-research-paper-discovery](https://huggingface.co/datasets/XOR-2025/paperhunter-research-paper-discovery)

Run every command from the repository root with the environment active.

```bash
cd ~/Documents/paperhunter-research-paper-discovery
source .venv/bin/activate
```

## 1. Setup (once per machine)

1. Create a token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) with "Write access to contents/settings of all repos under your personal namespace".
2. Log in. Answer `n` to "Add token as git credential?".

```bash
hf auth login
hf auth whoami
```

## 2. Create the repo (once)

```bash
hf repo create paperhunter-research-paper-discovery --repo-type dataset --private
```

## 3. Checkpoint

Merges pending writes into `paperhunter.db`. Fails if any script or `sqlite3` shell still has it open.

```bash
make db-checkpoint
```

## 4. Upload a version

Stop every script first. Runs the checkpoint, then uploads `paperhunter.db` with an automatic message: `version <n> | <timestamp>`. `MSG` is optional and gets appended.

```bash
make db-push
make db-push MSG="<note>"
```

## 5. List versions

Shows every version: commit ID, date, message.

```bash
make db-versions
```

## 6. Download a version

Saves to `a-database/paperhunter-<version message in kebab-case>.db`, e.g. `paperhunter-version-1-2026-10-31-18-02-14-06.db`. Never replaces an existing file. Without `REV`, gets the latest.

```bash
make db-pull
make db-pull REV=<commit_id>
```
