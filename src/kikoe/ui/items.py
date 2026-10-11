"""GObject wrappers so core dataclasses can live in a ``Gio.ListStore``."""

from gi.repository import GObject

from kikoe.core.models import ModelSpec, Provider


class ProviderItem(GObject.Object):
    def __init__(self, provider: Provider) -> None:
        super().__init__()
        self.provider = provider


class ModelItem(GObject.Object):
    def __init__(self, model: ModelSpec) -> None:
        super().__init__()
        self.model = model
