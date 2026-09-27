"""Read-only identity-level workload estimates, independent of job/page limits.

All eight counters come from one database snapshot. A price awaiting human
confirmation is not necessarily an image awaiting inference. Existing results
are matched by full identity, month and current image before estimating work.
"""
from django.db import connection


def system_kpis() -> dict[str, int]:
    # Only the JSON-array expansion differs in the isolated SQLite preview.
    segments = ("jsonb_array_elements_text(prompt.segments_json) AS segment(value)"
                if connection.vendor == "postgresql" else "json_each(prompt.segments_json) AS segment")
    sql = f"""
    WITH identities AS MATERIALIZED (
      SELECT category, scope, ranking_dimension, sku_code FROM market_master_identities
    ), active_prompts AS MATERIALIZED (
      SELECT prompt.category, prompt.segments_json
      FROM market_annotation_prompt_versions prompt
      WHERE prompt.status='active'
        AND EXISTS (SELECT 1 FROM {segments})
        AND (NOT EXISTS (
          SELECT 1 FROM market_subcategory_taxonomy t WHERE t.category=prompt.category AND t.status='active'
        ) OR (NOT EXISTS (
          SELECT 1 FROM market_subcategory_taxonomy t
          WHERE t.category=prompt.category AND t.status='active'
            AND NOT EXISTS (SELECT 1 FROM {segments} WHERE segment.value=t.subcategory)
        ) AND NOT EXISTS (
          SELECT 1 FROM {segments}
          WHERE NOT EXISTS (SELECT 1 FROM market_subcategory_taxonomy t
            WHERE t.category=prompt.category AND t.status='active' AND t.subcategory=segment.value)
        )))
    ), snapshots AS MATERIALIZED (
      SELECT s.category,s.scope,s.ranking_dimension,s.sku_code,s.month,
        s.confirmed_market_price_cents,s.ai_price_type,s.image_content_sha256,
        COALESCE(NULLIF(cache.content_sha256,''),s.image_content_sha256,'') current_hash
      FROM market_price_snapshots s
      JOIN identities i ON i.category=s.category AND i.scope=s.scope
        AND i.ranking_dimension=s.ranking_dimension AND i.sku_code=s.sku_code
      LEFT JOIN market_image_cache cache ON cache.source_url=s.image_url AND cache.status='ready'
    ), price_state AS MATERIALIZED (
      SELECT category,scope,ranking_dimension,sku_code,
        MAX(CASE WHEN confirmed_market_price_cents IS NULL THEN 1 ELSE 0 END) has_pending
      FROM snapshots GROUP BY category,scope,ranking_dimension,sku_code
    ), pending AS MATERIALIZED (
      SELECT s.category,s.scope,s.ranking_dimension,s.sku_code,s.month,s.current_hash
      FROM snapshots s WHERE s.confirmed_market_price_cents IS NULL
        AND NOT EXISTS (
          SELECT 1 FROM market_annotation_items result
          WHERE result.category=s.category AND result.scope=s.scope
            AND result.ranking_dimension=s.ranking_dimension AND result.sku_code=s.sku_code
            AND result.month=s.month AND result.image_content_sha256=s.current_hash AND s.current_hash<>''
            AND result.status IN ('review_pending','approved','rejected','committed')
            AND (result.ai_segment<>'' OR result.ai_image_price_cents IS NOT NULL
              OR result.ai_confidence_bps IS NOT NULL OR result.ai_reason<>'')
        )
      UNION ALL
      SELECT i.category,i.scope,i.ranking_dimension,i.sku_code,'',''
      FROM identities i LEFT JOIN price_state s ON s.category=i.category AND s.scope=i.scope
        AND s.ranking_dimension=i.ranking_dimension AND s.sku_code=i.sku_code
      WHERE s.sku_code IS NULL
    ), segment_history AS MATERIALIZED (
      SELECT DISTINCT history.category,history.scope,history.ranking_dimension,history.sku_code
      FROM market_annotation_items history JOIN active_prompts prompt ON prompt.category=history.category
      WHERE history.status='committed' AND history.reviewed_segment<>''
        AND EXISTS (SELECT 1 FROM {segments} WHERE segment.value=history.reviewed_segment)
    ), same_image AS MATERIALIZED (
      SELECT DISTINCT category,scope,ranking_dimension,sku_code,image_content_sha256
      FROM snapshots WHERE confirmed_market_price_cents IS NOT NULL
        AND ai_price_type='标准售价' AND image_content_sha256<>''
        AND current_hash=image_content_sha256
    ), terminal_failures AS MATERIALIZED (
      SELECT DISTINCT failed.category,failed.scope,failed.ranking_dimension,failed.sku_code,
        failed.month,failed.image_content_sha256
      FROM market_annotation_items failed
      WHERE failed.status='failed' AND failed.attempt_count>=3
        AND NOT EXISTS (
          SELECT 1 FROM market_annotation_items replacement
          WHERE replacement.id<>failed.id AND replacement.category=failed.category AND replacement.scope=failed.scope
            AND replacement.ranking_dimension=failed.ranking_dimension AND replacement.sku_code=failed.sku_code
            AND replacement.month=failed.month AND replacement.image_content_sha256=failed.image_content_sha256
            AND (replacement.status IN ('queued','claimed','inferencing','review_pending','approved','rejected','committed')
              OR (replacement.status='failed' AND replacement.attempt_count<3))
        )
    ), routes AS MATERIALIZED (
      SELECT p.category,p.scope,p.ranking_dimension,p.sku_code,
        MAX(CASE WHEN p.ranking_dimension<>'SKU' OR prompt.category IS NULL
            OR p.current_hash='' OR failure.sku_code IS NOT NULL THEN 0
          WHEN reuse.sku_code IS NOT NULL THEN 1
          WHEN segment.sku_code IS NOT NULL THEN 2 ELSE 3 END) route
      FROM pending p
      LEFT JOIN active_prompts prompt ON prompt.category=p.category
      LEFT JOIN same_image reuse ON reuse.category=p.category AND reuse.scope=p.scope
        AND reuse.ranking_dimension=p.ranking_dimension AND reuse.sku_code=p.sku_code
        AND reuse.image_content_sha256=p.current_hash
      LEFT JOIN segment_history segment ON segment.category=p.category AND segment.scope=p.scope
        AND segment.ranking_dimension=p.ranking_dimension AND segment.sku_code=p.sku_code
      LEFT JOIN terminal_failures failure ON failure.category=p.category AND failure.scope=p.scope
        AND failure.ranking_dimension=p.ranking_dimension AND failure.sku_code=p.sku_code
        AND failure.month=p.month AND failure.image_content_sha256=p.current_hash
      GROUP BY p.category,p.scope,p.ranking_dimension,p.sku_code
    )
    SELECT (SELECT COUNT(*) FROM identities),
      (SELECT COUNT(*) FROM identities i LEFT JOIN price_state s ON s.category=i.category AND s.scope=i.scope
        AND s.ranking_dimension=i.ranking_dimension AND s.sku_code=i.sku_code
        WHERE COALESCE(s.has_pending,1)=1),
      COUNT(*),(SELECT COUNT(*) FROM identities)-COUNT(*),
      COALESCE(SUM(CASE WHEN route=1 THEN 1 ELSE 0 END),0),
      COALESCE(SUM(CASE WHEN route=2 THEN 1 ELSE 0 END),0),
      COALESCE(SUM(CASE WHEN route=3 THEN 1 ELSE 0 END),0),
      COALESCE(SUM(CASE WHEN route=0 THEN 1 ELSE 0 END),0)
    FROM routes
    """
    with connection.cursor() as cursor:
        cursor.execute(sql)
        row = cursor.fetchone()
    return dict(zip(("marketIdentityTotal", "pendingPriceCount", "pendingAiCount", "completedAiCount",
                     "sameImageReuseCount", "priceOnlyRecognitionCount", "fullRecognitionCount",
                     "blockedRecognitionCount"), map(int, row)))
