import * as vscode from "vscode";
import { DocHandle } from "./docs";
import { LineEdit, Side } from "./protocol";

/** DocHandle over a real vscode.TextDocument. Line edits are applied as one WorkspaceEdit each. */
export class VsDoc implements DocHandle {
  constructor(readonly side: Side, readonly doc: vscode.TextDocument) {}

  get version(): number {
    return this.doc.version;
  }

  getText(): string {
    return this.doc.getText();
  }

  async applyLineEdit(le: LineEdit): Promise<boolean> {
    const start = new vscode.Position(le.start, 0);
    const end = this.doc.validatePosition(new vscode.Position(le.end, 0));
    let text = le.new_text;
    // If the range runs to the end of a document that lacks a trailing newline, don't add one.
    if (le.end >= this.doc.lineCount && !this.doc.getText().endsWith("\n")) text = text.replace(/\n$/, "");
    const edit = new vscode.WorkspaceEdit();
    edit.replace(this.doc.uri, new vscode.Range(start, end), text);
    return vscode.workspace.applyEdit(edit);
  }

  async replaceAll(text: string): Promise<boolean> {
    const full = new vscode.Range(new vscode.Position(0, 0), this.doc.lineAt(this.doc.lineCount - 1).range.end);
    const edit = new vscode.WorkspaceEdit();
    edit.replace(this.doc.uri, full, text);
    return vscode.workspace.applyEdit(edit);
  }
}
