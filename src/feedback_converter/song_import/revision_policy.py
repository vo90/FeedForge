"""Validate captured revision-selection evidence, without network access.

Keep in step with Electron's revision-policy.cjs. Approval is a review status,
not a substitute for a receipt authorizing an unreviewed revision.
"""
from datetime import datetime
import re

APPROVED = {"approved", "fair", "average", "good", "excellent"}
POLICY = "reviewed-first-with-unreviewed-fallback"


def _numeric(value):
    return str(value) if re.fullmatch(r"[1-9][0-9]{0,11}", str(value)) else None


def _date(value):
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def valid_revision_selection(metadata):
    if not isinstance(metadata, dict):
        return False
    song_id, revision_id = _numeric(metadata.get("songId", metadata.get("id"))), _numeric(metadata.get("revisionId"))
    if not song_id or not revision_id:
        return False
    evidence = metadata.get("revisionEvidence")
    if evidence is None:
        return metadata.get("approval") == "approved"  # Existing saved jobs.
    if not isinstance(evidence, dict):
        return False
    if evidence.get("songId") != song_id or evidence.get("revisionId") != revision_id:
        return False
    basis = evidence.get("basis")
    if evidence.get("version") == 1:
        return (metadata.get("approval") == "approved" and evidence.get("policy") == "reviewed-or-current-moderator"
                and basis in ("reviewed", "moderator_default")
                and all(evidence.get(key) is False for key in ("isDeleted", "isBlocked", "isOnModeration")))
    if (evidence.get("version") != 2 or evidence.get("policy") != POLICY
            or not _numeric(evidence.get("authorId")) or not _numeric(evidence.get("defaultRevisionId"))
            or evidence.get("selection") not in ("automatic", "explicit")
            or not all(_date(evidence.get(key)) for key in ("createdAt", "checkedAt"))
            or not all(isinstance(evidence.get(key), bool) for key in ("isDeleted", "isBlocked", "isOnModeration"))
            or evidence["isDeleted"]):
        return False
    mode, review = evidence.get("moderationType"), evidence.get("reviewConclusion")
    if mode not in (None, "no", "pre", "post") or review not in (None, *APPROVED):
        return False
    positive = review in APPROVED
    if evidence["isBlocked"]:
        status = "alternative"
        if not positive or evidence["isOnModeration"] or evidence["selection"] != "explicit":
            return False
    elif evidence["isOnModeration"]:
        if positive or mode not in ("pre", "post"):
            return False
        status = "awaiting_review" if mode == "post" else "awaiting_moderation"
    else:
        status = "reviewed" if positive else "unreviewed"
    if basis == "moderator_default":
        if (status != "unreviewed" or mode != "no" or evidence["defaultRevisionId"] != revision_id
                or evidence.get("moderatorVerified") is not True):
            return False
    elif basis != status:
        return False
    approval = "approved" if basis in ("reviewed", "moderator_default") else "alternative" if basis == "alternative" else "unreviewed"
    return metadata.get("approval") == approval
