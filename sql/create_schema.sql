-- name: create_schema()#
CREATE TABLE IF NOT EXISTS local_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER NOT NULL,       -- 父文件夹的 local_items.id
    name TEXT NOT NULL,       -- 仅存当前文件名
    size_bytes INTEGER,
    is_folder INTEGER NOT NULL, -- 1表示文件夹 0表示文件
    last_modified TEXT,
    cloud_id TEXT,               -- 用来存放云端的 item_id

    UNIQUE(parent_id, name), -- 同一文件夹下不允许有同名文件/文件夹
    FOREIGN KEY (cloud_id) REFERENCES cloud_items(item_id) ON DELETE SET NULL -- 级联置空 cloud_items 对应行删除时，此处的 cloud_id 变 NULL
);
CREATE TABLE IF NOT EXISTS cloud_items (
    -- 1. 身份信息 (微软 API 原生数据)
    item_id TEXT PRIMARY KEY,        -- OneDrive的唯一ID (核心！用它来追踪重命名和移动)
    parent_id TEXT NOT NULL,                  -- 父文件夹ID
    is_folder INTEGER NOT NULL,      -- 1表示文件夹 0表示文件

    -- 2. 路径与元数据
    name TEXT NOT NULL,              -- 文件/文件夹名
    size_bytes INTEGER DEFAULT 0,    -- 文件大小
    sha1_hash TEXT,                  -- SHA1哈希值 (文件夹可为空)
    last_modified TEXT,              -- 云端最后修改时间

    -- 3. 同步相关 标识已被删除
    is_deleted INTEGER DEFAULT 0     -- 0表示存在，1表示已删除
);

-- 创建一个视图来动态生成云端文件的完整相对路径
CREATE VIEW IF NOT EXISTS v_cloud_items AS
WITH RECURSIVE path_cte(item_id, parent_id, name, is_folder, relative_path) AS (
    -- 1. 找到根节点 (没有 parent_id 的或者 parent_id 为某个特定根目录ID的)
    SELECT
        item_id, parent_id, name, is_folder, '/' || name AS relative_path
    FROM cloud_items
    WHERE parent_id IS NULL OR parent_id = '{{ parent_id }}'

    UNION ALL

    -- 2. 递归查找子节点并拼接路径
    SELECT
        c.item_id, c.parent_id, c.name, c.is_folder, p.relative_path || '/' || c.name
    FROM cloud_items c
    JOIN path_cte p ON c.parent_id = p.item_id
)
SELECT item_id, parent_id, is_folder, relative_path FROM path_cte;

-- 建立索引以极大提升查询速度 (30万文件必备)
CREATE INDEX IF NOT EXISTS idx_local_cloud_id ON local_items(cloud_id);
CREATE INDEX IF NOT EXISTS idx_local_parent_id ON local_items(parent_id);
CREATE INDEX IF NOT EXISTS idx_cloud_parent_id ON cloud_items(parent_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_parent_name ON cloud_items(parent_id, name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_local_parent_name ON local_items(parent_id, name);
