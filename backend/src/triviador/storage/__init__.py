"""S3 adapters. Implementations only — the ports are in `services/storage.py`.

This package sits where `maps/` sits: a concrete adapter with no port of
its own to hide behind, one layer below `api/` and beside `db/`.
`tests/test_layering.py` holds it to naming neither `api` nor `db`.
"""
