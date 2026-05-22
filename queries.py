from pathlib import Path
from jinja2 import Environment, FileSystemLoader
import aiosql
import aiosql.queries

class AioSQLQueries():
    def __init__(self, sql: str):
        self._render_result = sql
        self._queries = aiosql.from_str(sql, driver_adapter="sqlite3")
    def __getattr__(self, name: str):
        return getattr(self._queries, name)

class Queries:
    def __init__(self, sql_dir: str):
        self.sql_dir = Path(sql_dir)
        if not self.sql_dir.exists() or not self.sql_dir.is_dir():
            raise FileNotFoundError(f"指定的 SQL 目录不存在: {self.sql_dir}")

        # FileSystemLoader 会自动处理模板的加载和缓存
        self._env = Environment(loader=FileSystemLoader(str(self.sql_dir), encoding="utf-8"))

    def render(self, name: str, **kwargs) -> str:
        """
        显式调用：传递名称和参数进行渲染。
        自动补全 .sql 后缀（如果未提供）。
        """
        filename = name if name.endswith('.sql') else f"{name}.sql"
        template = self._env.get_template(filename)
        return template.render(**kwargs)

    def __getattr__(self, name: str):
        """支持魔术调用: queries.get_local_id(segments=...)"""
        def wrapper(**kwargs):
            sql = self.render(name, **kwargs)
            return AioSQLQueries(sql)
        return wrapper

if __name__ == "__main__":
    queries = Queries("./sql")
    #print(queries.get_local_id_by_path(len=5)._render_result)

    #print(queries.create_schema(parent_id="TEST_PARENT_ID"))

    print(queries.get_local_path_by_local_id()._render_result)


