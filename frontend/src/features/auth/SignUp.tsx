/**
 * Screen 01: create an account.
 *
 * The twelve-character floor is enforced server-side (TC-AUTH-002, 003); the
 * `minLength` here is a courtesy that saves a round trip, not the rule.
 */

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "../../api/client";
import { useRegister } from "../../auth/mutations";

export function SignUp() {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const register = useRegister();
  const navigate = useNavigate();

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await register.mutateAsync({ email, password, name: name || undefined });
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the account.");
    }
  }

  return (
    <section className="screen">
      <div className="auth-layout">
        <div className="auth-hero">
          <span className="brand">Ledger</span>
          <h1>One account, one private ledger.</h1>
          <p>
            No bank connection. You upload the PDFs, Ledger reads them, and nothing
            leaves your account.
          </p>
          <p className="auth-hero-foot">Private by default · 256-bit encryption at rest</p>
        </div>

        <div className="auth-panel">
          <h1>Create your account</h1>

          <form className="form" onSubmit={handleSubmit}>
            <div className="field">
              <label htmlFor="name">Name (optional)</label>
              <input id="name" value={name} onChange={(event) => setName(event.target.value)} />
            </div>
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
                autoComplete="new-password"
                required
                minLength={12}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
              <p className="field-hint">At least 12 characters. Length beats character rules.</p>
            </div>

            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}

            <div className="button-row">
              <button type="submit" className="button--primary" disabled={register.isPending}>
                {register.isPending ? "Creating…" : "Create account"}
              </button>
            </div>
          </form>

          <hr className="divider" />

          <a className="button" href="/api/v1/auth/oidc/start">
            Continue with Auth0
          </a>

          <p className="muted">
            Already have an account? <Link to="/sign-in">Sign in</Link>.
          </p>
        </div>
      </div>
    </section>
  );
}
