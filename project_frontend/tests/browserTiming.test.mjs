import assert from "node:assert/strict";
import test from "node:test";
import { backendTimingMetric, browserTiming } from "../src/shared/browserTiming.ts";

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

test("canonical null is not replaced by stale benchmark or legacy values", () => {
  assert.equal(backendTimingMetric({ image_siglip_runtime_ms: null, siglip_inference_ms: 90,
    benchmark: { image_siglip_runtime_ms: 80 } }, "image_siglip_runtime_ms", "siglip_inference_ms"), null);
});

test("legacy fallback, zero, invalid durations and missing values", () => {
  assert.equal(backendTimingMetric({ siglip_inference_ms: 0 }, "image_siglip_runtime_ms", "siglip_inference_ms"), 0);
  assert.equal(backendTimingMetric({ benchmark: { total_runtime_ms: 100 } }, "total_runtime_ms"), 100);
  for (const value of [true, -1, Infinity, NaN, "10", undefined]) {
    assert.equal(backendTimingMetric({ total_runtime_ms: value }, "total_runtime_ms"), null);
  }
});
