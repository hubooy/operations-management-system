"""Read-only, database-side ranking pagination; never materialize the whole range.

The caller validates filters and runs inside the market revision read fence.
All user values are bound parameters. SQL names and expressions are fixed here.
"""
from django.db import connection


def _read(sql, params):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]


def _projected(source):
    # source is an internal CTE name, never request text.
    return f"""SELECT p.ranking_dimension,p.sku_code,p.period_start,p.period_end,
          SUM(m.transaction_amount_cents) amount
        FROM (SELECT DISTINCT ranking_dimension,sku_code,period_start,period_end FROM {source}) p
        JOIN market_netshop_projection m ON
          m.projection_revision=(SELECT active_revision FROM market_netshop_projection_control WHERE id=1)
          AND m.kind='metric' AND m.source='jd_sku_daily'
          AND m.business_date>=p.period_start AND m.business_date<=p.period_end
          AND m.dataset=CASE WHEN p.ranking_dimension='SPU' THEN 'spu_daily' ELSE 'sku_daily' END
          AND p.sku_code=CASE WHEN m.dataset='spu_daily' THEN m.spu_id ELSE m.sku_id END
        GROUP BY p.ranking_dimension,p.sku_code,p.period_start,p.period_end"""


def _scope(queryset, price_bands, *, include_bands=True, include_prices=True, selected_columns="*"):
    # Compile the existing ORM predicates: search escaping, dates and all domain
    # filters stay identical to the report path, before price-band preference.
    columns = ("id", "period_start", "period_end", "category", "scope",
               "ranking_dimension", "sku_code", "brand", "price_band_filter", "rank", "gmv_cents")
    source, params = queryset.order_by().values(*columns).query.sql_with_params()
    band_filter = ""
    if price_bands:
        band_filter = "WHERE price_band IN (" + ",".join(["%s"] * len(price_bands)) + ")"
    identity = "period_start,period_end,category,scope,ranking_dimension,sku_code"
    preference = "CASE price_band_filter WHEN '全部' THEN 0 WHEN '' THEN 1 ELSE 2 END,price_band_filter,id DESC"
    preferred = f"SELECT source.*, ROW_NUMBER() OVER (PARTITION BY {identity} ORDER BY {preference}) preference FROM source"
    preferred_filter = "WHERE p.preference=1"
    if connection.vendor == "postgresql":
        # DISTINCT ON retains a useful cardinality estimate for downstream joins.
        preferred = f"SELECT DISTINCT ON ({identity}) source.*, 1 preference FROM source ORDER BY {identity},{preference}"
        preferred_filter = ""
    materialized = "MATERIALIZED" if connection.vendor == "postgresql" else ""
    # Length plus removal of the exact ASCII alphabet matches the original
    # full SHA256 regex, including rejection of a trailing newline.
    valid_hash = "TRANSLATE(s.image_content_sha256,'0123456789abcdef','')=''" if connection.vendor == "postgresql" else "s.image_content_sha256 REGEXP '^[a-f0-9]{64}$'"
    priced_columns = ','.join('p.'+name for name in columns if name != 'price_band_filter')
    # Hash-expression filters have no column statistics. Combining both as
    # WHERE predicates can estimate a large price set as one row and choose
    # an unindexed nested loop across every preferred entry. Keep invalid
    # hashes as a NULL price; the unique snapshot identity preserves semantics
    # while ordinary status/type/amount predicates retain useful estimates.
    priced = f"""
        SELECT {priced_columns},s.confirmed_market_price_cents official
        FROM preferred p LEFT JOIN (SELECT category,scope,ranking_dimension,sku_code,month,
          CASE WHEN LENGTH(s.image_content_sha256)=64 AND {valid_hash}
            THEN confirmed_market_price_cents ELSE NULL END confirmed_market_price_cents
          FROM market_price_snapshots s WHERE s.confirmation_status='confirmed'
          AND s.ai_price_type IN ('标准售价','到手价','券后价')
          AND s.confirmed_market_price_cents>0) s ON
          s.category=p.category AND s.scope=p.scope AND s.ranking_dimension=p.ranking_dimension
          AND s.sku_code=p.sku_code AND s.month=SUBSTR(p.period_end,1,7)
        {preferred_filter}
    """ if include_prices else f"SELECT p.*,CAST(NULL AS BIGINT) official FROM preferred p {preferred_filter}"
    banded = """SELECT p.*,'未确认价格' price_band FROM priced p WHERE p.official IS NULL
        UNION ALL SELECT p.*,COALESCE(b.price_band,'未确认价格') price_band FROM priced p
        LEFT JOIN band_lookup b ON b.category=p.category AND b.period_end=p.period_end AND b.official=p.official
        WHERE p.official IS NOT NULL""" if include_bands else "SELECT p.*,NULL price_band FROM priced p"
    return f"""WITH source AS ({source}),
      preferred AS ({preferred}), priced AS ({priced}), price_keys AS (
        SELECT DISTINCT category,period_end,official FROM priced WHERE official IS NOT NULL
      ), band_lookup AS {materialized} (
        SELECT p.*, COALESCE((SELECT b.label FROM market_price_band_items b
          JOIN market_price_band_versions v ON v.id=b.version_id
          WHERE p.official IS NOT NULL AND v.status='published'
            AND v.category IN (p.category,'*') AND v.effective_from<=p.period_end
            AND (b.min_cents IS NULL OR p.official>=b.min_cents)
            AND (b.max_cents IS NULL OR p.official<b.max_cents)
          ORDER BY CASE WHEN v.category='*' THEN 1 ELSE 0 END,
            v.effective_from DESC,v.version DESC,b.sort_order,b.id LIMIT 1), '未确认价格') price_band
        FROM price_keys p
      ), banded AS ({banded}), selected AS (SELECT {selected_columns} FROM banded {band_filter})
    """, [*params, *price_bands]


