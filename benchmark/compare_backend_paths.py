"""Compare backend /health round trips from the same machine, without creating jobs."""
import argparse
import statistics
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit


def health_url(base):
    parsed = urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Expected an HTTP(S) base URL without credentials")
    return urlunsplit((parsed.scheme, parsed.netloc, "/health", "", ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", default="http://127.0.0.1:18081", help="Backend base URL inside Kaggle")
    parser.add_argument("--public", required=True, help="Public tunnel base URL")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=15)
    args = parser.parse_args()
    if not 1 <= args.runs <= 100 or args.timeout <= 0:
        parser.error("runs must be between 1 and 100; timeout must be positive")
    urls = {"local": health_url(args.local), "tunnel": health_url(args.public)}
    results = {name: [] for name in urls}
    for index in range(args.runs):
        names = ("local", "tunnel") if index % 2 == 0 else ("tunnel", "local")
        for name in names:
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(urls[name], timeout=args.timeout) as response:
                    response.read()
                    if response.status != 200:
                        raise RuntimeError("non-200 response")
                results[name].append((time.perf_counter() - started) * 1000)
            except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as error:
                print(f"{name} run {index + 1}: failed ({type(error).__name__})")
    for name, values in results.items():
        if values:
            print(f"{name}: {len(values)}/{args.runs} success, median={statistics.median(values):.2f} ms, "
                  f"min={min(values):.2f} ms, max={max(values):.2f} ms")
        else:
            print(f"{name}: 0/{args.runs} success")
    print("Health GET only: does not measure image upload, browser-to-tunnel latency, or template search.")
    return 0 if all(len(values) == args.runs for values in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
