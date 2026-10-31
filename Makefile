HF_REPO := XOR-2025/paperhunter-research-paper-discovery
DB_DIR  := a-database
DB      := $(DB_DIR)/paperhunter.db
HF      := .venv/bin/hf
PYTHON  := .venv/bin/python

.PHONY: db-checkpoint db-push db-versions db-pull

db-checkpoint:
	@out=$$(sqlite3 $(DB) "PRAGMA wal_checkpoint(TRUNCATE);") || exit 1; \
	echo "checkpoint: $$out"; \
	case "$$out" in 0\|*) ;; *) echo "database is busy: stop every script and sqlite3 shell using it"; exit 1;; esac; \
	if [ -e $(DB)-wal ] || [ -e $(DB)-shm ]; then echo "$(DB)-wal or -shm still exists: the database is still open somewhere"; exit 1; fi; \
	ls $(DB)*

db-push:
	@test -n "$(MSG)" || { echo 'usage: make db-push MSG="<step and counts>"'; exit 1; }
	@$(MAKE) --no-print-directory db-checkpoint
	$(HF) upload $(HF_REPO) $(DB) paperhunter.db --repo-type dataset --commit-message "$(MSG)"

db-versions:
	@$(PYTHON) -c "from huggingface_hub import HfApi; [print(c.commit_id[:10], c.created_at.strftime('%Y-%m-%d %H:%M'), c.title) for c in HfApi().list_repo_commits('$(HF_REPO)', repo_type='dataset')]"

db-pull:
	rm -f $(DB)-wal $(DB)-shm
	$(HF) download $(HF_REPO) paperhunter.db --repo-type dataset --local-dir $(DB_DIR) $(if $(REV),--revision $(REV))
