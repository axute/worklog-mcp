"""worklog-import – bring earlier AI sessions into the work log.

Runs where the data lives (usually the workstation), standard library only,
Python 3.9+. Steps: extract sessions -> map to projects -> filter secrets ->
summarize (claude -p or a simple heuristic) -> preview -> upload in bulk.
Only finished summaries are uploaded, never raw transcripts.
"""

__version__ = "1.1.0"
