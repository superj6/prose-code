import * as path from "path";
import * as vscode from "vscode";
import { Decorations } from "./decorations";
import { PairRegistry } from "./pairRegistry";
import { getSettings } from "./settings";
import { StatusBar } from "./statusBar";
import { SyncClient } from "./syncClient";

export function activate(context: vscode.ExtensionContext): void {
  const out = vscode.window.createOutputChannel("Prose Code");
  const status = new StatusBar();
  const decorations = new Decorations();
  const client = new SyncClient(getSettings, out);
  const registry = new PairRegistry(client, status, decorations, getSettings, out);
  context.subscriptions.push(out, status, decorations, client, registry);

  const withError = (fn: () => Promise<void>) => () =>
    fn().catch((e) => {
      out.appendLine(`error: ${e?.stack ?? e}`);
      void vscode.window.showErrorMessage(`Prose Code: ${e?.message ?? e}`);
    });

  context.subscriptions.push(
    vscode.commands.registerCommand("prosecode.openPair", (resource?: vscode.Uri) =>
      withError(async () => {
        const doc = resource ? await vscode.workspace.openTextDocument(resource) : vscode.window.activeTextEditor?.document;
        if (!doc || doc.isUntitled) throw new Error("open a saved source file first");
        if (doc.languageId === "prose") throw new Error("run this from the code side");
        await registry.openPair(doc);
      })(),
    ),
    vscode.commands.registerCommand(
      "prosecode.regenerateProse",
      withError(async () => {
        const doc = vscode.window.activeTextEditor?.document;
        if (!doc || doc.languageId === "prose") throw new Error("run this from the code side");
        const ok = await vscode.window.showWarningMessage("Regenerate the prose from the code? The current prose file is overwritten.", { modal: true }, "Regenerate");
        if (ok === "Regenerate") await registry.openPair(doc, true);
      }),
    ),
    vscode.commands.registerCommand(
      "prosecode.syncNow",
      withError(async () => {
        const doc = vscode.window.activeTextEditor?.document;
        const m = doc && registry.managerFor(doc);
        if (!m) throw new Error("no open pair for this file — run Prose Code: Open Pair");
        await m.syncNow();
      }),
    ),
    vscode.commands.registerCommand("prosecode.toggleAutoSync", () => {
      const cfg = vscode.workspace.getConfiguration("prosecode");
      const on = !cfg.get("autoSync", true);
      void cfg.update("autoSync", on, vscode.ConfigurationTarget.Global);
      registry.forEach((m) => m.setAutoSync(on));
      void vscode.window.showInformationMessage(`Prose Code: auto sync ${on ? "on" : "off"}`);
    }),
    vscode.commands.registerCommand("prosecode.toggleEnabled", () => {
      const cfg = vscode.workspace.getConfiguration("prosecode");
      const on = !cfg.get("enabled", true);
      void cfg.update("enabled", on, vscode.ConfigurationTarget.Global);
      registry.forEach((m) => m.setAutoSync(on && getSettings().autoSync));
      status.set(on ? "idle" : "paused", on ? "enabled" : "disabled (master switch)");
      void vscode.window.showInformationMessage(`Prose Code: ${on ? "enabled" : "disabled"}`);
    }),
    vscode.commands.registerCommand("prosecode.showLog", () => out.show()),
    vscode.commands.registerCommand("prosecode.openDirectoryProse", (resource?: vscode.Uri) =>
      withError(async () => {
        const doc = vscode.window.activeTextEditor?.document;
        const dir = resource?.fsPath ?? (doc && !doc.isUntitled ? path.dirname(doc.uri.fsPath) : vscode.workspace.workspaceFolders?.[0]?.uri.fsPath);
        if (!dir) throw new Error("open a file or a workspace folder first");
        await registry.openDirectoryProse(dir);
      })(),
    ),
    vscode.commands.registerCommand(
      "prosecode.pushDown",
      withError(async () => {
        const doc = vscode.window.activeTextEditor?.document;
        if (!doc || path.basename(doc.uri.fsPath) !== "DIR.prose") throw new Error("run this from a DIR.prose file");
        if (doc.isDirty) await doc.save();
        await registry.pushDown(path.dirname(doc.uri.fsPath));
      }),
    ),
    vscode.workspace.onDidChangeTextDocument((e) => {
      if (e.contentChanges.length === 0 || !getSettings().enabled) return;
      const found = registry.find(e.document);
      if (found) found.manager.onUserEdit(found.side);
    }),
    vscode.workspace.onDidSaveTextDocument((doc) => {
      if (!getSettings().enabled) return;
      const found = registry.find(doc);
      if (found && getSettings().syncOnSave) found.manager.onSave(found.side);
      if (path.basename(doc.uri.fsPath) === "DIR.prose") void maybePushDown(doc);
      if (!found && doc.uri.scheme === "file" && getSettings().autoGenerate !== "off") {
        if (doc.languageId === "prose") void registry.ensureCode(doc.uri.fsPath);
        else void registry.ensureProse(doc.uri.fsPath, "saved");
      }
    }),
    vscode.workspace.onDidCreateFiles((e) => {
      if (!getSettings().enabled || getSettings().autoGenerate !== "onCreate") return;
      for (const f of e.files) void registry.ensureProse(f.fsPath, "created");
    }),
    vscode.workspace.onDidDeleteFiles((e) => getSettings().enabled && registry.onFilesDeleted(e.files.map((f) => f.fsPath))),
    vscode.workspace.onDidRenameFiles((e) => getSettings().enabled && registry.onFilesRenamed(e.files.map((f) => ({ oldPath: f.oldUri.fsPath, newPath: f.newUri.fsPath })))),
    vscode.commands.registerCommand("prosecode.initFolder", (resource?: vscode.Uri) =>
      withError(async () => {
        let dir = resource?.fsPath;
        if (!dir) {
          const doc = vscode.window.activeTextEditor?.document;
          const fallback = doc && !doc.isUntitled ? path.dirname(doc.uri.fsPath) : vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
          const picked = await vscode.window.showOpenDialog({ canSelectFiles: false, canSelectFolders: true, canSelectMany: false, defaultUri: fallback ? vscode.Uri.file(fallback) : undefined, openLabel: "Initialize prose here" });
          dir = picked?.[0]?.fsPath;
        }
        if (!dir) return;
        const overwrite = (await vscode.window.showQuickPick(["Keep existing prose (only new files)", "Regenerate everything"], { placeHolder: `Initialize prose under ${dir}` })) === "Regenerate everything";
        await registry.initFolder(dir, overwrite);
      })(),
    ),
    vscode.workspace.onDidCloseTextDocument((doc) => registry.onDocumentClosed(doc)),
  );
}

async function maybePushDown(doc: vscode.TextDocument): Promise<void> {
  const mode = getSettings().pushDownOnSave;
  if (mode === "never") return;
  if (mode === "ask") {
    const pick = await vscode.window.showInformationMessage("Push this directory prose down into its children?", "Push down", "Not now");
    if (pick !== "Push down") return;
  }
  await vscode.commands.executeCommand("prosecode.pushDown");
}

export function deactivate(): void {
  /* disposables handle cleanup */
}
