-- name: get_local_path_by_local_id(local_id)$
WITH RECURSIVE path_cte AS (
    -- 起点：传入你想要的那个 ID (这里使用 ? 作为参数占位符)
    SELECT id, parent_id, name AS path
    FROM local_items
    WHERE id = :local_id

    UNION ALL

    -- 递归：向上寻找父节点并拼接
    SELECT c.id, p.parent_id, p.name || '/' || c.path
    FROM path_cte c
    JOIN local_items p ON c.parent_id = p.id
)
-- 取出回溯到根目录的最终路径
SELECT path FROM path_cte WHERE parent_id = 0;