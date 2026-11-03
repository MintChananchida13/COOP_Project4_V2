/** Apply the same boundary detector and axis-aligned crop as the user AdjustZone. */
export async function autoCropDetectionImage(file: File, signal: AbortSignal, sourceType: "image" | "pdf" = "image"): Promise<File> {
  if (!file.type.startsWith("image/") || signal.aborted) return file;
  const url = URL.createObjectURL(file);
  let worker: Worker | null = null;
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    if (signal.aborted) return file;
    const scale = Math.min(1, 1600 / Math.max(image.naturalWidth, image.naturalHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) return file;
    context.fillStyle = "white";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height);
    worker = new Worker("/card-crop-worker.js");
    const detector = worker;
    const box = await new Promise<number[] | null>((resolve) => {
      const finish = (value: number[] | null) => {
        clearTimeout(timer);
        signal.removeEventListener("abort", abort);
        detector.onmessage = null;
        detector.onerror = null;
        resolve(value);
      };
      const abort = () => finish(null);
      const timer = setTimeout(abort, 45000);
      signal.addEventListener("abort", abort, { once: true });
      detector.onerror = abort;
      detector.onmessage = (event: MessageEvent<{ box?: number[]; error?: string }>) => {
        finish(event.data.error ? null : event.data.box || null);
      };
      detector.postMessage({ id: 1, width: canvas.width, height: canvas.height,
        pixels: pixels.data.buffer, sourceType }, [pixels.data.buffer]);
    });
    if (signal.aborted || !box || box.length !== 4 || !box.every(Number.isFinite)) return file;
    const left = Math.max(0, box[0] * image.naturalWidth / canvas.width);
    const top = Math.max(0, box[1] * image.naturalHeight / canvas.height);
    const right = Math.min(image.naturalWidth, box[2] * image.naturalWidth / canvas.width);
    const bottom = Math.min(image.naturalHeight, box[3] * image.naturalHeight / canvas.height);
    const width = Math.round(right - left);
    const height = Math.round(bottom - top);
    if (width < 24 || height < 24) return file;
    const cropped = document.createElement("canvas");
    cropped.width = width;
    cropped.height = height;
    const cropContext = cropped.getContext("2d");
    if (!cropContext) return file;
    cropContext.fillStyle = "white";
    cropContext.fillRect(0, 0, width, height);
    cropContext.drawImage(image, left, top, width, height, 0, 0, width, height);
    const blob = await new Promise<Blob | null>((resolve) => cropped.toBlob(resolve, "image/jpeg", 0.95));
    return blob && !signal.aborted
      ? new File([blob], `${file.name.replace(/\.[^.]+$/, "")}-auto-crop.jpg`, { type: "image/jpeg" })
      : file;
  } catch (error) {
    console.warn("Automatic document crop unavailable; using original image.", error);
    return file;
  } finally {
    worker?.terminate();
    URL.revokeObjectURL(url);
  }
}
