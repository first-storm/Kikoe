"""Installed engines and models on disk, each in its own folder with a receipt.

Layout under the root (``paths.data_dir()/local`` by default)::

    engines/<engine id>/install.json    which build and version is installed
    engines/<engine id>/<target>/...    unpacked components
    models/<model id>/model.json        checksum of the installed file
    models/<model id>/<file name>
    staging/                            downloads in progress; emptied on start

A folder without a valid receipt counts as not installed. Installation fills a folder
under ``staging`` and moves it into place only after every file has been verified, so
an interrupted install never leaves a half-working engine.
"""

import asyncio
import json
import logging
import shutil
import tarfile
import uuid
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

from kikoe.core.local_models import (
    ArchiveKind,
    Download,
    EngineBuild,
    EngineSpec,
    LocalModelSpec,
)
from kikoe.i18n import _
from kikoe.storage.downloads import DownloadError, ProgressCallback, download

if TYPE_CHECKING:
    import httpx

log = logging.getLogger(__name__)

RECEIPT_VERSION = 1


class InstallError(Exception):
    """Installing or removing failed; the message is shown to the user."""


@dataclass(frozen=True, slots=True)
class EngineReceipt:
    engine_id: str
    build_id: str
    version: str
    path: Path
    bundled: bool = False
    """Shipped inside the application, so it can be neither removed nor replaced."""


class LocalInstalls:
    """Blocking file operations, except the async installs; call the rest in a thread."""

    def __init__(
        self,
        root: Path,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        bundled: Path | None = None,
    ) -> None:
        self.root = root
        self._transport = transport
        self._bundled = bundled
        """Engines shipped with the app (``<bundled>/<engine id>``), e.g. on macOS."""

    def engine_dir(self, engine_id: str) -> Path:
        return self.root / "engines" / engine_id

    def model_dir(self, model_id: str) -> Path:
        return self.root / "models" / model_id

    def model_path(self, model: LocalModelSpec) -> Path:
        return self.model_dir(model.model_id) / model.file.filename

    def clean_staging(self) -> None:
        """Remove what an earlier, interrupted install left behind."""
        shutil.rmtree(self.root / "staging", ignore_errors=True)

    def engine_receipt(self, engine_id: str) -> EngineReceipt | None:
        """A bundled engine takes precedence over a downloaded one."""
        if self._bundled is not None:
            receipt = _receipt(self._bundled / engine_id, engine_id)
            if receipt is not None:
                return replace(receipt, bundled=True)
        return _receipt(self.engine_dir(engine_id), engine_id)

    def model_installed(self, model: LocalModelSpec) -> bool:
        data = _read_json(self.model_dir(model.model_id) / "model.json")
        return (
            data is not None
            and data.get("version") == RECEIPT_VERSION
            and data.get("sha256") == model.file.sha256
            and self.model_path(model).is_file()
        )

    async def install_engine(
        self, engine: EngineSpec, build: EngineBuild, on_progress: ProgressCallback
    ) -> None:
        """Download, unpack and swap in ``build``, replacing any installed build."""
        staging = await asyncio.to_thread(self._new_staging)
        try:
            folder = staging / "install"
            for item in engine.downloads(build):
                file = staging / item.filename
                await self._download(item, file, on_progress)
                await asyncio.to_thread(_unpack, item, file, folder)
                if item.archive is not ArchiveKind.FILE:
                    await asyncio.to_thread(file.unlink)
            receipt = {
                "version": RECEIPT_VERSION,
                "engine": engine.engine_id,
                "build": build.build_id,
                "engine_version": engine.version,
            }
            await asyncio.to_thread(_write_json, folder / "install.json", receipt)
            await asyncio.to_thread(_swap, folder, self.engine_dir(engine.engine_id))
        finally:
            await asyncio.to_thread(shutil.rmtree, staging, True)
        log.info("Installed engine %s build %s", engine.engine_id, build.build_id)

    async def install_model(self, model: LocalModelSpec, on_progress: ProgressCallback) -> None:
        staging = await asyncio.to_thread(self._new_staging)
        try:
            folder = staging / "install"
            await asyncio.to_thread(folder.mkdir)
            await self._download(model.file, folder / model.file.filename, on_progress)
            receipt = {"version": RECEIPT_VERSION, "sha256": model.file.sha256}
            await asyncio.to_thread(_write_json, folder / "model.json", receipt)
            await asyncio.to_thread(_swap, folder, self.model_dir(model.model_id))
        finally:
            await asyncio.to_thread(shutil.rmtree, staging, True)
        log.info("Installed model %s", model.model_id)

    def remove_engine(self, engine_id: str) -> None:
        _remove(self.engine_dir(engine_id))

    def remove_model(self, model_id: str) -> None:
        _remove(self.model_dir(model_id))

    def _new_staging(self) -> Path:
        folder = self.root / "staging" / uuid.uuid4().hex
        folder.mkdir(parents=True)
        return folder

    async def _download(self, item: Download, file: Path, on_progress: ProgressCallback) -> None:
        await download(item, file, on_progress, transport=self._transport)


