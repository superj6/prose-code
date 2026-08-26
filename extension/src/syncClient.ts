import { ChildProcess, spawn } from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { Block, Feedback, GenerateResponse, SyncEvent, SyncRequest } from "./protocol";
import { Settings } from "./settings";

/** Talks to the prosesync HTTP server; spawns it when endpoint === "auto". */
export class SyncClient implements vscode.Disposable {
  private proc: ChildProcess | undefined;
  private baseUrl: string | undefined;
  private starting: Promise<string> | undefined;

  constructor(private readonly settings: () => Settings, private readonly out: vscode.OutputChannel) {}

  async url(): Promise<string> {
    const s = this.settings();
    if (s.endpoint !== "auto") return s.endpoint.replace(/\/$/, "");
    if (this.baseUrl) return this.baseUrl;
    if (!this.starting) this.starting = this.spawnServer().finally(() => (this.starting = undefined));
    return this.starting;
  }

  private repoRoot(): string {
    // extension/ lives inside the monorepo; the sync service is a sibling.
    return path.resolve(__dirname, "..", "..");
  }

  private python(): string {
    const s = this.settings();
    if (s.pythonPath) return s.pythonPath;
    const venv = path.join(this.repoRoot(), ".venv", "bin", "python");
    return fs.existsSync(venv) ? venv : "python3";
  }

  private async spawnServer(): Promise<string> {
    const s = this.settings();
    const args = ["-m", "prosesync.cli", "--backend", s.backend, "serve"];
    this.out.appendLine(`[server] ${this.python()} ${args.join(" ")}`);
    const proc = spawn(this.python(), args, { cwd: this.repoRoot(), env: { ...process.env, PYTHONUNBUFFERED: "1" } });
    this.proc = proc;
    const port = await new Promise<number>((resolve, reject) => {
      let buf = "";
      proc.stdout?.on("data", (d) => {
        buf += d.toString();
        const m = buf.match(/PROSESYNC_PORT=(\d+)/);
        if (m) resolve(Number(m[1]));
      });
      proc.stderr?.on("data", (d) => this.out.append(`[server] ${d}`));
      proc.on("exit", (code) => {
        this.out.appendLine(`[server] exited with ${code}`);
        this.baseUrl = undefined;
        reject(new Error(`prosesync server exited with code ${code}`));
      });
      setTimeout(() => reject(new Error("prosesync server did not start within 20s")), 20000);
    });
    const url = `http://127.0.0.1:${port}`;
    for (let i = 0; i < 50; i++) {
      try {
        const r = await fetch(`${url}/health`);
        if (r.ok) {
          this.out.appendLine(`[server] healthy: ${await r.text()}`);
          this.baseUrl = url;
          return url;
        }
      } catch {
        /* not up yet */
      }
      await new Promise((r) => setTimeout(r, 200));
    }
    throw new Error("prosesync server never became healthy");
  }

  async generate(code: string, language: string, codePath: string): Promise<GenerateResponse> {
    const s = this.settings();
    const r = await fetch(`${await this.url()}/generate`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ code, language, code_path: codePath, model: s.model || null }),
    });
    if (!r.ok) throw new Error(`generate failed: ${r.status} ${await r.text()}`);
    return (await r.json()) as GenerateResponse;
  }

  /** Rebuild the block map for an existing pair without the model; undefined when the prose is stale. */
  async align(prose: string, code: string, language: string): Promise<Block[] | undefined> {
    const r = await fetch(`${await this.url()}/align`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ prose, code, language }),
    });
    if (r.status === 409) return undefined;
    if (!r.ok) throw new Error(`align failed: ${r.status} ${await r.text()}`);
    return ((await r.json()) as { blocks: Block[] }).blocks;
  }

  /** Streams SSE events; resolves when the stream ends. Abort via signal. */
  async sync(req: SyncRequest, onEvent: (e: SyncEvent) => Promise<void>, signal: AbortSignal): Promise<void> {
    const r = await fetch(`${await this.url()}/sync`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(req),
      signal,
    });
    if (!r.ok || !r.body) throw new Error(`sync failed: ${r.status} ${await r.text()}`);
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const ev = parseSse(chunk);
        if (ev) await onEvent(ev);
      }
    }
  }

  async feedback(fb: Feedback): Promise<void> {
    try {
      await fetch(`${await this.url()}/feedback`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(fb),
      });
    } catch (e) {
      this.out.appendLine(`[feedback] failed: ${e}`);
    }
  }

  dispose(): void {
    this.proc?.kill();
    this.proc = undefined;
    this.baseUrl = undefined;
  }
}

export function parseSse(chunk: string): SyncEvent | undefined {
  let event = "";
  let data = "";
  for (const line of chunk.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7).trim();
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (!event || !data) return undefined;
  return { event, data: JSON.parse(data) } as SyncEvent;
}
