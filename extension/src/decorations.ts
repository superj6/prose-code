import * as vscode from "vscode";
import { LineEdit } from "./protocol";

const FADE_MS = 8000;

/** Highlights ranges the model just changed, with the model's reason as trailing text. */
export class Decorations implements vscode.Disposable {
  private readonly changed = vscode.window.createTextEditorDecorationType({
    backgroundColor: new vscode.ThemeColor("diffEditor.insertedLineBackground"),
    isWholeLine: true,
    overviewRulerColor: new vscode.ThemeColor("editorOverviewRuler.addedForeground"),
    overviewRulerLane: vscode.OverviewRulerLane.Right,
  });
  private readonly reason = vscode.window.createTextEditorDecorationType({
    after: { color: new vscode.ThemeColor("editorCodeLens.foreground"), margin: "0 0 0 2em", fontStyle: "italic" },
  });
  private active = new Map<string, { ranges: vscode.Range[]; reasons: vscode.DecorationOptions[] }>();

  show(doc: vscode.TextDocument, le: LineEdit): void {
    const editor = vscode.window.visibleTextEditors.find((e) => e.document === doc);
    if (!editor) return;
    const lines = le.new_text === "" ? 0 : le.new_text.replace(/\n$/, "").split("\n").length;
    const range = new vscode.Range(le.start, 0, Math.max(le.start, le.start + lines - 1), 0);
    const key = doc.uri.toString();
    const cur = this.active.get(key) ?? { ranges: [], reasons: [] };
    cur.ranges.push(range);
    if (le.reason) {
      cur.reasons.push({
        range: new vscode.Range(le.start, 0, le.start, 0),
        renderOptions: { after: { contentText: `⟵ ${le.reason}` } },
      });
    }
    this.active.set(key, cur);
    editor.setDecorations(this.changed, cur.ranges);
    editor.setDecorations(this.reason, cur.reasons);
    setTimeout(() => this.clear(doc), FADE_MS);
  }

  clear(doc?: vscode.TextDocument): void {
    for (const editor of vscode.window.visibleTextEditors) {
      if (doc && editor.document !== doc) continue;
      editor.setDecorations(this.changed, []);
      editor.setDecorations(this.reason, []);
      this.active.delete(editor.document.uri.toString());
    }
  }

  dispose(): void {
    this.changed.dispose();
    this.reason.dispose();
  }
}
