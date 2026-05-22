import datetime
import queue
import sys
import threading
import time
import typing

from O365 import Account, Connection
from O365.drive import Folder, Drive
import os
import sqlite3
import logging
import atexit
from contextlib import closing
from collections import deque, namedtuple
from itertools import batched
from requests import HTTPError

from operation import LocalOperation
from queries import Queries
from utils import observe_query

def config_log(log_folder: str) -> None:

    log = logging.getLogger()
    log.setLevel(logging.INFO)

    formatter = logging.Formatter('[%(asctime)s][%(name)s][%(levelname)s] %(message)s')

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    file_handler = logging.FileHandler(os.path.join(log_folder, f"{datetime.datetime.now().strftime(f"%Y%m%d%H%M%S")}.log"), encoding="utf-8")
    file_handler.setFormatter(formatter)

    log.addHandler(handler)
    log.addHandler(file_handler)



class DeltaScanner():

    DeletedItem = namedtuple("DeletedItem", ["item_id"])
    FileItem = namedtuple("FileItem", ["item_id", "parent_id", "relative_path", "name", "size_bytes", "sha1_hash", "last_modified"])
    FolderItem = namedtuple("FolderItem", ["item_id", "parent_id", "relative_path", "name", "size_bytes", "last_modified"])
    ResyncRequired = namedtuple("ResyncRequired", [])

    # 默认增量链接，第一次运行时会从这个链接开始扫描，扫描完成后会更新为新的 delta_link 以供下次增量扫描使用
    DEFAULT_DELTA_LINK = "https://graph.microsoft.com/v1.0/me/drive/root/delta"


    def __init__(self, connection: Connection):
        self.connection = connection

    def __get_delta_link(self):
        try:
            with open(os.path.join(os.getenv('DATA_FOLDER', "."), 'delta_link.txt'), 'r') as f:
                return f.read().strip()
        except FileNotFoundError:
            return DeltaScanner.DEFAULT_DELTA_LINK

    def __set_delta_link(self, link: str):
        with open(os.path.join(os.getenv('DATA_FOLDER', "."), 'delta_link.txt'), 'w') as f:
            f.write(link)

    def __iter__(self):
        next_link = self.__get_delta_link()

        while next_link:
            try:
                res = self.connection.get(next_link)
            except HTTPError as e:# Server response with 4XX or 5XX error status codes
                res = e.response

            if res.status_code == 200:
                # 处理返回的增量数据
                data: dict = res.json()
                for item in data.get('value', []):

                    item_id = item.get('id')

                    if "deleted" in item:
                        # 处理删除的文件/文件夹
                        item_id = item.get('id')
                        yield DeltaScanner.DeletedItem(item_id=item_id)
                        continue

                    parent_id = item.get('parentReference', {}).get('id')
                    name = item.get('name')
                    last_modified = item.get('lastModifiedDateTime')
                    size_bytes = item.get('size')

                    # 计算相对路径 (去掉 "/drive/root:" 前缀)
                    parent_path_raw = item.get('parentReference', {}).get('path', "")
                    prefix = "/drive/root:"
                    if not parent_path_raw.startswith(prefix):
                        logging.debug(f"意外的路径格式: {parent_path_raw}，跳过该项 完整项数据: {item}")
                        continue
                    parent_path = parent_path_raw[len(prefix):]
                    relative_path = f"{parent_path}/{name}"

                    if item.get('folder'):
                        yield DeltaScanner.FolderItem(item_id=item_id, parent_id=parent_id, relative_path=relative_path, name=name, size_bytes=size_bytes, last_modified=last_modified)

                    elif item.get('file'):
                        sha1_hash = item.get('file', {}).get('hashes', {}).get('sha1Hash')
                        yield DeltaScanner.FileItem(item_id=item_id, parent_id=parent_id, relative_path=relative_path, name=name, size_bytes=size_bytes, sha1_hash=sha1_hash, last_modified=last_modified)

                next_link: str = data.get('@odata.nextLink', "")  # 获取下一页链接

                if delta_link := data.get('@odata.deltaLink'):
                    self.__set_delta_link(delta_link)  # 更新 delta_link 以便下次增量扫描使用
                    break

            elif res.status_code == 410:
                # resyncRequired，需要重新进行完整扫描以获取新的 delta_link
                logging.warning(f"增量链接已过期, 需要重新进行完整扫描! {res.text}")

                yield DeltaScanner.ResyncRequired()

                self.__set_delta_link(DeltaScanner.DEFAULT_DELTA_LINK)  # 重置 delta_link
                next_link = DeltaScanner.DEFAULT_DELTA_LINK  # 从默认链接重新开始扫描

            else:

                logging.error(f"Delta API 请求失败: {res.status_code} - {res.text}")

                raise Exception(f"Delta API 请求失败: {res.status_code} - {res.text}")

                break

