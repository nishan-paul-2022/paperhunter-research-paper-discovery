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

Stop every script first. Runs the checkpoint, then uploads `paperhunter.db` as a new version with your message.

```bash
make db-push MSG="<step and counts>"
```

## 5. List versions

Shows every version: commit ID, date, message.

```bash
make db-versions
```

## 6. Download a version

Saves to `a-database/paperhunter-<commit_id>.db`. Never replaces an existing file. Without `REV`, gets the latest.

```bash
make db-pull
make db-pull REV=<commit_id>
```
