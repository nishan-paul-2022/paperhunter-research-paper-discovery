# Hugging Face: Database Versioning

Repo: [XOR-2025/paperhunter-research-paper-discovery](https://huggingface.co/datasets/XOR-2025/paperhunter-research-paper-discovery)

Run every command from the repository root.

```bash
cd ~/Documents/paperhunter-research-paper-discovery
```

## 1. Setup (once per machine)

1. Create a token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) with "Write access to contents/settings of all repos under your personal namespace".
2. Log in. Answer `n` to "Add token as git credential?".

```bash
.venv/bin/hf auth login
.venv/bin/hf auth whoami
```

## 2. Create the repo (once)

```bash
.venv/bin/hf repo create paperhunter-research-paper-discovery --repo-type dataset --private
```

## 3. Upload a version

1. Stop every script and `sqlite3` shell that uses the database.
2. Checkpoint. Expected: `0|0|0`, then only `b-config/paperhunter.db`.

```bash
sqlite3 b-config/paperhunter.db "PRAGMA wal_checkpoint(TRUNCATE);" && ls b-config/paperhunter.db*
```

3. Upload.

```bash
.venv/bin/hf upload XOR-2025/paperhunter-research-paper-discovery b-config/paperhunter.db paperhunter.db \
  --repo-type dataset --commit-message "<step and counts>"
```

## 4. List versions

```bash
.venv/bin/python -c "
from huggingface_hub import HfApi
for c in HfApi().list_repo_commits('XOR-2025/paperhunter-research-paper-discovery', repo_type='dataset'):
    print(c.commit_id[:10], c.created_at.strftime('%Y-%m-%d %H:%M'), c.title)"
```

## 5. Download a version

1. Stop every script and `sqlite3` shell that uses the database.
2. Download. Omit `--revision` for the latest version.

```bash
rm -f b-config/paperhunter.db-wal b-config/paperhunter.db-shm
.venv/bin/hf download XOR-2025/paperhunter-research-paper-discovery paperhunter.db \
  --repo-type dataset --local-dir b-config --revision <commit_id>
```
