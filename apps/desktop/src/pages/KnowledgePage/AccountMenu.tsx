import { useEffect, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";

import { api } from "../../api";
import type { KnowledgeUser } from "../../types";

export function AccountMenu() {
  const [account, setAccount] = useState<KnowledgeUser | null>(null);
  const [users, setUsers] = useState<KnowledgeUser[]>([]);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [resetTarget, setResetTarget] = useState<KnowledgeUser | null>(null);
  const [resetPasswordValue, setResetPasswordValue] = useState("");
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    void api.getAuthStatus().then((status) => setAccount(status.account || null));
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  async function show() {
    setOpen(true);
    setError("");
    if (account?.is_admin) {
      try { setUsers(await api.listKnowledgeUsers()); }
      catch (reason) { setError(reason instanceof Error ? reason.message : "加载账户失败"); }
    }
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try { await action(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败"); }
    finally { setBusy(false); }
  }

  async function createUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await run(async () => {
      await api.createKnowledgeUser(name.trim(), password);
      setUsers(await api.listKnowledgeUsers());
      setName("");
      setPassword("");
      setNotice("账户已创建。");
    });
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await run(async () => {
      await api.changePassword(oldPassword, newPassword);
      await api.logoutAccount();
      window.location.reload();
    });
  }

  async function resetPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const user = resetTarget;
    if (!user) return;
    await run(async () => {
      await api.resetKnowledgeUserPassword(user.user_id, resetPasswordValue);
      setResetPasswordValue("");
      setResetTarget(null);
      if (user.user_id === account?.user_id) {
        window.location.reload();
        return;
      }
      setUsers(await api.listKnowledgeUsers());
      setNotice(`已设置“${user.name}”的密码，原会话已失效。`);
    });
  }

  async function renameUser(user: KnowledgeUser) {
    const next = window.prompt("账户名称", user.name)?.trim();
    if (!next || next === user.name) return;
    await run(async () => {
      await api.renameKnowledgeUser(user.user_id, next);
      setUsers(await api.listKnowledgeUsers());
      if (user.user_id === account?.user_id) setAccount({ ...account, name: next });
    });
  }

  async function deleteUser(user: KnowledgeUser) {
    if (!window.confirm(`删除“${user.name}”的账户、知识库和问答记录？共享视频和文章仍会保留。`)) return;
    await run(async () => {
      await api.deleteKnowledgeUser(user.user_id);
      setUsers(await api.listKnowledgeUsers());
    });
  }

  return <div className="account-menu">
    <button className="account-trigger" type="button" onClick={() => void show()} title="账户与权限">
      <span className="account-avatar" aria-hidden="true">{account?.name.slice(0, 1) || "?"}</span>
      <span className="account-trigger-copy"><strong>{account?.name || "账户"}</strong><small>{account?.is_admin ? "管理员" : "个人知识库"}</small></span>
    </button>
    {open ? createPortal(<div className="account-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
      <section className="account-dialog" role="dialog" aria-modal="true" aria-label="账户管理">
        <header><div><h2>账户管理</h2><p>{account?.name} · {account?.is_admin ? "管理员" : "普通用户"}</p></div>
          <button type="button" className="secondary-button" onClick={() => setOpen(false)} aria-label="关闭">关闭</button></header>
        {error ? <p className="auth-error" role="alert">{error}</p> : null}
        {notice ? <p className="account-notice" role="status">{notice}</p> : null}
        <form className="account-form" onSubmit={(event) => void changePassword(event)}>
          <h3>修改我的密码</h3>
          <input className="sr-only" tabIndex={-1} autoComplete="username" value={account?.name || ""} readOnly aria-hidden="true" />
          <label>当前密码<input type="password" autoComplete="current-password" value={oldPassword} onChange={(event) => setOldPassword(event.target.value)} required /></label>
          <label>新密码<input type="password" autoComplete="new-password" minLength={8} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required /></label>
          <button type="submit" className="secondary-button" disabled={busy}>修改密码</button>
        </form>
        {account?.is_admin ? <div className="account-admin">
          <h3>成员账户</h3>
          <div className="account-list">{users.map((user) => <div className="account-row" key={user.user_id}>
            <div><strong>{user.name}</strong><small>{user.is_default ? "管理员" : user.has_login ? "可登录" : "未设置密码"}</small></div>
            <button type="button" className="secondary-button" disabled={busy} onClick={() => void renameUser(user)}>重命名</button>
            <button type="button" className="secondary-button" disabled={busy} onClick={() => { setResetTarget(user); setResetPasswordValue(""); }}>设置密码</button>
            {!user.is_default ? <button type="button" className="secondary-button" disabled={busy} onClick={() => void deleteUser(user)}>删除</button> : null}
          </div>)}</div>
          {resetTarget ? <form className="account-form" onSubmit={(event) => void resetPassword(event)}>
            <h3>设置“{resetTarget.name}”的密码</h3>
            <label>新密码<input type="password" autoComplete="new-password" minLength={8} maxLength={256}
              required autoFocus value={resetPasswordValue} onChange={(event) => setResetPasswordValue(event.target.value)} /></label>
            <div className="account-form-actions">
              <button type="submit" className="primary-button" disabled={busy}>保存密码</button>
              <button type="button" className="secondary-button" onClick={() => setResetTarget(null)}>取消</button>
            </div>
          </form> : null}
          <form className="account-form" onSubmit={(event) => void createUser(event)}>
            <h3>添加成员</h3>
            <label>用户名<input value={name} maxLength={60} required onChange={(event) => setName(event.target.value)} /></label>
            <label>初始密码<input type="password" minLength={8} maxLength={256} value={password} required autoComplete="new-password" onChange={(event) => setPassword(event.target.value)} /></label>
            <button type="submit" className="primary-button" disabled={busy}>创建账户</button>
          </form>
        </div> : null}
        <footer><button type="button" className="secondary-button" disabled={busy} onClick={() => void run(async () => { await api.logoutAccount(); window.location.reload(); })}>退出登录</button></footer>
      </section>
    </div>, document.body) : null}
  </div>;
}
