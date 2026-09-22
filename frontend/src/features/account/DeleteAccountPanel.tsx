/**
 * Screen 08c: delete everything.
 *
 * Confirmation is typed, not clicked, because a checkbox is the kind of
 * thing a person hits by reflex and this is not recoverable (`DeleteAccountRequest`
 * requires the literal, case-sensitive string "DELETE").
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../api/client";
import { useAccountInventory, useDeleteAccount } from "./queries";

export function DeleteAccountPanel() {
  const inventory = useAccountInventory();
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const deleteAccount = useDeleteAccount();
  const navigate = useNavigate();

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await deleteAccount.mutateAsync({ confirmation: confirmation as "DELETE" });
      navigate("/sign-in", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete the account.");
    }
  }

  return (
    <div className="stack">
      <div className="banner">
        <h2 style={{ marginTop: 0 }}>This cannot be undone</h2>
        {inventory.data ? (
          <p>
            Deleting your account permanently removes {inventory.data.statements} statement(s),{" "}
            {inventory.data.transactions} transaction(s), {inventory.data.rules} rule(s),{" "}
            {inventory.data.categories} categor{inventory.data.categories === 1 ? "y" : "ies"} and{" "}
            {inventory.data.cards} card(s). You are signed out immediately; the data is purged
            shortly after.
          </p>
        ) : (
          <p className="muted">Loading what will be deleted…</p>
        )}
      </div>

      <form className="form" onSubmit={handleSubmit}>
        <div className="field">
          <label htmlFor="delete-confirm">
            Type <code>DELETE</code> to confirm
          </label>
          <input
            id="delete-confirm"
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            autoComplete="off"
          />
        </div>

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}

        <div className="button-row">
          <button
            type="submit"
            className="button--danger button--primary"
            disabled={confirmation !== "DELETE" || deleteAccount.isPending}
          >
            {deleteAccount.isPending ? "Deleting…" : "Permanently delete my account"}
          </button>
        </div>
      </form>
    </div>
  );
}
