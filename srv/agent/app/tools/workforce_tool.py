"""
Workforce-specific tools for the busibox-workforce HR assistant agent.

These tools encapsulate workforce domain knowledge (employee field names,
analytics formulas, check-in statuses) so the LLM receives pre-structured
data instead of having to construct raw data-api queries.

All tools call the existing data-api via BusiboxClient.request().
"""

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Tool, RunContext

from app.agents.core import BusiboxDeps


MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

PAGE_SIZE = 1000


# =============================================================================
# Helpers
# =============================================================================

def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _month_key(dt: datetime) -> str:
    return f"{dt.year}-{dt.month:02d}"


def _month_label(key: str) -> str:
    year, month = key.split("-")
    return f"{MONTH_NAMES[int(month) - 1]} {year}"


def _trailing_months(count: int) -> List[str]:
    now = datetime.now(timezone.utc)
    months: List[str] = []
    y, m = now.year, now.month
    for _ in range(count):
        months.append(f"{y}-{m:02d}")
        m -= 1
        if m < 1:
            m = 12
            y -= 1
    months.reverse()
    return months


async def _fetch_all_records(
    client: Any,
    document_id: str,
    where: Optional[Dict[str, Any]] = None,
    select: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Paginate through all records in a data document."""
    all_records: List[Dict[str, Any]] = []
    offset = 0
    while True:
        body: Dict[str, Any] = {"limit": PAGE_SIZE, "offset": offset}
        if where:
            body["where"] = where
        if select:
            body["select"] = select
        resp = await client.request(
            method="POST",
            path=f"/data/{document_id}/query",
            json=body,
            timeout=60,
        )
        records = resp.get("records", [])
        all_records.extend(records)
        total = resp.get("total", 0)
        if len(all_records) >= total or len(records) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return all_records


def _build_employee_filters(
    departments: Optional[List[str]] = None,
    divisions: Optional[List[str]] = None,
    statuses: Optional[List[str]] = None,
    employee_types: Optional[List[str]] = None,
    hire_date_from: Optional[str] = None,
    hire_date_to: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build a data-api WHERE clause from workforce filter parameters."""
    conditions: List[Dict[str, Any]] = []

    if departments:
        conditions.append({"field": "department", "op": "in", "value": departments})
    if divisions:
        conditions.append({"field": "division", "op": "in", "value": divisions})
    if statuses:
        conditions.append({"field": "employeeStatus", "op": "in", "value": statuses})
    if employee_types:
        conditions.append({"field": "employeeType", "op": "in", "value": employee_types})
    if hire_date_from:
        conditions.append({"field": "hireDate", "op": "gte", "value": hire_date_from})
    if hire_date_to:
        conditions.append({"field": "hireDate", "op": "lte", "value": hire_date_to})

    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"and": conditions}


# =============================================================================
# Output Schemas
# =============================================================================

class WorkforceSearchEmployeesOutput(BaseModel):
    success: bool = Field(description="Whether the query succeeded")
    employees: List[Dict[str, Any]] = Field(default_factory=list, description="Matching employee records")
    total: int = Field(default=0, description="Total matching employees (before limit)")
    limit: int = Field(description="Limit used")
    offset: int = Field(description="Offset used")
    error: Optional[str] = Field(default=None)


class WorkforceGetStatsOutput(BaseModel):
    success: bool = Field(description="Whether the stats computation succeeded")
    total_headcount: int = Field(default=0, description="Active employees")
    total_employees: int = Field(default=0, description="All employees (any status)")
    turnover_rate: float = Field(default=0.0, description="Turnover rate as percentage for the period")
    net_growth: int = Field(default=0, description="Net hires minus departures for the period")
    average_tenure_years: float = Field(default=0.0, description="Average tenure of active employees in years")
    hires_by_month: List[Dict[str, Any]] = Field(default_factory=list, description="Monthly hire counts")
    departures_by_month: List[Dict[str, Any]] = Field(default_factory=list, description="Monthly departure counts")
    employees_by_department: List[Dict[str, Any]] = Field(default_factory=list, description="Active headcount per department")
    employees_by_division: List[Dict[str, Any]] = Field(default_factory=list, description="Active headcount per division")
    status_breakdown: Dict[str, int] = Field(default_factory=dict, description="Count per employee status")
    type_breakdown: Dict[str, int] = Field(default_factory=dict, description="Count per employee type")
    error: Optional[str] = Field(default=None)


