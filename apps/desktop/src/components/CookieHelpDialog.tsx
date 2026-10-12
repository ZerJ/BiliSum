import { useEffect, useRef, useState } from "react";
import QRCode from "qrcode";

import { api, type BilibiliCookieCaptureResponse } from "../api";
import { idleBilibiliQrcodeState, runBilibiliQrcodeLogin } from "../bilibiliQrcodeLogin";

type CookieHelpDialogProps = {
  isOpen: boolean;
  onClose(): void;
  onOpenTutorial(): void;
  onOpenSettings(): void;
  onCaptureLoginCookies?: () => void | Promise<void>;
  onCookiesCaptured?: (cookies: BilibiliCookieCaptureResponse) => Promise<void>;
};

export function CookieHelpDialog({
  isOpen,
  onClose,
  onOpenTutorial,
  onOpenSettings,
  onCaptureLoginCookies,
  onCookiesCaptured,
}: CookieHelpDialogProps) {
  const [qrcode, setQrcode] = useState(idleBilibiliQrcodeState);
  const loginController = useRef<AbortController | null>(null);
  const isDesktop = Boolean(window.desktop?.bilibili);
  const loginLabel = isDesktop ? "打开 B 站登录窗口" : "B 站扫码登录";
  const busy = ["loading", "pending", "scanned", "saving"].includes(qrcode.status);

  useEffect(() => {
    if (!isOpen) setQrcode(idleBilibiliQrcodeState);
    return () => loginController.current?.abort();
  }, [isOpen]);

  function startWebLogin() {
    if (!onCookiesCaptured || busy) return;
    loginController.current?.abort();
    const controller = new AbortController();
    loginController.current = controller;
    void runBilibiliQrcodeLogin({
      signal: controller.signal,
      createQrcode: () => api.createBilibiliCookieQrcode(),
      pollQrcode: (key) => api.pollBilibiliCookieQrcode(key),
      renderQrcode: (url) => QRCode.toDataURL(url, { margin: 1, width: 200 }),
      onChange: setQrcode,
      onConfirmed: onCookiesCaptured,
    });
  }

  if (!isOpen) {
    return null;
  }

  return (
    <div className="update-dialog-overlay" onClick={onClose}>
      <div className="update-dialog cookie-help-dialog" role="dialog" aria-modal="true" aria-label="B 站需要登录态" onClick={(event) => event.stopPropagation()}>
        <div className="update-dialog-header">
          <h2>B 站需要登录态</h2>
          <button className="close-button" type="button" onClick={onClose} aria-label="关闭">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <path d="M2 2L12 12M12 2L2 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        <div className="update-dialog-body cookie-help-body">
          <div className="cookie-help-hero">
            <span className="section-kicker">HTTP 412</span>
            <strong>当前请求可能被 B 站风控拦截</strong>
            <p>{isDesktop ? "打开 BiliSum 自带的 B 站登录窗口，登录完成后会自动保存登录态。" : "使用手机 B 站扫码登录，确认后会自动将登录态保存到当前 BiliSum 服务。"}</p>
          </div>

          {!isDesktop && qrcode.status !== "idle" ? (
            <div className="cookie-help-qrcode" aria-busy={qrcode.status === "loading" || qrcode.status === "saving"}>
              {qrcode.image ? <img src={qrcode.image} alt="B 站扫码登录二维码" width="200" height="200" /> : null}
              <p role="status" aria-live="polite">{qrcode.message}</p>
            </div>
          ) : null}
          {!isDesktop && !onCookiesCaptured ? <p role="status">请联系管理员扫码登录并配置 B 站 Cookies。</p> : null}

          <div className="cookie-help-summary">
            <strong>处理方法汇总</strong>
            <p>1. 点击“{loginLabel}”，{isDesktop ? "在新窗口完成扫码或账号登录。" : "用手机 B 站扫描二维码并确认登录。"}</p>
            <p>2. 登录成功后应用会自动导出 B 站 cookies.txt，并写入设置。</p>
            <p>3. 重新提交任务；如果还是 412，切换网络/IP 或稍后重试。</p>
            <p>4. 自动捕获失败时，再按教程手动导出 Netscape/Mozilla 格式 cookies.txt。</p>
          </div>

          <div className="cookie-help-steps">
            <div className="cookie-help-step">
              <span>1</span>
              <div>
                <strong>{isDesktop ? "先在浏览器登录 B 站" : "用手机 B 站扫码"}</strong>
                <p>{isDesktop ? "使用 BiliSum 打开的登录窗口，不再读取 Chrome/Edge/Firefox 的本机数据库。" : "直接扫描弹窗中的二维码，无需打开其他网页或手动导出 Cookies。"}</p>
              </div>
            </div>
            <div className="cookie-help-step">
              <span>2</span>
              <div>
                <strong>自动捕获 cookies.txt</strong>
                <p>确认登录后，登录态会保存为当前服务可用的 Netscape cookies 文件。</p>
              </div>
            </div>
            <div className="cookie-help-step">
              <span>3</span>
              <div>
                <strong>重新提交任务</strong>
                <p>保存成功后重新提交视频链接；如果仍然 412，换网络或稍后重试。</p>
              </div>
            </div>
          </div>
        </div>

        <div className="update-dialog-footer">
          {(isDesktop ? onCaptureLoginCookies : onCookiesCaptured) ? (
            <button className="primary-button" type="button" disabled={busy} onClick={isDesktop ? onCaptureLoginCookies : startWebLogin}>
              {busy ? "登录处理中…" : qrcode.status === "expired" || qrcode.status === "error" ? "重新获取二维码" : loginLabel}
            </button>
          ) : null}
          <button className="secondary-button" type="button" onClick={onOpenTutorial}>打开导出教程</button>
          <button className="secondary-button" type="button" onClick={onOpenSettings}>前往 Cookies 设置</button>
        </div>
      </div>
    </div>
  );
}
