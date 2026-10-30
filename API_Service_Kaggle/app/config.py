import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    api_key: str = os.getenv("MODEL_GATEWAY_API_KEY", "")
    device: str = os.getenv("MODEL_DEVICE", "cpu")
    cpu_threads: int = int(os.getenv("MODEL_CPU_THREADS", "2"))
    max_concurrent: int = int(os.getenv("MAX_CONCURRENT_INFERENCE", "1"))
    queue_limit: int = int(os.getenv("INFERENCE_QUEUE_LIMIT", "8"))
    max_image_bytes: int = int(os.getenv("MAX_IMAGE_BYTES", "15000000"))
    max_image_pixels: int = int(os.getenv("MAX_IMAGE_PIXELS", "25000000"))
    batch_chunk_size: int = int(os.getenv("BATCH_CHUNK_SIZE", "16"))
    recognition_batch_size: int = int(os.getenv("OCR_RECOGNITION_BATCH_SIZE", "4"))


settings = Settings()
if settings.device != "cpu":
    raise ValueError("This service supports MODEL_DEVICE=cpu only")
if min(settings.cpu_threads, settings.max_concurrent, settings.queue_limit, settings.batch_chunk_size, settings.recognition_batch_size) < 1:
    raise ValueError("CPU threads, concurrency, queue and chunk size must be positive")
