import assert from "node:assert/strict";
import test from "node:test";
import { browserTiming } from "../src/shared/browserTiming.ts";

test("browser elapsed is measured while one-way and render times remain unknown", () => {
  assert.deepEqual(browserTiming(100.05, 250.07), {
    end_to_end_ms: 150.02,
    frontend_to_backend_ms: null,
    backend_to_frontend_ms: null,
    frontend_render_ms: null,
  });
});

test("invalid negative elapsed is not exposed as a negative duration", () => {
  assert.equal(browserTiming(250, 100).end_to_end_ms, 0);
});