class WorkforceGetEmployeeOutput(BaseModel):
    success: bool = Field(description="Whether the lookup succeeded")
    employee: Optional[Dict[str, Any]] = Field(default=None, description="Full employee record")
    checkins: List[Dict[str, Any]] = Field(default_factory=list, description="Related check-ins")
    checkin_count: int = Field(default=0, description="Total check-ins for this employee")
    error: Optional[str] = Field(default=None)


class WorkforceSearchCheckinsOutput(BaseModel):
    success: bool = Field(description="Whether the query succeeded")
    checkins: List[Dict[str, Any]] = Field(default_factory=list, description="Matching check-in records")
    total: int = Field(default=0, description="Total matching check-ins")
    error: Optional[str] = Field(default=None)


class WorkforceGetFacetsOutput(BaseModel):
    success: bool = Field(description="Whether facet extraction succeeded")
    departments: List[str] = Field(default_factory=list)
    divisions: List[str] = Field(default_factory=list)
    employee_types: List[str] = Field(default_factory=list)
    statuses: List[str] = Field(default_factory=list)
    pay_groups: List[str] = Field(default_factory=list)
    years: List[int] = Field(default_factory=list, description="Years with hire or termination activity")
    total_employees: int = Field(default=0)
    error: Optional[str] = Field(default=None)


# =============================================================================
# Tool Implementations
# =============================================================================

