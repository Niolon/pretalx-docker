"""Replace a tested upstream seam, including already imported references."""

import sys


def replace_function(module, name, replacement):
    original = getattr(module, name)
    for loaded in tuple(sys.modules.values()):
        if loaded and getattr(loaded, "__name__", "").startswith("pretalx."):
            for key, value in tuple(vars(loaded).items()):
                if value is original:
                    setattr(loaded, key, replacement)
    setattr(module, name, replacement)
    return original