class TimingCursor(sqlite3.Cursor):
    """自定义游标类，用于自动统计 SQL 执行时间"""

    def execute(self, sql, parameters=()):
        start_time = time.perf_counter()  # perf_counter 比 time.time() 精度更高
        try:
            # 调用父类的执行方法
            return super().execute(sql, parameters)
        finally:
            elapsed = time.perf_counter() - start_time
            # 截取过长的 SQL 语句以防刷屏
            sql_preview = sql.strip()[:100] + ('...' if len(sql) > 100 else '')
            logging.info(f"耗时: {elapsed:.6f} 秒 | SQL: {sql_preview}")

    def executemany(self, sql, seq_of_parameters):
        start_time = time.perf_counter()
        try:
            return super().executemany(sql, seq_of_parameters)
        finally:
            elapsed = time.perf_counter() - start_time
            sql_preview = sql.strip()[:100] + ('...' if len(sql) > 100 else '')
            logging.info(f"耗时: {elapsed:.6f} 秒 | 批量 SQL: {sql_preview}")

    def executescript(self, sql_script):
        start_time = time.perf_counter()
        try:
            return super().executescript(sql_script)
        finally:
            elapsed = time.perf_counter() - start_time
            sql_preview = sql_script.strip()[:100] + ('...' if len(sql_script) > 100 else '')
            logging.info(f"耗时: {elapsed:.6f} 秒 | SQL 脚本: {sql_preview}")

