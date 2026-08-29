# prose-code

Edit a program as **English prose** or as **source code** — side by side — and the other side follows.

```
examples/calc.py                     examples/calc.py.prose
───────────────────────────────      ──────────────────────────────────────────────
def evaluate(tokens):                ## evaluate
    if not tokens:                   Fold `tokens` left to right with no precedence.
        return 0.0                   Return `0.0` for an empty list.
    ...
```

The prose is a *synchronised view* of the code, not a replacement: the code is what runs, both
files are committed, and an LLM keeps them consistent with block-level minimal edits. See
[`docs/prose-grammar.md`](docs/prose-grammar.md) for the prose contract and
[`docs/roadmap.md`](docs/roadmap.md) for where this is going (custom model, etc.).

## Layout

```
configs/base.yaml   single source of truth (model, prompt version, segmentation, logging)
sync/               Python service "prosesync": segmentation, alignment, prompts, backends, server, CLI
extension/          VS Code extension (two real editors side by side, talks to the service over HTTP+SSE)
ml/                 eval harness + reward scorers now; data generation + training later
examples/           small files in python / typescript / go to try it on
docs/               prose grammar, roadmap
```

## Setup

```sh
make setup                      # .venv + prosesync (editable) + extension npm deps
cp .env.example .env            # add OPENAI_API_KEY (and OPENAI_BASE_URL for a self-hosted model)
make test                       # python tests + extension typecheck/tests (no API key needed)
.venv/bin/prosesync check-model # verifies configs/base.yaml sync.model exists on the endpoint
```

## Try it from the CLI (Phase 0)

```sh
.venv/bin/prosesync gen examples/calc.py            # writes calc.py.prose + .prose/calc.py.map.json
$EDITOR examples/calc.py                            # change something
.venv/bin/prosesync sync examples/calc.py --changed code    # the affected paragraph(s) update
$EDITOR examples/calc.py.prose                      # change a paragraph
.venv/bin/prosesync sync examples/calc.py --changed prose   # the matching code block updates
```

Add `--backend mock` to any command to exercise the whole pipeline without an API key
(the mock writes placeholder text but goes through the real segmentation/alignment/apply path).

## Try it in VS Code (Phase 1)

1. `cd extension && npm run build`, then open the `extension/` folder in VS Code and press F5
   (Extension Development Host). Or `npm run package` and install the `.vsix`.
2. Open a source file, run **Prose Code: Open Pair**. The prose is generated (once) and opened beside.
3. Edit either side. ~700 ms after you stop typing the other side updates; changed lines are
   highlighted with the model's one-line reason. `Ctrl+Alt+S` syncs immediately.
4. Settings: `prosecode.model`, `prosecode.endpoint` (`auto` spawns the local service),
   `prosecode.backend` (`mock` for no key), `prosecode.debounceMs`, `prosecode.autoSync`, ...

Every sync and the user's reaction to it (accepted / modified / reverted within 30 s) is logged to
`~/.prosecode/logs/<date>.jsonl` (`prosecode.logInteractions` / `log.enabled` to disable). That log
is the training signal for the custom model.

## How sync works

* The code is cut into **blocks** (tree-sitter top-level units, small ones grouped); the prose has
  exactly one paragraph per block. The server keeps the block map; the model only ever names
  blocks (`replace b3`, `delete b5`) — never line numbers.
* On a user edit the server diffs against the last synced snapshot, shifts the block map
  arithmetically, marks the touched blocks AFFECTED, and asks the model for edits to the other
  side, restricted to affected blocks ± 1 neighbour. Everything else is preserved verbatim.
* Edits are strict-JSON, streamed one at a time, validated, applied, and pushed to the editor.
  If a replaced block now contains several units on both sides, it is split.
* The extension never re-syncs its own edits (an `applying` flag plus snapshot-based diffing),
  cancels in-flight requests when you keep typing, and drops a response if you edited the target
  side while it was in flight.

## Hierarchical prose

Every file's prose starts with a `# file` summary block; every directory can have a `DIR.prose`
with a `# dir/` summary and one `## child` paragraph per file or subdirectory. The directory pair's
"code side" is synthetic — the children's summaries — so the same block machinery keeps it in sync:

```sh
.venv/bin/prosesync gen src/                 # prose for every file under src/ + DIR.prose per directory (deepest first)
.venv/bin/prosesync sync src/calc.py --changed code   # ...then ancestors whose child paragraph changed are re-synced (upward)
.venv/bin/prosesync push-down src/           # you edited src/DIR.prose: update the children's summaries, then their code,
                                             # then the paragraphs describing the changed code (downward, depth-limited)
```

