-- name: insert_items(parent_id, name, size_bytes, is_folder, last_modified)*!
INSERT INTO local_items (parent_id, name, size_bytes, is_folder, last_modified)
VALUES (:parent_id, :name, :size_bytes, :is_folder, :last_modified)
ON CONFLICT(parent_id, name) DO UPDATE SET
    size_bytes = EXCLUDED.size_bytes,
    is_folder = EXCLUDED.is_folder,
    last_modified = EXCLUDED.last_modified

-- name: insert_item(parent_id, name, size_bytes, is_folder, last_modified)$
INSERT INTO local_items (parent_id, name, size_bytes, is_folder, last_modified)
VALUES (:parent_id, :name, :size_bytes, :is_folder, :last_modified)
ON CONFLICT(parent_id, name) DO UPDATE SET
    size_bytes = EXCLUDED.size_bytes,
    is_folder = EXCLUDED.is_folder,
    last_modified = EXCLUDED.last_modified
RETURNING id