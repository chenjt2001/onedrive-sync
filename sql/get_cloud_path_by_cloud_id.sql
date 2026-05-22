-- name: get_cloud_path_by_cloud_id(cloud_id, root_folder_id)$
WITH RECURSIVE path_cte AS (
    -- 起点：传入你想要的那个 ID (这里使用 ? 作为参数占位符)
    SELECT item_id, parent_id, name AS path
    FROM cloud_items
    WHERE item_id = :cloud_id

    UNION ALL

    -- 递归：向上寻找父节点并拼接
    SELECT c.item_id, p.parent_id, p.name || '/' || c.path
    FROM path_cte c
    JOIN cloud_items p ON c.parent_id = p.item_id
)
-- 取出回溯到根目录的最终路径
SELECT path FROM path_cte WHERE parent_id = :root_folder_id;