# OCI Agentic Dev OS — developer convenience targets.
.PHONY: help test lint fmt gate ansible-check check deploy verify

help:
	@echo "make test     - run the unit test suite"
	@echo "make lint     - black --check + ruff (CI parity)"
	@echo "make fmt      - auto-format with black + ruff --fix"
	@echo "make gate     - run the security gate scanner"
	@echo "make check    - lint + gate + ansible-check + test (full local CI)"
	@echo "make deploy   - ./scripts/deploy.sh (pass ARGS=...)"
	@echo "make verify   - post-deploy verification (run on the VM)"

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

ansible-check:
	ansible-playbook --syntax-check ansible/playbook.yml

check: lint gate ansible-check test
	@echo "All local CI checks passed."

deploy:
	./scripts/deploy.sh $(ARGS)

verify:
	./scripts/verify.sh
