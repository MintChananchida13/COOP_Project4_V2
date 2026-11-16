# Backend inference adapters

`inference_adapters.py` is the shared entry point for parsing model responses.
It performs no HTTP calls, model inference, ROI filtering, signature generation,
or business scoring.

- `adapt_layout(output, expected_count=...)`: raw Layout predictions, preserving order.
- `adapt_text_detection` / `adapt_text_recognition`: single or batch normalization;
  batch results must match `expected_count` without padding or truncation.
- `adapt_siglip(output, categories)`: validates the existing SigLIP leaf contract,
  prompt order and finite logits. Scoring remains in `siglip_service_contract.py`.
- `adapt_table` / `adapt_table_structure`: unwrap table results without modifying content.

Existing `normalize_single` / `normalize_batch` names remain available centrally.
`text_response_contract.py` re-exports them for compatibility. Current model-side
contracts and endpoint routing are unchanged by this refactor.
