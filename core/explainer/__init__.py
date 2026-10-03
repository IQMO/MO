"""First-party narrated explainer-video production for MO Agent.

The package owns a small, deterministic scene format and its local renderer.
Provider reasoning stays in MO's normal turn; this package validates and renders
the resulting evidence-backed project without importing a second agent stack.
"""

from .model import ExplainerProject, ProjectValidationError, load_project, validate_project
from .storage import create_project, project_directory

__all__ = [
    "ExplainerProject",
    "ProjectValidationError",
    "create_project",
    "load_project",
    "project_directory",
    "validate_project",
]
