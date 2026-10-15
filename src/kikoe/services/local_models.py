"""Local engines and models: what can be installed here and what is installed."""

import logging
from collections.abc import Callable

from kikoe.core.listeners import Listeners
from kikoe.core.local_models import (
    EngineSpec,
    InstallState,
    LocalCatalog,
    LocalModelSpec,
    Platform,
)

log = logging.getLogger(__name__)


class LocalModelManager:
    """The one place that knows the state of local engines and models; screens observe it."""

    def __init__(self, catalog: LocalCatalog, platform: Platform | None) -> None:
        self.catalog = catalog
        self.platform = platform
        self._listeners = Listeners[()]()

    def connect_changed(self, listener: Callable[[], None]) -> Callable[[], None]:
        return self._listeners.connect(listener)

    @property
    def engines(self) -> tuple[EngineSpec, ...]:
        """Engines with a build for this computer, in registry order."""
        platform = self.platform
        if platform is None:
            return ()
        return tuple(e for e in self.catalog.engines if e.builds_for(platform))

    @property
    def models(self) -> tuple[LocalModelSpec, ...]:
        """Models of the engines available here."""
        engines = {e.engine_id for e in self.engines}
        return tuple(m for m in self.catalog.models if m.engine_id in engines)

    def engine_state(self, engine_id: str) -> InstallState:
        return InstallState()

    def model_state(self, model_id: str) -> InstallState:
        return InstallState()
