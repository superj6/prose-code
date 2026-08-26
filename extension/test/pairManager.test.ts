import assert from "node:assert/strict";
import { test } from "node:test";
import { DocHandle, Timer, Ui } from "../src/docs";
import { ManagerClient, PairManager } from "../src/pairManager";
import { LineEdit, Side, Snapshot, SyncEvent, SyncRequest } from "../src/protocol";

class FakeDoc implements DocHandle {
  version = 1;
  constructor(readonly side: Side, private text: string, private manager?: () => PairManager) {}
  getText() { return this.text; }
  setText(t: string) { this.text = t; this.version++; }
  async applyLineEdit(le: LineEdit) {
    const lines = this.text.split("\n"); if (lines[lines.length - 1] === "") lines.pop();
    const repl = le.new_text === "" ? [] : le.new_text.replace(/\n$/, "").split("\n");
    lines.splice(le.start, le.end - le.start, ...repl);
    this.text = lines.join("\n") + "\n"; this.version++;
    this.manager?.().onUserEdit(this.side); // vscode fires the change event during applyEdit
    return true;
  }
  async replaceAll(t: string) { this.text = t; this.version++; return true; }
}

class FakeTimer implements Timer {
  pending: { fn: () => void; ms: number; id: number }[] = []; next = 1;
  set(fn: () => void, ms: number) { const id = this.next++; this.pending.push({ fn, ms, id }); return id; }
  clear(h: unknown) { this.pending = this.pending.filter((p) => p.id !== h); }
  async fire() { const p = this.pending.shift(); if (p) { p.fn(); await new Promise((r) => setImmediate(r)); } }
}

const ui: Ui = { setStatus() {}, showEdit() {}, clearEdits() {}, info() {}, warn() {} };
const snap: Snapshot = { prose: "P1\n\nP2\n", code: "x = 1\n\ndef f():\n    pass\n", blocks: [
  { id: "b1", prose: [0, 2], code: [0, 2] }, { id: "b2", prose: [2, 3], code: [2, 4] }] };

function setup(client: ManagerClient) {
  const timer = new FakeTimer();
  let manager!: PairManager;
  const prose = new FakeDoc("prose", snap.prose, () => manager);
  const code = new FakeDoc("code", snap.code, () => manager);
  manager = new PairManager(prose, code, snap, client, ui, {
    pairId: "t", language: "python", codePath: "x.py", debounceMs: 700, autoSync: true, feedbackWindowS: 30, timer,
  });
  return { manager, timer, prose, code };
}

/** A client that answers a code->prose sync by replacing b2's prose. */
function echoClient(log: SyncRequest[] = []): ManagerClient & { log: SyncRequest[] } {
  return {
    log,
    async sync(req, onEvent) {
      log.push(req);
      const le: LineEdit = { side: "prose", start: 2, end: 3, new_text: "P2 updated\n", block: "b2" };
      await onEvent({ event: "edit", data: le });
      await onEvent({ event: "done", data: {
        request_id: req.request_id, base_prose_version: req.pair.prose_version, base_code_version: req.pair.code_version,
        target_side: "prose", line_edits: [le], prose: "P1\n\nP2 updated\n", code: req.pair.code,
        blocks: snap.blocks, latency_ms: 5, model: "fake", usage: {}, warnings: [] } });
    },
    async feedback() {},
  };
}

test("user edit on code -> debounce -> one sync -> prose updated, no echo", async () => {
  const client = echoClient();
  const { manager, timer, prose, code } = setup(client);
  code.setText("x = 1\n\ndef f():\n    return 2\n");
  manager.onUserEdit("code");
  assert.equal(manager.state, "debouncing");
  assert.equal(timer.pending[0].ms, 700);
  await timer.fire();
  assert.equal(manager.state, "idle");
  assert.equal(prose.getText(), "P1\n\nP2 updated\n");
  assert.equal(client.log.length, 1);
  assert.equal(client.log[0].change.side, "code");
  assert.equal(client.log[0].other_side_dirty, false);
  // the model's edit fired a change event but must not have scheduled another sync
  assert.equal(timer.pending.filter((p) => p.ms === 700).length, 0);
  assert.equal(manager.snapshot.prose, "P1\n\nP2 updated\n");
});

test("typing again during a sync cancels it and reschedules", async () => {
  let aborted = false;
  const client: ManagerClient = {
    async sync(_req, _on, signal) {
      await new Promise<void>((resolve) => signal.addEventListener("abort", () => { aborted = true; resolve(); }));
      throw new Error("aborted");
    },
    async feedback() {},
  };
  const { manager, timer, code } = setup(client);
  code.setText("x = 2\n\ndef f():\n    pass\n");
  manager.onUserEdit("code");
  await timer.fire();
  assert.equal(manager.state, "syncing");
  code.setText("x = 3\n\ndef f():\n    pass\n");
  manager.onUserEdit("code");
  assert.equal(aborted, true);
  assert.equal(manager.state, "debouncing");
});

test("edits on the target side mid-sync discard the response and mark both dirty", async () => {
  const client = echoClient();
  const { manager, timer, prose, code } = setup({
    async sync(req, onEvent, signal) {
      prose.setText("P1 (user)\n\nP2\n"); // user types on the target while the model works
      await client.sync(req, onEvent, signal);
    },
    async feedback() {},
  });
  code.setText("x = 9\n\ndef f():\n    pass\n");
  manager.onUserEdit("code");
  await timer.fire();
  assert.equal(prose.getText(), "P1 (user)\n\nP2\n"); // the model edit was NOT applied
  assert.equal(manager.dirty.prose, true);
  assert.equal(manager.dirty.code, true);
  assert.equal(manager.state, "debouncing");
  await timer.fire(); // second attempt now carries other_side_dirty
  assert.equal(client.log.length, 2);
  assert.equal(client.log[1].other_side_dirty, true);
});

test("paused manager never syncs; syncNow works when paused", async () => {
  const client = echoClient();
  const { manager, timer, code } = setup(client);
  manager.setAutoSync(false);
  code.setText("x = 5\n\ndef f():\n    pass\n");
  manager.onUserEdit("code");
  assert.equal(timer.pending.length, 0);
  await manager.syncNow();
  assert.equal(client.log.length, 1);
});
