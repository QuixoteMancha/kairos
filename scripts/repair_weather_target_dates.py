"""Clear only target dates that cannot be parsed from their stored series/ticker.

Safe repair of a prior parser bug. Raw source payloads are retained; this changes
only a derived label which had no supporting date token.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from database.db import init_db_historical
from database.historical.weather import WeatherCaptureJob, WeatherContract, WeatherEvidence
from outer_apis.scrapers.weather.evidence_capture import target_date


def repair(apply: bool = False) -> dict[str, int]:
    init_db_historical()
    fixed_contracts = fixed_evidence = terminal_jobs = 0
    for contract in WeatherContract.select().where(WeatherContract.target_date.is_null(False)):
        expected = target_date(contract.ticker, contract.series)
        if expected == contract.target_date:
            continue
        fixed_contracts += 1
        if apply:
            WeatherContract.update(target_date=expected).where(WeatherContract.id == contract.id).execute()
            fixed_evidence += WeatherEvidence.update(target_date=expected).where(
                WeatherEvidence.contract == contract.id).execute()
    # These are known source rejections, not transient transport failures.
    # Keep the error for auditability but stop the 1-hour retry loop.
    for job in WeatherCaptureJob.select().where(
        WeatherCaptureJob.error.contains("HTTPError: 400"),
        ~WeatherCaptureJob.error.startswith("terminal_source_rejection:"),
    ):
        terminal_jobs += 1
        if apply:
            job.error = "terminal_source_rejection: " + job.error
            job.next_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=3650)
            job.save()
    return {"contracts_to_fix": fixed_contracts, "evidence_rows_to_fix": fixed_evidence,
            "terminal_jobs_to_mark": terminal_jobs}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    print(repair(parser.parse_args().apply))
