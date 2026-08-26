import * as esbuild from "esbuild";
const watch = process.argv.includes("--watch");
const common = { bundle: true, platform: "node", format: "cjs", sourcemap: true, target: "node18", logLevel: "info" };
const ext = { ...common, entryPoints: ["src/extension.ts"], outfile: "dist/extension.js", external: ["vscode"] };
const tests = { ...common, entryPoints: ["test/pairManager.test.ts"], outdir: "dist/test", external: ["vscode"] };
if (watch) {
  const ctx = await esbuild.context(ext);
  await ctx.watch();
} else {
  await esbuild.build(ext);
  await esbuild.build(tests);
}
