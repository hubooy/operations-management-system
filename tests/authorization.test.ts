import assert from "node:assert/strict";
import test from "node:test";

import {
  decideLocalDirectAccess,
  isLoopbackRequestHost,
} from "../lib/auth/local-direct-access";

const allRoles = ["viewer", "analyst", "operator", "admin"];

test("local direct access stays disabled in production despite explicit opt-in", () => {
  assert.equal(
    decideLocalDirectAccess(allRoles, {
      enabled: "true",
      runtimeEnvironment: "production",
      viteDevelopment: false,
      viteProduction: true,
    }),
    "disabled",
  );

  assert.equal(
    decideLocalDirectAccess(allRoles, {
      enabled: "true",
      runtimeEnvironment: "development",
      viteDevelopment: false,
      viteProduction: true,
      nodeEnvironment: "development",
    }),
    "disabled",
  );

  assert.equal(
    decideLocalDirectAccess(allRoles, {
      enabled: "true",
      runtimeEnvironment: "development",
      viteDevelopment: false,
      viteProduction: true,
    }),
    "disabled",
  );

  assert.equal(
    decideLocalDirectAccess(allRoles, {
      enabled: "true",
      runtimeEnvironment: "production",
      viteDevelopment: false,
      viteProduction: true,
      localBuild: true,
    }),
    "disabled",
  );
});

test("local direct access requires explicit opt-in and verified development", () => {
  assert.equal(
    decideLocalDirectAccess(allRoles, {
      enabled: "true",
      runtimeEnvironment: "development",
      viteDevelopment: true,
      viteProduction: false,
    }),
    "allowed",
  );

  for (const enabled of [undefined, "false", "1", "yes"]) {
    assert.equal(
      decideLocalDirectAccess(allRoles, {
        enabled,
        runtimeEnvironment: "development",
        viteDevelopment: true,
        viteProduction: false,
      }),
      "disabled",
    );
  }
});

test("Vinext server development mode is a verified local runtime signal", () => {
  assert.equal(
    decideLocalDirectAccess(allRoles, {
      enabled: "true",
      runtimeEnvironment: "development",
      viteDevelopment: false,
      viteProduction: false,
      nodeEnvironment: "development",
    }),
    "allowed",
  );

  assert.equal(
    decideLocalDirectAccess(allRoles, {
      enabled: "true",
      runtimeEnvironment: "development",
      viteDevelopment: false,
      viteProduction: true,
      nodeEnvironment: "production",
    }),
    "disabled",
  );
});

test("a loopback Host header is never accepted as development evidence", () => {
  assert.equal(
    decideLocalDirectAccess(allRoles, {
      enabled: "true",
      runtimeEnvironment: "development",
      viteDevelopment: false,
      viteProduction: true,
      nodeEnvironment: "production",
    }),
    "disabled",
  );

  assert.equal(
    decideLocalDirectAccess(allRoles, {
      enabled: "true",
      runtimeEnvironment: "production",
      viteDevelopment: false,
      viteProduction: true,
      nodeEnvironment: "production",
    }),
    "disabled",
  );
});

test("local direct access accepts only exact loopback request hosts", () => {
  for (const host of ["127.0.0.1", "127.0.0.1:3000", "localhost", "LOCALHOST:3000", "[::1]:3000"]) {
    assert.equal(isLoopbackRequestHost(host), true, host);
  }
  for (const host of [undefined, "", "0.0.0.0:3000", "192.168.1.10", "attacker.example", "foo.localhost", "localhost.evil.example", "127.0.0.1.evil.example", "user@localhost", "localhost/path"]) {
    assert.equal(isLoopbackRequestHost(host), false, String(host));
  }
});

test("local admin cannot bypass the caller's allowed roles", () => {
  assert.equal(
    decideLocalDirectAccess(["viewer", "analyst"], {
      enabled: "true",
      runtimeEnvironment: "development",
      viteDevelopment: true,
      viteProduction: false,
    }),
    "role_denied",
  );
});

test("an explicitly stamped local worker build permits direct access", () => {
  assert.equal(
    decideLocalDirectAccess(allRoles, {
      enabled: "true",
      runtimeEnvironment: "development",
      viteDevelopment: false,
      viteProduction: true,
      localBuild: true,
    }),
    "allowed",
  );
});
