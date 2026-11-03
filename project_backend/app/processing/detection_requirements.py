"""Both supported detection modes verify Text and Image anchors."""


def verification_anchor_types(strategy: str) -> frozenset[str]:
    return frozenset({"text", "image"})
