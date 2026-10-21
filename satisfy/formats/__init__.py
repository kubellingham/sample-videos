"""Format plugins, discovered from the modules in this folder.

To add a format, drop a module here that defines a ``Simulation`` subclass
with a unique ``name``. Nothing else needs to change.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

from .base import PHYSICS_HZ, Simulation, SimResult

_registry: dict[str, type[Simulation]] | None = None


def available() -> dict[str, type[Simulation]]:
    global _registry
    if _registry is None:
        found: dict[str, type[Simulation]] = {}
        for mod in pkgutil.iter_modules(__path__):
            if mod.name.startswith("_") or mod.name == "base":
                continue
            module = importlib.import_module(f"{__name__}.{mod.name}")
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, Simulation) and obj is not Simulation and obj.name:
                    if obj.name in found and found[obj.name] is not obj:
                        raise RuntimeError(f"Duplicate format name '{obj.name}'")
                    found[obj.name] = obj
        _registry = found
    return _registry


def get(name: str) -> type[Simulation]:
    formats = available()
    if name not in formats:
        raise ValueError(f"Unknown format '{name}'. Available: {', '.join(sorted(formats))}")
    return formats[name]


__all__ = ["PHYSICS_HZ", "Simulation", "SimResult", "available", "get"]
