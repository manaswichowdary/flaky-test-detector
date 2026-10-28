_PLUGINS: dict[str, object] = {}


def register(name: str, plugin: object) -> None:
    _PLUGINS[name] = plugin


def lookup(name: str) -> object:
    return _PLUGINS[name]


def names() -> list[str]:
    return sorted(_PLUGINS)