def ranking_page(queryset, price_bands, page, page_size):
    cte, params = _scope(queryset, price_bands)
    materialized = "MATERIALIZED" if connection.vendor == "postgresql" else ""
    # Only aggregated scalars/options cross the DB boundary. COUNT DISTINCT is
    # over separate identity columns (never ambiguous concatenated strings).
    # Stats, price-band counts and page now share the same selected relation.
    # No cross-request cache: every call remains inside the existing revision fence.
    cte += """, identities AS (
        SELECT category,scope,ranking_dimension,sku_code,
          MAX(CASE WHEN official IS NULL THEN 1 ELSE 0 END) pending
        FROM selected GROUP BY category,scope,ranking_dimension,sku_code
      ), stats AS (SELECT COUNT(*) total, COUNT(DISTINCT category) category_count,
        COUNT(DISTINCT COALESCE(NULLIF(brand,''),'未识别品牌')) brand_count,
        (SELECT COUNT(*) FROM identities) product_count,
        (SELECT COALESCE(SUM(pending),0) FROM identities) pending_count
      FROM selected), bands AS (
        SELECT price_band value,COUNT(*) count FROM selected GROUP BY price_band
      )"""
    # Projection values affect ranking ties, so apply the existing positive-only
    # replacement before LIMIT. Aggregate by SKU/SPU and exact period once even
    # when several categories/scopes share that period.
    # Rank precedes GMV. Retain ALL ties at the page boundary, then apply the
    # projection before sorting those ties; never truncate by raw GMV or ID.
    records = _read(cte + f""", ranked AS (
        SELECT s.*,RANK() OVER (ORDER BY CASE WHEN rank IS NULL THEN 1 ELSE 0 END,
          COALESCE(rank,2147483647)) rank_position FROM selected s
      ), candidates AS (SELECT * FROM ranked WHERE rank_position<=%s),
      projected AS {materialized} ({_projected('candidates')}), ordered AS (
        SELECT s.id, CASE WHEN m.amount>0 THEN m.amount ELSE s.gmv_cents END effective_gmv,
          s.rank, s.official, s.price_band,s.category,s.scope,s.ranking_dimension,s.sku_code,s.period_end
        FROM candidates s LEFT JOIN projected m ON m.ranking_dimension=s.ranking_dimension
          AND m.sku_code=s.sku_code AND m.period_start=s.period_start AND m.period_end=s.period_end
      ), page_rows AS (
        SELECT * FROM ordered ORDER BY CASE WHEN rank IS NULL THEN 1 ELSE 0 END,
          COALESCE(rank,2147483647),effective_gmv DESC,id LIMIT %s OFFSET %s
      ), page_identities AS (
        SELECT DISTINCT category,scope,ranking_dimension,sku_code FROM page_rows
      ), history_rows AS (
        SELECT s.* FROM selected s JOIN page_identities p ON p.category=s.category AND p.scope=s.scope
          AND p.ranking_dimension=s.ranking_dimension AND p.sku_code=s.sku_code
      ), history_projection AS {materialized} ({_projected('history_rows')}), history_ordered AS (
        SELECT s.*,CASE WHEN m.amount>0 THEN m.amount ELSE s.gmv_cents END effective_gmv
        FROM history_rows s LEFT JOIN history_projection m ON m.ranking_dimension=s.ranking_dimension
          AND m.sku_code=s.sku_code AND m.period_start=s.period_start AND m.period_end=s.period_end
      ), history AS (
        SELECT o.id,LAG(o.rank) OVER (PARTITION BY o.category,o.scope,o.ranking_dimension,o.sku_code
          ORDER BY o.period_end,CASE WHEN o.rank IS NULL THEN 1 ELSE 0 END,
            COALESCE(o.rank,2147483647),o.effective_gmv DESC,o.id) previous_rank,
          COUNT(*) OVER (PARTITION BY o.category,o.scope,o.ranking_dimension,o.sku_code) period_count
        FROM history_ordered o
      ), result AS (SELECT p.id,p.effective_gmv,p.rank,p.official,p.price_band,h.previous_rank,h.period_count
        FROM page_rows p JOIN history h ON h.id=p.id
      ) SELECT 'stats' kind,total,category_count,brand_count,product_count,pending_count,
          CAST(NULL AS BIGINT) id,CAST(NULL AS BIGINT) effective_gmv,CAST(NULL AS BIGINT) rank,
          CAST(NULL AS BIGINT) official,CAST(NULL AS TEXT) price_band,
          CAST(NULL AS BIGINT) previous_rank,CAST(NULL AS BIGINT) period_count,CAST(NULL AS BIGINT) band_count
        FROM stats
      UNION ALL SELECT 'band',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,value,NULL,NULL,count FROM bands
      UNION ALL SELECT 'page',NULL,NULL,NULL,NULL,NULL,id,effective_gmv,rank,official,price_band,previous_rank,period_count,NULL FROM result
    """, [*params, page * page_size, page_size, (page - 1) * page_size])
    stats_record = next(r for r in records if r['kind'] == 'stats')
    stats = {key: stats_record[key] for key in ('total', 'category_count', 'brand_count', 'product_count', 'pending_count')}
    bands = sorted(({'value': r['price_band'], 'count': r['band_count']} for r in records if r['kind'] == 'band'),
                   key=lambda r: (-r['count'], r['value']))
    result = [{key: r[key] for key in ('id', 'effective_gmv', 'rank', 'official', 'price_band', 'previous_rank', 'period_count')}
              for r in records if r['kind'] == 'page']
    result.sort(key=lambda r: (r['rank'] is None, r['rank'] if r['rank'] is not None else 2147483647,
                               -r['effective_gmv'], r['id']))
    return stats, bands, result
