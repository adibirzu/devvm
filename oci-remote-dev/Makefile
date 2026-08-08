# OCI Agentic Dev OS — developer convenience targets.
.PHONY: help test lint lint-shell fmt gate ansible-check check install install-check deploy verify apply-pending

help:
	@echo "make test          - run the unit test suite"
	@echo "make lint          - black --check + ruff (CI parity)"
	@echo "make lint-shell    - shellcheck the entry point + scripts (CI parity)"
	@echo "make fmt           - auto-format with black + ruff --fix"
	@echo "make gate          - run the security gate scanner"
	@echo "make check         - lint + lint-shell + gate + ansible-check + test (full local CI)"
	@echo "make install       - ./install.sh on this machine (pass ARGS=...)"
	@echo "make install-check - ./install.sh --check: report changes, apply none"
	@echo "make deploy        - ./scripts/deploy.sh (pass ARGS=...)"
	@echo "make verify        - post-deploy verification (run on the VM)"
	@echo "make apply-pending - materialize the control-plane's queued developer"
	@echo "                     changes via Ansible (pass ARGS=--dry-run first)"

test:
	python3 -m pytest tests/ -q

lint:
	python3 -m black --check scripts/*.py
	python3 -m ruff check scripts/*.py

fmt:
	python3 -m black scripts/*.py
	python3 -m ruff check --fix scripts/*.py

gate:
	python3 scripts/security_gate.py --mode full

lint-shell:
	shellcheck --severity=error -e SC2002 install.sh scripts/*.sh scripts/lib/*.sh

ansible-check:
	ansible-playbook --syntax-check ansible/playbook.yml
	ansible-playbook --syntax-check ansible/apply_changes.yml

check: lint lint-shell gate ansible-check test
	@echo "All local CI checks passed."

install:
	./install.sh $(ARGS)

install-check:
	./install.sh --check $(ARGS)

deploy:
	./scripts/deploy.sh $(ARGS)

# Consume /etc/agent-os/pending-changes.jsonl (the control-plane's queue) and
# materialize each entry through ansible/apply_changes.yml. Removals only
# disable accounts; ARGS=--purge is the destructive opt-in.
apply-pending:
	python3 scripts/apply_pending.py $(ARGS)

verify:
	./scripts/verify.sh
