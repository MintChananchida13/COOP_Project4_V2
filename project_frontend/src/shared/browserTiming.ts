export function browserTiming(startedAt: number, endedAt: number) {
  return {
    end_to_end_ms: Math.round(Math.max(0, endedAt - startedAt) * 100) / 100,
    frontend_to_backend_ms: null,
    backend_to_frontend_ms: null,
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
