"""Mermaid.js diagram export from GraphData.

Converts a parsed structure graph into Mermaid flowchart syntax,
renderable in GitHub, VS Code, MO's TUI, or any Mermaid viewer.
"""

from __future__ import annotations

from .graph import GraphData, NodeData


def _safe_mermaid_text(text: str) -> str:
    """Escape special Mermaid characters in node labels."""
    return text.replace('"', "#quot;")


def _node_shape_label(node: NodeData) -> str:
    """Build a Mermaid node label from NodeData rows.

    Uses Mermaid's `["label"]` rectangular node syntax with `<br/>` line breaks.
    """
    lines: list[str] = []
    for row in node.text:
        key_str = row.key or ""
        val = row.value
        if row.type == "object":
            val_str = f"{{{row.children_count or 0} keys}}"
        elif row.type == "array":
            val_str = f"[{row.children_count or 0} items]"
        elif val is None:
            val_str = "null"
        else:
            val_str = str(val)
        if key_str:
            lines.append(f"<b>{_safe_mermaid_text(key_str)}</b>: {_safe_mermaid_text(val_str)}")
        else:
            lines.append(_safe_mermaid_text(val_str))
    return "<br/>".join(lines)


def to_mermaid(graph: GraphData, *, title: str = "", direction: str = "TD") -> str:
    """Render a GraphData as a Mermaid flowchart.

    Args:
        graph: Parsed JSON graph.
        title: Optional diagram title.
        direction: Flow direction — 'TD' (top-down), 'LR' (left-right),
                   'RL', 'BT', etc.

    Returns:
        Valid Mermaid.js flowchart syntax string.
    """
    lines: list[str] = ["```mermaid", f"flowchart {direction}"]

    if title:
        safe_title = _safe_mermaid_text(title)
        lines.append(f"  title[{safe_title}]")

    # --- nodes ---
    for node in graph.nodes:
        label = _node_shape_label(node)
        lines.append(f"  {node.id}[\"{label}\"]")

    # --- edges ---
    for edge in graph.edges:
        edge_label = _safe_mermaid_text(edge.text) if edge.text else ""
        if edge_label:
            lines.append(f"  {edge.from_id} -->|{edge_label}| {edge.to_id}")
        else:
            lines.append(f"  {edge.from_id} --> {edge.to_id}")

    lines.append("```")
    return "\n".join(lines)


def to_ascii_tree(graph: GraphData) -> str:
    """Render a GraphData as an indented ASCII tree.

    Useful for terminal display when Mermaid rendering is not available.
    """
    if not graph.nodes:
        return "(empty graph)"

    # build adjacency: parent_id → list of children
    children_of: dict[str, list[tuple[str, str | None]]] = {}
    for edge in graph.edges:
        children_of.setdefault(edge.from_id, []).append((edge.to_id, edge.text))

    node_map: dict[str, NodeData] = {n.id: n for n in graph.nodes}

    def _render_node(nid: str, indent: str, is_last: bool, edge_label: str | None) -> list[str]:
        node = node_map.get(nid)
        if not node:
            return [f"{indent}{'└── ' if is_last else '├── '}{nid} (missing)"]

        # first line: connector + value
        connector = "└── " if is_last else "├── "
        first_row = node.text[0] if node.text else None
        if first_row:
            key_str = f"{first_row.key}: " if first_row.key else ""
            val = first_row.value
            if first_row.type == "object":
                val_str = f"{{{first_row.children_count or 0} keys}}"
            elif first_row.type == "array":
                val_str = f"[{first_row.children_count or 0} items]"
            elif val is None:
                val_str = "null"
            else:
                val_str = str(val)
            label_line = f"{key_str}{val_str}"
        else:
            label_line = "(empty)"

        if edge_label:
            header = f"{label_line}  ← {edge_label}"
        else:
            header = label_line

        result = [f"{indent}{connector}{header}"]

        # subsequent rows (for objects with multiple keys shown inline)
        for row in node.text[1:]:
            key_str = f"{row.key}: " if row.key else ""
            val = row.value
            if row.type == "object":
                val_str = f"{{{row.children_count or 0} keys}}"
            elif row.type == "array":
                val_str = f"[{row.children_count or 0} items]"
            elif val is None:
                val_str = "null"
            else:
                val_str = str(val)
            continuation = "    " if is_last else "│   "
            result.append(f"{indent}{continuation}  {key_str}{val_str}")

        # children
        child_entries = children_of.get(nid, [])
        for i, (child_id, child_label) in enumerate(child_entries):
            is_last_child = i == len(child_entries) - 1
            child_indent = indent + ("    " if is_last else "│   ")
            result.extend(_render_node(child_id, child_indent, is_last_child, child_label))

        return result

    # root is the first node (id "1")
    root_id = "1"
    output = _render_node(root_id, "", True, None)
    return "\n".join(output)
