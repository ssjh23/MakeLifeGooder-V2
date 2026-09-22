/**
 * Password reset request and confirm, in one screen.
 *
 * The request half always answers the same way regardless of whether the
 * address is registered (TC-AUTH-007), so the UI shows one fixed message
 * rather than branching on the response — branching here would leak exactly
 * the thing the endpoint's timing and body were designed to hide.
 *
 * The confirm half activates once a `token` query parameter is present,
 * which is how the emailed link would arrive. There is no screen for sending
 * that email (delivery is unwritten, per the build order), so this is reached
 * by pasting a token by hand until that lands.
 */

import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError } from "../../api/client";
import { useConfirmPasswordReset, useRequestPasswordReset } from "../../auth/mutations";

export function ForgotPassword() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");
  return (
    <section className="screen">
      <h1>Reset your password</h1>
      {token ? <ConfirmForm token={token} /> : <RequestForm />}
    </section>
  );
}

function RequestForm() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const request = useRequestPasswordReset();

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    await request.mutateAsync(email);
    // Sent regardless of outcome: a different message per outcome is the
    // account-enumeration oracle this endpoint is designed not to be.
    setSent(true);
  }

  if (sent) {
    return (
      <p>
        If an account exists for that address, we've sent a link to reset the password.
      </p>
    );
  }

  return (
    <form className="form" onSubmit={handleSubmit}>
      <div className="field">
        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </div>
      <div className="button-row">
        <button type="submit" className="button--primary" disabled={request.isPending}>
          Send reset link
        </button>
      </div>
      <p className="muted">
        <Link to="/sign-in">Back to sign in</Link>
      </p>
    </form>
  );
}

function ConfirmForm({ token }: { token: string }) {
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirm = useConfirmPasswordReset();

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await confirm.mutateAsync({ token, newPassword: password });
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That reset link no longer works.");
    }
  }

  if (done) {
    return (
      <p>
        Password updated. <Link to="/sign-in">Sign in</Link> with your new password.
      </p>
    );
  }

  return (
    <form className="form" onSubmit={handleSubmit}>
      <div className="field">
        <label htmlFor="new-password">New password</label>
        <input
          id="new-password"
          type="password"
          autoComplete="new-password"
          required
          minLength={12}
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
        <button type="submit" className="button--primary" disabled={confirm.isPending}>
          Set new password
        </button>
      </div>
    </form>
  );
}
