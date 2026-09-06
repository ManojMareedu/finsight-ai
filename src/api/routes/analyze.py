import asyncio
import base64
import collections
import logging
import os
import secrets
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from src.graph.workflow import build_workflow
from src.models.schemas import AnalyzeRequest, AnalyzeResponse
from src.utils.data_fetchers import CompanyNotResolvedError, get_company_cik, get_latest_10k_date
from src.utils.pdf_generator import generate_pdf

logger = logging.getLogger(__name__)
router = APIRouter()

# Build once at startup - not per request
workflow = build_workflow()

# --- Auth ---------------------------------------------------------------
_API_KEY = os.getenv("FINSIGHT_API_KEY", "")
if not _API_KEY:
    # Fail-open is deliberate: the deployed HF Space exposes only the Streamlit
    # UI (port 7860), so this API is container-internal there and in local dev.
    # Set FINSIGHT_API_KEY to require it once this is exposed directly.
    logger.warning("FINSIGHT_API_KEY not set — /analyze auth is DISABLED (fail-open)")


def _verify_api_key(x_api_key: str = Header(default="")) -> None:
    # Compare as bytes: compare_digest raises TypeError on non-ASCII str,
    # which would surface as a 500 instead of a 401.
    if _API_KEY and not secrets.compare_digest(x_api_key.encode(), _API_KEY.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# --- Rate limiting --------------------------------------------------------
# A per-IP fixed window in a plain dict: single-process, resets on restart, and
# one bucket per replica if this is ever scaled out. That ceiling is fine for a
# single container; a shared store or a gateway-level limiter is the answer if
# it is ever hit.
_RATE_LIMIT = 10
_RATE_WINDOW_SECONDS = 60.0
_MAX_TRACKED_IPS = 10_000
_request_times: dict[str, list[float]] = collections.defaultdict(list)


def _check_rate_limit(request: Request) -> None:
    # F-7: behind a reverse proxy, request.client.host is the proxy's address,
    # not the caller's — every caller shares one bucket unless the proxy is
    # configured to forward the real client IP (e.g. via X-Forwarded-For) and
    # something upstream of this trusts/parses that header. Not done here:
    # trusting a client-supplied header without a trusted-proxy allowlist is
    # its own vulnerability, and this deployment has no proxy in front of it.
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    if len(_request_times) > _MAX_TRACKED_IPS:
        # An O(n) sweep, but only once the dict passes the cap. Without it
        # every IP ever seen keeps a key forever.
        for stale in [
            k for k, v in _request_times.items() if not v or now - v[-1] >= _RATE_WINDOW_SECONDS
        ]:
            del _request_times[stale]
        if len(_request_times) > _MAX_TRACKED_IPS:
            # Still over cap: a burst of distinct live IPs sweeps nothing
            # (none are stale yet), so the cap above is a trigger, not a
            # bound. Evict the least-recently-seen IPs to make it a real one
            # — worst case a busy IP gets a fresh window early, which is a
            # far cheaper failure than an unbounded dict.
            by_age = sorted(_request_times.items(), key=lambda kv: kv[1][-1])
            for stale_ip, _ in by_age[: len(_request_times) - _MAX_TRACKED_IPS]:
                del _request_times[stale_ip]
    hits = [t for t in _request_times[ip] if now - t < _RATE_WINDOW_SECONDS]
    if len(hits) >= _RATE_LIMIT:
        retry_after = int(_RATE_WINDOW_SECONDS - (now - hits[0])) + 1
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    hits.append(now)
    _request_times[ip] = hits


# --- Per-company response cache --------------------------------------------
# A plain dict, single process, lost on restart, per-replica — the same ceiling
# as the rate limiter above, and acceptable for the same reason. A shared store
# is what this needs to survive a restart or be shared across replicas.
#
# TTL is not wall-clock: a 10-K is filed annually, so a clock-based expiry is
# either wrong (too short, recomputes an unchanged report) or wrong the other
# way (too long, serves a stale one after a new filing). Instead the cache is
# keyed on the filing date synthesis_agent already stamps onto report_date,
# and a cache hit re-checks that date against EDGAR (no LLM call) before
# serving.
_MAX_CACHE_ENTRIES = 500
_report_cache: dict[str, tuple[str, dict]] = {}


def _cache_key(company: str) -> str:
    return company.strip().lower()


def _cached_report(company: str) -> dict | None:
    cached = _report_cache.get(_cache_key(company))
    if cached is None:
        return None
    cached_date, report = cached
    try:
        cik = get_company_cik(company)
        latest_date = get_latest_10k_date(cik) if cik else None
    except Exception as e:
        logger.warning(f"Cache freshness check failed for {company}, recomputing: {e}")
        return None
    if latest_date and latest_date != cached_date:
        logger.info(f"New 10-K filed for {company} ({cached_date} -> {latest_date}), cache stale")
        del _report_cache[_cache_key(company)]
        return None
    return report


def _store_report(company: str, report: dict) -> None:
    if len(_report_cache) >= _MAX_CACHE_ENTRIES:
        _report_cache.pop(next(iter(_report_cache)))  # evict oldest inserted
    _report_cache[_cache_key(company)] = (report.get("report_date", ""), report)


def _run_pipeline(company: str, ticker: str):
    """Cache check + workflow.invoke. Runs on a worker thread — see
    asyncio.to_thread calls below — so it must not touch the event loop."""
    cached = _cached_report(company)
    if cached is not None:
        logger.info(f"Cache hit for {company} — skipping workflow")
        return cached

    result = workflow.invoke(
        {
            "company_name": company,
            "company_ticker": ticker,
            "iterations": 0,
            "research_complete": False,
            "error_messages": [],
            "web_search_results": [],
            "news_articles": [],
            "financial_metrics": {},
            "filing_chunks": [],
            "retrieved_context": [],
            "identified_risks": [],
            "risk_score": 0.0,
            "degraded": False,
            "final_report": None,
        }
    )
    report = result.get("final_report")
    if report:
        _store_report(company, report)
    return report


# --- Background jobs --------------------------------------------------------
# An in-process dict: lost on restart, per-replica, invisible across workers.
# start.sh runs a single uvicorn worker, so none of that is a regression today
# (see main._check_single_worker, which turns a drift off that loud). A shared
# queue is what this needs to survive a restart or run with more than one
# worker.
#
# Trust model (T2-2): there are no user accounts, so a job id can't be scoped
# to "the caller that created it" any more precisely than to "whoever holds
# the one shared API key" — binding to the key would add a check, not a
# boundary. So: when FINSIGHT_API_KEY is set, only key holders can submit or
# poll at all; when it isn't (the fail-open HF deployment), the id itself —
# 256 bits from secrets.token_urlsafe, not a guessable uuid1 or counter — is
# the entire capability, same as a bearer token.
_MAX_JOBS = 1000
_JOB_TTL_SECONDS = 3600.0
_jobs: dict[str, dict] = {}


def _sweep_jobs(now: float) -> None:
    if len(_jobs) <= _MAX_JOBS:
        return
    for job_id, job in list(_jobs.items()):
        if now - job["created"] >= _JOB_TTL_SECONDS:
            del _jobs[job_id]


async def _run_job(job_id: str, company: str, ticker: str) -> None:
    try:
        report = await asyncio.to_thread(_run_pipeline, company, ticker)
        if not report:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = "Workflow completed but no report was generated."
        else:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["report"] = report
    except CompanyNotResolvedError as e:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = str(e)
    except Exception as e:
        logger.error(f"Job {job_id} failed for {company}: {e}", exc_info=True)
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = f"Workflow error: {e}"


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    dependencies=[Depends(_verify_api_key), Depends(_check_rate_limit)],
)
async def analyze(request: AnalyzeRequest):
    """
    Run the full multi-agent due diligence pipeline for a company.
    Set include_pdf=true to receive a base64-encoded PDF in the response.

    This blocks until the pipeline finishes (45-135s) — same contract as
    before. The blocking work now runs in a worker thread (asyncio.to_thread)
    so it no longer holds up the event loop for other requests; a client that
    can't wait that long should use POST /analyze/jobs instead.
    """
    start = time.time()
    company = request.company_name.strip()

    if not company:
        raise HTTPException(status_code=422, detail="company_name cannot be empty")

    logger.info(f"Starting analysis for: {company}")

    try:
        report = await asyncio.to_thread(_run_pipeline, company, request.company_ticker or "")
    except CompanyNotResolvedError as e:
        logger.warning(f"Company resolution failed for {company}: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Workflow failed for {company}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Workflow error: {str(e)}")

    if not report:
        raise HTTPException(
            status_code=500,
            detail="Workflow completed but no report was generated. "
            "Check that your OPENROUTER_API_KEY is set and the model name is valid.",
        )

    # Generate PDF only if requested
    pdf_b64 = None
    if request.include_pdf:
        try:
            from src.models.schemas import DueDiligenceReport

            report_obj = DueDiligenceReport(**report)
            pdf_bytes = generate_pdf(report_obj)
            pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
            logger.info(f"PDF generated for {company}: {len(pdf_bytes):,} bytes")
        except Exception as e:
            # PDF failure should not kill the whole response
            logger.warning(f"PDF generation failed for {company}: {e}")

    return AnalyzeResponse(
        company=company,
        report=report,
        pdf_base64=pdf_b64,
        processing_time_seconds=round(time.time() - start, 2),
    )


