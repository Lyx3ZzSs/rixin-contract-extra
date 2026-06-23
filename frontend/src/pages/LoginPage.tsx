import { FormEvent, useState } from "react";
import type { CSSProperties } from "react";

import loginBackground from "../picture/登录背景图.png";

interface LoginPageProps {
  onLogin: (apiKey: string) => Promise<boolean>;
}

export function LoginPage({ onLogin }: LoginPageProps) {
  const [apiKey, setApiKeyState] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const ok = await onLogin(apiKey.trim());
    if (!ok) {
      setError("API Key 无效，请检查后重试。");
    }
    setBusy(false);
  }

  return (
    <main className="login-page" style={{ "--login-bg": `url("${loginBackground}")` } as CSSProperties}>
      <section className="login-slogan" aria-label="登录页标语">
        <h1>合同规范管理  合作高效共赢</h1>
        <p>严控风险.提升效率.保障合规.驱动价值</p>
      </section>

      <section className="login-card" aria-labelledby="login-title">
        <div className="login-card-header">
          <p className="eyebrow">账户登录</p>
          <h2 id="login-title">欢迎回来</h2>
        </div>
        <form onSubmit={handleSubmit}>
          <label className="field-row">
            <span>API Key</span>
            <input
              autoComplete="off"
              autoFocus
              name="apiKey"
              placeholder="请输入 API Key"
              type="password"
              value={apiKey}
              disabled={busy}
              onChange={(event) => {
                setApiKeyState(event.target.value);
                setError("");
              }}
            />
          </label>
          <button className="primary-action login-action" type="submit" disabled={busy || !apiKey.trim()}>
            {busy ? "验证中…" : "登录系统"}
          </button>
          <p className={error ? "status-line error" : "status-line"} role="status">
            {error || " "}
          </p>
        </form>
      </section>
    </main>
  );
}
