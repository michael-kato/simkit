"""Engine adapters, imported lazily so each engine's SDK is only needed when used."""

from importlib import import_module

ADAPTERS = {
    "mock": "simkit.adapters.mock:MockAdapter",
    "gazebo": "simkit.adapters.gazebo:GazeboAdapter",
    "isaac": "simkit.adapters.isaac:IsaacAdapter",
    "unity": "simkit.adapters.unity:UnityAdapter",
    "unreal": "simkit.adapters.unreal:UnrealAdapter",
    "blender": "simkit.adapters.blender:BlenderAdapter",
}


def get_adapter(name):
    """Import and return the adapter class (not an instance) for engine ``name``."""
    try:
        module_name, class_name = ADAPTERS[name].split(":")
    except KeyError:
        raise ValueError(f"unknown engine {name!r}; choose from {sorted(ADAPTERS)}") from None
    return getattr(import_module(module_name), class_name)
