"""Small dict helpers ported from the super-fiesta pattern.

``update()`` performs a recursive (deep) merge of ``u`` INTO ``d``. Mappings
merge key-by-key; every other type (scalars, lists) REPLACES wholesale — so an
account that specifies its own list fully replaces the globals list rather than
appending. That is intentional and matches the super-fiesta / cluster-cauldron
merge semantics.
"""

import collections.abc


def update(d: dict, u: collections.abc.Mapping) -> dict:
    """Recursively merge ``u`` into ``d`` and return ``d``.

    Mappings merge key-by-key; every other type (scalars, lists) replaces.
    """
    for k, v in u.items():
        if isinstance(v, collections.abc.Mapping):
            d[k] = update(d.get(k, {}), dict(v))
        else:
            d[k] = v
    return d
