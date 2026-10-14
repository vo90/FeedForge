"""Shared bounds for one source recording and its permitted silent padding."""
MIN_DURATION = 2.0
MAX_DURATION = 60 * 60.0
MAX_PREPARATION_SECONDS = 2.0
MAX_ENDING_SECONDS = 2.0
MAX_ANALYSIS_DURATION = MAX_DURATION + MAX_PREPARATION_SECONDS + MAX_ENDING_SECONDS
DURATION_MESSAGE = "Choose a recording between 2 seconds and 60 minutes."
