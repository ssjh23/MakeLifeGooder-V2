/**
 * Screens 02c and 02d: a single card's detail, archive and restore.
 */

import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../api/client";
import {
  useArchiveCard,
  useArchivePreview,
  useCard,
  useCards,
  useDeleteCardStatements,
  useRestoreCard,
  useUpdateCard,
} from "./queries";

const COLOUR_PATTERN = /^#[0-9A-Fa-f]{6}$/;
const LAST4_PATTERN = /^\d{4}$/;

export function CardDetail() {
  const { cardId } = useParams<{ cardId: string }>();
  const { data: card, isLoading } = useCard(cardId);

  if (isLoading) return <p className="muted">Loading card…</p>;
  if (!card) {
    return (
      <section className="screen">
        <p className="error" role="alert">
          No such card.
        </p>
        <Link to="/cards">Back to cards</Link>
      </section>
    );
  }

  return (
    <section className="screen">
      <p className="muted">
        <Link to="/cards">Cards</Link>
      </p>
      <h1>{card.nickname}</h1>
      <p className="muted">
        {card.institution} · •••• {card.last4} · <span className="badge badge--muted">{card.status}</span>
      </p>

      <EditCardForm
        cardId={card.id}
        nickname={card.nickname}
        colour={card.colour ?? null}
        institution={card.institution}
        last4={card.last4}
      />

      {card.archived ? <RestoreCard cardId={card.id} /> : <ArchiveCard cardId={card.id} />}

      <DeleteStatements cardId={card.id} statementCount={card.statement_count} />
    </section>
  );
}

