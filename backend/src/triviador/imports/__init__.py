"""The two-phase question import (§10.3, §9.3).

`parse.py` is pure and knows nothing about storage; `retire.py` (Task 9)
owns the expiry state machine. The orchestration — read, validate, stage,
confirm — lives in `api/http/admin/imports.py`, where the transaction
boundary and the request are.
"""
