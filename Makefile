.PHONY: demo-b1-replay demo-b1-live

PYTHON ?= control-plane/.venv/bin/python
EVAL_PYTHON ?= eval-harness/.venv/bin/python
SUITE_PYTHON ?= $(EVAL_PYTHON)

demo-b1-replay:
	$(PYTHON) scripts/run_b1_replay.py --suite-python "$(SUITE_PYTHON)"

demo-b1-live:
	$(PYTHON) scripts/run_b1_live.py --eval-python $(EVAL_PYTHON)
