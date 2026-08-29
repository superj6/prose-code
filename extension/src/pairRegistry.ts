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
import { codePathFor, isSupportedSource, mapPathFor, prosePathFor } from "./files";

const MAP_VERSION = 1;

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
      const snap = await this.client.align(prose, codeDoc.getText(), codeDoc.languageId, codePath, prosePath);
      if (snap) {
        snapshot = snap; // base = committed versions when in git, so uncommitted edits sync on first change
        saveSnapshot(codePath, snapshot);
        this.out.appendLine(`[align] rebuilt map for ${codePath}: ${snap.blocks.length} blocks`);
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
      this.propagateUp(codePath, 500); // a new child summary: tell the directory prose
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
        onSynced: () => this.propagateUp(codePath),
        log: (line) => this.out.appendLine(line),
      },
    );
    this.pairs.set(codePath, { manager, code: codeDoc, prose: proseDoc });
    this.out.appendLine(`[pair] open ${codePath} <-> ${prosePath}`);
  }

  private readonly propagateTimers = new Map<string, NodeJS.Timeout>();

  /** After a file sync, refresh ancestor DIR.prose files (server writes them; open editors reload).
   *  Coalesced per directory: a burst of syncs in one directory yields one propagation. */
  propagateUp(codePath: string, delayMs = 3000): void {
    const s = this.settings();
    if (!s.propagateUp) return;
    const dir = path.dirname(codePath);
    const pending = this.propagateTimers.get(dir);
    if (pending) clearTimeout(pending);
    this.propagateTimers.set(
      dir,
      setTimeout(() => {
        this.propagateTimers.delete(dir);
        void this.propagateUpNow(codePath);
      }, delayMs),
    );
  }

  private async propagateUpNow(codePath: string): Promise<void> {
    const s = this.settings();
    try {
      const r = await this.client.tree("propagate_up", codePath, s.sidecarDir);
      for (const x of r.synced) this.out.appendLine(`[tree] ${x.path}: ${x.edits} edit(s)`);
      for (const e of r.errors) this.out.appendLine(`[tree] error ${e.path}: ${e.error}`);
      if (r.synced.length) this.status.set("idle", `updated ${r.synced.length} DIR.prose`);
      const unpushed = r.errors.find((e) => e.error.includes("unpushed"));
      if (unpushed) void vscode.window.showWarningMessage(`Prose Code: ${path.basename(path.dirname(unpushed.path))}/DIR.prose has unpushed edits — run "Push Down" to apply them.`);
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

  async initFolder(dir: string, overwrite: boolean): Promise<void> {
    const s = this.settings();
    const r = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: `Prose Code: initializing prose under ${path.basename(dir)}/…` },
      () => this.client.tree("generate", dir, s.sidecarDir, overwrite),
    );
    for (const g of r.generated) this.out.appendLine(`[tree] wrote ${g}`);
    for (const e of r.errors) this.out.appendLine(`[tree] error ${e.path}: ${e.error}`);
    void vscode.window.showInformationMessage(`Prose Code: ${r.generated.length} file(s) written${r.errors.length ? `, ${r.errors.length} error(s) (see log)` : ""}`);
    const dirProse = path.join(dir, "DIR.prose");
    if (fs.existsSync(dirProse)) await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(dirProse), { preview: false });
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

  private generating = new Set<string>();

  /** Generate prose for a source file that has none (auto-generate on save/create). Returns true if generated. */
  async ensureProse(fsPath: string, reason: string): Promise<boolean> {
    const s = this.settings();
    if (!isSupportedSource(fsPath) || this.generating.has(fsPath)) return false;
    const prosePath = prosePathFor(fsPath, s.sidecarDir);
    if (fs.existsSync(prosePath)) return false;
    let code: string;
    try {
      code = fs.readFileSync(fsPath, "utf8");
    } catch {
      return false;
    }
    if (!code.trim()) return false; // empty new file: wait for content
    const language = languageForPath(fsPath);
    this.generating.add(fsPath);
    try {
      this.status.set("syncing", `generating prose for ${path.basename(fsPath)} (${reason})`);
      const gen = await this.client.generate(code, language, fsPath);
      fs.mkdirSync(path.dirname(prosePath), { recursive: true });
      fs.writeFileSync(prosePath, gen.prose);
      saveSnapshot(fsPath, { prose: gen.prose, code, blocks: gen.blocks });
      this.out.appendLine(`[auto] generated ${prosePath} (${reason}, ${gen.blocks.length} blocks, ${gen.latency_ms} ms)`);
      this.status.set("idle", `prose generated for ${path.basename(fsPath)}`);
      this.propagateUp(fsPath, 500);
      return true;
    } catch (e) {
      this.out.appendLine(`[auto] generate failed for ${fsPath}: ${(e as Error).message}`);
      this.status.set("error", `could not generate prose for ${path.basename(fsPath)}`);
      return false;
    } finally {
      this.generating.delete(fsPath);
    }
  }

  /** The inverse: a .prose file was saved but its code file does not exist yet -> create the code. */
  async ensureCode(prosePath: string): Promise<boolean> {
    const s = this.settings();
    const name = path.basename(prosePath);
    if (!name.endsWith(".prose") || name === "DIR.prose" || this.generating.has(prosePath)) return false;
    const codePath = codePathFor(prosePath, s.sidecarDir);
    if (fs.existsSync(codePath) || !isSupportedSource(codePath)) return false;
    let prose: string;
    try {
      prose = fs.readFileSync(prosePath, "utf8");
    } catch {
      return false;
    }
    if (!prose.trim()) return false;
    this.generating.add(prosePath);
    try {
      this.status.set("syncing", `writing ${path.basename(codePath)} from its prose`);
      const r = await this.client.create(prose, languageForPath(codePath), codePath);
      fs.writeFileSync(codePath, r.code);
      fs.writeFileSync(prosePath, r.prose);
      saveSnapshot(codePath, { prose: r.prose, code: r.code, blocks: r.blocks });
      this.out.appendLine(`[auto] created ${codePath} from ${prosePath} (${r.blocks.length} blocks)`);
      this.status.set("idle", `created ${path.basename(codePath)}`);
      this.propagateUp(codePath, 500);
      const codeDoc = await vscode.workspace.openTextDocument(codePath);
      await this.openPair(codeDoc);
      return true;
    } catch (e) {
      this.out.appendLine(`[auto] create failed for ${prosePath}: ${(e as Error).message}`);
      this.status.set("error", `could not create ${path.basename(codePath)}`);
      return false;
    } finally {
      this.generating.delete(prosePath);
    }
  }

  /** Source files were deleted: drop their sidecars/maps and let the directory prose forget them. */
  onFilesDeleted(fsPaths: string[]): void {
    const s = this.settings();
    for (const fsPath of fsPaths) {
      if (!isSupportedSource(fsPath)) continue;
      this.pairs.get(fsPath)?.manager.dispose();
      this.pairs.delete(fsPath);
      for (const p of [prosePathFor(fsPath, s.sidecarDir), mapPathFor(fsPath)]) {
        try {
          if (fs.existsSync(p)) fs.rmSync(p);
        } catch (e) {
          this.out.appendLine(`[auto] could not remove ${p}: ${(e as Error).message}`);
        }
      }
      this.out.appendLine(`[auto] removed prose for deleted ${fsPath}`);
      this.propagateUp(fsPath, 500);
    }
  }

  /** Source files were renamed/moved: move sidecars and maps with them, update both directories. */
  onFilesRenamed(moves: { oldPath: string; newPath: string }[]): void {
    const s = this.settings();
    for (const { oldPath, newPath } of moves) {
      if (!isSupportedSource(oldPath)) continue;
      this.pairs.get(oldPath)?.manager.dispose();
      this.pairs.delete(oldPath);
      const pairs: [string, string][] = [
        [prosePathFor(oldPath, s.sidecarDir), prosePathFor(newPath, s.sidecarDir)],
        [mapPathFor(oldPath), mapPathFor(newPath)],
      ];
      for (const [from, to] of pairs) {
        try {
          if (fs.existsSync(from)) {
            fs.mkdirSync(path.dirname(to), { recursive: true });
            fs.renameSync(from, to);
          }
        } catch (e) {
          this.out.appendLine(`[auto] could not move ${from}: ${(e as Error).message}`);
        }
      }
      // the summary heading names the file: fix it so DIR.prose and headings stay consistent
      const prosePath = prosePathFor(newPath, s.sidecarDir);
      if (fs.existsSync(prosePath) && path.basename(oldPath) !== path.basename(newPath)) {
        const text = fs.readFileSync(prosePath, "utf8");
        const fixed = text.replace(new RegExp(`^# ${escapeRegExp(path.basename(oldPath))}$`, "m"), `# ${path.basename(newPath)}`);
        if (fixed !== text) fs.writeFileSync(prosePath, fixed);
        const mapPath = mapPathFor(newPath);
        if (fs.existsSync(mapPath)) {
          try {
            const snap = JSON.parse(fs.readFileSync(mapPath, "utf8"));
            snap.prose = fixed;
            fs.writeFileSync(mapPath, JSON.stringify(snap, null, 1));
          } catch {
            /* the server rebuilds it */
          }
        }
      }
      this.out.appendLine(`[auto] moved prose ${oldPath} -> ${newPath}`);
      this.propagateUp(oldPath, 500);
      if (path.dirname(oldPath) !== path.dirname(newPath)) this.propagateUp(newPath, 500);
    }
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
    for (const t of this.propagateTimers.values()) clearTimeout(t);
    this.propagateTimers.clear();
    this.forEach((m) => m.dispose());
    this.pairs.clear();
  }
}

const LANGUAGE_BY_SUFFIX: Record<string, string> = {
  ".py": "python", ".ts": "typescript", ".tsx": "tsx", ".js": "javascript", ".jsx": "jsx", ".go": "go", ".rs": "rust",
  ".java": "java", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".rb": "ruby", ".cs": "c_sharp",
  ".kt": "kotlin", ".swift": "swift", ".php": "php", ".sh": "bash", ".lua": "lua",
};

function languageForPath(fsPath: string): string {
  return LANGUAGE_BY_SUFFIX[path.extname(fsPath).toLowerCase()] ?? "text";
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
