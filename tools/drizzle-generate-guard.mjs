const message = [
  "Blocked: ordinary Drizzle migration generation is disabled after the sales-domain retirement contract.",
  "Migration 0092_sales_domain_retirement.sql is operator-only; migrations 0093_finance_write_authority.sql, 0094_netshop_write_authority.sql, 0095_market_netshop_projection.sql, 0096_netshop_domain_retirement.sql, 0097_market_write_authority.sql, 0098_market_domain_retirement.sql, 0099_product_write_authority.sql, 0100_product_domain_retirement.sql, 0101_inventory_write_authority.sql, 0102_inventory_domain_retirement.sql, 0103_workflow_launch_write_authority.sql, 0104_workflow_launch_domain_retirement.sql, 0105_workflow_operations_write_authority.sql, 0106_workflow_operations_domain_retirement.sql, 0107_customer_service_write_authority.sql, 0108_customer_service_domain_retirement.sql, 0109_erp_reference_write_authority.sql, and 0110_erp_reference_domain_retirement.sql are also operator-only and intentionally excluded from the Drizzle journal.",
  "All new business schemas use Django migrations and PostgreSQL. Ordinary D1 generation remains permanently disabled; historical migrations are isolated audit evidence.",
].join(" ");

console.error(message);
process.exitCode = 1;
