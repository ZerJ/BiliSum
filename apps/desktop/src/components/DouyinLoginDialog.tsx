import { useEffect, useRef, useState, type ChangeEvent } from "react";

import { api, type BilibiliCookieCaptureResponse } from "../api";

type DouyinLoginDialogProps = {
  isOpen: boolean;
  onClose(): void;
  onCookiesCaptured?: (cookies: BilibiliCookieCaptureResponse) => Promise<void>;
};

export function DouyinLoginDialog({ isOpen, onClose, onCookiesCaptured }: DouyinLoginDialogProps) {
  const [content, setContent] = useState("");
  const [fileName, setFileName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const attempt = useRef(0);

  useEffect(() => {
    if (!isOpen) {
      setContent("");
      setFileName("");
      setBusy(false);
      setError("");
    }
    return () => { attempt.current += 1; };
  }, [isOpen]);

  async function readCookieFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    const id = ++attempt.current;
    setError("");
    setContent("");
    setFileName("");
    if (file.size > 1024 * 1024) {
      setError("Cookies 文件不能超过 1 MB。");
      return;
    }
    setBusy(true);
    try {
      const text = await file.text();
      if (id !== attempt.current) return;
      setContent(text);
      setFileName(file.name);
    } catch {
      if (id === attempt.current) setError("无法读取文件，请重新选择 UTF-8 格式的 cookies.txt。");
    } finally {
      if (id === attempt.current) setBusy(false);
    }
  }

  async function saveCookies() {
    if (!onCookiesCaptured || busy || !content.trim()) return;
    const id = ++attempt.current;
    setBusy(true);
    setError("");
    try {
      const captured = await api.importDouyinCookies(content);
      if (id !== attempt.current) return;
      await onCookiesCaptured(captured);
      if (id === attempt.current) onClose();
    } catch (failure) {
      if (id === attempt.current) setError(failure instanceof Error ? failure.message : "导入登录态失败，请重试。");
    } finally {
      if (id === attempt.current) setBusy(false);
    }
  }

  if (!isOpen) return null;
  const close = () => { if (!busy) onClose(); };
  return (
    <div className="update-dialog-overlay" onClick={close}>
      <div className="update-dialog cookie-help-dialog" role="dialog" aria-modal="true" aria-label="抖音网页登录" onClick={(event) => event.stopPropagation()}>
        <div className="update-dialog-header">
          <h2>抖音网页登录</h2>
          <button className="close-button" type="button" aria-label="关闭" disabled={busy} onClick={close}>×</button>
        </div>
        <div className="update-dialog-body cookie-help-body">
          <div className="cookie-help-hero">
            <strong>登录抖音后导入 Cookies</strong>
            <p>先在抖音官网完成扫码或验证码登录，再导入登录态。保存后当前 BiliSum 服务即可使用，两站登录态会自动合并。</p>
            <a className="secondary-button" href="https://www.douyin.com/" target="_blank" rel="noopener noreferrer">打开抖音登录页</a>
          </div>
          {onCookiesCaptured ? <>
            <label className="cookie-import-field">
              <strong>上传 cookies.txt</strong>
              <input type="file" accept=".txt,text/plain" disabled={busy} onChange={(event) => void readCookieFile(event)} />
              {fileName ? <span>已选择：{fileName}</span> : null}
              <span>选择从浏览器导出的 Netscape 格式文件，无需填写服务器文件路径。</span>
            </label>
            <label className="cookie-import-field">
              <strong>或粘贴 Cookie 请求头</strong>
              <textarea className="textarea-field" rows={5} value={fileName ? "" : content} disabled={busy} autoComplete="off" spellCheck={false}
                placeholder="sessionid=…; sid_tt=…"
                onChange={(event) => { attempt.current += 1; setFileName(""); setContent(event.target.value); setError(""); }} />
            </label>
            <details className="cookie-import-help">
              <summary>如何获取登录态？</summary>
              <p>方式一：在抖音官网登录后，使用浏览器的 Cookies 导出工具，将 douyin.com 的 Cookies 导出为 Netscape cookies.txt。</p>
              <p>方式二：在抖音页面按 F12 打开开发者工具，在“网络 / Network”中刷新页面，选择发往 www.douyin.com 的请求，复制“请求标头 / Request Headers”里的 Cookie 值。</p>
            </details>
          </> : <p>请联系管理员登录并导入抖音 Cookies。</p>}
          {error ? <p className="cookie-import-error" role="alert">{error}</p> : null}
        </div>
        <div className="update-dialog-footer">
          <button className="secondary-button" type="button" disabled={busy} onClick={close}>取消</button>
          {onCookiesCaptured ? <button className="primary-button" type="button" disabled={busy || !content.trim()} onClick={() => void saveCookies()}>{busy ? "处理中…" : "保存抖音登录态"}</button> : null}
        </div>
      </div>
    </div>
  );
}