@router.post(
    "/analyze/jobs",
    dependencies=[Depends(_verify_api_key), Depends(_check_rate_limit)],
)
async def submit_analyze_job(request: AnalyzeRequest):
    """Submit a pipeline run and return immediately with a job id — for a
    client that can't hold a connection open for the 45-135s /analyze can
    take. Poll GET /analyze/jobs/{job_id} for the result."""
    company = request.company_name.strip()
    if not company:
        raise HTTPException(status_code=422, detail="company_name cannot be empty")

    now = time.time()
    _sweep_jobs(now)
    if len(_jobs) >= _MAX_JOBS:
        # A real bound, not just a sweep trigger: the sweep above only clears
        # jobs past the TTL, so a burst of live jobs would otherwise grow
        # this dict without limit. Shed load instead.
        raise HTTPException(
            status_code=503,
            detail="Too many jobs in flight, try again shortly",
            headers={"Retry-After": "30"},
        )
    job_id = secrets.token_urlsafe(32)
    _jobs[job_id] = {"status": "pending", "created": now, "company": company}
    asyncio.create_task(_run_job(job_id, company, request.company_ticker or ""))
    return {"job_id": job_id, "status": "pending"}


@router.get("/analyze/jobs/{job_id}", dependencies=[Depends(_verify_api_key)])
def get_analyze_job(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown or expired job id")
    body = {"job_id": job_id, "status": job["status"]}
    if "report" in job:
        body["report"] = job["report"]
    if "error" in job:
        body["error"] = job["error"]
    return body