async def workforce_search_employees(
    ctx: RunContext[BusiboxDeps],
    document_id: str,
    departments: Optional[List[str]] = None,
    divisions: Optional[List[str]] = None,
    statuses: Optional[List[str]] = None,
    employee_types: Optional[List[str]] = None,
    search_term: Optional[str] = None,
    hire_date_from: Optional[str] = None,
    hire_date_to: Optional[str] = None,
    limit: int = 25,
    offset: int = 0,
) -> WorkforceSearchEmployeesOutput:
    """
    Search and filter workforce employees using HR-friendly parameters.

    Use this instead of raw query_data when looking for employees by department,
    division, status, type, name, title, or hire date range. The search_term
    parameter matches against firstName, lastName, and title fields.

    Args:
        ctx: RunContext with authenticated client
        document_id: Employee data document UUID (from schemaDocumentId in metadata)
        departments: Filter by department names (e.g., ["Marine", "Equipment"])
        divisions: Filter by division names
        statuses: Filter by status (e.g., ["Active", "Terminated"])
        employee_types: Filter by type (e.g., ["Full-Time", "Part-Time"])
        search_term: Free-text search across name and title fields
        hire_date_from: Hire date lower bound (ISO format, e.g. "2020-01-01")
        hire_date_to: Hire date upper bound (ISO format)
        limit: Max records to return (default 25, max 100)
        offset: Pagination offset (default 0)
    """
    try:
        where = _build_employee_filters(
            departments=departments,
            divisions=divisions,
            statuses=statuses,
            employee_types=employee_types,
            hire_date_from=hire_date_from,
            hire_date_to=hire_date_to,
        )

        if search_term:
            term = search_term.strip()
            name_conditions: List[Dict[str, Any]] = [
                {"field": "firstName", "op": "contains", "value": term},
                {"field": "lastName", "op": "contains", "value": term},
                {"field": "title", "op": "contains", "value": term},
            ]
            name_clause: Dict[str, Any] = {"or": name_conditions}

            if where:
                where = {"and": [where, name_clause]}
            else:
                where = name_clause

        limit = min(max(limit, 1), 100)

        body: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "orderBy": [{"field": "lastName", "direction": "asc"}],
        }
        if where:
            body["where"] = where

        resp = await ctx.deps.busibox_client.request(
            method="POST",
            path=f"/data/{document_id}/query",
            json=body,
        )

        return WorkforceSearchEmployeesOutput(
            success=True,
            employees=resp.get("records", []),
            total=resp.get("total", 0),
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        return WorkforceSearchEmployeesOutput(
            success=False, total=0, limit=limit, offset=offset, error=str(e),
        )


async def workforce_get_stats(
    ctx: RunContext[BusiboxDeps],
    document_id: str,
    months_back: int = 12,
    departments: Optional[List[str]] = None,
    divisions: Optional[List[str]] = None,
    statuses: Optional[List[str]] = None,
    employee_types: Optional[List[str]] = None,
) -> WorkforceGetStatsOutput:
    """
    Compute workforce analytics: headcount, turnover rate, hires/departures by
    month, department/division breakdowns, and average tenure.

    This mirrors the dashboard stats shown in the workforce app. Use this tool
    for any question about workforce metrics, trends, or summaries rather than
    fetching raw records and computing manually.

    Args:
        ctx: RunContext with authenticated client
        document_id: Employee data document UUID (from schemaDocumentId in metadata)
        months_back: How many trailing months to include (default 12)
        departments: Optional department filter
        divisions: Optional division filter
        statuses: Optional status filter (default: all statuses included in computation)
        employee_types: Optional employee type filter
    """
    try:
        where = _build_employee_filters(
            departments=departments,
            divisions=divisions,
            statuses=statuses,
            employee_types=employee_types,
        )
        all_employees = await _fetch_all_records(
            ctx.deps.busibox_client, document_id, where=where,
        )

        active = [e for e in all_employees if e.get("employeeStatus") == "Active"]
        total_headcount = len(active)
        trailing = _trailing_months(months_back)

        hires_counter: Counter[str] = Counter()
        departures_counter: Counter[str] = Counter()
        for emp in all_employees:
            hd = _parse_date(emp.get("hireDate"))
            if hd:
                hires_counter[_month_key(hd)] += 1
            td = _parse_date(emp.get("terminationDate"))
            if td:
                departures_counter[_month_key(td)] += 1

        hires_by_month = [
            {"month": k, "label": _month_label(k), "count": hires_counter.get(k, 0)}
            for k in trailing
        ]
        departures_by_month = [
            {"month": k, "label": _month_label(k), "count": departures_counter.get(k, 0)}
            for k in trailing
        ]

        total_departures = sum(m["count"] for m in departures_by_month)
        total_hires = sum(m["count"] for m in hires_by_month)
        avg_headcount = total_headcount if total_headcount > 0 else 1
        turnover_rate = round((total_departures / avg_headcount) * 100, 1)
        net_growth = total_hires - total_departures

        dept_counter: Counter[str] = Counter()
        div_counter: Counter[str] = Counter()
        status_counter: Counter[str] = Counter()
        type_counter: Counter[str] = Counter()
        now = datetime.now(timezone.utc)
        total_tenure_days = 0.0
        tenure_count = 0

        for emp in all_employees:
            status_counter[emp.get("employeeStatus", "Unknown")] += 1
            type_counter[emp.get("employeeType", "Unknown")] += 1

        for emp in active:
            dept_counter[emp.get("department") or "Other"] += 1
            div_counter[emp.get("division") or "Other"] += 1
            hd = _parse_date(emp.get("hireDate"))
            if hd:
                total_tenure_days += (now - hd).total_seconds() / 86400
                tenure_count += 1

        avg_tenure = round(total_tenure_days / tenure_count / 365.25, 1) if tenure_count else 0.0

        by_dept = sorted(
            [{"name": k, "count": v} for k, v in dept_counter.items()],
            key=lambda x: x["count"], reverse=True,
        )
        by_div = sorted(
            [{"name": k, "count": v} for k, v in div_counter.items()],
            key=lambda x: x["count"], reverse=True,
        )

        return WorkforceGetStatsOutput(
            success=True,
            total_headcount=total_headcount,
            total_employees=len(all_employees),
            turnover_rate=turnover_rate,
            net_growth=net_growth,
            average_tenure_years=avg_tenure,
            hires_by_month=hires_by_month,
            departures_by_month=departures_by_month,
            employees_by_department=by_dept,
            employees_by_division=by_div,
            status_breakdown=dict(status_counter),
            type_breakdown=dict(type_counter),
        )
    except Exception as e:
        return WorkforceGetStatsOutput(success=False, error=str(e))


async def workforce_get_employee(
    ctx: RunContext[BusiboxDeps],
    document_id: str,
    employee_id: str,
    checkins_document_id: Optional[str] = None,
) -> WorkforceGetEmployeeOutput:
    """
    Get a single employee's full profile and their related check-ins.

    Returns the complete employee record plus up to 20 recent check-ins
    (with summaries and transcription excerpts). Use this when the user
    is viewing or asking about a specific employee.

    Args:
        ctx: RunContext with authenticated client
        document_id: Employee data document UUID (from schemaDocumentId in metadata)
        employee_id: The employee's record id
        checkins_document_id: Check-ins data document UUID (from checkinsDocumentId in metadata). If omitted, only the employee record is returned.
    """
    try:
        emp_resp = await ctx.deps.busibox_client.request(
            method="POST",
            path=f"/data/{document_id}/query",
            json={
                "where": {"field": "id", "op": "eq", "value": employee_id},
                "limit": 1,
            },
        )
        records = emp_resp.get("records", [])
        if not records:
            return WorkforceGetEmployeeOutput(
                success=False, error=f"Employee not found: {employee_id}",
            )

        employee = records[0]
        checkins: List[Dict[str, Any]] = []
        checkin_count = 0

        if checkins_document_id:
            ci_resp = await ctx.deps.busibox_client.request(
                method="POST",
                path=f"/data/{checkins_document_id}/query",
                json={
                    "where": {"field": "employeeId", "op": "eq", "value": employee_id},
                    "orderBy": [{"field": "scheduledDate", "direction": "desc"}],
                    "limit": 20,
                },
            )
            checkins = ci_resp.get("records", [])
            checkin_count = ci_resp.get("total", len(checkins))

        return WorkforceGetEmployeeOutput(
            success=True,
            employee=employee,
            checkins=checkins,
            checkin_count=checkin_count,
        )
    except Exception as e:
        return WorkforceGetEmployeeOutput(success=False, error=str(e))


async def workforce_search_checkins(
    ctx: RunContext[BusiboxDeps],
    document_id: str,
    employee_id: Optional[str] = None,
    checkin_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
) -> WorkforceSearchCheckinsOutput:
    """
    Search check-in and exit interview records with workforce-aware filters.

    Args:
        ctx: RunContext with authenticated client
        document_id: Check-ins data document UUID (from checkinsDocumentId in metadata)
        employee_id: Filter by employee record id
        checkin_type: Filter by type: "checkin" or "exit-interview"
        status: Filter by status: "scheduled", "invited", "survey-complete", "interview-complete", "summarized", "shared"
        limit: Max records to return (default 20, max 100)
    """
    try:
        conditions: List[Dict[str, Any]] = []
        if employee_id:
            conditions.append({"field": "employeeId", "op": "eq", "value": employee_id})
        if checkin_type:
            conditions.append({"field": "type", "op": "eq", "value": checkin_type})
        if status:
            conditions.append({"field": "status", "op": "eq", "value": status})

        where: Optional[Dict[str, Any]] = None
        if len(conditions) == 1:
            where = conditions[0]
        elif len(conditions) > 1:
            where = {"and": conditions}

        limit = min(max(limit, 1), 100)

        body: Dict[str, Any] = {
            "limit": limit,
            "orderBy": [{"field": "scheduledDate", "direction": "desc"}],
        }
        if where:
            body["where"] = where

        resp = await ctx.deps.busibox_client.request(
            method="POST",
            path=f"/data/{document_id}/query",
            json=body,
        )

        return WorkforceSearchCheckinsOutput(
            success=True,
            checkins=resp.get("records", []),
            total=resp.get("total", 0),
        )
    except Exception as e:
        return WorkforceSearchCheckinsOutput(success=False, error=str(e))


async def workforce_get_facets(
    ctx: RunContext[BusiboxDeps],
    document_id: str,
) -> WorkforceGetFacetsOutput:
    """
    Get available filter values for the workforce dataset.

    Returns lists of unique departments, divisions, employee types, statuses,
    pay groups, and years with hire/termination activity. Use this before
    constructing queries so you know what valid filter values exist.

    Args:
        ctx: RunContext with authenticated client
        document_id: Employee data document UUID (from schemaDocumentId in metadata)
    """
    try:
        all_employees = await _fetch_all_records(
            ctx.deps.busibox_client,
            document_id,
            select=[
                "department", "division", "employeeType",
                "employeeStatus", "payrollPayGroup", "hireDate", "terminationDate",
            ],
        )

        departments: set[str] = set()
        divisions: set[str] = set()
        employee_types: set[str] = set()
        statuses: set[str] = set()
        pay_groups: set[str] = set()
        years: set[int] = set()

        for emp in all_employees:
            if emp.get("department"):
                departments.add(emp["department"])
            if emp.get("division"):
                divisions.add(emp["division"])
            if emp.get("employeeType"):
                employee_types.add(emp["employeeType"])
            if emp.get("employeeStatus"):
                statuses.add(emp["employeeStatus"])
            if emp.get("payrollPayGroup"):
                pay_groups.add(emp["payrollPayGroup"])
            hd = _parse_date(emp.get("hireDate"))
            if hd:
                years.add(hd.year)
            td = _parse_date(emp.get("terminationDate"))
            if td:
                years.add(td.year)

        return WorkforceGetFacetsOutput(
            success=True,
            departments=sorted(departments),
            divisions=sorted(divisions),
            employee_types=sorted(employee_types),
            statuses=sorted(statuses),
            pay_groups=sorted(pay_groups),
            years=sorted(years),
            total_employees=len(all_employees),
        )
    except Exception as e:
        return WorkforceGetFacetsOutput(success=False, error=str(e))


# =============================================================================
# PydanticAI Tool Objects
# =============================================================================

workforce_search_employees_tool = Tool(
    workforce_search_employees,
    takes_ctx=True,
    name="workforce_search_employees",
    description="""Search and filter workforce employees using HR-friendly parameters.

Supports filtering by department, division, status, employee type, name/title
search, and hire date range. Returns paginated results sorted by last name.

Use this instead of raw query_data when looking for employees. The document_id
should come from schemaDocumentId in your Application Context metadata.""",
)

workforce_get_stats_tool = Tool(
    workforce_get_stats,
    takes_ctx=True,
    name="workforce_get_stats",
    description="""Compute workforce analytics: headcount, turnover rate, hires and
departures by month, department/division breakdowns, tenure, and status/type counts.

Use this for ANY question about workforce metrics, trends, or summaries. The stats
match what the workforce dashboard displays. Do NOT try to compute these manually
from raw records -- always use this tool.

Supports optional department/division/status/type filters to scope the analysis.""",
)

workforce_get_employee_tool = Tool(
    workforce_get_employee,
    takes_ctx=True,
    name="workforce_get_employee",
    description="""Get a single employee's full profile and their related check-ins.

Returns the complete employee record plus up to 20 recent check-ins with summaries.
Use this when the user is asking about a specific employee or viewing an employee
detail page.

Pass both document_id (schemaDocumentId) and checkins_document_id (checkinsDocumentId)
from your Application Context metadata to get the full picture.""",
)

workforce_search_checkins_tool = Tool(
    workforce_search_checkins,
    takes_ctx=True,
    name="workforce_search_checkins",
    description="""Search check-in and exit interview records with filters.

Supports filtering by employee, type (checkin / exit-interview), and status
(scheduled, invited, survey-complete, interview-complete, summarized, shared).

The document_id should come from checkinsDocumentId in your Application Context.""",
)

workforce_get_facets_tool = Tool(
    workforce_get_facets,
    takes_ctx=True,
    name="workforce_get_facets",
    description="""Get available filter values for the workforce dataset.

Returns lists of all unique departments, divisions, employee types, statuses,
pay groups, and years with activity. Call this first when you need to know what
valid filter values exist before constructing employee queries.

The document_id should come from schemaDocumentId in your Application Context.""",
)

WORKFORCE_TOOLS = [
    workforce_search_employees_tool,
    workforce_get_stats_tool,
    workforce_get_employee_tool,
    workforce_search_checkins_tool,
    workforce_get_facets_tool,
]
