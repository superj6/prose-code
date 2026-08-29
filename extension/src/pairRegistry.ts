import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { VsDoc } from "./applier";
import { Decorations } from "./decorations";
import { PairManager } from "./pairManager";
import { Side, Snapshot } from "./protocol";
import { Settings } from "./settings";
import { StatusBar } from "./statusBar";
import { SyncClient } from "./syncClient";

const MAP_VERSION = 1;

export function prosePathFor(codePath: string, sidecarDir: string): string {
  const dir = path.dirname(codePath);
  const name = path.basename(codePath) + ".prose";
  return sidecarDir ? path.join(dir, sidecarDir, name) : path.join(dir, name);
}

export function codePathFor(prosePath: string, sidecarDir: string): string {
  const name = path.basename(prosePath).replace(/\.prose$/, "");
  let dir = path.dirname(prosePath);
  if (sidecarDir && path.basename(dir) === sidecarDir) dir = path.dirname(dir);
  return path.join(dir, name);
}

export function mapPathFor(codePath: string): string {
  return path.join(path.dirname(codePath), ".prose", path.basename(codePath) + ".map.json");
}

function loadSnapshot(codePath: string): Snapshot | undefined {
  try {
    const data = JSON.parse(fs.readFileSync(mapPathFor(codePath), "utf8"));
    if (data.version !== MAP_VERSION) return undefined;
    return { prose: data.prose, code: data.code, blocks: data.blocks };
  } catch {
    return undefined;
  }
}

function saveSnapshot(codePath: string, snap: Snapshot): void {
  const p = mapPathFor(codePath);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify({ version: MAP_VERSION, ...snap }, null, 1));
}

/** Tracks open pairs (one PairManager per code file) and resolves documents to their pair. */
export class PairRegistry implements vscode.Disposable {
  private readonly pairs = new Map<string, { manager: PairManager; code: vscode.TextDocument; prose: vscode.TextDocument }>();

  constructor(
    private readonly client: SyncClient,
    private readonly status: StatusBar,
    private readonly decorations: Decorations,
    private readonly settings: () => Settings,
    private readonly out: vscode.OutputChannel,
  ) {}

  /** Resolve any document (code or prose) to the code path of its pair, if the pair is open. */
  find(doc: vscode.TextDocument): { manager: PairManager; side: Side } | undefined {
    const s = this.settings();
    const fsPath = doc.uri.fsPath;
    const codePath = doc.languageId === "prose" ? codePathFor(fsPath, s.sidecarDir) : fsPath;
    const entry = this.pairs.get(codePath);
    if (!entry) return undefined;
    return { manager: entry.manager, side: doc.languageId === "prose" ? "prose" : "code" };
  }

  managerFor(doc: vscode.TextDocument): PairManager | undefined {
    return this.find(doc)?.manager;
  }

