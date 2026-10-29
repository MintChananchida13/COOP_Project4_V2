"""Verification inputs required by each detection strategy."""


def verification_anchor_types(strategy: str) -> frozenset[str]:
    if strategy == "layout_only":
        return frozenset()
    if strategy == "text_only":
        return frozenset({"text"})
    if strategy == "image_only":
        return frozenset({"image"})
    # standard/strict verify both types, including required anchors even when
    # their scoring weight is zero. Unknown values retain the standard policy.
    return frozenset({"text", "image"})
