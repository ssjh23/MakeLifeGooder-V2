/**
 * Screen 00: sign in.
 *
 * Password and OIDC lead to the same session cookie (see `app/api/routers/
 * auth.py`), so this screen offers both rather than picking one for the user.
 * The OIDC button is a plain navigation, not a fetch: `/auth/oidc/start`
 * answers with a redirect to Auth0, and a fetch would just follow that
 * redirect in the background and leave the user looking at a login form that
 * never moved.
 */

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "../../api/client";
import { useLogin } from "../../auth/mutations";

export function SignIn() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const login = useLogin();
  const navigate = useNavigate();

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await login.mutateAsync({ email, password });
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign in failed. Try again.");
    }
  }

  return (
    <section className="screen">
      <div className="auth-layout">
        <div className="auth-hero">
          <span className="brand">Ledger</span>
          <h1>Welcome back to your private ledger.</h1>
          <p>
            No bank connection. You upload the PDFs, Ledger reads them, and nothing
            leaves your account.
          </p>
          <p className="auth-hero-foot">Private by default · 256-bit encryption at rest</p>
        </div>

        <div className="auth-panel">
          <h1>Sign in</h1>

          <form className="form" onSubmit={handleSubmit}>
            <div className="field">
              <label htmlFor="email">Email</label>
              <input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="password">Password</label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </div>

            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}

            <div className="button-row">
              <button type="submit" className="button--primary" disabled={login.isPending}>
                {login.isPending ? "Signing in…" : "Sign in"}
              </button>
            </div>
          </form>

          <hr className="divider" />

          <a className="button" href="/api/v1/auth/oidc/start">
            Continue with Auth0
          </a>

          <p className="muted">
            <Link to="/forgot-password">Forgot your password?</Link>
          </p>
          <p className="muted">
            New here? <Link to="/sign-up">Create an account</Link>.
          </p>
        </div>
      </div>
    </section>
  );
}
