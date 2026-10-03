# MO core.graph subpackage.
#
# Keep these convenience exports lazy. Importing core.graph.code_graph runs this
# package initializer, and eager search/callgraph imports pull in the structural
# graph stack during agent import.


def fuzzy_search(*args, **kwargs):
    from .search import search
    return search(*args, **kwargs)


def get_callers(*args, **kwargs):
    from .callgraph import get_callers as _get_callers
    return _get_callers(*args, **kwargs)


def get_callees(*args, **kwargs):
    from .callgraph import get_callees as _get_callees
    return _get_callees(*args, **kwargs)


def graph_explain(*args, **kwargs):
    from .query import explain
    return explain(*args, **kwargs)


def graph_neighbors(*args, **kwargs):
    from .query import neighbors
    return neighbors(*args, **kwargs)


def graph_shortest_path(*args, **kwargs):
    from .query import shortest_path
    return shortest_path(*args, **kwargs)


def graph_stats(*args, **kwargs):
    from .query import stats
    return stats(*args, **kwargs)


__all__ = [
    "fuzzy_search",
    "get_callers",
    "get_callees",
    "graph_explain",
    "graph_neighbors",
    "graph_shortest_path",
    "graph_stats",
]
