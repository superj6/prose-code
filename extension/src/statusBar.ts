import * as vscode from "vscode";

const ICON: Record<string, string> = {
  idle: "$(check)",
  debouncing: "$(clock)",
  syncing: "$(sync~spin)",
  error: "$(warning)",
  paused: "$(circle-slash)",
  none: "$(book)",
};

export class StatusBar implements vscode.Disposable {
  private readonly item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);

  constructor() {
    this.item.command = "prosecode.syncNow";
    this.set("none", "no pair open — run Prose Code: Open Pair");
    this.item.show();
  }

  set(state: string, detail?: string): void {
    this.item.text = `${ICON[state] ?? ICON.none} Prose`;
    this.item.tooltip = `Prose Code: ${state}${detail ? ` — ${detail}` : ""}\nClick to sync now`;
    this.item.backgroundColor = state === "error" ? new vscode.ThemeColor("statusBarItem.warningBackground") : undefined;
  }

  dispose(): void {
    this.item.dispose();
  }
}
