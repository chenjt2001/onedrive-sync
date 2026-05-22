import contextlib
import logging
import time

from queries import Queries

_queries = Queries("./sql")

@contextlib.contextmanager
def observe_query(func):
    op = func.operation
    sql = func.sql
    start = time.perf_counter()
    yield func
    end = time.perf_counter()
    elapsed = end - start
    sql_preview = sql.strip().replace("\n", " ")
    sql_preview = sql_preview[:100] + ('...' if len(sql_preview) > 100 else '')
    logging.info(f"执行 {op} 耗时 {elapsed:.6f} 秒 | SQL: {sql_preview}")


def get_local_id_by_path(conn, local_path: str) -> int | None:

    segments = local_path.split("/")
    n = len(segments)

    with observe_query(_queries.get_local_id_by_path(len=n).get_local_id_by_path) as query:
        return query(conn=conn, **{f"name{i}": segments[i] for i in range(n)})
