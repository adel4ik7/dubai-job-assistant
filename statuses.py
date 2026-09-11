"""Stable database values, independent of interface language."""
STATUSES = ("saved", "applied", "hr_screening", "interview", "test_task",
            "final_interview", "offer", "rejected", "withdrawn")
LEGACY_STATUSES = ("Saved", "Applied", "HR screening", "Interview", "Test task",
                   "Final interview", "Offer", "Rejected", "Withdrawn")


def normalize_status(value: str) -> str:
    from locales import text
    for code, legacy in zip(STATUSES, LEGACY_STATUSES):
        if value.casefold() in {code.casefold(), legacy.casefold(), text("ru", "status_" + code).casefold()}:
            return code
    return value
