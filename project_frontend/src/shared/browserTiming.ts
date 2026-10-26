export function browserTiming(startedAt: number, endedAt: number) {
  return {
    end_to_end_ms: Math.round(Math.max(0, endedAt - startedAt) * 100) / 100,
    frontend_to_backend_ms: null,
    backend_to_frontend_ms: null,
    frontend_render_ms: null,
  };
}

export interface DetectionTransportTiming {
  uploadJobMs?: number;
  jobPollingMs?: number;
  frontendResponseHeadersAt?: number;
  frontendResponseMappedAt?: number;
  frontend_response_json_ms?: number;
  frontend_response_processing_ms?: number;
}

export function frontendCompletionTiming(flowStartedAt: number, flowCompleteAt: number, committedAt: number,
  transport: DetectionTransportTiming) {
  const elapsed = (start: number | undefined, end: number | undefined): number | null =>
    typeof start === "number" && typeof end === "number" && Number.isFinite(start) && Number.isFinite(end) && end >= start
      ? Math.round((end - start) * 100) / 100 : null;
  return {
    frontend_response_json_ms: elapsed(0, transport.frontend_response_json_ms),
    frontend_response_processing_ms: elapsed(0, transport.frontend_response_processing_ms),
    frontend_result_preparation_ms: elapsed(transport.frontendResponseMappedAt, flowCompleteAt),
    frontend_state_to_commit_ms: elapsed(flowCompleteAt, committedAt),
    frontend_response_to_commit_ms: elapsed(transport.frontendResponseHeadersAt, committedAt),
    frontend_flow_complete_ms: elapsed(flowStartedAt, flowCompleteAt),
    frontend_result_visible_ms: null,
    frontend_render_ms: null,
  };
}

export function backendTimingMetric(timing: Record<string, unknown> | undefined, key: string, alias?: string): number | null {
  const benchmark = timing?.benchmark as Record<string, unknown> | undefined;
  for (const source of [timing, benchmark]) {
    if (source && Object.prototype.hasOwnProperty.call(source, key)) {
      const value = source[key];
      return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
    }
  }
  return alias ? backendTimingMetric(timing, alias) : null;
}
