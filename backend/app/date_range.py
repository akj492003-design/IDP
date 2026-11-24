"""Shared timezone-aware date-range parsing for list endpoints."""

from datetime import datetime, timezone


def _parse_aware_datetime(value):
    """Parse an ISO-8601 timestamp that includes a timezone offset."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Date boundaries must be ISO-8601 timestamps.")

    raw = value.strip()

    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            "Use valid ISO-8601 date-time values."
        ) from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(
            "Date boundaries must include a timezone offset."
        )

    return parsed.astimezone(timezone.utc)


def parse_date_range_args(args):
    """
    Return an exclusive MongoDB date range and an optional validation error.

    Query parameters:
      date_from: inclusive ISO-8601 timestamp
      date_to: exclusive ISO-8601 timestamp

    Both parameters must be supplied together.
    """
    raw_from = args.get("date_from")
    raw_to = args.get("date_to")

    if not raw_from and not raw_to:
        return None, None

    if not raw_from or not raw_to:
        return None, (
            "Both date_from and date_to are required for date filtering."
        )

    try:
        start = _parse_aware_datetime(raw_from)
        end = _parse_aware_datetime(raw_to)
    except ValueError as exc:
        return None, str(exc)

    if end <= start:
        return None, (
            "date_to must be later than date_from."
        )

    return {
        "$gte": start,
        "$lt": end,
    }, None


def processed_or_created_date_clause(date_range):
    """
    Classification date preference:
    use processed_at when available; otherwise use created_at.
    """
    if not date_range:
        return None

    return {
        "$or": [
            {
                "processed_at": date_range,
            },
            {
                "$and": [
                    {
                        "$or": [
                            {"processed_at": {"$exists": False}},
                            {"processed_at": None},
                        ],
                    },
                    {
                        "created_at": date_range,
                    },
                ],
            },
        ],
    }
