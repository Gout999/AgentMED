"""Public API v4 wire and authorization contracts.

Importing this package has no database, network, or credential-resolution side
effects.  Runtime routes and services are added in later delivery slices.
"""

from .auth_contract import AcceptedPrincipalContext, PublicRequestHeaders
from .errors import PublicErrorEnvelope, map_public_error
from .models import (
    CaseResponse,
    CaseTimelineResponse,
    EvidenceResponse,
    ServerCapabilitiesResponse,
    SignalSubmission,
    SignalSubmissionResponse,
)

__all__ = [
    "AcceptedPrincipalContext",
    "CaseResponse",
    "CaseTimelineResponse",
    "EvidenceResponse",
    "PublicErrorEnvelope",
    "PublicRequestHeaders",
    "ServerCapabilitiesResponse",
    "SignalSubmission",
    "SignalSubmissionResponse",
    "map_public_error",
]
