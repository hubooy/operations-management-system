"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Scope = { warehouses: string[]; channels: string[]; platforms: string[] } | null;
type Role = { code: string; label: string; description: string; rank: number; permissions: string[]; version: number };
type User = { email: string; displayName: string; role: string; status: "active" | "disabled"; scope: Scope; version: number; updatedAt: string };
type Audit = { sequence: number; actorEmail: string; targetEmail: string; action: string; reason: string; occurredAt: string };

type FormState = {
  email: string;
  displayName: string;
  role: string;
  status: "active" | "disabled";
  unrestricted: boolean;
  warehouses: string;
  channels: string;
  platforms: string;
  reason: string;
  expectedVersion?: number;
};

const EMPTY_FORM: FormState = {
  email: "", displayName: "", role: "viewer", status: "active", unrestricted: true,
  warehouses: "", channels: "", platforms: "", reason: "",
};

function errorMessage(payload: unknown, fallback: string) {
  return payload && typeof payload === "object" && "error" in payload && typeof payload.error === "string"
    ? payload.error
    : fallback;
}

function listValue(value: string): string[] {
  return [...new Set(value.split(/[，,\n]/).map((item) => item.trim()).filter(Boolean))];
}

function fromUser(user: User): FormState {
  return {
    email: user.email,
    displayName: user.displayName,
    role: user.role,
    status: user.status,
    unrestricted: user.scope === null,
    warehouses: user.scope?.warehouses.join("，") ?? "",
    channels: user.scope?.channels.join("，") ?? "",
    platforms: user.scope?.platforms.join("，") ?? "",
    reason: "",
    expectedVersion: user.version,
  };
}

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

