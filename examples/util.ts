import { readFileSync } from "fs";

export interface Config {
  retries: number;
  verbose: boolean;
}

export const DEFAULTS: Config = { retries: 3, verbose: false };

export function loadConfig(path: string): Config {
  const raw = JSON.parse(readFileSync(path, "utf8"));
  return { ...DEFAULTS, ...raw };
}

export function retry<T>(fn: () => T, times: number): T {
  let last: unknown;
  for (let i = 0; i < times; i++) {
    try {
      return fn();
    } catch (e) {
      last = e;
    }
  }
  throw last;
}
