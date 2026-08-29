# Roadmap

Goal: an English "prose code" dialect compiled in near-real time to source code, shown side by
side, editable on either side, with an ML model keeping the two consistent — and eventually a
custom model trained for exactly that.

Decisions: language-agnostic target code; VS Code extension as the interface; OpenAI
`gpt-5.6-luna` for the MVP via an OpenAI-compatible client (so a self-hosted fine-tune is a
`base_url` change); full custom-model plan.

| Phase | Scope | Done when |
|---|---|---|
| **0 — Spike** ✅ | `sync/` package: segmentation, block map, prompts, OpenAI backend, CLI, tests | Done 2026-08-26: 10/10 hand-made edits across py/ts/go with `gpt-5.6-luna`, zero collateral edits, 1.7–4.2 s typical (16 s worst). See [`spike-results.md`](spike-results.md). |
| **1 — MVP** ✅ (code) | FastAPI server with SSE, interaction log, VS Code extension (two editors, debounce/cancel/echo-safe state machine, decorations, status bar) | Manual session in the Extension Development Host: edit code → prose follows in ~1–2 s; edit prose → code follows; echo loop untriggerable; every sync logged. **Needs VS Code installed to verify.** |
| **2 — Robustness** (mostly done) | Feedback tracking ✅, eval harness ✅ (18-item set v1: 17/18 with `gpt-5.6-luna`), conflict policy ✅, streaming preview + `reasoning_effort` ✅, cache-oriented prompt + `prompt_cache_key` ✅, windowed context ✅, verifiers + repair round ✅, name-validated re-pairing + `/align` ✅; **todo**: symbol-path block keys that survive reordering, `extension/test` integration tests (needs VS Code on the dev box), grow the eval set to ~100 |
| **3 — Data** | `ml/src/data/{seed_corpus,synth_pairs,perturb,interactions_export,dataset}.py`, Modal `gen_data` job | ≥ 30k examples across 5 languages on the Modal volume; `test.jsonl` frozen. |
| **4 — Custom model** | LoRA SFT of Qwen2.5-Coder-1.5B-Instruct on Modal (A100), then DPO with `ml/src/rewards/sync_reward.py`; configs mirror `../latent-memory` | Checkpoints + harness table vs `gpt-5.6-luna` on `test.jsonl`. |
| **5 — Switchover** | vLLM OpenAI-compatible serving on Modal, `prosecode.endpoint`/`sync.base_url` A/B | Schema validity ≥ 99 %, syntax validity and collateral ≤ OpenAI's, round-trip within 5 pts, p50 < 1.5 s served, revert rate not worse over ≥ 200 real syncs. |

## Hierarchical prose (Phase 2.5 — proposed)

Idea: keep a prose file at **every directory level**, all in sync. `src/README.prose` (or
`src/.prose/DIR.prose`) has one paragraph per child — each file's paragraph is a one-paragraph
summary of that file, each subdirectory's paragraph summarises *its* directory prose — so the
repository reads top-down as nested English, and editing any level propagates.

Design that reuses the block machinery unchanged:

- A directory prose is a normal pair whose "code side" is a synthetic document: one block per
  child, containing the child's **first paragraph(s)** (files) or the child's directory prose
  (subdirectories). Same `Block` partition, same `replace/delete` ops, same snapshot diffing.
- **Upward propagation** (file → dir): when a file sync changes the file's prose, the synthetic
  document for the parent changes in exactly one block → an ordinary sync request for the parent
  pair with `changed="code"`. Debounced and coalesced per directory so a burst of edits yields one
  parent sync; ancestors chain the same way. Cost is bounded because each level only sees the
  summaries, not the sources.
- **Downward propagation** (dir → file): editing a child's paragraph in the directory prose is a
  `changed="prose"` sync whose target is the child's *summary*, then a file-level
  `changed="prose"` sync from the summary into that file's full prose/code. Guarded by a
  confirmation in the UI, since one sentence can fan out into many edits.
- File summaries need a stable place: add an optional leading `# summary` paragraph to the file
  prose (block `b0`, paired with an empty code range) so the directory level has something
  well-defined to read and write. The generator writes it; the grammar doc documents it.
- New pieces: `sync/src/prosesync/tree.py` (synthetic parent documents, dependency graph,
  coalescing scheduler), a `prosecode.openDirectoryProse` command, and a `--recursive` mode for
  `prosesync gen`. Eval: a "propagation consistency" item type (edit a file, check the parent
  paragraph changes and nothing else does).

Risks: cascades (an edit at depth 3 touching 3 ancestors ≈ 4 model calls — acceptable at ~2 s each
with cached prefixes, but never synchronous with typing); summaries drifting from their files if a
file-level sync fails midway (make the parent sync depend on the file sync's `done`).

## Data recipe (Phase 3)

1. Seed corpus: 20–300-line permissive files (The Stack v2 dedup, CodeSearchNet, MBPP/HumanEval
   for test-bearing items, own repos); python/typescript/javascript/go/rust; ~20k files.
2. Aligned pairs `(P0, C0, blocks)` with the *production* segmenter and generate prompt
   (`prosesync.prompts.build_generate_messages`); for test-bearing items, regenerate code from
   prose and require the tests to pass (proves the prose is sufficient).
3. Perturbation quads `(P0, C0) → (P1, C1)`: (a) git-mined small commits give `C0→C1`, the big
   model writes the minimal `P1`; (b) model-generated per-block code edits; (c) model-generated
   prose edits. Each quad yields two examples (code changed → prose edits; prose changed → code
   edits) with labels derived mechanically as block ops.
4. Filters: syntax ok, tests pass if available, untouched blocks identical, ≤ 3 edits, dedupe.
   Real logged interactions join in; `modified` outcomes use the user's final text as the label,
   `reverted` become DPO rejections. Split by repo.

Dataset record: `{id, source, language, prompt_version, prose, code, blocks, changed_side, hunks,
other_side_dirty, target_edits, prose_after, code_after, meta}`; training renders chat messages
with the same `build_sync_messages` the server uses, assistant turn = the JSON edit list.

## Training (Phase 4)

SFT: LoRA r=32/α=64 on all linear layers, lr 2e-4 cosine, 2–3 epochs, bf16, loss on assistant
tokens only, seq len 4096 (windowed context for longer files). Then sample K=4 per prompt, score
with `sync_reward.reward`, DPO on best/worst pairs; adapt `latent-memory`'s Dr. GRPO only if DPO
leaves headroom. Modal jobs `gen_data | sft | dpo | eval | serve` follow the `latent-memory`
`modal_app.py` pattern (volume at `/data`, `Secret.from_dotenv`).

## Known risks

- Prose→code is underdetermined; the prompt anchors on the existing implementation.
- Line-based block maps break under external changes (git, formatters) → tree-sitter re-pairing
  is the fallback now; symbol-path keys are the fix.
- Whole-pair prompts get expensive past ~500 lines → windowed context before real-repo use.
- Latency: a small model on CPU is slower than the API, not faster; serve on GPU.
- The interaction log holds user code: local only, opt-out.
