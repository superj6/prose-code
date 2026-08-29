import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";

const root = path.resolve(__dirname, "..", "..");
const pkg = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
const src = fs.readFileSync(path.join(root, "src", "extension.ts"), "utf8");
const contributed = new Set<string>(pkg.contributes.commands.map((c: { command: string }) => c.command));

test("every menu and keybinding entry names a contributed command", () => {
  for (const [menu, items] of Object.entries(pkg.contributes.menus as Record<string, { command: string }[]>)) {
    for (const it of items) assert.ok(contributed.has(it.command), `${menu}: ${it.command} not in contributes.commands`);
  }
  for (const k of pkg.contributes.keybindings as { command: string }[]) assert.ok(contributed.has(k.command), `keybinding ${k.command}`);
});

test("every contributed command is registered in extension.ts", () => {
  for (const id of contributed) assert.ok(src.includes(`"${id}"`), `${id} is contributed but never registered`);
});
