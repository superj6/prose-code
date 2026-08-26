import { LineEdit, Side } from "./protocol";

/** Minimal document surface the state machine needs; implemented for vscode in applier.ts and by fakes in tests. */
export interface DocHandle {
  readonly side: Side;
  readonly version: number;
  getText(): string;
  /** Apply one line edit atomically. Returns false if the editor refused it. */
  applyLineEdit(le: LineEdit): Promise<boolean>;
  /** Replace the whole document (recovery path). */
  replaceAll(text: string): Promise<boolean>;
}

export interface Ui {
  setStatus(state: string, detail?: string): void;
  showEdit(side: Side, le: LineEdit): void;
  clearEdits(): void;
  info(message: string): void;
  warn(message: string): void;
}

export type Timer = { set(fn: () => void, ms: number): unknown; clear(handle: unknown): void };
export const realTimer: Timer = { set: (fn, ms) => setTimeout(fn, ms), clear: (h) => clearTimeout(h as NodeJS.Timeout) };
