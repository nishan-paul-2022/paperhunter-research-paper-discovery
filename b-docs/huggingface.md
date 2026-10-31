# Hugging Face: Database Versioning

Repo: [XOR-2025/paperhunter-research-paper-discovery](https://huggingface.co/datasets/XOR-2025/paperhunter-research-paper-discovery)

Run every command from the repository root.

```bash
cd ~/Documents/paperhunter-research-paper-discovery
source .venv/bin/activate
```

## Make shortcuts

```bash
make db-checkpoint
make db-push MSG="<step and counts>"
make db-versions
make db-pull
make db-pull REV=<commit_id>
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

## 3. Upload a version

1. Stop every script and `sqlite3` shell that uses the database.
2. Checkpoint. Expected: `0|0|0`, then only `a-database/paperhunter.db`.

```bash
sqlite3 a-database/paperhunter.db "PRAGMA wal_checkpoint(TRUNCATE);" && ls a-database/paperhunter.db*
```

3. Upload.

```bash
hf upload XOR-2025/paperhunter-research-paper-discovery a-database/paperhunter.db paperhunter.db \
  --repo-type dataset --commit-message "<step and counts>"
```

## 4. List versions

```bash
python -c "
from huggingface_hub import HfApi
for c in HfApi().list_repo_commits('XOR-2025/paperhunter-research-paper-discovery', repo_type='dataset'):
    print(c.commit_id[:10], c.created_at.strftime('%Y-%m-%d %H:%M'), c.title)"
```

## 5. Download a version

1. Stop every script and `sqlite3` shell that uses the database.
2. Download. Omit `--revision` for the latest version.

```bash
rm -f a-database/paperhunter.db-wal a-database/paperhunter.db-shm
hf download XOR-2025/paperhunter-research-paper-discovery paperhunter.db \
  --repo-type dataset --local-dir a-database --revision <commit_id>
```
