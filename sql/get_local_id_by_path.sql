-- name: get_local_id_by_path({%- for i in range(len) %}name{{ i }}{% if not loop.last %}, {% endif %}{% endfor -%})$
SELECT t{{ len - 1 }}.id FROM local_items t0
{% for i in range(1, len) %}
JOIN local_items t{{ i }} ON t{{ i }}.parent_id = t{{ i-1 }}.id AND t{{ i }}.name = :name{{ i }}
{% endfor %}
WHERE t0.parent_id = 0 AND t0.name = :name0

-- 构建动态 SQL
-- SELECT t2.id
-- FROM local_items t0
-- JOIN local_items t1 ON t1.parent_id = t0.id AND t1.name = ?
-- JOIN local_items t2 ON t2.parent_id = t1.id AND t2.name = ?
-- WHERE t0.parent_id = 0 AND t0.name = ?