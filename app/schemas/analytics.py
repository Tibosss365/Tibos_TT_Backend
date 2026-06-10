from pydantic import BaseModel


class AnalyticsOut(BaseModel):
    status_distribution: dict[str, int]
    category_distribution: dict[str, int]
    priority_distribution: dict[str, int]
    resolution_rate: float
    sla_compliance: dict[str, float]
    tickets_over_time: list[dict]
    avg_resolution_hours: float
    # Extended metrics
    source_distribution: dict[str, int] = {}
    csat_avg: float | None = None
    csat_count: int = 0
    csat_distribution: dict[str, int] = {}
    avg_first_response_hours: float | None = None
    reopen_rate: float = 0.0
