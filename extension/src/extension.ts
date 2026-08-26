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
    vscode.commands.registerCommand(
      "prosecode.openPair",
      withError(async () => {
        const doc = vscode.window.activeTextEditor?.document;
        if (!doc || doc.isUntitled) throw new Error("open a saved source file first");
        if (doc.languageId === "prose") throw new Error("run this from the code side");
        await registry.openPair(doc);
      }),
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
    vscode.commands.registerCommand("prosecode.showLog", () => out.show()),
    vscode.workspace.onDidChangeTextDocument((e) => {
      if (e.contentChanges.length === 0) return;
      const found = registry.find(e.document);
      if (found) found.manager.onUserEdit(found.side);
    }),
    vscode.workspace.onDidSaveTextDocument((doc) => {
      const found = registry.find(doc);
      if (found && getSettings().syncOnSave) found.manager.onSave(found.side);
    }),
    vscode.workspace.onDidCloseTextDocument((doc) => registry.onDocumentClosed(doc)),
  );
}

export function deactivate(): void {
  /* disposables handle cleanup */
}
