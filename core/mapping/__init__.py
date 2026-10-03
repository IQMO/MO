"""Project orientation (``mapthis``): bounded, skeleton-based multi-worker mapping.

A user-facing MO convention triggered by the inline keyword ``mapthis`` (like
``extrathink``, not a slash command). The keyword activates MO's native
``map_project`` tool inside the normal turn instead of routing around Gateway. The
tool builds the file inventory, partitions it into balanced non-overlapping slices,
dispatches coordinated no-tools mappers over bounded code skeletons, existence-checks
their cited paths, and synthesizes the result into a map. This is orientation, not a
full-file source audit. Neutral product code — no owner codenames.
"""
from .pipeline import run_project_map
from .partition import partition_files, project_file_inventory, slice_scope_label, subsystem_key
from .synthesize import build_overview, resolve_docs_dir, synthesize_map, write_map_doc
from .verify import verify_slice_map
from .workers import MAPPER_SURFACE, dispatch_mappers, slice_objective

__all__ = [
    "run_project_map",
    "partition_files",
    "project_file_inventory",
    "slice_scope_label",
    "subsystem_key",
    "dispatch_mappers",
    "slice_objective",
    "MAPPER_SURFACE",
    "verify_slice_map",
    "synthesize_map",
    "build_overview",
    "write_map_doc",
    "resolve_docs_dir",
]