class APP:
    def __init__(self, account: Account):
        self.account = account
        self.storage = self.account.storage()
        self.my_drive :Drive = typing.cast(Drive, self.storage.get_default_drive())
        self.root_folder :Folder = typing.cast(Folder, self.my_drive.get_root_folder())

        self.queue = queue.Queue()  # 用于存储待处理的同步操作
        self.stop_event = threading.Event()  # 用于控制线程停止

        self.conn = sqlite3.connect(os.path.join(os.getenv('DATA_FOLDER', "."), 'onedrive.db'), check_same_thread=False, timeout=10) # 允许多线程访问
        self.queries = Queries("./sql")  # 加载 SQL 查询模板

        self.__init_sqlite()  # 初始化 SQLite 数据库

        atexit.register(self.close)  # 注册程序退出时的清理函数

        self.local_operation = LocalOperation(os.getenv('LOCAL_ROOT'), self.conn, 0, self.root_folder.object_id, os.getenv('DRY_RUN') != '0')  # 初始化本地操作对象

        self.delta_scanner = DeltaScanner(self.account.connection)

        self.sync_thread = threading.Thread(target=self.sync_worker)
        self.sync_thread.start()


    def sync_worker(self):
        while not self.stop_event.is_set():
            try:
                func, args = self.queue.get(timeout=1)  # 等待1秒获取操作
                # 在这里处理同步操作，例如调用 LocalOperation 的方法
                if func == LocalOperation.rename:
                    if new_op := self.local_operation.rename(*args):
                        self.queue.put(new_op)

                elif func == LocalOperation.move:
                    if new_op := self.local_operation.move(*args):
                        self.queue.put(new_op)

                elif func == LocalOperation.remove:
                    if new_op := self.local_operation.remove(*args):
                        self.queue.put(new_op)

                elif func == LocalOperation.download:
                    if new_op := self.local_operation.download(*args):
                        self.queue.put(new_op)

            except queue.Empty:
                continue  # 如果队列为空，继续等待

            except Exception as e:
                logging.error(f"同步线程发生错误: {e}", exc_info=e)
                try:
                    logging.info(f"当前操作: {func}，参数: {args}")
                except:
                    pass

                input("按回车键继续...")  # 暂停以便查看错误日志



    def close(self):
        self.stop_event.set()  # 通知线程停止

        if hasattr(self, 'conn'):
            self.conn.commit()
            self.conn.close()


    def __init_sqlite(self):

        # 优化项：提升 SQLite 读写性能（Write-Ahead Logging 模式）
        self.conn.execute('PRAGMA journal_mode=WAL;')

        # 开启外键约束
        self.conn.execute('PRAGMA foreign_keys=ON;')

        self.queries.create_schema(parent_id=self.root_folder.object_id).create_schema(conn=self.conn)  # 创建数据库表结构

        self.conn.commit()

    def local_scan(self):
        local_items = []
        local_sync_folder: str = typing.cast(str, os.getenv('LOCAL_ROOT'))
        dir_id_cache = {local_sync_folder: 0}
        count = 0

        for root, dirs, files in os.walk(local_sync_folder):

            current_parent_id = dir_id_cache.get(root)

            for dir in dirs:
                dir_path = os.path.join(root, dir)
                last_modified = datetime.datetime.fromtimestamp(os.path.getmtime(dir_path), tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                # 插入文件夹记录，获取 ID
                dir_id = self.queries.local_scan().insert_item(conn=self.conn, parent_id=current_parent_id, name=dir, size_bytes=0, is_folder=1, last_modified=last_modified)
                self.conn.commit()
                dir_id_cache[dir_path] = dir_id  # 缓存当前文件夹的 ID 以便子文件夹使用
                count += 1

            for file in files:
                file_path = os.path.join(root, file)
                size_bytes = os.path.getsize(file_path)
                last_modified = datetime.datetime.fromtimestamp(os.path.getmtime(file_path), tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                local_items.append({
                    "parent_id": current_parent_id,
                    "name": file,
                    "size_bytes": size_bytes,
                    "is_folder": 0,
                    "last_modified": last_modified
                })

                if len(local_items) >= 1000:
                    with observe_query(self.queries.local_scan().insert_items) as query:
                        query(self.conn, local_items)
                    self.conn.commit()
                    count += len(local_items)
                    logging.info(f"已处理 {count} 条本地数据...")
                    local_items.clear()

        if local_items:
            with observe_query(self.queries.local_scan().insert_items) as query:
                query(self.conn, local_items)
            self.conn.commit()
            count += len(local_items)
            logging.info(f"已处理 {count} 条本地数据...")
            local_items.clear()

    def cloud_scan(self):
        count = 0

        with closing(self.conn.cursor(factory=TimingCursor)) as cursor:
            for chunk in batched(self.delta_scanner, 1000):  # 使用 itertools.batched 批量处理增量数据

                items_to_insert = []

                for item in chunk:
                    if isinstance(item, DeltaScanner.DeletedItem):
                        # 标记数据库中的对应项为已删除 (is_deleted=1)
                        cursor.execute('UPDATE cloud_items SET is_deleted = 1 WHERE item_id = ?', (item.item_id,))
                        continue

                    elif isinstance(item, DeltaScanner.FolderItem):
                        items_to_insert.append((item.item_id, item.parent_id, 1, item.name, item.size_bytes, None, item.last_modified, 0))

                    elif isinstance(item, DeltaScanner.FileItem):
                        items_to_insert.append((item.item_id, item.parent_id, 0, item.name, item.size_bytes, item.sha1_hash, item.last_modified, 0))

                    elif isinstance(item, DeltaScanner.ResyncRequired):
                        # 全表is_deleted置1
                        logging.warning("检测到需要重新同步，正在标记所有云端数据为已删除...")
                        cursor.execute('UPDATE cloud_items SET is_deleted = 1')
                        continue

                    else:
                        raise ValueError(f"未知的增量项类型: {type(item)}，内容: {item}")

                # 批量插入数据库
                if len(items_to_insert) > 0:
                    count += len(items_to_insert)
                    cursor.executemany('''
                        INSERT INTO cloud_items (item_id, parent_id, is_folder, name, size_bytes, sha1_hash, last_modified, is_deleted)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(item_id) DO UPDATE SET
                            parent_id = EXCLUDED.parent_id,
                            is_folder = EXCLUDED.is_folder,
                            name = EXCLUDED.name,
                            size_bytes = EXCLUDED.size_bytes,
                            sha1_hash = EXCLUDED.sha1_hash,
                            last_modified = EXCLUDED.last_modified,
                            is_deleted = EXCLUDED.is_deleted
                    ''', items_to_insert)
                    self.conn.commit()

                logging.info(f"已处理 {count} 条增量数据...")

            # 删除标记为已删除的项
            cursor.execute('DELETE FROM cloud_items WHERE is_deleted = 1')
            logging.info(f"已删除 {cursor.rowcount} 条云端数据...")
            self.conn.commit()

        logging.info("增量扫描完成。")


    # 按照层级排序
    def __sort_items_by_hierarchy(self, items : list[tuple[int, int]] | list[tuple[str, str]]) -> list:
        """
        将 (item_id, parent_id) 列表按拓扑序排列
        保证每一项的panrt_id在它之前出现
        """

        # 建一个集合，快速判断某个 parent_id 是否也在待创建列表中
        id_set = {item[0] for item in items}

        # 构建父子关系，寻找根节点
        children: dict[int|str, list[int|str]] = {}
        roots: list[int|str] = []
        for item_id, parent_id in items:
            if parent_id in id_set:
                children.setdefault(parent_id, []).append(item_id)
            else:
                roots.append(item_id)

        # 使用 deque 进行 O(N) 性能的层序遍历 (BFS)
        result = []
        queue = deque(roots)
        while queue:
            node = queue.popleft()
            result.append(node)
            queue.extend(children.get(node, []))

        # 安全检查
        if len(result) != len(items):
            raise ValueError(f"严重错误：检测到孤立的环形依赖，丢失了 {len(items) - len(result)} 个节点！")

        logging.info(f"已按层级排序 {len(items)} 个项目，根节点数量: {len(roots)}")

        return result


    def sync_change(self):
        """
        文件删除 -> 目录创建 -> 目录重命名 -> 目录移动 -> 文件重命名 -> 文件移动 -> 文件修改 -> 文件下载 -> 目录删除
        """

        with self.queries.sync_change().sync_change_cursor(conn=self.conn, root_folder_id=self.root_folder.object_id) as cursor:
            columns = [col[0] for col in cursor.description]
            results = [dict(zip(columns, row)) for row in cursor.fetchall()]

        # 统计更改数量
        count = 0

        # 1. 文件删除
        logging.info(f"处理更改: 文件删除...")
        for log in results:
            if log['diff_type'] == 'LOCAL_ONLY' and log['is_folder'] == 0:
                self.queue.put((LocalOperation.remove, (log['local_id'],)))
                count += 1

        # 2. 目录创建
        logging.info(f"处理更改: 目录创建...")
        # 必须按照文件夹层级顺序创建，否则可能出现父目录不存在导致的创建失败问题
        cloud_ids: list[str] = []
        for log in results:
            if log['diff_type'] == 'CLOUD_ONLY' and log['is_folder'] == 1:
                cloud_ids.append(log['cloud_id'])
        # 查询parent_id以便正确创建目录
        with closing(self.conn.cursor()) as cursor:
            cursor.execute('CREATE TEMP TABLE temp_ids (id TEXT PRIMARY KEY)')
            cursor.executemany('INSERT INTO temp_ids (id) VALUES (?)', [(cloud_id,) for cloud_id in cloud_ids])
            cursor.execute('SELECT item_id, parent_id FROM cloud_items WHERE item_id IN (SELECT id FROM temp_ids)')
            id_parent_pairs = cursor.fetchall()
            cursor.execute('DROP TABLE temp_ids')
        # 构建 cloud_ids 和 parent_cloud_ids 的映射关系以便排序
        sorted_cloud_ids = self.__sort_items_by_hierarchy(id_parent_pairs)
        for item_id in sorted_cloud_ids:
            self.queue.put((LocalOperation.download, (self.my_drive, item_id, False)))
            count += 1

        # 3. 目录重命名
        logging.info(f"处理更改: 目录重命名...")
        with closing(self.conn.cursor()) as cursor:
            for log in results:
                if log['diff_type'] == 'CHANGED' and log['is_renamed'] and log['is_folder'] == 1:
                    new_name = cursor.execute('SELECT name FROM cloud_items WHERE item_id = ?', (log['cloud_id'],)).fetchone()[0]
                    self.queue.put((LocalOperation.rename, (log['local_id'], new_name)))
                    count += 1


        # 4. 目录移动
        logging.info(f"处理更改: 目录移动...")
        with closing(self.conn.cursor()) as cursor:
            for log in results:
                if log['diff_type'] == 'CHANGED' and log['is_moved'] and log['is_folder'] == 1:
                    old_local_parent_id = cursor.execute('SELECT parent_id FROM local_items WHERE id = ?', (log['local_id'],)).fetchone()[0]
                    new_cloud_parent_id = cursor.execute('SELECT parent_id FROM cloud_items WHERE item_id = ?', (log['cloud_id'],)).fetchone()[0]
                    if new_cloud_parent_id == self.root_folder.object_id:
                        new_local_parent_id = 0
                    else:
                        new_local_parent_id = cursor.execute('SELECT id FROM local_items WHERE cloud_id = ?', (new_cloud_parent_id,)).fetchone()[0]
                    self.queue.put((LocalOperation.move, (log['local_id'], old_local_parent_id, new_local_parent_id)))
                    count += 1

        # 5. 文件重命名
        logging.info(f"处理更改: 文件重命名...")
        with closing(self.conn.cursor()) as cursor:
            for log in results:
                if log['diff_type'] == 'CHANGED' and log['is_renamed'] and log['is_folder'] == 0:
                    new_name = cursor.execute('SELECT name FROM cloud_items WHERE item_id = ?', (log['cloud_id'],)).fetchone()[0]
                    self.queue.put((LocalOperation.rename, (log['local_id'], new_name)))
                    count += 1

        # 6. 文件移动
        logging.info(f"处理更改: 文件移动...")
        with closing(self.conn.cursor()) as cursor:
            for log in results:
                if log['diff_type'] == 'CHANGED' and log['is_moved'] and log['is_folder'] == 0:
                    old_local_parent_id = cursor.execute('SELECT parent_id FROM local_items WHERE id = ?', (log['local_id'],)).fetchone()[0]
                    new_cloud_parent_id = cursor.execute('SELECT parent_id FROM cloud_items WHERE item_id = ?', (log['cloud_id'],)).fetchone()[0]
                    if new_cloud_parent_id == self.root_folder.object_id:
                        new_local_parent_id = 0
                    else:
                        new_local_parent_id = cursor.execute('SELECT id FROM local_items WHERE cloud_id = ?', (new_cloud_parent_id,)).fetchone()[0]
                    self.queue.put((LocalOperation.move, (log['local_id'], old_local_parent_id, new_local_parent_id)))
                    count += 1

        # 7. 文件修改
        logging.info(f"处理更改: 文件修改...")
        for log in results:
            if log['diff_type'] == 'CHANGED' and log['is_modified'] and log['is_folder'] == 0:
                self.queue.put((LocalOperation.download, (self.my_drive, log['cloud_id'], True)))
                count += 1

        # 8. 文件下载
        logging.info(f"处理更改: 文件下载...")
        for log in results:
            if log['diff_type'] == 'CLOUD_ONLY' and log['is_folder'] == 0:
                self.queue.put((LocalOperation.download, (self.my_drive, log['cloud_id'], False)))
                count += 1

        # 9. 目录删除
        logging.info(f"处理更改: 目录删除...")
        # 必须按照从子目录到父目录的顺序删除
        local_ids: list[int] = []
        for log in results:
            if log['diff_type'] == 'LOCAL_ONLY' and log['is_folder'] == 1:
                local_ids.append(log['local_id'])
        # 查询parent_id以便正确删除目录
        with closing(self.conn.cursor()) as cursor:
            cursor.execute('CREATE TEMP TABLE temp_ids (id INTEGER PRIMARY KEY)')
            cursor.executemany('INSERT INTO temp_ids (id) VALUES (?)', [(local_id,) for local_id in local_ids])
            cursor.execute('SELECT id, parent_id FROM local_items WHERE id IN (SELECT id FROM temp_ids)')
            id_parent_pairs : list[tuple[int, int]] = cursor.fetchall()
            cursor.execute('DROP TABLE temp_ids')
        # 构建 local_ids 和 parent_local_ids 的映射关系以便排序
        sorted_local_ids = self.__sort_items_by_hierarchy(id_parent_pairs)
        for local_id in reversed(sorted_local_ids):  # 删除时需要反向处理
            self.queue.put((LocalOperation.remove, (local_id,)))
            count += 1

        # 10. 统计结果
        if count < len(results):
            logging.warning(f"存在未处理的更改项，已处理 {count} 条更改，但总共发现 {len(results)} 条更改！")
        else:
            # count > len(results) 是正常的，因为某些更改可能会触发多个操作
            # 例如一个文件被移动和重命名了，就会有两个操作
            logging.info(f"已发起所有 {count} 条更改！共发现 {len(results)} 条更改。")


    def link_cloud_and_local(self):
        """关联云端和本地数据 (全 SQL 树状层级匹配)"""
        logging.info("执行 SQL 递归匹配 local_items.cloud_id -> cloud_items.item_id...")

        with observe_query(self.queries.link_cloud_and_local().link_cloud_and_local) as query:
            local_ids = query(conn=self.conn, root_folder_id=self.root_folder.object_id)
        self.conn.commit()
        logging.info(f"  [树状匹配完成] 共关联了 {len(list(local_ids))} 个项目！")


    def sync(self):

        logging.info(f"当前本地根目录: {os.getenv('LOCAL_ROOT')}")
        logging.info(f"当前云端根目录 ID: {self.root_folder.object_id}")

        # 1. 将云端内容和数据库中的数据进行增量扫描和更新，保持数据库与云端状态同步
        logging.info("正在扫描云端状态...")
        self.cloud_scan()

        # 2. 扫描本地文件系统
        logging.info("正在扫描本地文件系统...")
        self.local_scan()

        # 3. 关联云端和本地数据
        self.link_cloud_and_local()

        # 4. 处理更改
        if self.local_operation.dry_run:
            logging.info("当前处于 Dry Run 模式")

        self.sync_change()

    def run(self, iterations: int = 1):
        if iterations < 1:
            iterations = int(1e9)

        for i in range(iterations):
            try:
                logging.info("开始新一轮同步...")
                try:
                    self.sync()
                except Exception as e:
                    logging.error(f"同步过程中发生错误: {e}", exc_info=e)

                # 等待队列中的同步操作完成
                logging.info("正在等待同步操作完成...")
                while not self.queue.empty():
                    time.sleep(1)

                if i < iterations - 1:
                    logging.info("等待 10 分钟后进行下一轮同步...")
                    time.sleep(600)  # 每10分钟同步一次
            except KeyboardInterrupt:
                logging.info("检测到键盘中断，正在停止同步...")
                break

        self.stop_event.set()  # 通知线程停止


if __name__ == "__main__":

    # 加载环境变量
    from dotenv import load_dotenv
    load_dotenv()

    log_folder = os.getenv("LOG_FOLDER", "log")
    data_folder = os.getenv('DATA_FOLDER', "data")
    if not os.path.exists(log_folder):
        os.makedirs(log_folder)
    if not os.path.exists(data_folder):
        os.makedirs(data_folder)

    config_log(log_folder)

    # 在 Azure 注册应用时获取的 Client ID 和 Client Secret
    credentials = (typing.cast(str, os.getenv('CLIENT_ID')), typing.cast(str, os.getenv('CLIENT_SECRET')))

    # 初始化账号对象
    from O365 import FileSystemTokenBackend
    account = Account(credentials, token_backend=FileSystemTokenBackend(token_path=os.path.join(data_folder, 'o365_token.txt')))

    # 第一次运行会生成一个验证链接，控制台会提示你打开链接并输入回调 URL
    if not account.is_authenticated:
        # 请求 OneDrive 读写权限
        account.authenticate(requested_scopes=['Files.Read.All'])

    app = APP(account)
    app.run(iterations=1)
