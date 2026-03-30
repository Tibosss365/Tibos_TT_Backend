from pydantic import BaseModel


class AnalyticsOut(BaseModel):
    status_distribution: dict[str, int]
    category_distribution: dict[str, int]
    priority_distribution: dict[str, int]
    resolution_rate: float
    sla_compliance: dict[str, float]
    tickets_over_time: list[dict]
    avg_resolution_hours: float
