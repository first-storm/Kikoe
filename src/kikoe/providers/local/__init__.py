"""Engines that transcribe on this device, each running in its own worker process.

Kikoe talks to an engine only through ``worker_host`` and the JSON-lines protocol in
``protocol``; nothing outside ``engines/<engine>/`` knows how the engine works inside.
"""
