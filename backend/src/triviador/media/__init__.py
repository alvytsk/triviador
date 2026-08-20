"""Everything that turns an uploaded file into a servable asset.

Pure functions plus one small class that owns a semaphore. No session, no
client, no FastAPI — `tests/test_layering.py` enforces it, and it is what
lets `tests/media/` run with both containers stopped.
"""
