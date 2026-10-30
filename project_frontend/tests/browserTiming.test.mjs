import assert from "node:assert/strict";
import test from "node:test";
import { backendTimingMetric, browserTiming, frontendCompletionTiming, summarizeLayoutCalls } from "../src/shared/browserTiming.ts";

test("layout calls include search and flexible ROI exactly once", () => {
  const search = { call_id: "one", kind: "layout", queue_wait_ms: 2, inference_ms: 99,
    service_total_runtime_ms: 20, model_timings: { layout: { inference_ms: 10 } } };
  const flexible = { call_id: "two", kind: "layout", queue_wait_ms: 3, inference_ms: 7,
    service_total_runtime_ms: 14, model_timings: { layout: { inference_ms: 7 } } };
  const summary = summarizeLayoutCalls({ model_calls: [search] }, [{ model_calls: [search, flexible] }]);
  assert.equal(summary.calls.length, 2);
  assert.equal(summary.queue_wait_ms, 5);
  assert.equal(summary.inference_ms, 17);
  assert.equal(summary.total_ms, 34);
  assert.equal(summarizeLayoutCalls({}, []).inference_ms, null);
});

test("layout total is unavailable for shared layout and detector calls", () => {
  const summary = summarizeLayoutCalls({ model_calls: [{ call_id: "one", kind: "layout", queue_wait_ms: 1,
    inference_ms: 20, service_total_runtime_ms: 30,
    model_timings: { layout: { inference_ms: 10 }, text_detection: { inference_ms: 9 } } }] }, []);
  assert.equal(summary.inference_ms, 10);
  assert.equal(summary.total_ms, null);
});

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

test("response processing, preparation and commit are distinct measured boundaries, never paint", () => {
  const values = frontendCompletionTiming(100, 300, 315, {
    frontendResponseHeadersAt: 200, frontendResponseMappedAt: 225,
    frontend_response_json_ms: 20, frontend_response_processing_ms: 5,
  });
  assert.deepEqual(values, {
    frontend_response_json_ms: 20, frontend_response_processing_ms: 5,
    frontend_result_preparation_ms: 75, frontend_state_to_commit_ms: 15,
    frontend_response_to_commit_ms: 115, frontend_flow_complete_ms: 200,
    frontend_result_visible_ms: null, frontend_render_ms: null,
  });
  assert.equal(browserTiming(100, 315).end_to_end_ms, 215);
});

test("missing or invalid response clocks stay null, no inferred network/paint time", () => {
  const values = frontendCompletionTiming(100, 200, 190, { frontendResponseMappedAt: 250,
    frontend_response_json_ms: Infinity, frontend_response_processing_ms: -1 });
  for (const key of ['frontend_response_json_ms', 'frontend_response_processing_ms', 'frontend_result_preparation_ms',
    'frontend_state_to_commit_ms', 'frontend_response_to_commit_ms', 'frontend_result_visible_ms', 'frontend_render_ms']) {
    assert.equal(values[key], null, key);
  }
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
