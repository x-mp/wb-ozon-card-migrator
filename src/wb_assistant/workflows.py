from __future__ import annotations

from enum import Enum


class JobState(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    POLLING = "polling"
    READY = "ready"
    DOWNLOADING = "downloading"
    VERIFIED = "verified"
    NORMALIZED = "normalized"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


ALLOWED_JOB_TRANSITIONS = {
    JobState.DRAFT: {JobState.SUBMITTED, JobState.CANCELLED},
    JobState.SUBMITTED: {JobState.POLLING, JobState.READY, JobState.FAILED},
    JobState.POLLING: {
        JobState.POLLING,
        JobState.READY,
        JobState.FAILED,
        JobState.EXPIRED,
        JobState.CANCELLED,
    },
    JobState.READY: {JobState.DOWNLOADING, JobState.EXPIRED},
    JobState.DOWNLOADING: {JobState.VERIFIED, JobState.READY, JobState.FAILED},
    JobState.VERIFIED: {JobState.NORMALIZED, JobState.DONE},
    JobState.NORMALIZED: {JobState.DONE, JobState.FAILED},
    JobState.DONE: set(),
    JobState.FAILED: {JobState.SUBMITTED, JobState.CANCELLED},
    JobState.CANCELLED: set(),
    JobState.EXPIRED: {JobState.SUBMITTED, JobState.CANCELLED},
}


def assert_job_transition(current: JobState, target: JobState) -> None:
    if target not in ALLOWED_JOB_TRANSITIONS[current]:
        raise ValueError(f"Invalid job transition: {current.value} -> {target.value}")

