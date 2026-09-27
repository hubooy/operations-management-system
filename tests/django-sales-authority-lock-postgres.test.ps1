[CmdletBinding()]
param(
  [string]$RuntimeRoot = "D:\teruisi-runtime\django-sales"
)

$ErrorActionPreference = "Stop"
$PostgresBin = Join-Path $RuntimeRoot "postgresql-17.11\bin"
$PostgresData = Join-Path $RuntimeRoot "postgres-data"
$Python = Join-Path $RuntimeRoot "venv\Scripts\python.exe"
$CredentialPath = Join-Path $RuntimeRoot "secrets\credentials.dpapi.json"
$LogDirectory = Join-Path $RuntimeRoot "logs"
$PgCtl = Join-Path $PostgresBin "pg_ctl.exe"
$DatabaseName = "teruisi_sales_locktest_{0}" -f ([Guid]::NewGuid().ToString("N").Substring(0, 12))
$StartedPostgres = $false

foreach ($required in @($PgCtl, $Python, $CredentialPath, $PostgresData)) {
  if (-not (Test-Path -LiteralPath $required)) {
    throw "authority lock PostgreSQL test prerequisite missing"
  }
}
if ($DatabaseName -notmatch '^teruisi_sales_locktest_[0-9a-f]{12}$') {
  throw "authority lock PostgreSQL test database identity invalid"
}

function Unprotect-TestValue([string]$ProtectedValue) {
  if ([string]::IsNullOrWhiteSpace($ProtectedValue)) {
    throw "authority lock PostgreSQL test credential missing"
  }
  $secure = ConvertTo-SecureString $ProtectedValue
  $credential = [System.Management.Automation.PSCredential]::new("local", $secure)
  return $credential.GetNetworkCredential().Password
}

