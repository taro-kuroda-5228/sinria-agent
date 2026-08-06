"""Non-blocking correction records for Sinria.

Corrections improve execution but never authorize, deny, pause, or otherwise
control tool dispatch.  Runtime safety and approvals live in independent
boundaries.
"""

from .records import CorrectionRecord
from .retrieval import CorrectionAdvice, retrieve_advice

__all__ = ["CorrectionAdvice", "CorrectionRecord", "retrieve_advice"]