In VS Code: **Prose Code: Open Directory Prose** (generates if missing), upward propagation runs
after every file sync (`prosecode.propagateUp`), and saving a `DIR.prose` offers to push down
(`prosecode.pushDownOnSave`: ask | always | never). Propagation stops at the first ancestor whose
own summary did not change, so a local edit costs one extra model call, not one per level.

## Latency knobs

All in `configs/base.yaml` (`sync.*`), overridable with `--override sync.key=value`:

- `reasoning_effort` (`low` by default): biggest lever on the API model; re-check quality on the eval set when changing it.
- Prompt layout is cache-oriented — system prompt, then both documents in a fixed order with no
  per-request markers, then the diff/instructions — and every request carries
  `prompt_cache_key=<pair id>`. Consecutive syncs of the same pair hit the provider's prefix
  cache for everything up to the first changed block (`cache_hit` column in the eval report).
- `preview` streams the block text as it is generated; the editor shows it as ghost text on the
  block being rewritten, so the first feedback arrives well before the edit lands.
- `service_tier` (e.g. `priority`) is passed through if set.

## Verification (optional)

`verify.enabled: true` (or the `prosecode.verify` setting) syntax-checks every code-side result with
tree-sitter (any language) and `ast.parse` (Python), plus any per-language command you configure
(`verify.commands.typescript: ["npx", "tsc", "--noEmit", "{file}"]`). On failure the model gets one
repair round with the error; if the repaired edits pass they replace the first attempt, otherwise the
extension shows a warning with the failing line and the sync is marked unverified.

## Data pipeline (Phase 3)

```sh
make seed DIRS="~/project/foo ~/project/bar"   # local repos -> ml/data/seed/ (20-300 line files, deduped)
make synth PER_FILE=2                           # generate prose, propose small edits, sync -> ml/data/synth.jsonl
make export-interactions                        # ~/.prosecode/logs -> ml/data/interactions.jsonl (real usage)
make git-mine REPOS="~/src/a ~/src/b" && make git-label   # real small commits (1-3 hunks, one file) -> labelled records
make data-stats
.venv/bin/python ml/src/data/dataset.py render ml/data/synth.jsonl > /tmp/train.jsonl   # chat examples
```

Records (`ml/src/data/records.py`) hold the synced snapshot, the user's edited side, and the
block-op label; `dataset.py` renders them with the **same** `build_sync_messages` / realign /
window code the server uses, so training prompts equal serving prompts by construction.
Synthetic records are produced by the production engine itself (propose an edit → `Engine.sync`),
so labels match the serving distribution; real interactions carry accept/modify/revert outcomes;
git-mined edits are the most realistic code changes (use repos with granular, single-file commits —
squash-style histories yield nothing).

## Training a custom model (Phase 4)

```sh
.venv/bin/python ml/src/data/prepare.py --records ml/data/synth.jsonl --records ml/data/interactions.jsonl --out-dir outputs/data
.venv/bin/python ml/src/training/sft.py --config configs/local_smoke.yaml        # CPU smoke on the cached Qwen3-0.6B
.venv/bin/modal volume put prose-code outputs/data /datasets/prosesync/v1         # or run gen_data/prepare on Modal
.venv/bin/modal run ml/src/modal_app.py --job sft --config configs/modal_sft_smoke.yaml   # 20 steps on an A100
.venv/bin/modal run ml/src/modal_app.py --job sft --config configs/modal_sft.yaml         # the real run
.venv/bin/modal run ml/src/modal_app.py --job pairs --adapter /data/outputs/sft/final     # sample K, score with sync_reward -> DPO pairs
.venv/bin/modal run ml/src/modal_app.py --job dpo --adapter /data/outputs/sft/final       # preference stage (frozen SFT reference)
.venv/bin/modal run ml/src/modal_app.py --job serve --adapter /data/outputs/sft/final     # vLLM, OpenAI-compatible
make eval OVERRIDE="sync.base_url=https://<modal-url>/v1 sync.model=prosesync-ft"        # same harness, same items
```

`configs/train_base.yaml` extends `base.yaml`, so the prompt/segmentation/window settings used to
render training examples are exactly the serving ones. Base model Qwen2.5-Coder-1.5B-Instruct,
LoRA r=32/α=64 on all linear projections. The eval harness compares the fine-tune against
`gpt-5.6-luna` on the same items; the switch criteria are in `docs/roadmap.md`.

## Evaluate a backend

```sh
make eval BACKEND=mock                       # sanity: exercises the harness
make eval                                    # gpt-5.6-luna on ml/data/eval_v1.jsonl (18 cases; see docs/spike-results.md)
make eval OVERRIDE="sync.base_url=http://localhost:8000/v1 sync.model=local"  # a served fine-tune
```

Reports schema validity, syntax validity (tree-sitter), collateral-edit rate (non-editable blocks
touched — should be 0), similarity/contains vs expected, latency; writes `outputs/eval/<stamp>.md`.