export default function AccessControlManagement({ canManage }: { canManage: boolean }) {
  const [roles, setRoles] = useState<Role[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [audits, setAudits] = useState<Audit[]>([]);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [mode, setMode] = useState<"create" | "edit">("create");
  const [query, setQuery] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const generation = useRef(0);
  const requestController = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    if (!canManage) return;
    const current = ++generation.current;
    setState("loading");
    setError("");
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    try {
      const [rolesResponse, usersResponse, auditsResponse] = await Promise.all([
        fetch("/api/access-control/roles", { cache: "no-store", signal: controller.signal }),
        fetch("/api/access-control/users?page=1&pageSize=100", { cache: "no-store", signal: controller.signal }),
        fetch("/api/access-control/audits?page=1&pageSize=30", { cache: "no-store", signal: controller.signal }),
      ]);
      const [rolesPayload, usersPayload, auditsPayload] = await Promise.all([
        rolesResponse.json().catch(() => null), usersResponse.json().catch(() => null), auditsResponse.json().catch(() => null),
      ]) as [unknown, unknown, unknown];
      if (!rolesResponse.ok || !usersResponse.ok || !auditsResponse.ok) {
        throw new Error(errorMessage(!rolesResponse.ok ? rolesPayload : !usersResponse.ok ? usersPayload : auditsPayload, "权限数据加载失败"));
      }
      if (current !== generation.current) return;
      const roleItems = (rolesPayload as { items?: unknown }).items;
      const userItems = (usersPayload as { items?: unknown }).items;
      const auditItems = (auditsPayload as { items?: unknown }).items;
      if (!Array.isArray(roleItems) || !Array.isArray(userItems) || !Array.isArray(auditItems)) throw new Error("权限服务返回格式无效");
      setRoles(roleItems as Role[]);
      setUsers(userItems as User[]);
      setAudits(auditItems as Audit[]);
      setState("ready");
    } catch (reason) {
      if (current !== generation.current || controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : "权限数据加载失败");
      setState("error");
    } finally {
      if (requestController.current === controller) requestController.current = null;
    }
  }, [canManage]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => { window.clearTimeout(timer); generation.current += 1; requestController.current?.abort(); };
  }, [load]);

  const visibleUsers = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return needle ? users.filter((user) => `${user.email}\n${user.displayName}`.toLowerCase().includes(needle)) : users;
  }, [query, users]);

  const submit = async () => {
    if (!canManage || saving) return;
    setSaving(true);
    setError("");
    setNotice("");
    const scope = form.unrestricted ? null : {
      warehouses: listValue(form.warehouses), channels: listValue(form.channels), platforms: listValue(form.platforms),
    };
    const payload: Record<string, unknown> = {
      email: form.email.trim().toLowerCase(), displayName: form.displayName.trim(), role: form.role,
      status: form.status, scope, reason: form.reason.trim(),
    };
    if (mode === "edit") payload.expectedVersion = form.expectedVersion;
    const url = mode === "create"
      ? "/api/access-control/users"
      : `/api/access-control/users/${encodeURIComponent(form.email.trim().toLowerCase())}`;
    try {
      const response = await fetch(url, {
        method: mode === "create" ? "POST" : "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result: unknown = await response.json().catch(() => null);
      if (!response.ok) throw new Error(errorMessage(result, "用户权限保存失败"));
      const saved = (result as { user?: User }).user;
      if (!saved) throw new Error("权限服务未返回已保存用户");
      setNotice(mode === "create" ? "用户已创建并写入权限审计。" : "用户角色、状态与数据范围已更新并写入权限审计。");
      setMode("edit");
      setForm(fromUser(saved));
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "用户权限保存失败");
    } finally {
      setSaving(false);
    }
  };

  if (!canManage) {
    return <section className="panel data-state data-state-error" role="alert">
      <span className="state-symbol" aria-hidden="true">!</span>
      <strong>需要无限制管理员权限</strong>
      <p>用户、角色、数据范围与权限审计仅允许数据范围不受限的管理员查看和修改。</p>
    </section>;
  }
  if (state === "loading" && users.length === 0) {
    return <section className="panel data-state" role="status"><span className="state-spinner" /><strong>正在读取权限权威数据</strong><p>从 Django/PostgreSQL 加载用户、角色与审计…</p></section>;
  }

  return <div className="access-control-layout">
    {(error || notice) && <section className={`inventory-feedback ${error ? "inventory-feedback-error" : "inventory-feedback-success"}`} role={error ? "alert" : "status"}>
      <span>{error ? "!" : "✓"}</span><div><strong>{error ? "处理失败" : "已完成"}</strong><p>{error || notice}</p></div>
    </section>}
    <section className="access-control-summary">
      {roles.map((role) => <article className="panel" key={role.code}>
        <small>{role.code}</small><strong>{role.label}</strong><p>{role.description}</p><em>{role.permissions.length} 项固定权限</em>
      </article>)}
    </section>
    <section className="settings-grid access-control-grid">
      <article className="panel settings-menu access-control-users">
        <div className="section-header"><div><h2>系统用户</h2><p>{users.length} 个已登记账号</p></div><button type="button" className="secondary-button" onClick={() => { setMode("create"); setForm(EMPTY_FORM); }}>新增用户</button></div>
        <input aria-label="搜索用户" placeholder="搜索邮箱或名称" value={query} onChange={(event) => setQuery(event.target.value)} />
        <div className="access-control-user-list">
          {visibleUsers.map((user) => <button type="button" key={user.email} className={mode === "edit" && form.email === user.email ? "active" : ""} onClick={() => { setMode("edit"); setForm(fromUser(user)); setNotice(""); setError(""); }}>
            <span>{user.role.slice(0, 1).toUpperCase()}</span><div><strong>{user.displayName}</strong><small>{user.email}</small></div><em>{user.status === "active" ? "启用" : "停用"}</em>
          </button>)}
        </div>
      </article>
      <article className="panel settings-form">
        <div className="section-header"><div><h2>{mode === "create" ? "新增系统用户" : "编辑用户权限"}</h2><p>角色、账号状态与数据范围在服务端统一生效</p></div></div>
        <div className="form-grid">
          <label><span>账号邮箱</span><input type="email" value={form.email} disabled={mode === "edit"} onChange={(event) => setForm((value) => ({ ...value, email: event.target.value }))} /></label>
          <label><span>显示名称</span><input value={form.displayName} onChange={(event) => setForm((value) => ({ ...value, displayName: event.target.value }))} /></label>
          <label><span>应用角色</span><select value={form.role} onChange={(event) => setForm((value) => ({ ...value, role: event.target.value }))}>{roles.map((role) => <option key={role.code} value={role.code}>{role.label}</option>)}</select></label>
          <label><span>账号状态</span><select value={form.status} onChange={(event) => setForm((value) => ({ ...value, status: event.target.value as FormState["status"] }))}><option value="active">启用</option><option value="disabled">停用</option></select></label>
        </div>
        <div className="form-section">
          <div className="toggle-row"><div><strong>不限制数据范围</strong><small>关闭后必须分别声明允许的仓库、渠道和平台；空数组表示该维度无权访问</small></div><button type="button" role="switch" aria-checked={form.unrestricted} className={`toggle ${form.unrestricted ? "on" : ""}`} onClick={() => setForm((value) => ({ ...value, unrestricted: !value.unrestricted }))}><i /></button></div>
          {!form.unrestricted && <div className="form-grid">
            <label><span>允许仓库</span><textarea rows={2} value={form.warehouses} placeholder="多个值用逗号分隔" onChange={(event) => setForm((value) => ({ ...value, warehouses: event.target.value }))} /></label>
            <label><span>允许渠道</span><textarea rows={2} value={form.channels} placeholder="多个值用逗号分隔" onChange={(event) => setForm((value) => ({ ...value, channels: event.target.value }))} /></label>
            <label><span>允许平台</span><textarea rows={2} value={form.platforms} placeholder="多个值用逗号分隔" onChange={(event) => setForm((value) => ({ ...value, platforms: event.target.value }))} /></label>
          </div>}
        </div>
        <label><span>变更原因</span><textarea rows={2} maxLength={200} required value={form.reason} placeholder="必填，1—200 字" onChange={(event) => setForm((value) => ({ ...value, reason: event.target.value }))} /></label>
        <footer className="form-actions"><span>{mode === "edit" ? `当前版本 ${form.expectedVersion ?? "—"}` : "保存后立即生效"}</span><button type="button" className="primary-button" disabled={saving || !form.email.trim() || !form.displayName.trim() || !form.reason.trim()} onClick={() => void submit()}>{saving ? "保存中…" : "保存并审计"}</button></footer>
      </article>
    </section>
    <section className="panel access-control-audits">
      <div className="section-header"><div><h2>权限审计</h2><p>最近 30 条追加式变更记录；审计不可从页面修改或删除</p></div><button type="button" className="secondary-button" disabled={state === "loading"} onClick={() => void load()}>刷新</button></div>
      <div className="access-control-audit-list">
        {audits.map((audit) => <article key={audit.sequence}><time>{formatTime(audit.occurredAt)}</time><strong>{audit.targetEmail || "权限域"}</strong><span>{audit.action}</span><small>{audit.actorEmail}{audit.reason ? ` · ${audit.reason}` : ""}</small></article>)}
        {audits.length === 0 && <p className="soft-text">暂无权限变更审计。</p>}
      </div>
    </section>
  </div>;
}
