"""Minimal live-user metadata for owning analysis options and continuation."""
from psycopg import sql

ACTOR_COLUMNS = ("email", "role", "status", "scope", "version")


def grant_actor_read(cursor, role="teruisi_netshop_reader"):
    cursor.execute(sql.SQL("GRANT SELECT ({}) ON access_control_users TO {}").format(
        sql.SQL(",").join(map(sql.Identifier, ACTOR_COLUMNS)), sql.Identifier(role)))


def validate_actor_read(cursor):
    for column in ACTOR_COLUMNS:
        cursor.execute("SELECT has_column_privilege(current_user,'access_control_users',%s,'SELECT')", [column])
        if cursor.fetchone()[0] is not True:
            raise ValueError("netshop_analysis_actor_columns_missing")
