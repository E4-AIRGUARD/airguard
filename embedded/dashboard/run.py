"""Run the Airguard static dashboard locally. Requires Python 3."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse

def main():
    parser = argparse.ArgumentParser(description="Airguard dashboard local preview")
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    directory = Path(__file__).resolve().parent / "dist"
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    try:
        with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
            print(f"Airguard: http://127.0.0.1:{args.port} (Ctrl+C to stop)", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    except OSError as error:
        raise SystemExit(f"Cannot start server: {error}. Try --port 8093")

if __name__ == "__main__":
    main()
