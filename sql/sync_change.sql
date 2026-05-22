-- name: sync_change(root_folder_id)
WITH root_cloud_id AS (
    SELECT :root_folder_id AS rid
)
-- ============================================================================
-- Part A: 本地独有项（上传候选）
-- ============================================================================
SELECT
    'LOCAL_ONLY' AS diff_type,
    l.id         AS local_id,
    NULL         AS cloud_id,
    l.is_folder,
    0            AS is_renamed,
    0            AS is_moved,
    0            AS is_modified
FROM local_items l
WHERE l.cloud_id IS NULL

UNION ALL

-- ============================================================================
-- Part B: 云端独有项（下载候选）
-- ============================================================================
SELECT
    'CLOUD_ONLY' AS diff_type,
    NULL         AS local_id,
    c.item_id    AS cloud_id,
    c.is_folder,
    0            AS is_renamed,
    0            AS is_moved,
    0            AS is_modified
FROM cloud_items c
WHERE NOT EXISTS (SELECT 1 FROM local_items l WHERE l.cloud_id = c.item_id)

UNION ALL

-- ============================================================================
-- Part C: 已关联但属性有变动的项（更新候选）
-- ============================================================================
SELECT
    'CHANGED' AS diff_type,
    l.id      AS local_id,
    l.cloud_id,
    l.is_folder,
    (l.name IS NOT c.name) AS is_renamed,
    (
        CASE
            WHEN l.parent_id = 0 THEN r.rid
            ELSE p.cloud_id
        END IS NOT c.parent_id
    ) AS is_moved,
    (l.is_folder = 0 AND l.last_modified IS NOT c.last_modified) AS is_modified
FROM local_items l
-- 先做 INNER JOIN 过滤出真正双边都存在的数据
INNER JOIN cloud_items c ON l.cloud_id = c.item_id
CROSS JOIN root_cloud_id r
-- 延迟自连接 (Delayed Self-Join)。只对双边存在的记录去查找父节点云 ID
LEFT JOIN local_items p ON l.parent_id = p.id
WHERE
    (l.name IS NOT c.name)
    OR (
        CASE
            WHEN l.parent_id = 0 THEN r.rid
            ELSE p.cloud_id
        END IS NOT c.parent_id
    )
    OR (l.is_folder = 0 AND l.last_modified IS NOT c.last_modified)

-- ============================================================================
-- 排序
-- ============================================================================
ORDER BY
	diff_type ASC,
    is_folder DESC,
    local_id ASC;