// Mirrors sync/src/prosesync/models.py. Keep in sync by hand (Phase 2: generate from pydantic).
export type Side = "prose" | "code";
export const otherSide = (s: Side): Side => (s === "prose" ? "code" : "prose");

export interface Block { id: string; prose: [number, number]; code: [number, number] }
export interface Snapshot { prose: string; code: string; blocks: Block[] }
export interface Pair {
  pair_id: string; language: string; code_path: string; prose: string; code: string;
  prose_version: number; code_version: number;
}
export interface SyncRequest {
  request_id: string; pair: Pair; base: Snapshot; change: { side: Side; cursor_line?: number | null };
  other_side_dirty: boolean; options: { verify?: boolean | null; model?: string | null; max_edits?: number | null };
}
export interface LineEdit { side: Side; start: number; end: number; new_text: string; block: string; reason?: string | null }
export interface VerifyResult { ok: boolean; verifier: string; message?: string | null; line?: number | null }
export interface SyncResponse {
  request_id: string; base_prose_version: number; base_code_version: number; target_side: Side;
  line_edits: LineEdit[]; prose: string; code: string; blocks: Block[]; latency_ms: number; model: string;
  usage: Record<string, unknown>; warnings: string[]; verification?: VerifyResult | null; code_blocks?: Block[];
}
export interface GenerateResponse { prose: string; blocks: Block[]; code_blocks?: Block[]; latency_ms: number; model: string }
export interface CreateResponse { prose: string; code: string; blocks: Block[] }
export interface Feedback {
  sync_id: string; outcome: "accepted" | "modified" | "reverted"; dwell_s: number; final_text_by_block: Record<string, string>;
}
export interface Preview { side: Side; block: string; start: number; text: string; done: boolean }
export type SyncEvent =
  | { event: "preview"; data: Preview }
  | { event: "edit"; data: LineEdit }
  | { event: "done"; data: SyncResponse }
  | { event: "error"; data: { message: string; needs_regenerate: boolean } };
export interface TreeResult {
  generated: string[];
  synced: { path: string; edits: number }[];
  unchanged: string[];
  errors: { path: string; error: string }[];
}
