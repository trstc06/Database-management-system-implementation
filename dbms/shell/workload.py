"""Run a file of queries and report, for each one, what Section 6 asks for:
the result row count, the page I/O, the wall time, and the estimated
against actual rows at the root.
"""

from dataclasses import dataclass
from pathlib import Path
from ..common.errors import DbError
from .session import Session
from .statements import split_statements


@dataclass(frozen=True)
class WorkloadRow:
    number: int
    rows: int | None
    page_io: int | None
    milliseconds: float | None
    est_root_rows: int | None
    error: str | None = None

    def q_error(self) -> float | None:
        if self.rows is None:
            return None
        actual = max(self.rows, 1)
        return max(self.est_root_rows, actual) / min(self.est_root_rows, actual)


def run_workload(session: Session, path: Path) -> list[WorkloadRow]:
    statements, rest = split_statements(Path(path).read_text(encoding='utf-8'))
    if rest.strip() and not rest.strip().startswith('--'):
        statements.append(rest)
    results = []
    for number, text in enumerate(statements, start=1):
        try:
            run = session.query(text)
        except DbError as error:
            results.append(WorkloadRow(number, None, None, None, None, error.unified()))
            continue
        results.append(WorkloadRow(number, run.rows, run.page_io, run.seconds * 1000, run.est_root_rows))
    return results


def format_report(rows: list[WorkloadRow], frames: int) -> str:
    lines = [f'Workload at B = {frames} frames.', '',
             '| Query | Rows | Page I/O | Wall time (ms) | Est. rows at root | Actual rows at root | q-error |',
             '|---|---|---|---|---|---|---|']
    for r in rows:
        if r.error:
            lines.append(f'| {r.number} | error | | | | | {r.error} |')
            continue
        lines.append(f'| {r.number} | {r.rows} | {r.page_io} | {r.milliseconds:.1f} | {r.est_root_rows} '
                     f'| {r.rows} | {r.q_error():.2f} |')
    return '\n'.join(lines)
