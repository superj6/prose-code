import { DocHandle, Timer, Ui, realTimer } from "./docs";
import { Feedback, LineEdit, Side, Snapshot, SyncEvent, SyncRequest, SyncResponse, otherSide } from "./protocol";

export type State = "idle" | "debouncing" | "syncing" | "error" | "paused";

export interface ManagerClient {
  sync(req: SyncRequest, onEvent: (e: SyncEvent) => Promise<void>, signal: AbortSignal): Promise<void>;
  feedback(fb: Feedback): Promise<void>;
}

export interface ManagerOptions {
  pairId: string;
  language: string;
  codePath: string;
  debounceMs: number;
  autoSync: boolean;
  model?: string;
  verify?: boolean;
  feedbackWindowS: number;
  maxDiscards?: number;
  timer?: Timer;
  onSnapshot?: (s: Snapshot) => void;
  log?: (line: string) => void;
}

/**
 * Per-pair state machine: debounce user edits, run one sync at a time, apply streamed edits,
 * and never let the model's own edits trigger another sync (the echo loop).
 */
export class PairManager {
  state: State = "idle";
  snapshot: Snapshot;
  dirty: Record<Side, boolean> = { prose: false, code: false };
  primary: Side | null = null;
  generation = 0;
  applying = false;
  discards = 0;
  lastError: string | undefined;
  private inflight: AbortController | null = null;
  private timerHandle: unknown = null;
  private readonly timer: Timer;
  private readonly docs: Record<Side, DocHandle>;

  constructor(
    prose: DocHandle,
    code: DocHandle,
    snapshot: Snapshot,
    private readonly client: ManagerClient,
    private readonly ui: Ui,
    private readonly opts: ManagerOptions,
  ) {
    this.docs = { prose, code };
    this.snapshot = snapshot;
    this.timer = opts.timer ?? realTimer;
    if (!opts.autoSync) this.state = "paused";
    this.ui.setStatus(this.state);
  }

  private log(line: string): void {
    this.opts.log?.(`[${this.opts.pairId}] ${line}`);
  }

  /** Called for every change event on either document. */
  onUserEdit(side: Side): void {
    if (this.applying) return; // our own edit: echo breaker #1
    this.dirty[side] = true;
    this.primary = side;
    if (this.state === "paused") return;
    if (this.state === "syncing") this.cancelInflight("superseded by a newer edit");
    this.schedule(this.opts.debounceMs);
  }

  onSave(side: Side): void {
    if (this.applying || this.state === "paused") return;
    if (this.dirty[side]) this.schedule(0);
  }

  setAutoSync(on: boolean): void {
    if (on) {
      this.state = "idle";
      this.discards = 0;
      this.ui.setStatus("idle");
      if (this.dirty.prose || this.dirty.code) this.schedule(0);
    } else {
      this.clearTimer();
      this.cancelInflight("auto sync paused");
      this.state = "paused";
      this.ui.setStatus("paused");
    }
  }

  /** Manual sync: uses the last edited side, or whichever side differs from the snapshot. */
  async syncNow(): Promise<void> {
    this.clearTimer();
    if (this.state === "syncing") this.cancelInflight("manual sync");
    if (!this.primary) {
      if (this.docs.code.getText() !== this.snapshot.code) this.primary = "code";
      else if (this.docs.prose.getText() !== this.snapshot.prose) this.primary = "prose";
      else {
        this.ui.info("Prose Code: both sides already in sync");
        return;
      }
      this.dirty[this.primary] = true;
    }
    this.discards = 0;
    await this.runSync();
  }

  private schedule(ms: number): void {
    this.clearTimer();
    this.state = "debouncing";
    this.ui.setStatus("debouncing");
    this.timerHandle = this.timer.set(() => void this.runSync(), ms);
  }

  private clearTimer(): void {
    if (this.timerHandle !== null) this.timer.clear(this.timerHandle);
    this.timerHandle = null;
  }

  private cancelInflight(reason: string): void {
    if (this.inflight) {
      this.log(`cancel: ${reason}`);
      this.inflight.abort();
      this.inflight = null;
    }
  }

