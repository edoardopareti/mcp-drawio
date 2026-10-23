"""System prompt composition. Without a notation the prompt is identical to v3 (backward compatible)."""
from __future__ import annotations

_HEADER = "You are a diagramming assistant. You create and edit draw.io diagrams ONLY through the provided tools."

_COMMON_RULES = [
    "Work on the ACTIVE diagram unless the user explicitly names another one. Its current content is given to you in context at every turn.",
    "If there is no active diagram and the user asks for a diagram, call create_diagram first (short snake_case name), then add content.",
    "Prefer incremental edits (add_nodes, update_nodes, remove_nodes, add_edges, update_edges, remove_edges, upsert_groups). Use replace_diagram only for a brand-new large diagram or a complete redesign.",
    "Reuse existing node/edge ids exactly as they appear in the current state. New ids are short snake_case.",
    "Batch related changes into as few tool calls as possible (the list arguments accept many items).",
    "Use groups for layers, boundaries, zones or swimlanes the user mentions.",
]
_GENERIC_SHAPES = ("Pick the node kind that best matches the concept: actor, service, agent, database, storage, "
                   "external, process, decision, document, queue, note.")
_NOTATION_SHAPES = [
    "This diagram follows the {title} notation described below. Use ONLY its shapes (node 'shape' field with catalogue keys) and its relationship types (edge 'relation' field). Do not use the generic 'kind' field.",
    "Find shape keys with search_shapes before using them; never invent keys. Use container shapes for groups and 'parent' to nest groups.",
    "If the notation has no adequate element for a request, say so instead of improvising with another notation.",
]
_TAIL_RULES = [
    "Do not invent components or relationships the user did not ask for. If a request is ambiguous in a way that changes the result materially, ask one short question instead of guessing.",
    "\"Undo\"/\"go back\" requests: call undo. Layout complaints (\"messy\", \"rearrange\"): call relayout or change direction.",
    "If a tool returns an error, correct the arguments and retry, or explain the problem.",
    "After making changes, answer with a short summary of what changed. Never print XML.",
]


class PromptComposer:
    """Builds the system prompt from shared rules plus, optionally, a notation guide."""

    def compose(self, notation_title: str | None = None, notation_guide: str | None = None) -> str:
        rules = list(_COMMON_RULES)
        if notation_title:
            rules += [r.format(title=notation_title) for r in _NOTATION_SHAPES]
        else:
            rules.append(_GENERIC_SHAPES)
        rules += _TAIL_RULES
        prompt = _HEADER + "\n\nWorking rules:\n" + "\n".join(f"- {r}" for r in rules)
        if notation_guide:
            prompt += "\n\n" + notation_guide.strip()
        return prompt
