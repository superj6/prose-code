import * as path from "path";

/** Mirrors sync/src/prosesync/store.py (_EXT_TO_LANGUAGE) and tree.py (SKIP_DIRS). */
export const SUPPORTED_SUFFIXES = new Set([
  ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".c", ".h", ".cpp", ".cc", ".hpp", ".rb", ".cs", ".kt", ".swift", ".php", ".sh", ".lua",
]);
export const SKIP_DIRS = new Set([".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", "outputs", ".prose", "wandb"]);

export function isSupportedSource(fsPath: string): boolean {
  const name = path.basename(fsPath);
  if (name.endsWith(".prose") || name === "DIR.prose") return false;
  if (!SUPPORTED_SUFFIXES.has(path.extname(fsPath).toLowerCase())) return false;
  return !fsPath.split(path.sep).some((part) => SKIP_DIRS.has(part) || (part.startsWith(".") && part !== "."));
}

export function prosePathFor(codePath: string, sidecarDir: string): string {
  const dir = path.dirname(codePath);
  const name = path.basename(codePath) + ".prose";
  return sidecarDir ? path.join(dir, sidecarDir, name) : path.join(dir, name);
}

export function codePathFor(prosePath: string, sidecarDir: string): string {
  const name = path.basename(prosePath).replace(/\.prose$/, "");
  let dir = path.dirname(prosePath);
  if (sidecarDir && path.basename(dir) === sidecarDir) dir = path.dirname(dir);
  return path.join(dir, name);
}

export function mapPathFor(codePath: string): string {
  return path.join(path.dirname(codePath), ".prose", path.basename(codePath) + ".map.json");
}