  private async runSync(): Promise<void> {
    this.clearTimer();
    const primary = this.primary;
    if (!primary) return;
    const target = otherSide(primary);
    const gen = ++this.generation;
    const controller = new AbortController();
    this.inflight = controller;
    this.state = "syncing";
    this.ui.setStatus("syncing", `${primary} → ${target}`);
    const baseVersions = { prose: this.docs.prose.version, code: this.docs.code.version };
    const req: SyncRequest = {
      request_id: `${this.opts.pairId}-${gen}-${Math.random().toString(36).slice(2, 8)}`,
      pair: {
        pair_id: this.opts.pairId,
        language: this.opts.language,
        code_path: this.opts.codePath,
        prose: this.docs.prose.getText(),
        code: this.docs.code.getText(),
        prose_version: baseVersions.prose,
        code_version: baseVersions.code,
      },
      base: this.snapshot,
      change: { side: primary },
      other_side_dirty: this.dirty[target],
      options: { model: this.opts.model || null, verify: this.opts.verify ?? null },
    };
    const applied: LineEdit[] = [];
    const originals: string[] = [];
    let expectedTargetVersion = baseVersions[target];
    let response: SyncResponse | undefined;
    let failure: { message: string; needs_regenerate: boolean } | undefined;
    this.ui.clearEdits();

    const onEvent = async (e: SyncEvent): Promise<void> => {
      if (gen !== this.generation || controller.signal.aborted) return;
      if (e.event === "edit") {
        const le = e.data;
        if (this.docs[target].version !== expectedTargetVersion) {
          // the user typed on the target side while we were syncing: drop this response
          this.discard(controller, "target document changed during sync");
          return;
        }
        const lines = this.docs[target].getText().split("\n");
        originals.push(lines.slice(le.start, le.end).join("\n"));
        this.applying = true;
        let ok = false;
        try {
          ok = await this.docs[target].applyLineEdit(le);
        } finally {
          this.applying = false;
        }
        if (!ok) {
          this.discard(controller, "editor refused the edit");
          return;
        }
        expectedTargetVersion = this.docs[target].version;
        applied.push(le);
        this.ui.showEdit(target, le);
      } else if (e.event === "done") {
        response = e.data;
      } else if (e.event === "error") {
        failure = e.data;
      }
    };

    try {
      await this.client.sync(req, onEvent, controller.signal);
    } catch (err) {
      if (controller.signal.aborted) return; // cancelled on purpose; a newer sync is scheduled or pending
      this.fail(`sync request failed: ${(err as Error).message}`);
      return;
    }
    if (controller.signal.aborted || gen !== this.generation) return;
    this.inflight = null;
    if (failure) {
      this.fail(failure.needs_regenerate ? `${failure.message} — run "Regenerate Prose"` : failure.message);
      return;
    }
    if (!response) {
      this.fail("sync ended without a result");
      return;
    }
    await this.finish(response, target, applied, originals, req.request_id);
  }

  private discard(controller: AbortController, why: string): void {
    controller.abort();
    this.inflight = null;
    this.discards += 1;
    this.log(`discarded: ${why} (${this.discards})`);
    this.dirty.prose = this.dirty.code = true;
    if (this.discards >= (this.opts.maxDiscards ?? 3)) {
      this.state = "error";
      this.lastError = "out of sync after repeated interruptions — run Sync Now";
      this.ui.setStatus("error", this.lastError);
      return;
    }
    this.schedule(this.opts.debounceMs);
  }

  private fail(message: string): void {
    this.inflight = null;
    this.state = "error";
    this.lastError = message;
    this.log(`error: ${message}`);
    this.ui.setStatus("error", message);
    this.ui.warn(`Prose Code: ${message}`);
  }

  private async finish(resp: SyncResponse, target: Side, applied: LineEdit[], originals: string[], syncId: string): Promise<void> {
    // Safety net: the document must now equal what the server thinks it is.
    const expected = target === "prose" ? resp.prose : resp.code;
    if (normalize(this.docs[target].getText()) !== normalize(expected)) {
      this.log("post-apply mismatch; replacing whole target document");
      this.applying = true;
      try {
        await this.docs[target].replaceAll(expected);
      } finally {
        this.applying = false;
      }
    }
    this.snapshot = { prose: resp.prose, code: resp.code, blocks: resp.blocks }; // echo breaker #2
    this.opts.onSnapshot?.(this.snapshot);
    this.dirty = { prose: false, code: false };
    this.primary = null;
    this.discards = 0;
    this.state = "idle";
    for (const w of resp.warnings) this.log(`warning: ${w}`);
    this.ui.setStatus("idle", `${applied.length} edit(s) in ${resp.latency_ms} ms`);
    if (applied.length) this.trackFeedback(syncId, target, applied, originals);
  }

  private trackFeedback(syncId: string, target: Side, applied: LineEdit[], originals: string[]): void {
    const windowMs = this.opts.feedbackWindowS * 1000;
    this.timer.set(() => {
      const text = this.docs[target].getText();
      let outcome: Feedback["outcome"] = "accepted";
      applied.forEach((le, i) => {
        const kept = le.new_text === "" ? !text.includes(originals[i]) || originals[i] === "" : text.includes(le.new_text.replace(/\n$/, ""));
        if (kept) return;
        const reverted = originals[i] !== "" && text.includes(originals[i]);
        outcome = reverted && outcome !== "modified" ? "reverted" : "modified";
      });
      void this.client.feedback({ sync_id: syncId, outcome, dwell_s: this.opts.feedbackWindowS, final_text_by_block: {} });
    }, windowMs);
  }

  dispose(): void {
    this.clearTimer();
    this.cancelInflight("disposed");
  }
}

function normalize(text: string): string {
  return text.endsWith("\n") || text === "" ? text : text + "\n";
}
