"""Collect the GLib 2.80+ introspection API used by PyGObject 3.58."""

from PyInstaller.utils.hooks.gi import GiModuleInfo

module_info = GiModuleInfo("GIRepository", "3.0")
binaries, datas, hiddenimports = module_info.collect_typelib_data()
