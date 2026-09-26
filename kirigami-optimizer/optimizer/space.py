"""Search space: config.SEARCH_SPACE <-> skopt dimensions <-> design params."""

from skopt.space import Integer, Real


def dimensions(search_space):
    dims = []
    for d in search_space:
        cls = Integer if d["type"] == "int" else Real
        dims.append(cls(d["low"], d["high"], name=d["name"], prior=d.get("prior", "uniform")))
    return dims


def to_params(search_space, x):
    """Optimizer vector -> {name: value} with plain Python types."""

    out = {}
    for d, v in zip(search_space, x):
        out[d["name"]] = int(v) if d["type"] == "int" else float(v)
    return out


def from_params(search_space, params):
    return [params[d["name"]] for d in search_space]


def normalize(search_space, x):
    """Scales each coordinate to [0, 1] (used for design similarity)."""

    return [(float(v) - d["low"]) / (d["high"] - d["low"]) for d, v in zip(search_space, x)]
