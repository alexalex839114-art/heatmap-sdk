import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const css = await readFile(new URL("../static/style.css", import.meta.url), "utf8");

function block(selector) {
  const escaped = selector.replaceAll(".", "\\.");
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  return match?.[1] ?? "";
}

test("signal row containers do not color inactive exchange halves", () => {
  assert.doesNotMatch(block(".buy-light"), /background:/);
  assert.doesNotMatch(block(".sell-light"), /background:/);
});

test("signal colors are applied to active exchange halves only", () => {
  assert.match(css, /\.buy-light\s+\.exchange-half\.active\s*\{[^}]*background:/s);
  assert.match(css, /\.sell-light\s+\.exchange-half\.active\s*\{[^}]*background:/s);
});
