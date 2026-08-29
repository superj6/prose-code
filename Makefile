PY := .venv/bin/python
.PHONY: setup test test-sync test-ext build-ext serve gen sync eval lint seed synth export-interactions data-stats

setup:            ## venv + python deps + extension deps
	python3 -m venv .venv && $(PY) -m pip install -q -U pip && $(PY) -m pip install -q -e "./sync[develop]"
	cd extension && npm install --no-audit --no-fund

test: test-sync test-ext

test-sync:
	cd sync && ../$(PY) -m pytest -q

test-ext:
	cd extension && npm run build >/dev/null && npm run typecheck && node --test dist/test/*.test.js

build-ext:
	cd extension && npm run build

serve:            ## local sync server (BACKEND=mock for no API key)
	$(PY) -m prosesync.cli --backend $(or $(BACKEND),openai) serve

gen:              ## make FILE=examples/calc.py gen
	$(PY) -m prosesync.cli --backend $(or $(BACKEND),openai) gen $(FILE)

sync:             ## make FILE=examples/calc.py SIDE=code sync
	$(PY) -m prosesync.cli --backend $(or $(BACKEND),openai) sync $(FILE) --changed $(or $(SIDE),code)

eval:             ## make eval [BACKEND=mock] [ITEMS=ml/data/eval_v1.jsonl]
	$(PY) ml/src/training/evaluate.py --backend $(or $(BACKEND),openai) --items $(or $(ITEMS),ml/data/eval_v1.jsonl) $(if $(OVERRIDE),--override $(OVERRIDE))

lint:
	.venv/bin/ruff check sync ml

# ---- data pipeline (Phase 3)
seed:             ## make seed DIRS="~/project/foo ~/project/bar" [MAX=200]
	$(PY) ml/src/data/seed_corpus.py --dirs $(DIRS) --out ml/data/seed --max $(or $(MAX),200)

synth:            ## make synth [PER_FILE=2] [LIMIT=0] [BACKEND=mock]
	$(PY) ml/src/data/perturb.py --manifest ml/data/seed/manifest.jsonl --out ml/data/synth.jsonl --per-file $(or $(PER_FILE),2) --limit $(or $(LIMIT),0) $(if $(BACKEND),--backend $(BACKEND))

export-interactions:
	$(PY) ml/src/data/interactions_export.py --out ml/data/interactions.jsonl

data-stats:
	$(PY) ml/src/data/dataset.py stats ml/data/synth.jsonl; $(PY) ml/src/data/dataset.py stats ml/data/interactions.jsonl
