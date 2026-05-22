
from contextlib import closing
import datetime
import logging
import os
import pathlib
import shutil
import random
import string
from queries import Queries
import O365.drive
from O365.drive import Drive

import dotenv
dotenv.load_dotenv()

def random_string(length=16):
    """生成一个随机字符串"""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

class LocalOperation():

    def __init__(self, root_path, conn, root_local_id, root_cloud_id, dry_run=True):
        self.root = pathlib.Path(root_path)
        self.conn = conn
        self.root_local_id = root_local_id
        self.root_cloud_id = root_cloud_id
        self.queries = Queries("./sql")
        self.dry_run = dry_run


    def __get_parent_local_dir_by_parent_local_id(self, parent_local_id: int) -> str:

        if parent_local_id == self.root_local_id:
            return ""
        else:
            r = self.queries.get_local_path_by_local_id().get_local_path_by_local_id(conn=self.conn, local_id=parent_local_id)
            assert r is not None
            return r

    def rename(self, local_id: int, new_name: str):
        new_op = None

        with closing(self.conn.cursor()) as cursor:
            old_name, parent_id = cursor.execute('SELECT name, parent_id FROM local_items WHERE id = ?', (local_id,)).fetchone()
        parent_dir :str = self.__get_parent_local_dir_by_parent_local_id(parent_id)
        parent = self.root / parent_dir
        old_path = parent / old_name
        new_path: pathlib.Path = parent / new_name
        logging.info(f"正在执行重命名操作: {old_path} -> {new_path}")

        if not old_path.exists():
            raise FileNotFoundError(f"无法重命名: 源文件不存在 - {old_path}")

        if new_path.exists():
            # 向文件名添加随机字符串
            conflict_name = f"{new_name}_{random_string()}.conflict"
            conflict_path = parent / conflict_name
            logging.warning(f"目标文件已存在 - {new_path}，将重命名为冲突文件: {conflict_path}")

            new_op = (LocalOperation.rename, (local_id, new_name))

            new_name = conflict_name
            new_path = conflict_path

        if self.dry_run:
            return

        old_path.rename(new_path)

        with closing(self.conn.cursor()) as cursor:
            cursor.execute('UPDATE local_items SET name = ? WHERE id = ?', (new_name, local_id))
            self.conn.commit()

        return new_op

    def move(self, local_id: int, old_parent_id: int, new_parent_id: int):
        new_op = None
        with closing(self.conn.cursor()) as cursor:
            name = cursor.execute('SELECT name FROM local_items WHERE id = ?', (local_id,)).fetchone()[0]

        old_parent_dir :str = self.__get_parent_local_dir_by_parent_local_id(old_parent_id)
        new_parent_dir :str = self.__get_parent_local_dir_by_parent_local_id(new_parent_id)

        source_path = self.root / old_parent_dir / name
        dest_dir = self.root / new_parent_dir
        dest_path = dest_dir / name

        logging.info(f"正在执行移动操作: {source_path} -> {dest_path}")

        if not source_path.exists():
            raise FileNotFoundError(f"无法移动: 源文件不存在 - {source_path}")

        if dest_path.exists():
            # 向文件名添加随机字符串
            conflict_name = f"{name}_{random_string()}.conflict"
            conflict_path = dest_dir / conflict_name
            logging.warning(f"目标已存在 - {dest_path}，将重命名为: {conflict_path}")

            new_op = (LocalOperation.rename, (local_id, name))

            dest_path = conflict_path
            name = conflict_name

        if self.dry_run:
            return

        if not dest_dir.exists():
            raise FileNotFoundError(f"无法移动: 目标目录不存在 - {dest_dir}")

        shutil.move(source_path, dest_path)

        with closing(self.conn.cursor()) as cursor:
            cursor.execute('UPDATE local_items SET parent_id = ?, name = ? WHERE id = ?', (new_parent_id, name, local_id))
            self.conn.commit()

        return new_op


    def remove(self, local_id: int):
        target_path :str = self.queries.get_local_path_by_local_id().get_local_path_by_local_id(conn=self.conn, local_id=local_id)

        path = self.root / target_path

        logging.info(f"正在执行删除操作: {path}")

        if not path.exists():
            if not path.is_symlink():
                logging.warning(f"无法删除: 文件或目录不存在 - {path}")
                return

        if self.dry_run:
            return

        if path.is_file() or path.is_symlink():
            path.unlink()

        elif path.is_dir():
            shutil.rmtree(path)

        else:
            raise ValueError(f"无法删除: 不支持的文件类型 - {path}")

        # 从数据库删除
        with closing(self.conn.cursor()) as cursor:
            cursor.execute('DELETE FROM local_items WHERE id = ?', (local_id,))
            self.conn.commit()

    def download(self, drive :Drive, cloud_id: str, is_modified: bool):
        new_op = None

        parent_cloud_id, name, is_folder, last_modified = self.conn.execute('SELECT parent_id, name, is_folder, last_modified FROM cloud_items WHERE item_id = ?', (cloud_id,)).fetchone()
        if parent_cloud_id == self.root_cloud_id:
            parent_local_id = self.root_local_id
        else:
            parent_local_id = self.conn.execute('SELECT id FROM local_items WHERE cloud_id = ?', (parent_cloud_id,)).fetchone()[0]

        local_path = self.root / self.__get_parent_local_dir_by_parent_local_id(parent_local_id) / name

        if is_modified and is_folder == 1:
            raise ValueError(f"无法修改: 目录修改不受支持 - {local_path}")

        if not local_path.parent.exists():
            raise FileNotFoundError(f"无法下载: 目标目录不存在 - {local_path.parent}")

        if local_path.exists() and (not is_modified):
            safe_name = f"{name}_{random_string()}.conflict"
            logging.warning(f"目标已存在 - {local_path}，将重命名为: {safe_name}")
            local_path = local_path.parent / safe_name

        # 文件夹
        if is_folder:

            logging.info(f"正在执行创建目录操作: {local_path}")

            if self.dry_run:
                return

            local_path.mkdir()

        # 如果是文件，下载文件
        else:

            logging.info(f"正在执行下载操作: {local_path}")

            if self.dry_run:
                return

            drive_item : O365.drive.File = drive.get_item(cloud_id)

            if not drive_item.download(local_path.parent, local_path.name):
                raise IOError(f"下载失败: 无法下载文件 - {local_path}")

        # 修改最后修改时间为云端的时间
        mtime = datetime.datetime.strptime(last_modified, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
        os.utime(local_path, (mtime, mtime))

        # 将信息插入数据库
        query = self.queries.local_scan().insert_item
        local_id = query(
            conn=self.conn,
            parent_id=parent_local_id,
            name=local_path.name,
            size_bytes=0 if is_folder else os.path.getsize(local_path),
            is_folder=is_folder,
            last_modified=last_modified
        )
        with closing(self.conn.cursor()) as cursor:
            cursor.execute('UPDATE local_items SET cloud_id = ? WHERE id = ?', (cloud_id, local_id))
        self.conn.commit()

        # 如果下载的文件名与本地已有文件名冲突，生成一个新的重命名操作
        if local_path.name != name:
            new_op = (LocalOperation.rename, (local_id, name))

        return new_op
