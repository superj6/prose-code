import * as vscode from "vscode";

export interface Settings {
  model: string;
  endpoint: string;
  pythonPath: string;
  backend: "openai" | "mock";
  debounceMs: number;
  autoSync: boolean;
  syncOnSave: boolean;
  verify: boolean;
  logInteractions: boolean;
  sidecarDir: string;
  feedbackWindowS: number;
  propagateUp: boolean;
  pushDownOnSave: "ask" | "always" | "never";
  autoGenerate: "onFirstSave" | "onCreate" | "off";
}

export function getSettings(): Settings {
  const c = vscode.workspace.getConfiguration("prosecode");
  return {
    model: c.get("model", ""),
    endpoint: c.get("endpoint", "auto"),
    pythonPath: c.get("pythonPath", ""),
    backend: c.get("backend", "openai"),
    debounceMs: c.get("debounceMs", 700),
    autoSync: c.get("autoSync", true),
    syncOnSave: c.get("syncOnSave", true),
    verify: c.get("verify", false),
    logInteractions: c.get("logInteractions", true),
    sidecarDir: c.get("sidecarDir", ""),
    feedbackWindowS: c.get("feedbackWindowS", 30),
    propagateUp: c.get("propagateUp", true),
    pushDownOnSave: c.get("pushDownOnSave", "ask"),
    autoGenerate: c.get("autoGenerate", "onFirstSave"),
  };
}
