export interface PreviewImage {
  complete: boolean;
  naturalWidth: number;
  src: string;
  decode?: () => Promise<void>;
}

export interface VisibilitySnapshot {
  current: boolean;
  domReady: boolean;
  overlay: boolean;
  previewRequired: boolean;
  image: PreviewImage | null;
  previewVisible?: boolean;
  pdfRender?: Promise<void>;
}

export interface VisibilityOutcome {
  status: "READY" | "PREVIEW_ERROR" | "VISIBILITY_TIMEOUT" | "RUN_CANCELLED" | "VISIBILITY_ERROR";
  readyAt: number | null;
  previewReadyAt: number | null;
  uiReadyAt: number | null;
  previewRequired: boolean;
  reason: string | null;
}

interface VisibilityScheduler {
  now: () => number;
  frame: (callback: () => void) => number;
  cancelFrame: (handle: number) => void;
  timeout: (callback: () => void, milliseconds: number) => ReturnType<typeof setTimeout>;
  cancelTimeout: (handle: ReturnType<typeof setTimeout>) => void;
}

export function observeResultVisibility(snapshot: () => VisibilitySnapshot, options: {
  signal: AbortSignal;
  timeoutMs?: number;
  scheduler?: VisibilityScheduler;
}): Promise<VisibilityOutcome> {
  const scheduler = options.scheduler ?? {
    now: () => performance.now(), frame: (callback: () => void) => requestAnimationFrame(callback),
    cancelFrame: (handle: number) => cancelAnimationFrame(handle),
    timeout: (callback: () => void, milliseconds: number) => setTimeout(callback, milliseconds),
    cancelTimeout: (handle: ReturnType<typeof setTimeout>) => clearTimeout(handle),
  };
  return new Promise(resolve => {
    let done = false;
    let frameHandle: number | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let previewRequired = false;
    let previewReadyAt: number | null = null;
    let uiReadyAt: number | null = null;
    let image: PreviewImage | null = null;
    let imageSrc = "";
    let pdfRender: Promise<void> | undefined;
    let generation = 0;
    let decoding = false;
    let previewReady = false;
    let uiFrames = 0;
    let readyFrames = 0;
    const finish = (status: VisibilityOutcome["status"], reason: string | null = null) => {
      if (done) return;
      done = true;
      if (frameHandle !== undefined) scheduler.cancelFrame(frameHandle);
      if (timer !== undefined) scheduler.cancelTimeout(timer);
      options.signal.removeEventListener("abort", cancelled);
      resolve({ status, reason, readyAt: status === "READY" ? scheduler.now() : null,
        previewReadyAt, uiReadyAt, previewRequired });
    };
    const cancelled = () => finish("RUN_CANCELLED", "run_changed_or_component_unmounted");
    const check = () => {
      if (done) return;
      try {
        const state = snapshot();
        if (!state.current || options.signal.aborted) return cancelled();
        const source = state.image?.src ?? "";
        const changed = image !== state.image || imageSrc !== source ||
          pdfRender !== state.pdfRender || previewRequired !== state.previewRequired;
        if (changed) {
          generation++;
          image = state.image;
          imageSrc = source;
          pdfRender = state.pdfRender;
          previewReadyAt = null;
          previewReady = false;
          decoding = false;
          readyFrames = 0;
        }
        previewRequired = state.previewRequired;
        if (!previewRequired) {
          previewReady = true;
          previewReadyAt = null;
        } else if (!previewReady && !decoding) {
          const version = generation;
          const success = () => {
            if (!done && version === generation && !options.signal.aborted && snapshot().current) {
              previewReady = true;
              previewReadyAt = scheduler.now();
            }
          };
          const failed = () => {
            if (!done && version === generation && !options.signal.aborted && snapshot().current) {
              finish("PREVIEW_ERROR", "required_preview_load_or_decode_failed");
            }
          };
          if (pdfRender) {
            decoding = true;
            pdfRender.then(success, failed).catch(() => finish("VISIBILITY_ERROR", "preview_observer_failed"));
          } else if (image?.complete) {
            if (image.naturalWidth <= 0) return failed();
            decoding = true;
            if (image.decode) {
              const decode = image.decode.bind(image);
              Promise.resolve().then(decode).then(success, failed)
                .catch(() => finish("VISIBILITY_ERROR", "preview_observer_failed"));
            }
            else success();
          }
        }
        const uiReady = state.domReady && !state.overlay;
        uiFrames = uiReady ? uiFrames + 1 : 0;
        if (uiFrames >= 2 && uiReadyAt === null) uiReadyAt = scheduler.now();
        readyFrames = uiReady && previewReady && (!previewRequired || state.previewVisible !== false) ? readyFrames + 1 : 0;
        if (readyFrames >= 2) return finish("READY");
        frameHandle = scheduler.frame(check);
      } catch {
        finish("VISIBILITY_ERROR", "result_dom_readiness_check_failed");
      }
    };
    options.signal.addEventListener("abort", cancelled, { once: true });
    if (options.signal.aborted) return cancelled();
    timer = scheduler.timeout(() => finish("VISIBILITY_TIMEOUT", "result_or_preview_not_ready_before_timeout"), options.timeoutMs ?? 30000);
    frameHandle = scheduler.frame(check);
  });
}

export function visibilityMetrics(startedAt: number, committedAt: number, responseAt: number | undefined, outcome: VisibilityOutcome) {
  const elapsed = (start: number | undefined, end: number | null) =>
    typeof start === "number" && end !== null && Number.isFinite(start) && Number.isFinite(end) && end >= start
      ? Math.round((end - start) * 100) / 100 : null;
  return {
    frontend_render_ms: elapsed(committedAt, outcome.readyAt),
    frontend_result_visible_ms: elapsed(responseAt, outcome.readyAt),
    frontend_preview_ready_ms: elapsed(responseAt, outcome.previewReadyAt),
    frontend_ui_ready_ms: elapsed(responseAt, outcome.uiReadyAt),
    frontend_commit_end_to_end_ms: elapsed(startedAt, committedAt),
    end_to_end_ms: elapsed(startedAt, outcome.readyAt),
  };
}

export function elementReady(element: HTMLElement | null): boolean {
  if (!element?.isConnected || document.visibilityState !== "visible") return false;
  for (let current: HTMLElement | null = element; current; current = current.parentElement) {
    const style = getComputedStyle(current);
    if (current.hidden || current.getAttribute("aria-hidden") === "true" || style.display === "none" ||
      style.visibility === "hidden" || style.visibility === "collapse" || Number(style.opacity) === 0) return false;
  }
  const rect = element.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0 &&
    rect.top < window.innerHeight && rect.left < window.innerWidth;
}
