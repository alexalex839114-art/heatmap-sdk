import test from "node:test";
import assert from "node:assert/strict";

import { colorForIntensity, tradeColor, tradeRadius } from "../static/renderer.js";

test("colorForIntensity maps low values to cold blue", () => {
  assert.deepEqual(colorForIntensity(0), [16, 40, 120, 255]);
});

test("colorForIntensity maps mid values into transition band", () => {
  const [r, g, b, a] = colorForIntensity(128);
  assert.equal(a, 255);
  assert.ok(r > 80);
  assert.ok(g < r);
  assert.ok(b > 90);
});

test("colorForIntensity maps high values to warm red", () => {
  const [r, g, b, a] = colorForIntensity(255);
  assert.equal(a, 255);
  assert.ok(r >= 240);
  assert.ok(g <= 90);
  assert.ok(b <= 40);
});

test("tradeColor distinguishes buyer and seller aggressor", () => {
  assert.equal(tradeColor({ is_buyer_maker: false }), "rgba(74,222,128,0.95)");
  assert.equal(tradeColor({ is_buyer_maker: true }), "rgba(248,113,113,0.95)");
});

test("tradeRadius scales with quantity but stays bounded", () => {
  assert.equal(tradeRadius({ qty: 0 }), 1.5);
  assert.ok(tradeRadius({ qty: 10 }) > tradeRadius({ qty: 1 }));
  assert.equal(tradeRadius({ qty: 10_000 }), 5);
});