def _receipt(folder: Path, engine_id: str) -> EngineReceipt | None:
    data = _read_json(folder / "install.json")
    if data is None or data.get("version") != RECEIPT_VERSION:
        return None
    build, version = data.get("build"), data.get("engine_version")
    if data.get("engine") != engine_id or not isinstance(build, str):
        return None
    if not isinstance(version, str):
        return None
    return EngineReceipt(engine_id, build, version, folder)


def _swap(source: Path, destination: Path) -> None:
    """Move ``source`` to ``destination``; the old folder disappears only after the move."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    old = destination.with_name(f".{destination.name}.old-{uuid.uuid4().hex}")
    try:
        if destination.exists():
            destination.rename(old)
        source.rename(destination)
    except OSError as error:
        if old.exists() and not destination.exists():
            old.rename(destination)
        raise InstallError(_("Could not finish installing. Check the free disk space.")) from error
    shutil.rmtree(old, ignore_errors=True)


def _remove(folder: Path) -> None:
    """Rename first, so a removal interrupted half-way never looks installed."""
    if not folder.exists():
        return
    doomed = folder.with_name(f".{folder.name}.removing-{uuid.uuid4().hex}")
    try:
        folder.rename(doomed)
    except OSError as error:
        raise InstallError(_("Could not remove the files. They may be in use.")) from error
    shutil.rmtree(doomed, ignore_errors=True)


def _unpack(item: Download, file: Path, folder: Path) -> None:
    target = folder / item.target if item.target else folder
    target.mkdir(parents=True, exist_ok=True)
    try:
        match item.archive:
            case ArchiveKind.FILE:
                file.replace(target / item.filename)
            case ArchiveKind.ZIP:
                _unzip(file, target, item.strip)
            case ArchiveKind.TAR_GZ:
                _untar(file, target, item.strip)
    except (OSError, zipfile.BadZipFile, tarfile.TarError) as error:
        log.warning("Could not unpack %s: %s", item.filename, type(error).__name__)
        raise DownloadError(
            _("“{name}” could not be unpacked.").format(name=item.filename)
        ) from error


def safe_member(name: str, strip: int) -> PurePosixPath | None:
    """The relative path an archive member may be written to; None to skip it.

    Rejects absolute paths and ``..`` so an archive cannot write outside its folder.
    """
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in name:
        raise DownloadError(_("The download contains an unsafe file path."))
    parts = [p for p in path.parts if p not in ("", ".")][strip:]
    return PurePosixPath(*parts) if parts else None


def _unzip(file: Path, target: Path, strip: int) -> None:
    with zipfile.ZipFile(file) as archive:
        for info in archive.infolist():
            relative = safe_member(info.filename, strip)
            if relative is None or info.is_dir():
                continue
            destination = target.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, destination.open("wb") as sink:
                shutil.copyfileobj(source, sink)


def _untar(file: Path, target: Path, strip: int) -> None:
    def keep(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo | None:
        relative = safe_member(member.name, strip)
        if relative is None:
            return None
        return tarfile.data_filter(member.replace(name=str(relative), deep=False), path)

    with tarfile.open(file, "r:gz") as archive:
        archive.extractall(target, filter=keep)


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    return cast(dict[str, object], data) if isinstance(data, dict) else None


def _write_json(path: Path, data: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
