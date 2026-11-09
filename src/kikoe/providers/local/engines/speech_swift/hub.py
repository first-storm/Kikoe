"""A stand-in for the Hugging Face Hub that serves one installed model folder.

speech-swift always asks the Hub for a model's file list before loading and retries
for minutes when it is unreachable. Pointing ``HF_ENDPOINT`` here lists the installed
files instead; speech-swift finds them complete on disk and downloads nothing.
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from kikoe.providers.local.adapter import EngineError
from kikoe.providers.local.protocol import ErrorKind

log = logging.getLogger(__name__)

CACHE_ROOT = Path("qwen3-speech", "models")
"""speech-swift's cache layout below ``QWEN3_CACHE_DIR``."""


class LocalHub:
    """Answers speech-swift's Hub requests from one installed model folder."""

    def __init__(self) -> None:
        self._root: Path | None = None
        self._http: ThreadingHTTPServer | None = None

    @property
    def port(self) -> int:
        assert self._http is not None
        return self._http.server_address[1]

    def serve(self, folder: Path) -> None:
        self._root = folder / CACHE_ROOT
        if self._http is None:
            self._http = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
            threading.Thread(target=self._http.serve_forever, daemon=True).start()

    def close(self) -> None:
        http, self._http = self._http, None
        if http is not None:
            http.shutdown()
            http.server_close()

    def listing(self, repo: str) -> list[dict[str, object]] | None:
        folder = self._repo_folder(repo)
        if folder is None:
            return None
        return [
            {"type": "file", "path": p.relative_to(folder).as_posix(), "size": p.stat().st_size}
            for p in sorted(folder.rglob("*"))
            if p.is_file()
        ]

    def file(self, repo: str, path: str) -> Path | None:
        folder = self._repo_folder(repo)
        if folder is None:
            return None
        candidate = (folder / path).resolve()
        if not candidate.is_relative_to(folder.resolve()) or not candidate.is_file():
            return None
        return candidate

    def _repo_folder(self, repo: str) -> Path | None:
        if self._root is None or repo.count("/") != 1 or ".." in repo:
            return None
        folder = self._root / repo
        return folder if folder.is_dir() else None

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                route = hub_route(urlsplit(self.path).path)
                body: bytes | None = None
                match route:
                    case ("tree", repo, _):
                        listing = hub.listing(repo)
                        body = None if listing is None else json.dumps(listing).encode()
                    case ("resolve", repo, path):
                        found = hub.file(repo, path)
                        body = None if found is None else found.read_bytes()
                    case _:
                        pass
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                log.debug("Hub: %s", format % args)

        return Handler


def hub_route(path: str) -> tuple[str, str, str] | None:
    """``("tree", repo, "")`` or ``("resolve", repo, file)`` for the Hub URLs speech-swift uses."""
    parts = [unquote(p) for p in path.strip("/").split("/")]
    if len(parts) >= 5 and parts[:2] == ["api", "models"] and parts[4] == "tree":
        return ("tree", f"{parts[2]}/{parts[3]}", "")
    if len(parts) >= 5 and parts[2] == "resolve":
        return ("resolve", f"{parts[0]}/{parts[1]}", "/".join(parts[4:]))
    return None


def model_repo(folder: Path) -> str:
    """The one ``<org>/<repo>`` a model folder holds."""
    root = folder / CACHE_ROOT
    repos = [
        f"{org.name}/{repo.name}"
        for org in (sorted(root.iterdir()) if root.is_dir() else ())
        if org.is_dir()
        for repo in sorted(org.iterdir())
        if repo.is_dir()
    ]
    if len(repos) != 1:
        raise EngineError(ErrorKind.MODEL, f"expected one repository in {folder.name}")
    return repos[0]
