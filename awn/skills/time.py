"""Time skill — reports current date and time."""

from __future__ import annotations

import datetime

from .base import BaseSkill


class TimeSkill(BaseSkill):
    """Report the current date and/or time."""

    names = ("time", "date", "now")
    description = "Show the current date and time."
    usage = "time [date|time|now]"

    def run(self, args: str) -> str:
        now = datetime.datetime.now()
        sub = args.strip().lower()
        if sub == "date":
            return now.strftime("Today is %A, %B %d, %Y.")
        if sub == "time":
            return now.strftime("The current time is %H:%M:%S.")
        return now.strftime("It is %H:%M:%S on %A, %B %d, %Y.")