  /** Open (creating if needed) the prose sidecar for a code document and start syncing the pair. */
  async openPair(codeDoc: vscode.TextDocument, regenerate = false): Promise<void> {
    const s = this.settings();
    const codePath = codeDoc.uri.fsPath;
    const prosePath = prosePathFor(codePath, s.sidecarDir);
    this.pairs.get(codePath)?.manager.dispose();
    this.pairs.delete(codePath);

    let snapshot = regenerate ? undefined : loadSnapshot(codePath);
    if (!regenerate && !snapshot && fs.existsSync(prosePath)) {
      // Prose exists (e.g. checked in) but no map: pair paragraphs with code units, no model call.
      const prose = fs.readFileSync(prosePath, "utf8");
      const blocks = await this.client.align(prose, codeDoc.getText(), codeDoc.languageId);
      if (blocks) {
        snapshot = { prose, code: codeDoc.getText(), blocks };
        saveSnapshot(codePath, snapshot);
        this.out.appendLine(`[align] rebuilt map for ${codePath}: ${blocks.length} blocks`);
      } else {
        this.out.appendLine(`[align] ${prosePath} is stale; regenerating`);
      }
    }
    if (regenerate || !fs.existsSync(prosePath) || !snapshot) {
      this.status.set("syncing", "generating prose");
      const gen = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "Prose Code: generating prose…" },
        () => this.client.generate(codeDoc.getText(), codeDoc.languageId, codePath),
      );
      fs.mkdirSync(path.dirname(prosePath), { recursive: true });
      fs.writeFileSync(prosePath, gen.prose);
      snapshot = { prose: gen.prose, code: codeDoc.getText(), blocks: gen.blocks };
      saveSnapshot(codePath, snapshot);
      this.out.appendLine(`[generate] ${prosePath}: ${gen.blocks.length} blocks in ${gen.latency_ms} ms (${gen.model})`);
    }

    const proseDoc = await vscode.workspace.openTextDocument(prosePath);
    await vscode.window.showTextDocument(codeDoc, { viewColumn: vscode.ViewColumn.One, preview: false });
    await vscode.window.showTextDocument(proseDoc, { viewColumn: vscode.ViewColumn.Beside, preview: false, preserveFocus: true });

    const manager = new PairManager(
      new VsDoc("prose", proseDoc),
      new VsDoc("code", codeDoc),
      snapshot,
      this.client,
      {
        setStatus: (state, detail) => this.status.set(state, detail),
        showEdit: (side, le) => this.decorations.show(side === "prose" ? proseDoc : codeDoc, le),
        showPreview: (pv) => this.decorations.showPreview(pv.side === "prose" ? proseDoc : codeDoc, pv),
        clearEdits: () => this.decorations.clear(),
        info: (m) => void vscode.window.showInformationMessage(m),
        warn: (m) => void vscode.window.showWarningMessage(m),
      },
      {
        pairId: path.basename(codePath),
        language: codeDoc.languageId,
        codePath,
        debounceMs: s.debounceMs,
        autoSync: s.autoSync,
        model: s.model || undefined,
        verify: s.verify,
        feedbackWindowS: s.feedbackWindowS,
        onSnapshot: (snap) => saveSnapshot(codePath, snap),
        onSynced: () => void this.propagateUp(codePath),
        log: (line) => this.out.appendLine(line),
      },
    );
    this.pairs.set(codePath, { manager, code: codeDoc, prose: proseDoc });
    this.out.appendLine(`[pair] open ${codePath} <-> ${prosePath}`);
  }

  /** After a file sync, refresh ancestor DIR.prose files (server writes them; open editors reload). */
  async propagateUp(codePath: string): Promise<void> {
    const s = this.settings();
    if (!s.propagateUp) return;
    try {
      const r = await this.client.tree("propagate_up", codePath, s.sidecarDir);
      for (const x of r.synced) this.out.appendLine(`[tree] ${x.path}: ${x.edits} edit(s)`);
      for (const e of r.errors) this.out.appendLine(`[tree] error ${e.path}: ${e.error}`);
      if (r.synced.length) this.status.set("idle", `updated ${r.synced.length} DIR.prose`);
    } catch (e) {
      this.out.appendLine(`[tree] propagate_up failed: ${(e as Error).message}`);
    }
  }

  async openDirectoryProse(dir: string): Promise<void> {
    const s = this.settings();
    const dirProse = path.join(dir, "DIR.prose");
    if (!fs.existsSync(dirProse)) {
      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: `Prose Code: generating prose for ${path.basename(dir)}/…` },
        async () => {
          const r = await this.client.tree("generate", dir, s.sidecarDir);
          for (const g of r.generated) this.out.appendLine(`[tree] wrote ${g}`);
          for (const e of r.errors) this.out.appendLine(`[tree] error ${e.path}: ${e.error}`);
        },
      );
    }
    const doc = await vscode.workspace.openTextDocument(dirProse);
    await vscode.window.showTextDocument(doc, { preview: false });
  }

  async pushDown(dir: string): Promise<void> {
    const s = this.settings();
    const r = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: `Prose Code: pushing ${path.basename(dir)}/DIR.prose down…` },
      () => this.client.tree("push_down", dir, s.sidecarDir),
    );
    for (const x of r.synced) this.out.appendLine(`[tree] ${x.path}: ${x.edits} edit(s)`);
    for (const e of r.errors) this.out.appendLine(`[tree] error ${e.path}: ${e.error}`);
    void vscode.window.showInformationMessage(`Prose Code: pushed down — ${r.synced.length} file(s) updated${r.errors.length ? `, ${r.errors.length} error(s) (see log)` : ""}`);
  }

  onDocumentClosed(doc: vscode.TextDocument): void {
    for (const [codePath, entry] of this.pairs) {
      if (entry.code === doc || entry.prose === doc) {
        entry.manager.dispose();
        this.pairs.delete(codePath);
        this.status.set("none", "pair closed");
      }
    }
  }

  forEach(fn: (m: PairManager) => void): void {
    for (const e of this.pairs.values()) fn(e.manager);
  }

  dispose(): void {
    this.forEach((m) => m.dispose());
    this.pairs.clear();
  }
}