function EditCardForm({
  cardId,
  nickname: initialNickname,
  colour: initialColour,
  institution: initialInstitution,
  last4: initialLast4,
}: {
  cardId: string;
  nickname: string;
  colour: string | null;
  institution: string;
  last4: string;
}) {
  const update = useUpdateCard();
  const [nickname, setNickname] = useState(initialNickname);
  const [colour, setColour] = useState(initialColour ?? "#3c7fc0");
  const [institution, setInstitution] = useState(initialInstitution);
  const [last4, setLast4] = useState(initialLast4);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSaved(false);
    if (colour && !COLOUR_PATTERN.test(colour)) {
      setError("Colour must be a hex code like #3c7fc0.");
      return;
    }
    if (!LAST4_PATTERN.test(last4)) {
      setError("Last 4 digits must be exactly four digits.");
      return;
    }
    if (!institution.trim()) {
      setError("Institution cannot be empty.");
      return;
    }
    try {
      await update.mutateAsync({ cardId, update: { nickname, colour, institution, last4 } });
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the card.");
    }
  }

  return (
    <form className="form panel" onSubmit={handleSubmit}>
      <h2>Edit card details</h2>
      <div className="field-row">
        <div className="field">
          <label htmlFor="nickname">Nickname</label>
          <input
            id="nickname"
            maxLength={100}
            value={nickname}
            onChange={(event) => setNickname(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="colour">Colour</label>
          <input
            id="colour"
            type="color"
            value={colour}
            onChange={(event) => setColour(event.target.value)}
          />
        </div>
      </div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="institution">Institution</label>
          <input
            id="institution"
            maxLength={100}
            value={institution}
            onChange={(event) => setInstitution(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="last4">Last 4 digits</label>
          <input
            id="last4"
            inputMode="numeric"
            maxLength={4}
            pattern="\d{4}"
            value={last4}
            onChange={(event) => setLast4(event.target.value.replace(/\D/g, "").slice(0, 4))}
          />
        </div>
      </div>
      <p className="muted">
        Corrections only — for a genuinely new physical card, add it separately instead.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {saved && <p className="muted">Saved.</p>}
      <div className="button-row">
        <button type="submit" disabled={update.isPending}>
          {update.isPending ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}

type Disposition = { action: "keep" | "reassign"; targetCardId: string | null };

function ArchiveCard({ cardId }: { cardId: string }) {
  const [confirming, setConfirming] = useState(false);
  const preview = useArchivePreview(confirming ? cardId : undefined);
  const otherCards = useCards();
  const archive = useArchiveCard();
  const [dispositions, setDispositions] = useState<Record<string, Disposition>>({});
  const [error, setError] = useState<string | null>(null);

  const alternatives = (otherCards.data ?? []).filter((c) => c.id !== cardId && !c.archived);

  function dispositionFor(statementId: string): Disposition {
    return dispositions[statementId] ?? { action: "keep", targetCardId: null };
  }

  function setDisposition(statementId: string, next: Partial<Disposition>) {
    setDispositions((prev) => ({ ...prev, [statementId]: { ...dispositionFor(statementId), ...next } }));
  }

  async function handleConfirm() {
    setError(null);
    if (!preview.data) return;
    const missing = preview.data.statements.find((statementId) => {
      const disposition = dispositionFor(statementId);
      return disposition.action === "reassign" && !disposition.targetCardId;
    });
    if (missing) {
      setError("Every statement needs a card to move to, or choose to keep it here.");
      return;
    }
    try {
      await archive.mutateAsync({
        cardId,
        request: {
          statement_dispositions: preview.data.statements.map((statementId) => {
            const disposition = dispositionFor(statementId);
            return {
              statement_id: statementId,
              action: disposition.action,
              target_card_id: disposition.action === "reassign" ? disposition.targetCardId : null,
            };
          }),
        },
      });
      setConfirming(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not archive the card.");
    }
  }

  if (!confirming) {
    return (
      <div className="panel">
        <h2>Archive this card</h2>
        <p className="muted">Reversible — an archived card can be restored later.</p>
        <button type="button" className="button--danger" onClick={() => setConfirming(true)}>
          Archive this card
        </button>
      </div>
    );
  }

  return (
    <div className="panel stack">
      <h2>Archive this card</h2>
      {preview.isLoading && <p className="muted">Working out the effect…</p>}
      {preview.data && (
        <>
          <p>
            {preview.data.totals_that_move} statement total
            {preview.data.totals_that_move === 1 ? "" : "s"} will move, and{" "}
            {preview.data.totals_unchanged} will stay as they are.
          </p>
          {preview.data.statements.length > 0 && (
            <ul className="list">
              {preview.data.statements.map((statementId) => {
                const disposition = dispositionFor(statementId);
                return (
                  <li key={statementId} className="list-item stack">
                    <code>{statementId}</code>
                    <div className="field-row">
                      <label className="checkbox-field">
                        <input
                          type="radio"
                          name={`disposition-${statementId}`}
                          checked={disposition.action === "keep"}
                          onChange={() => setDisposition(statementId, { action: "keep" })}
                        />
                        Keep on this card
                      </label>
                      <label className="checkbox-field">
                        <input
                          type="radio"
                          name={`disposition-${statementId}`}
                          checked={disposition.action === "reassign"}
                          onChange={() => setDisposition(statementId, { action: "reassign" })}
                        />
                        Reassign to…
                      </label>
                      {disposition.action === "reassign" && (
                        <select
                          value={disposition.targetCardId ?? ""}
                          onChange={(event) =>
                            setDisposition(statementId, { targetCardId: event.target.value })
                          }
                        >
                          <option value="">Choose a card</option>
                          {alternatives.map((alt) => (
                            <option key={alt.id} value={alt.id}>
                              {alt.nickname}
                            </option>
                          ))}
                        </select>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="button-row">
        <button type="button" className="button--danger" onClick={() => void handleConfirm()} disabled={archive.isPending}>
          {archive.isPending ? "Archiving…" : "Confirm archive"}
        </button>
        <button type="button" onClick={() => setConfirming(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function RestoreCard({ cardId }: { cardId: string }) {
  const restore = useRestoreCard();
  const [error, setError] = useState<string | null>(null);

  async function handleRestore() {
    setError(null);
    try {
      await restore.mutateAsync(cardId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not restore the card.");
    }
  }

  return (
    <div className="panel">
      <h2>This card is archived</h2>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <button type="button" className="button--primary" onClick={() => void handleRestore()} disabled={restore.isPending}>
        {restore.isPending ? "Restoring…" : "Restore this card"}
      </button>
    </div>
  );
}

function DeleteStatements({ cardId, statementCount }: { cardId: string; statementCount: number }) {
  const deleteStatements = useDeleteCardStatements();
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function handleDelete() {
    setError(null);
    try {
      await deleteStatements.mutateAsync(cardId);
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete the statements.");
    }
  }

  if (statementCount === 0) return null;

  return (
    <div className="panel stack">
      <h2>Delete all statements for this card</h2>
      <p className="error">
        Unlike archiving, this cannot be undone. It removes {statementCount} statement
        {statementCount === 1 ? "" : "s"} and everything imported from them.
      </p>
      {done ? (
        <p className="muted">Done. Those statements are gone.</p>
      ) : (
        <>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            I understand this cannot be undone.
          </label>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <div className="button-row">
            <button
              type="button"
              className="button--danger"
              disabled={!confirmed || deleteStatements.isPending}
              onClick={() => void handleDelete()}
            >
              {deleteStatements.isPending ? "Deleting…" : "Delete all statements"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