try {
  $statusProcess = Start-Process -FilePath $PgCtl -ArgumentList @("status", "-D", $PostgresData) -WindowStyle Hidden -PassThru
  $statusProcess.WaitForExit()
  if ($statusProcess.ExitCode -ne 0) {
    if (-not (Test-Path -LiteralPath $LogDirectory -PathType Container)) {
      throw "authority lock PostgreSQL test log directory missing"
    }
    $startLog = Join-Path $LogDirectory ("authority-lock-test-{0}.log" -f ([Guid]::NewGuid().ToString("N").Substring(0, 12)))
    $startProcess = Start-Process -FilePath $PgCtl -ArgumentList @("start", "-D", $PostgresData, "-w", "-l", $startLog) -WindowStyle Hidden -PassThru
    $startProcess.WaitForExit()
    if ($startProcess.ExitCode -ne 0) {
      throw "authority lock PostgreSQL test could not start managed PostgreSQL"
    }
    $StartedPostgres = $true
  }

  $secrets = Get-Content -Raw -LiteralPath $CredentialPath -Encoding UTF8 | ConvertFrom-Json
  $env:TERUISI_LOCK_TEST_ADMIN_PASSWORD = Unprotect-TestValue ([string]$secrets.postgresSuperuser)
  $env:TERUISI_LOCK_TEST_WRITER_PASSWORD = Unprotect-TestValue ([string]$secrets.databaseWriter)
  $env:TERUISI_LOCK_TEST_DATABASE = $DatabaseName

  $pythonCode = @'
import json
import os
import re

import psycopg
from psycopg import sql


LOCK_KEY = -8847588757640662873
database = os.environ["TERUISI_LOCK_TEST_DATABASE"]
if not re.fullmatch(r"teruisi_sales_locktest_[0-9a-f]{12}", database):
    raise RuntimeError("invalid test database identity")

admin_password = os.environ["TERUISI_LOCK_TEST_ADMIN_PASSWORD"]
writer_password = os.environ["TERUISI_LOCK_TEST_WRITER_PASSWORD"]
base = {"host": "127.0.0.1", "port": 5432}
created = False
connections = []


def connect_admin(dbname: str, *, autocommit: bool = False):
    return psycopg.connect(
        **base,
        dbname=dbname,
        user="postgres",
        password=admin_password,
        autocommit=autocommit,
    )


def connect_writer():
    return psycopg.connect(
        **base,
        dbname=database,
        user="teruisi_sales_writer",
        password=writer_password,
    )


def expect_sqlstate(connection, statement: str, state: str) -> None:
    try:
        connection.execute(statement)
    except psycopg.Error as error:
        if error.sqlstate != state:
            raise AssertionError(f"unexpected SQLSTATE {error.sqlstate}") from error
        connection.rollback()
        return
    raise AssertionError(f"statement unexpectedly succeeded; expected SQLSTATE {state}")


try:
    with connect_admin("postgres", autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    created = True

    with connect_admin(database, autocommit=True) as admin:
        admin.execute(
            "CREATE TABLE sales_write_authority ("
            "id bigint PRIMARY KEY, status text NOT NULL, authority_epoch uuid NOT NULL, "
            "cutover_id text NOT NULL)"
        )
        admin.execute(
            "CREATE TABLE sales_cutover_attestations ("
            "cutover_id text PRIMARY KEY, payload_sha256 text NOT NULL)"
        )
        admin.execute(
            "INSERT INTO sales_write_authority VALUES "
            "(1, 'active', '11111111-1111-4111-8111-111111111111', 'lock-test-cutover')"
        )
        admin.execute(
            "INSERT INTO sales_cutover_attestations VALUES "
            "('lock-test-cutover', repeat('a', 64))"
        )
        admin.execute("GRANT USAGE ON SCHEMA public TO teruisi_sales_writer")
        admin.execute(
            "GRANT SELECT ON sales_write_authority, sales_cutover_attestations "
            "TO teruisi_sales_writer"
        )
        for table in ("sales_write_authority", "sales_cutover_attestations"):
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                allowed = admin.execute(
                    "SELECT has_table_privilege(%s, %s, %s)",
                    ("teruisi_sales_writer", table, privilege),
                ).fetchone()[0]
                if allowed:
                    raise AssertionError("writer has protected table write privilege")
            for privilege in ("INSERT", "UPDATE"):
                allowed = admin.execute(
                    "SELECT has_any_column_privilege(%s, %s, %s)",
                    ("teruisi_sales_writer", table, privilege),
                ).fetchone()[0]
                if allowed:
                    raise AssertionError("writer has protected column write privilege")

    writer_one = connect_writer()
    writer_two = connect_writer()
    owner = connect_admin(database)
    blocked_writer = connect_writer()
    connections.extend((writer_one, writer_two, owner, blocked_writer))

    writer_one.execute("SELECT pg_advisory_xact_lock_shared(%s)", (LOCK_KEY,))
    writer_one.execute("SELECT status FROM sales_write_authority WHERE id = 1").fetchone()
    writer_one.execute(
        "SELECT payload_sha256 FROM sales_cutover_attestations "
        "WHERE cutover_id = 'lock-test-cutover'"
    ).fetchone()

    writer_two.execute("SET LOCAL lock_timeout = '250ms'")
    writer_two.execute("SELECT pg_advisory_xact_lock_shared(%s)", (LOCK_KEY,))

    owner.execute("SET LOCAL lock_timeout = '250ms'")
    expect_sqlstate(
        owner,
        f"SELECT pg_advisory_xact_lock({LOCK_KEY})",
        "55P03",
    )

    expect_sqlstate(
        blocked_writer,
        "SELECT * FROM sales_write_authority WHERE id = 1 FOR UPDATE",
        "42501",
    )
    expect_sqlstate(
        blocked_writer,
        "SELECT * FROM sales_cutover_attestations "
        "WHERE cutover_id = 'lock-test-cutover' FOR UPDATE",
        "42501",
    )
    expect_sqlstate(
        blocked_writer,
        "UPDATE sales_write_authority SET status = 'disabled' WHERE id = 1",
        "42501",
    )

    writer_two.commit()
    writer_one.commit()
    owner.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))

    blocked_writer.execute("SET LOCAL lock_timeout = '250ms'")
    expect_sqlstate(
        blocked_writer,
        f"SELECT pg_advisory_xact_lock_shared({LOCK_KEY})",
        "55P03",
    )
    owner.commit()

    blocked_writer.execute("SELECT pg_advisory_xact_lock_shared(%s)", (LOCK_KEY,))
    blocked_writer.rollback()
    owner.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
    owner.rollback()

    print(json.dumps({
        "status": "passed",
        "writerProtectedTables": 2,
        "sharedConcurrency": True,
        "exclusiveFence": True,
        "rollbackRelease": True,
    }, separators=(",", ":")))
finally:
    for candidate in connections:
        try:
            candidate.close()
        except Exception:
            pass
    if created:
        with connect_admin("postgres", autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database,),
            )
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
'@

  # Windows PowerShell 5.1 rewrites embedded quotes when a multiline script is
  # passed as a native-process argument. Feed the source through stdin so the
  # Python code reaches the interpreter byte-for-byte without exposing secrets
  # on the process command line.
  $pythonCode | & $Python -c "import sys; exec(sys.stdin.read())"
  if ($LASTEXITCODE -ne 0) {
    throw "authority lock PostgreSQL integration test failed"
  }
} finally {
  Remove-Item Env:TERUISI_LOCK_TEST_ADMIN_PASSWORD -ErrorAction SilentlyContinue
  Remove-Item Env:TERUISI_LOCK_TEST_WRITER_PASSWORD -ErrorAction SilentlyContinue
  Remove-Item Env:TERUISI_LOCK_TEST_DATABASE -ErrorAction SilentlyContinue
  if ($StartedPostgres) {
    $stopProcess = Start-Process -FilePath $PgCtl -ArgumentList @("stop", "-D", $PostgresData, "-m", "fast", "-w") -WindowStyle Hidden -PassThru
    $stopProcess.WaitForExit()
    if ($stopProcess.ExitCode -ne 0) {
      throw "authority lock PostgreSQL test could not stop managed PostgreSQL"
    }
  }
}
