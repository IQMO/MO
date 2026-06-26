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

__all__ = ["fuzzy_search", "get_callers", "get_callees"]
