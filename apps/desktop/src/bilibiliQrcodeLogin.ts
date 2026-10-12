import type { BilibiliCookieCaptureResponse, BilibiliQrcodeLoginResponse, BilibiliQrcodePollResponse } from "./api";

export type BilibiliQrcodeState = {
  status: "idle" | "loading" | "pending" | "scanned" | "saving" | "confirmed" | "expired" | "error";
  image: string;
  message: string;
};

export const idleBilibiliQrcodeState: BilibiliQrcodeState = { status: "idle", image: "", message: "" };

function waitForPoll(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    };
    const timer = setTimeout(finish, ms);
    signal.addEventListener("abort", finish, { once: true });
  });
}

export async function runBilibiliQrcodeLogin(options: {
  signal: AbortSignal;
  createQrcode(): Promise<BilibiliQrcodeLoginResponse>;
  pollQrcode(key: string): Promise<BilibiliQrcodePollResponse>;
  renderQrcode(url: string): Promise<string>;
  onChange(state: BilibiliQrcodeState): void;
  onConfirmed(cookies: BilibiliCookieCaptureResponse): Promise<void>;
  wait?: (ms: number, signal: AbortSignal) => Promise<void>;
}): Promise<void> {
  const { signal, onChange } = options;
  const wait = options.wait || waitForPoll;
  if (signal.aborted) return;
  onChange({ status: "loading", image: "", message: "正在生成 B 站登录二维码…" });
  try {
    const login = await options.createQrcode();
    if (signal.aborted) return;
    const expiresAt = Date.now() + login.expiresIn * 1000;
    const image = await options.renderQrcode(login.url);
    if (signal.aborted) return;
    onChange({ status: "pending", image, message: "请用手机 B 站扫码，并在手机上确认登录。" });
    while (!signal.aborted) {
      await wait(2000, signal);
      if (signal.aborted) return;
      if (Date.now() >= expiresAt) {
        onChange({ status: "expired", image: "", message: "二维码已过期，请重新获取。" });
        return;
      }
      const result = await options.pollQrcode(login.qrcodeKey);
      if (signal.aborted) return;
      if (result.status === "expired") {
        onChange({ status: "expired", image: "", message: result.message || "二维码已过期，请重新获取。" });
        return;
      }
      if (result.status === "confirmed") {
        if (!result.cookiesFile) throw new Error("登录已确认，但服务未返回 Cookies 文件，请重新登录。");
        onChange({ status: "saving", image: "", message: "登录已确认，正在保存登录态…" });
        await options.onConfirmed({ cookiesFile: result.cookiesFile, cookieCount: result.cookieCount || 0 });
        if (!signal.aborted) onChange({ status: "confirmed", image: "", message: "B 站登录态已保存，可以重新提交视频。" });
        return;
      }
      onChange({
        status: result.status,
        image,
        message: result.status === "scanned" ? "已扫码，请在手机 B 站上确认登录。" : "等待手机 B 站扫码。",
      });
    }
  } catch (error) {
    if (!signal.aborted) {
      onChange({ status: "error", image: "", message: error instanceof Error ? error.message : "扫码登录失败，请重试。" });
    }
  }
}
