let sharedWorker: Worker | null = null;

export function getCardCropWorker(): Worker {
  if (!sharedWorker) {
    const worker = new Worker('/card-crop-worker.js');
    sharedWorker = worker;
    worker.addEventListener('error', () => {
      if (sharedWorker === worker) disposeCardCropWorker(worker);
    });
    worker.postMessage({ id: 0, type: 'preload' });
  }
  return sharedWorker;
}

export function disposeCardCropWorker(worker: Worker | null = sharedWorker): void {
  if (!worker) return;
  worker.terminate();
  if (sharedWorker === worker) sharedWorker = null;
}
