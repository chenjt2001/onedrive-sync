-- name: link_cloud_and_local(root_folder_id)
WITH RECURSIVE matched_tree(local_id, cloud_id) AS (
    SELECT l.id, c.item_id
    FROM local_items l
    INNER JOIN cloud_items c ON l.name = c.name
    WHERE l.parent_id = 0 AND c.parent_id = :root_folder_id

    UNION ALL

    SELECT l.id, c.item_id
    FROM local_items l
    INNER JOIN matched_tree m ON l.parent_id = m.local_id
    INNER JOIN cloud_items c ON c.parent_id = m.cloud_id AND c.name = l.name
)
UPDATE local_items
SET cloud_id = matched_tree.cloud_id
FROM matched_tree
WHERE local_items.id = matched_tree.local_id
    AND local_items.cloud_id IS NULL
RETURNING local_items.id;