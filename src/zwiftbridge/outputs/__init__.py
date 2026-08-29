from .base import Output
from .console import ConsoleOutput
from .whoosh_link import WhooshLinkOutput
from .obp_mdns import ObpMdnsOutput
from .obp_dircon import ObpDirconOutput
from .zwift_dircon import ZwiftDirconOutput

__all__ = ["Output", "ConsoleOutput", "WhooshLinkOutput", "ObpMdnsOutput", "ObpDirconOutput", "ZwiftDirconOutput",
           "build_outputs"]


def build_outputs(names, settings=None):
    """Instantiate outputs by name. Imported lazily so a machine without
    pyobjc can still run the Link output."""
    settings = settings or {}
    built = []
    for name in names:
        if name == "console":
            built.append(ConsoleOutput())
        elif name == "whoosh_link":
            built.append(WhooshLinkOutput(**settings.get("whoosh_link", {})))
        elif name == "obp_mdns":
            built.append(ObpMdnsOutput(**settings.get("obp_mdns", {})))
        elif name == "obp_dircon":
            built.append(ObpDirconOutput(**settings.get("obp_dircon", {})))
        elif name == "zwift_dircon":
            built.append(ZwiftDirconOutput(**settings.get("zwift_dircon", {})))
        elif name == "keystrokes":
            from .keystrokes import KeystrokeOutput
            built.append(KeystrokeOutput(**settings.get("keystrokes", {})))
        else:
            raise ValueError(f"unknown output {name!r}")
    return built
