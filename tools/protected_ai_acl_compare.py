"""Pure restored-catalog comparison: ignore representation, never authority."""


def restored_equal(left, right):
    """Compare independent restore inventories with exact effective ACLs.

    A restore allocates new function OIDs and can materialize implicit owner
    ACLs. Only those two representations are ignored. Same-cluster freeze uses
    raw tuple equality instead of this function.
    """
    def functions_without_oid(snapshot):
        return [(None, *row[1:]) for row in snapshot[0]]

    def normalized_tables(snapshot):
        return [(name, kind, owner_name, table_acl,
            tuple((attnum, attname) for attnum, attname, _ in columns),
            column_acl, table_effective, column_effective)
            for (name, kind, owner_name, _raw_acl, table_acl, columns,
                column_acl, table_effective, column_effective) in snapshot[1]]

    return (functions_without_oid(left) == functions_without_oid(right)
        and normalized_tables(left) == normalized_tables(right)
        and left[2:] == right[2:])
