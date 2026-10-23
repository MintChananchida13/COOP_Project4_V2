export function browserTiming(startedAt: number, endedAt: number) {
  return {
    end_to_end_ms: Math.round(Math.max(0, endedAt - startedAt) * 100) / 100,
    frontend_to_backend_ms: null,
    backend_to_frontend_ms: null,
    frontend_render_ms: null,
  };
}
